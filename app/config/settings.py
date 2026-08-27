"""Configuração centralizada da aplicação via Pydantic Settings."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Todas as configurações da aplicação, carregadas de variáveis de ambiente ou `.env`.

    Nenhum valor sensível ou operacional deve ser hardcoded fora desta classe.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- Telegram / MTProto ---
    api_id: int
    api_hash: SecretStr
    session_string: SecretStr
    chat_id: int
    stream_chat_id: int | None = Field(
        default=None,
        description="Canal que recebe a live; vazio mantém CHAT_ID como destino.",
    )

    # --- Autorização do bot ---
    # NoDecode: sem isso, pydantic-settings tenta decodificar a env var como
    # JSON antes do field_validator rodar — "111" (um único ID) é um inteiro
    # JSON válido e "111,222" não é JSON válido nenhum, gerando ora um `int`
    # inesperado ora um erro de parsing. NoDecode entrega a string crua direto
    # para o validador abaixo, que faz o split por vírgula.
    # "all" (case-insensitive) autoriza qualquer membro atual do grupo em
    # `CHAT_ID` (checado em tempo real via get_chat_member) — não qualquer
    # usuário do Telegram. Ver app/bot/auth.py.
    authorized_user_ids: Annotated[list[int] | Literal["all"], NoDecode] = Field(
        default_factory=list,
        description=(
            "Whitelist de user_id do Telegram autorizados a controlar o bot, "
            "ou 'all' para qualquer membro do grupo em CHAT_ID."
        ),
    )
    # Ações de gerenciamento de addons (enable/disable/reload/uninstall) executam
    # código de terceiros no mesmo processo que tem a SESSION_STRING — não devem
    # ficar sob AUTHORIZED_USER_IDS=all (qualquer membro do grupo). Restritas a
    # este único user_id; None nega a todos (fail-safe). Ver app/bot/auth.py.
    owner_user_id: int | None = Field(
        default=None,
        description="user_id do Telegram autorizado a gerenciar addons (enable/disable/reload/uninstall).",
    )

    # --- Logging ---
    log_level: Literal["TRACE", "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    log_dir: Path = Path("logs")
    log_rotation: str = Field(
        default="10 MB", description="Política de rotação do Loguru (tamanho ou tempo)."
    )
    log_retention: str = Field(
        default="7 days", description="Política de retenção de logs antigos do Loguru."
    )

    # --- FFmpeg / mídia ---
    ffmpeg_path: str = "ffmpeg"
    media_path: Path = Path("media")
    ffmpeg_max_concurrent: int = Field(
        default=1, gt=0, description="Máximo de processos FFmpeg simultâneos."
    )
    ffmpeg_healthcheck_interval_seconds: float = Field(default=5.0, gt=0)
    ffmpeg_terminate_timeout_seconds: float = Field(
        default=5.0, gt=0, description="Prazo para saída graciosa (SIGTERM) antes de SIGKILL."
    )

    # --- Fila (player) ---
    queue_max_items: int = Field(default=50, gt=0, description="Limite máximo de itens na fila.")
    queue_data_path: Path = Path("data/queue.json")

    # --- Retry / resiliência (chamada e FFmpeg) ---
    retry_base_delay_seconds: float = Field(default=2.0, gt=0)
    retry_max_delay_seconds: float = Field(default=60.0, gt=0)
    retry_max_attempts: int = Field(
        default=8, gt=0, description="Tentativas antes de marcar falha permanente e alertar."
    )
    retry_jitter_seconds: float = Field(default=1.0, ge=0)

    # --- Addons ---
    addons_path: Path = Field(
        default=Path("addons"), description="Diretório com os addons instalados (um por subpasta)."
    )
    addons_state_path: Path = Field(
        default=Path("data/addons_state.json"),
        description="Onde persistir quais addons estão habilitados/desabilitados.",
    )
    addons_config_path: Path = Field(
        default=Path("config/addons"), description="Diretório com config própria de cada addon."
    )
    addon_search_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Timeout por addon numa chamada de /find."
    )
    addon_streams_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Timeout por addon ao resolver streams (/pick)."
    )
    addon_search_cache_ttl_seconds: float = Field(
        default=300.0, gt=0, description="TTL do cache de resultados de busca de addons."
    )

    # --- TMDB (metadados de filmes) ---
    # `None` desativa a integração por completo (nenhuma chamada é feita, /find
    # segue funcionando só com o que os addons já devolvem). Obtenha uma chave
    # em https://www.themoviedb.org/settings/api.
    tmdb_api_key: SecretStr | None = Field(
        default=None,
        description="Chave da API TMDB (v3, bearer token). Vazio desativa a integração.",
    )
    tmdb_language: str = Field(
        default="pt-BR", description="Idioma (ISO 639-1/BCP 47) dos metadados retornados pelo TMDB."
    )
    tmdb_request_timeout_seconds: float = Field(
        default=10.0, gt=0, description="Timeout de rede por requisição ao TMDB."
    )

    # --- OpenSubtitles (troca manual de legendas) ---
    opensubtitles_api_key: SecretStr | None = None
    opensubtitles_username: str | None = None
    opensubtitles_password: SecretStr | None = None
    opensubtitles_user_agent: str = "Telerion v1"

    # --- Bot (BotFather) ---
    # `None` mantém o comportamento atual: /find e /pick rodam no client de
    # sessão (SESSION_STRING), sem botões inline (o Telegram descarta
    # `reply_markup` em mensagens enviadas por conta de usuário). Setado, um
    # segundo Client autenticado via bot_token assume /find e /pick e envia
    # botões inline reais como forma primária de escolha. Ver app/main.py e
    # app/bot/client.py.
    bot_token: SecretStr | None = Field(
        default=None,
        description="Token do bot (BotFather) para enviar /find e /pick com botões inline. Vazio mantém o fallback de texto puro no client de sessão.",
    )

    # --- qBittorrent / Torrents ---
    # Suporte a streams que só trazem infoHash/magnet (ex.: Torrentio sem um
    # serviço de debrid configurado) — resolvidos via a Web API do qBittorrent
    # em vez de descartados. Ver app/services/torrent_service.py.
    qbittorrent_host: str = Field(
        default="localhost", description="Host da Web API do qBittorrent."
    )
    qbittorrent_port: int = Field(
        default=8080, gt=0, description="Porta da Web API do qBittorrent."
    )
    qbittorrent_username: str = Field(
        default="admin", description="Usuário da Web API do qBittorrent."
    )
    qbittorrent_password: SecretStr = Field(
        default=SecretStr(""), description="Senha da Web API do qBittorrent."
    )
    qbittorrent_category: str | None = Field(
        default=None,
        description="Categoria aplicada aos torrents adicionados pelo bot, se definida.",
    )
    # IMPORTANTE: precisa ser um caminho acessível localmente pelo processo do
    # bridge (mesma máquina ou volume compartilhado) — a Web API do qBittorrent
    # não expõe leitura de bytes, só metadados/controle; o FFmpeg lê o arquivo
    # direto do disco enquanto ele ainda está sendo baixado. Recomendado manter
    # dentro de `media_path` (mesmo diretório que `resolve_source` já confia).
    qbittorrent_save_path: Path = Field(
        default=Path("media/torrents"),
        description="Diretório de download dos torrents, acessível localmente pelo bridge.",
    )
    qbittorrent_local_path: Path = Field(
        default=Path("media/torrents"),
        description="Ponto onde o diretório do qBittorrent está montado dentro do bridge.",
    )
    torrent_buffer_mb: float = Field(
        default=50.0,
        gt=0,
        description="Buffer mínimo (MB) baixado do arquivo antes de liberar ao FFmpeg.",
    )
    torrent_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        description="Prazo para metadata chegar ou o buffer mínimo ser atingido antes de desistir do candidato.",
    )
    remove_torrent_after_play: bool = Field(
        default=False,
        description="Remove o torrent do qBittorrent ao fim da reprodução (mantém em seed se False).",
    )

    @field_validator("authorized_user_ids", mode="before")
    @classmethod
    def _split_authorized_user_ids(cls, value: object) -> object:
        # pydantic-settings tenta decodificar a env var como JSON antes deste
        # validador rodar. "111,222" não é JSON válido e chega aqui como str
        # (tratado abaixo) — mas um único ID, "111", É um inteiro JSON válido
        # e já chega decodificado como int, não como string.
        if isinstance(value, str):
            if value.strip().lower() == "all":
                return "all"
            return [int(item) for item in value.split(",") if item.strip()]
        if isinstance(value, int):
            return [value]
        return value

    @field_validator("retry_max_delay_seconds")
    @classmethod
    def _max_delay_not_below_base(cls, value: float, info: object) -> float:
        base = getattr(info, "data", {}).get("retry_base_delay_seconds")
        if base is not None and value < base:
            raise ValueError("retry_max_delay_seconds deve ser >= retry_base_delay_seconds")
        return value

    def masked_dict(self) -> dict[str, object]:
        """Retorna a configuração em dict com segredos mascarados, seguro para log/debug."""
        data = self.model_dump(mode="json")
        data["api_hash"] = "***MASKED***"
        data["session_string"] = "***MASKED***"
        if self.tmdb_api_key is not None:
            data["tmdb_api_key"] = "***MASKED***"
        if self.opensubtitles_api_key is not None:
            data["opensubtitles_api_key"] = "***MASKED***"
        if self.opensubtitles_password is not None:
            data["opensubtitles_password"] = "***MASKED***"
        if self.bot_token is not None:
            data["bot_token"] = "***MASKED***"
        data["qbittorrent_password"] = "***MASKED***"
        return data

    @property
    def opensubtitles_enabled(self) -> bool:
        return all(
            (
                self.opensubtitles_api_key,
                self.opensubtitles_username,
                self.opensubtitles_password,
            )
        )


@lru_cache
def get_settings() -> Settings:
    """Retorna a instância cacheada de `Settings` (singleton por processo)."""
    return Settings()
