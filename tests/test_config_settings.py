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


def test_authorized_user_ids_all_is_case_insensitive(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings(authorized_user_ids="ALL")
    assert settings.authorized_user_ids == "all"


def test_authorized_user_ids_all_via_real_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "all")
    assert Settings().authorized_user_ids == "all"  # type: ignore[call-arg]


def _base_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Configura as env vars obrigatórias, simulando carregamento real (não kwargs diretos).

    Regressão: pydantic-settings tenta decodificar env vars de campos complexos
    como JSON antes de qualquer `field_validator` rodar. Um único ID
    ("AUTHORIZED_USER_IDS=111") É um inteiro JSON válido e chega já decodificado
    como `int`, diferente de "111,222" (não é JSON, chega como `str`) — só
    aparece passando por env vars de verdade, não construindo `Settings(**kwargs)`
    diretamente (por isso este teste usa `monkeypatch.setenv`, não `make_settings`).
    """
    monkeypatch.setenv("API_ID", "1")
    monkeypatch.setenv("API_HASH", "hash")
    monkeypatch.setenv("SESSION_STRING", "session")
    monkeypatch.setenv("CHAT_ID", "-100")
    monkeypatch.setenv("MEDIA_PATH", str(tmp_path))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("QUEUE_DATA_PATH", str(tmp_path / "queue.json"))
    monkeypatch.chdir(tmp_path)


def test_authorized_user_ids_single_value_via_real_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "111")
    assert Settings().authorized_user_ids == [111]  # type: ignore[call-arg]


def test_authorized_user_ids_multiple_values_via_real_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _base_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AUTHORIZED_USER_IDS", "111,222")
    assert Settings().authorized_user_ids == [111, 222]  # type: ignore[call-arg]


def test_masked_dict_never_exposes_secrets(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(api_hash="super-secret-hash", session_string="super-secret-session")
    masked = settings.masked_dict()
    dumped = str(masked)
    assert "super-secret-hash" not in dumped
    assert "super-secret-session" not in dumped
    assert masked["api_hash"] == "***MASKED***"
    assert masked["session_string"] == "***MASKED***"


def test_masked_dict_masks_tmdb_api_key_when_set(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings(tmdb_api_key="super-secret-tmdb-key")
    masked = settings.masked_dict()
    assert "super-secret-tmdb-key" not in str(masked)
    assert masked["tmdb_api_key"] == "***MASKED***"


def test_masked_dict_omits_tmdb_api_key_when_unset(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    masked = settings.masked_dict()
    assert masked["tmdb_api_key"] is None


def test_tmdb_defaults(make_settings: Callable[..., Settings]) -> None:
    settings = make_settings()
    assert settings.tmdb_api_key is None
    assert settings.tmdb_language == "pt-BR"
    assert settings.tmdb_request_timeout_seconds == 10.0


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
