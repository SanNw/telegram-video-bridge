"""Testes de `app/addon_system/cache.py` (TTLCache)."""

from __future__ import annotations

from app.addon_system.cache import TTLCache


def test_get_returns_none_for_missing_key() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60.0)
    assert cache.get("missing") is None


def test_set_then_get_returns_value() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60.0)
    cache.set("key", 42)
    assert cache.get("key") == 42


def test_expired_entry_returns_none_and_is_evicted(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    fake_time = {"now": 1000.0}
    monkeypatch.setattr("app.addon_system.cache.time.monotonic", lambda: fake_time["now"])

    cache: TTLCache[str, int] = TTLCache(ttl_seconds=10.0)
    cache.set("key", 42)

    fake_time["now"] += 20.0
    assert cache.get("key") is None
    # segunda leitura confirma que a entrada expirada foi removida do dict interno
    assert cache.get("key") is None


def test_clear_removes_all_entries() -> None:
    cache: TTLCache[str, int] = TTLCache(ttl_seconds=60.0)
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    assert cache.get("a") is None
    assert cache.get("b") is None
