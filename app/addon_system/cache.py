"""Cache TTL simples em memória, usado para não rebater buscas repetidas nos addons."""

from __future__ import annotations

import time


class TTLCache[K, V]:
    """Cache chave→valor com expiração por tempo de vida (`ttl_seconds`).

    Sem limite de tamanho nem eviction por LRU — o volume esperado (buscas de
    addon) é pequeno o bastante pra não justificar essa complexidade. Se isso
    mudar, é hora de trocar por algo como `cachetools.TTLCache`.
    """

    def __init__(self, ttl_seconds: float) -> None:
        self._ttl_seconds = ttl_seconds
        self._store: dict[K, tuple[float, V]] = {}

    def get(self, key: K) -> V | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if time.monotonic() >= expires_at:
            del self._store[key]
            return None
        return value

    def set(self, key: K, value: V) -> None:
        self._store[key] = (time.monotonic() + self._ttl_seconds, value)

    def clear(self) -> None:
        self._store.clear()
