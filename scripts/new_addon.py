"""Gera o esqueleto de um novo addon em `addons/<nome>/`.

Cria `manifest.json`, `plugin.py` (implementação mínima de `BaseAddon`, só
com `NotImplementedError` nos métodos abstratos) e `README.md`, seguindo a
mesma estrutura de `addons/archive_org/` e `addons/stremio/` — ver
`app/addon_system/base.py` para a interface completa.

Não registra o addon em lugar nenhum: ele é descoberto automaticamente por
`AddonManager.discover()` na próxima vez que o processo iniciar (ou via
`/addon reload <nome>` se `ADDONS_PATH` já existir em disco).

Uso:
    uv run python scripts/new_addon.py <nome>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

_ADDONS_DIR = Path(__file__).resolve().parents[1] / "addons"
_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

_MANIFEST_TEMPLATE = """\
{{
  "name": "{name}",
  "version": "0.1.0",
  "description": "TODO: descreva o que este addon faz.",
  "entrypoint": "plugin:Addon",
  "min_core_version": "0.1.0"
}}
"""

_PLUGIN_TEMPLATE = '''\
"""Addon `{name}`: TODO descreva a fonte de mídia que este addon resolve."""

from __future__ import annotations

from app.addon_system.base import AddonHealth, BaseAddon, Metadata, SearchResult, StreamCandidate


class Addon(BaseAddon):
    """Implementação de `BaseAddon` para `{name}`."""

    name = "{name}"
    version = "0.1.0"

    async def search(self, query: str) -> list[SearchResult]:
        raise NotImplementedError

    async def get_metadata(self, media_id: str) -> Metadata:
        raise NotImplementedError

    async def get_streams(self, media_id: str) -> list[StreamCandidate]:
        raise NotImplementedError

    async def health(self) -> AddonHealth:
        return AddonHealth(healthy=True)

    async def close(self) -> None:
        pass
'''

_README_TEMPLATE = """\
# {name}

TODO: descreva o que este addon faz e de onde ele busca mídia.

## O que faz

- `search(query)` — TODO
- `get_metadata(media_id)` — TODO
- `get_streams(media_id)` — TODO

## Configuração

TODO: se este addon aceitar config, documente o formato de
`config/addons/{name}.json` (ou `.yaml`/`.yml`) aqui. Se não aceitar
nenhuma, apague esta seção.

## Estrutura

```
addons/{name}/
  manifest.json   metadados do addon (nome, versão, entrypoint)
  plugin.py       implementação de BaseAddon
  README.md       este arquivo
```
"""


def _validate_name(name: str) -> None:
    if not _NAME_PATTERN.match(name):
        raise SystemExit(
            f"Nome de addon inválido: {name!r}. Use letras minúsculas, dígitos e "
            "underscore, começando por letra (ex.: meu_addon)."
        )


def create_addon(name: str) -> Path:
    """Cria `addons/<name>/` com manifest/plugin/README e devolve o caminho criado."""
    _validate_name(name)
    addon_dir = _ADDONS_DIR / name
    if addon_dir.exists():
        raise SystemExit(f"Já existe um addon em {addon_dir}.")

    addon_dir.mkdir(parents=True)
    (addon_dir / "manifest.json").write_text(_MANIFEST_TEMPLATE.format(name=name), encoding="utf-8")
    (addon_dir / "plugin.py").write_text(_PLUGIN_TEMPLATE.format(name=name), encoding="utf-8")
    (addon_dir / "README.md").write_text(_README_TEMPLATE.format(name=name), encoding="utf-8")
    return addon_dir


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Uso: uv run python scripts/new_addon.py <nome>")

    addon_dir = create_addon(sys.argv[1])
    print(f"Addon criado em {addon_dir}")
    print("Próximos passos:")
    print(f"  1. Implemente search/get_metadata/get_streams em {addon_dir / 'plugin.py'}")
    print(f"  2. Preencha {addon_dir / 'README.md'}")
    print("  3. Reinicie o processo (ou /addon reload, se já estiver rodando)")


if __name__ == "__main__":
    main()
