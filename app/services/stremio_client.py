"""Cliente HTTP para consumir addons Stremio externos (protocolo HTTP/JSON).

Diferente de `app.addon_system` (addons Python nativos, carregados in-process
via `importlib`), este cliente fala com addons Stremio de terceiros — tipicamente
processos Node.js expondo sua própria API REST em `http://host:porta`. O bot não
reescreve esses addons, apenas consome os endpoints que o protocolo Stremio já
define: `/manifest.json`, `/catalog`, `/stream`, `/meta` e `/subtitles`.

Cada instância de `StremioAddonClient` fala com um único addon; para consumir
vários addons em paralelo, mantenha uma instância por `base_url` (ver
`StremioAddonRegistry` e o exemplo de uso no final do arquivo).
"""

from __future__ import annotations

from typing import Any

import httpx

from app.utils.logging import get_logger

_logger = get_logger("services")

_DEFAULT_TIMEOUT_SECONDS = 10.0
# Alguns addons Stremio (ex.: Torrentio) ficam atrás de proteção anti-bot que
# rejeita com 403 o User-Agent padrão do httpx ("python-httpx/x.y.z"). Um
# User-Agent de navegador comum contorna o filtro sem alterar o protocolo.
_DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class StremioAddonClient:
    """Cliente assíncrono para um único addon Stremio, identificado por `base_url`.

    Nenhum método levanta exceção por falha de rede ou payload inválido: erros
    são logados e o chamador recebe uma estrutura vazia (`{}` ou `[]`), para que
    um addon fora do ar nunca derrube o bot.
    """

    def __init__(self, base_url: str, timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS) -> None:
        """Inicializa o cliente para o addon hospedado em `base_url`.

        Args:
            base_url: URL base do addon (ex.: "http://localhost:7000" ou uma
                URL pública). A barra final, se houver, é removida.
            timeout_seconds: Timeout de rede aplicado a toda requisição.
        """
        self._base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(
            timeout=timeout_seconds,
            headers={"User-Agent": _DEFAULT_USER_AGENT},
            follow_redirects=True,
        )

    @property
    def base_url(self) -> str:
        """URL base do addon, sem barra final."""
        return self._base_url

    async def close(self) -> None:
        """Fecha a conexão HTTP subjacente. Chame ao descartar o cliente."""
        await self._client.aclose()

    async def get_manifest(self) -> dict[str, Any]:
        """Busca `/manifest.json` do addon.

        Returns:
            Dict com os campos do manifesto Stremio (`id`, `name`, `types`,
            `resources`, `catalogs`, etc.), ou `{}` se a requisição falhar.
        """
        return await self._get_json(f"{self._base_url}/manifest.json")

    async def get_catalog(
        self, type_: str, catalog_id: str, extra: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """Busca um catálogo de mídias.

        Args:
            type_: Tipo de mídia Stremio (ex.: "movie", "series").
            catalog_id: Identificador do catálogo, conforme anunciado no manifesto.
            extra: Filtros extras opcionais (ex.: `{"genre": "Horror", "skip": 20}`),
                codificados como `chave=valor` separados por `&` na URL, seguindo
                o protocolo Stremio.

        Returns:
            Lista de `metas` do catálogo, ou `[]` se a requisição falhar ou o
            campo `metas` estiver ausente.
        """
        url = f"{self._base_url}/catalog/{type_}/{catalog_id}"
        if extra:
            extra_path = "&".join(f"{key}={value}" for key, value in extra.items())
            url = f"{url}/{extra_path}"
        url = f"{url}.json"
        data = await self._get_json(url)
        metas = data.get("metas", [])
        return metas if isinstance(metas, list) else []

    async def get_streams(self, type_: str, id_: str) -> list[dict[str, Any]]:
        """Busca as fontes reproduzíveis (`streams`) de uma mídia.

        Args:
            type_: Tipo de mídia Stremio (ex.: "movie", "series").
            id_: Identificador da mídia (ex.: um IMDb ID).

        Returns:
            Lista de `streams`, ou `[]` se a requisição falhar ou o campo
            `streams` estiver ausente.
        """
        data = await self._get_json(f"{self._base_url}/stream/{type_}/{id_}.json")
        streams = data.get("streams", [])
        return streams if isinstance(streams, list) else []

    async def get_meta(self, type_: str, id_: str) -> dict[str, Any]:
        """Busca os metadados detalhados de uma mídia.

        Args:
            type_: Tipo de mídia Stremio (ex.: "movie", "series").
            id_: Identificador da mídia (ex.: um IMDb ID).

        Returns:
            Dict de `meta`, ou `{}` se a requisição falhar ou o campo `meta`
            estiver ausente.
        """
        data = await self._get_json(f"{self._base_url}/meta/{type_}/{id_}.json")
        meta = data.get("meta", {})
        return meta if isinstance(meta, dict) else {}

    async def get_subtitles(self, type_: str, id_: str) -> list[dict[str, Any]]:
        """Busca legendas disponíveis para uma mídia, se o addon suportar o recurso.

        Args:
            type_: Tipo de mídia Stremio (ex.: "movie", "series").
            id_: Identificador da mídia (ex.: um IMDb ID).

        Returns:
            Lista de `subtitles`, ou `[]` se o addon não suportar o recurso, a
            requisição falhar, ou o campo `subtitles` estiver ausente.
        """
        data = await self._get_json(f"{self._base_url}/subtitles/{type_}/{id_}.json")
        subtitles = data.get("subtitles", [])
        return subtitles if isinstance(subtitles, list) else []

    async def download_subtitle(self, url: str) -> bytes | None:
        """Baixa uma legenda UTF-8, limitada a 2 MB."""
        if not url.startswith("https://"):
            return None
        try:
            response = await self._client.get(url)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            _logger.warning("Falha ao baixar legenda: {err}", err=exc)
            return None
        if len(response.content) > 2 * 1024 * 1024:
            _logger.warning("Legenda descartada por exceder 2 MB.")
            return None
        return response.content

    async def _get_json(self, url: str) -> dict[str, Any]:
        """GET genérico com tratamento de timeout, status HTTP e JSON malformado.

        Nunca levanta: qualquer falha vira log + `{}`, para que os métodos
        públicos possam degradar para lista/dict vazio sem propagar a exceção.
        """
        try:
            response = await self._client.get(url)
            response.raise_for_status()
            data = response.json()
        except httpx.TimeoutException:
            _logger.error("Timeout ao consultar addon Stremio: {url}", url=url)
            return {}
        except httpx.HTTPStatusError as exc:
            _logger.error(
                "Addon Stremio respondeu {status}: {url}",
                status=exc.response.status_code,
                url=url,
            )
            return {}
        except httpx.HTTPError as exc:
            _logger.error("Erro de rede ao consultar addon Stremio {url}: {err}", url=url, err=exc)
            return {}
        except ValueError as exc:  # json.JSONDecodeError é subclasse de ValueError
            _logger.error("JSON inválido de addon Stremio {url}: {err}", url=url, err=exc)
            return {}
        if not isinstance(data, dict):
            _logger.error("Resposta inesperada (não é objeto JSON) de {url}", url=url)
            return {}
        return data


class StremioAddonRegistry:
    """Registro de múltiplos `StremioAddonClient`, um por addon, indexados por nome.

    Conveniência para o bot orquestrar vários addons Stremio simultaneamente
    sem precisar gerenciar o ciclo de vida de cada `httpx.AsyncClient` manualmente.
    """

    def __init__(self) -> None:
        self._clients: dict[str, StremioAddonClient] = {}

    def register(self, name: str, base_url: str) -> StremioAddonClient:
        """Cria e registra um cliente para o addon `name`, hospedado em `base_url`."""
        client = StremioAddonClient(base_url)
        self._clients[name] = client
        return client

    def get(self, name: str) -> StremioAddonClient | None:
        """Retorna o cliente registrado sob `name`, ou `None` se não existir."""
        return self._clients.get(name)

    def all(self) -> dict[str, StremioAddonClient]:
        """Todos os clientes registrados, indexados por nome."""
        return dict(self._clients)

    async def close_all(self) -> None:
        """Fecha a conexão HTTP de todos os clientes registrados."""
        for client in self._clients.values():
            await client.close()


if __name__ == "__main__":
    import asyncio

    async def _exemplo_de_uso() -> None:
        """Exemplo: registra dois addons Stremio, busca streams e formata para o Telegram."""
        registry = StremioAddonRegistry()
        registry.register("cinemeta", "https://v3-cinemeta.strem.io")
        registry.register("local", "http://localhost:7000")

        try:
            cinemeta = registry.get("cinemeta")
            assert cinemeta is not None

            manifest = await cinemeta.get_manifest()
            print(f"Addon conectado: {manifest.get('name', '?')} v{manifest.get('version', '?')}")

            # IMDb ID de exemplo (Big Buck Bunny não tem IMDb; use um filme real aqui).
            imdb_id = "tt1254207"
            streams = await cinemeta.get_streams("movie", imdb_id)

            if not streams:
                print("Nenhuma fonte encontrada.")
                return

            # Formato texto simples, para uma mensagem do bot.
            linhas = [f"🎬 Fontes disponíveis ({len(streams)}):"]
            for i, stream in enumerate(streams, start=1):
                titulo = stream.get("title") or stream.get("name") or "Fonte sem título"
                linhas.append(f"{i}. {titulo}")
            print("\n".join(linhas))

            # Formato botões inline, para python-telegram-bot ou aiogram.
            # (Import local só para o exemplo não exigir a dependência em tempo de import.)
            botoes_inline_pyrogram_estilo = [
                [{"text": stream.get("title") or f"Fonte {i}", "callback_data": f"play:{i}"}]
                for i, stream in enumerate(streams, start=1)
            ]
            print(f"Botões inline: {botoes_inline_pyrogram_estilo}")
        finally:
            await registry.close_all()

    asyncio.run(_exemplo_de_uso())
