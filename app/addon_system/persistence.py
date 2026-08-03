"""Persistência de quais addons estão habilitados/desabilitados, sobrevive a restart."""

from __future__ import annotations

import json
from pathlib import Path

import aiofiles

from app.utils.logging import get_logger

_logger = get_logger("services")


class AddonStatePersistence:
    """Lê/grava `{nome_addon: enabled}` em `path`, em JSON."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> dict[str, bool]:
        """Estado salvo, ou `{}` se não existir ou estiver corrompido."""
        if not self._path.is_file():
            return {}
        try:
            async with aiofiles.open(self._path, encoding="utf-8") as handle:
                raw = await handle.read()
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as exc:
            _logger.error(
                "Estado de addons corrompido em {path}: {err}. Ignorando.", path=self._path, err=exc
            )
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(key): bool(value) for key, value in data.items()}

    async def save(self, state: dict[str, bool]) -> None:
        """Grava `state` de forma atômica (arquivo temporário + rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        payload = json.dumps(state, ensure_ascii=False, indent=2)
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as handle:
            await handle.write(payload)
        tmp_path.replace(self._path)
