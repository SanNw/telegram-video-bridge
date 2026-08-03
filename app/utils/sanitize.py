"""Validação e sanitização de fontes de mídia recebidas de comandos do bot.

Toda entrada de `/play` passa por `resolve_source` antes de chegar a `FFmpegStreamer`.
Isso evita injeção de argumentos no FFmpeg (ex.: uma "fonte" que na verdade é uma flag)
e restringe caminhos locais ao diretório de mídia configurado.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlparse


class SourceType(StrEnum):
    """Classificação da fonte de mídia, usada por `FFmpegStreamer.build_command`."""

    LOCAL_FILE = "local_file"
    HTTP = "http"
    HLS = "hls"
    RTMP = "rtmp"
    RTSP = "rtsp"


class InvalidSourceError(ValueError):
    """Levantado quando a fonte fornecida pelo usuário é inválida ou insegura."""


_ALLOWED_LOCAL_EXTENSIONS = frozenset({".mp4", ".mkv", ".avi", ".mov"})
_ALLOWED_SCHEMES = frozenset({"http", "https", "rtmp", "rtmps", "rtsp"})
_CONTROL_CHARS = frozenset({"\n", "\r", "\x00"})


@dataclass(frozen=True, slots=True)
class MediaSource:
    """Fonte de mídia já validada e classificada, segura para repassar ao FFmpeg."""

    raw: str
    type: SourceType


def resolve_source(raw_source: str, media_root: Path) -> MediaSource:
    """Valida e classifica uma fonte de mídia informada via `/play <fonte>`.

    Levanta `InvalidSourceError` para: entrada vazia, entrada iniciando com "-"
    (evita ser interpretada como flag do FFmpeg), caracteres de controle, esquema
    de URL não suportado, caminho local fora de `media_root` ou com extensão não
    suportada, ou arquivo local inexistente.
    """
    candidate = raw_source.strip()
    if not candidate:
        raise InvalidSourceError("Fonte vazia.")
    if candidate.startswith("-"):
        raise InvalidSourceError(
            "Fonte não pode começar com '-' (evita injeção de flags no FFmpeg)."
        )
    if any(char in candidate for char in _CONTROL_CHARS):
        raise InvalidSourceError("Fonte contém caracteres de controle inválidos.")

    parsed = urlparse(candidate)
    if parsed.scheme:
        return _resolve_remote_source(candidate, parsed.scheme, parsed.netloc)
    return _resolve_local_source(candidate, media_root)


def _resolve_remote_source(candidate: str, scheme: str, netloc: str) -> MediaSource:
    if scheme not in _ALLOWED_SCHEMES:
        raise InvalidSourceError(f"Esquema de URL não suportado: {scheme!r}")
    if not netloc:
        raise InvalidSourceError("URL sem host válido.")
    if scheme in {"rtmp", "rtmps"}:
        return MediaSource(raw=candidate, type=SourceType.RTMP)
    if scheme == "rtsp":
        return MediaSource(raw=candidate, type=SourceType.RTSP)
    if candidate.lower().split("?", 1)[0].endswith(".m3u8"):
        return MediaSource(raw=candidate, type=SourceType.HLS)
    return MediaSource(raw=candidate, type=SourceType.HTTP)


def _resolve_local_source(candidate: str, media_root: Path) -> MediaSource:
    media_root_resolved = media_root.resolve()
    local_path = (media_root_resolved / candidate).resolve()

    if not local_path.is_relative_to(media_root_resolved):
        raise InvalidSourceError("Caminho local fora do diretório de mídia permitido.")
    if local_path.suffix.lower() not in _ALLOWED_LOCAL_EXTENSIONS:
        allowed = sorted(_ALLOWED_LOCAL_EXTENSIONS)
        raise InvalidSourceError(f"Extensão não suportada: {local_path.suffix!r}. Use: {allowed}.")
    if not local_path.is_file():
        raise InvalidSourceError(f"Arquivo não encontrado: {candidate!r}")

    return MediaSource(raw=str(local_path), type=SourceType.LOCAL_FILE)
