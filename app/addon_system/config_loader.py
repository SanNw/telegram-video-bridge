"""Carrega a config própria de um addon, de `config/addons/<nome>.{json,yaml,yml}`.

Suporta JSON e YAML (o mesmo dado, dois formatos — o addon nunca sabe qual
foi usado). Variáveis de ambiente não passam por aqui: um addon que precisa
delas lê `os.environ` diretamente, como qualquer código Python — não
duplicamos o mecanismo de `Settings` para isso.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml

from app.utils.logging import get_logger

_logger = get_logger("services")

_SUFFIXES = (".json", ".yaml", ".yml")


def load_addon_config(config_dir: Path, addon_name: str) -> dict[str, Any]:
    """Retorna a config de `addon_name` em `config_dir`, ou `{}` se não existir."""
    for suffix in _SUFFIXES:
        path = config_dir / f"{addon_name}{suffix}"
        if not path.is_file():
            continue
        try:
            raw = path.read_text(encoding="utf-8")
            data = yaml.safe_load(raw) if suffix in (".yaml", ".yml") else json.loads(raw)
        except (yaml.YAMLError, json.JSONDecodeError) as exc:
            _logger.error(
                "Config inválida para addon {name} em {path}: {err}",
                name=addon_name,
                path=path,
                err=exc,
            )
            return {}
        if not isinstance(data, dict):
            _logger.error(
                "Config de addon {name} em {path} não é um objeto/mapa; ignorando.",
                name=addon_name,
                path=path,
            )
            return {}
        return data
    return {}
