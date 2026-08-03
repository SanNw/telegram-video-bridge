"""Persistência da fila em disco (JSON), sobrevive a restart do processo.

Escrita atômica: grava em arquivo temporário e renomeia por cima do arquivo final,
evitando um `queue.json` truncado/corrompido caso o processo morra no meio da escrita.
Leitura corrompida (JSON inválido ou schema inesperado) é tratada como fila vazia,
nunca derruba o processo — é logada em `errors.log` para investigação.
"""

from __future__ import annotations

import json
from pathlib import Path

import aiofiles

from app.player.models import PlaybackState
from app.utils.logging import get_logger

_logger = get_logger("player")


class QueuePersistence:
    """Lê/grava o `PlaybackState` em `path`, em formato JSON."""

    def __init__(self, path: Path) -> None:
        self._path = path

    async def load(self) -> PlaybackState | None:
        """Carrega o estado salvo, ou `None` se não existir ou estiver corrompido."""
        if not self._path.exists():
            return None
        try:
            async with aiofiles.open(self._path, encoding="utf-8") as handle:
                raw = await handle.read()
            data = json.loads(raw)
            return PlaybackState.from_dict(data)
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            _logger.error(
                "Fila em disco corrompida em {path}: {err}. Iniciando com fila vazia.",
                path=self._path,
                err=exc,
            )
            return None

    async def save(self, state: PlaybackState) -> None:
        """Grava `state` de forma atômica (arquivo temporário + rename)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self._path.with_suffix(".tmp")
        payload = json.dumps(state.to_dict(), ensure_ascii=False, indent=2)
        async with aiofiles.open(tmp_path, "w", encoding="utf-8") as handle:
            await handle.write(payload)
        tmp_path.replace(self._path)
