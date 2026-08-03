"""Testes de `app/config/settings.py`."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config.settings import Settings, get_settings


def test_authorized_user_ids_parses_comma_separated_string(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids="111, 222,333")
    assert settings.authorized_user_ids == [111, 222, 333]


def test_authorized_user_ids_accepts_list_directly(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(authorized_user_ids=[1, 2])
    assert settings.authorized_user_ids == [1, 2]


def test_authorized_user_ids_empty_string_yields_empty_list(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids="")
    assert settings.authorized_user_ids == []


def test_masked_dict_never_exposes_secrets(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(api_hash="super-secret-hash", session_string="super-secret-session")
    masked = settings.masked_dict()
    dumped = str(masked)
    assert "super-secret-hash" not in dumped
    assert "super-secret-session" not in dumped
    assert masked["api_hash"] == "***MASKED***"
    assert masked["session_string"] == "***MASKED***"


def test_secret_str_repr_does_not_leak_value(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(api_hash="super-secret-hash")
    assert "super-secret-hash" not in repr(settings.api_hash)


def test_retry_max_delay_below_base_raises(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        make_settings(retry_base_delay_seconds=10.0, retry_max_delay_seconds=1.0)


def test_queue_max_items_must_be_positive(make_settings: Callable[..., Settings]) -> None:
    with pytest.raises(ValidationError):
        make_settings(queue_max_items=0)


def test_missing_required_field_raises(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        Settings(
            api_hash="x",
            session_string="x",
            chat_id=1,
            log_dir=tmp_path,
            media_path=tmp_path,
            queue_data_path=tmp_path / "q.json",
        )  # type: ignore[call-arg]


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("SESSION_STRING", "session")
    monkeypatch.setenv("CHAT_ID", "-100")
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("QUEUE_DATA_PATH", str(tmp_path / "queue.json"))
    monkeypatch.chdir(tmp_path)

    first = get_settings()
    second = get_settings()
    assert first is second
    get_settings.cache_clear()
