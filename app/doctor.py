"""Verifica se a instalação possui o mínimo necessário para iniciar."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

from pydantic import ValidationError

from app.config.settings import Settings


def diagnose(settings: Settings) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    if shutil.which(settings.ffmpeg_path) is None:
        errors.append(f"FFmpeg não encontrado: {settings.ffmpeg_path}")

    for label, path in (
        ("MEDIA_PATH", settings.media_path),
        ("LOG_DIR", settings.log_dir),
        ("QUEUE_DATA_PATH", settings.queue_data_path.parent),
    ):
        _check_writable_directory(label, path, errors)

    if settings.bot_token is not None and settings.owner_user_id is None:
        warnings.append("OWNER_USER_ID vazio: ninguém poderá gerenciar addons.")
    if settings.tmdb_api_key is None:
        warnings.append("TMDB_API_KEY vazio: catálogo enriquecido indisponível.")
    if settings.stream_chat_id is None:
        warnings.append("STREAM_CHAT_ID vazio: CHAT_ID será usado como destino da live.")

    return errors, warnings


def _check_writable_directory(label: str, path: Path, errors: list[str]) -> None:
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".telerion-write-check"
        probe.touch()
        probe.unlink()
    except OSError as exc:
        errors.append(f"{label} não pode ser escrito ({path}): {exc}")


def main() -> int:
    try:
        settings = Settings()
    except ValidationError as exc:
        print("ERRO: configuração inválida", file=sys.stderr)
        print(exc, file=sys.stderr)
        return 1

    errors, warnings = diagnose(settings)
    for warning in warnings:
        print(f"AVISO: {warning}")
    for error in errors:
        print(f"ERRO: {error}", file=sys.stderr)
    if errors:
        return 1
    print("OK: configuração, diretórios e FFmpeg estão prontos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
