from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.doctor import diagnose, main


def test_diagnose_accepts_ready_installation(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.doctor.shutil.which", lambda _command: "/usr/bin/ffmpeg")
    settings = make_settings(
        media_path=tmp_path / "media",
        log_dir=tmp_path / "logs",
        queue_data_path=tmp_path / "data" / "queue.json",
        stream_chat_id=1,
        owner_user_id=1,
        tmdb_api_key="token",
    )

    assert diagnose(settings) == ([], [])


def test_diagnose_reports_missing_ffmpeg(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.doctor.shutil.which", lambda _command: None)
    settings = make_settings(
        media_path=tmp_path / "media",
        log_dir=tmp_path / "logs",
        queue_data_path=tmp_path / "data" / "queue.json",
    )

    errors, _warnings = diagnose(settings)

    assert errors == ["FFmpeg não encontrado: ffmpeg"]


def test_diagnose_reports_unwritable_path(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("app.doctor.shutil.which", lambda _command: "/usr/bin/ffmpeg")
    blocked = tmp_path / "file"
    blocked.write_text("not a directory", encoding="utf-8")
    settings = make_settings(
        media_path=blocked,
        log_dir=tmp_path / "logs",
        queue_data_path=tmp_path / "data" / "queue.json",
    )

    errors, warnings = diagnose(settings)

    assert errors and errors[0].startswith("MEDIA_PATH não pode ser escrito")
    assert len(warnings) == 2


def test_main_returns_failure_for_diagnostic_error(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    monkeypatch.setattr("app.doctor.Settings", lambda: settings)
    monkeypatch.setattr("app.doctor.diagnose", lambda _settings: (["falha"], ["atenção"]))

    assert main() == 1


def test_main_returns_success(
    make_settings: Callable[..., Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = make_settings()
    monkeypatch.setattr("app.doctor.Settings", lambda: settings)
    monkeypatch.setattr("app.doctor.diagnose", lambda _settings: ([], []))

    assert main() == 0
