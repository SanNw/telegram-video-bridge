"""Comparação difusa (fuzzy) de títulos, usada pelo filtro de `/find`.

Função pura, sem dependência de TMDB/Pyrogram — testável isoladamente. Usa
`token_set_ratio` (em vez de `ratio` simples) porque tolera ruído comum em
títulos vindos de addons: sufixos como "1080p Dublado" ou "(2008)", e ordem
de palavras trocada, sem penalizar o score tanto quanto uma comparação
caractere-a-caractere.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence

from rapidfuzz import fuzz

_RESOLUTION_RE = re.compile(r"(?<!\d)(720|1080|1440|2160|4320)p?\b", re.IGNORECASE)
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_RELEASE_NOISE_RE = re.compile(
    r"\b(?:bluray|blu-ray|web(?:-?dl)?|webrip|hdrip|hdr|dv|x26[45]|h26[45]|hevc|av1|dts|aac|ddp?\d?(?:\.\d)?|remux|dublado|legendado|dual(?:[\s-]?audio)?)\b",
    re.IGNORECASE,
)


def matches_any(candidate: str, references: Sequence[str | None], threshold: float) -> bool:
    """`True` se `candidate` bate com qualquer string em `references` (score >= `threshold`).

    `references` vazias (ou só com strings vazias/`None`) nunca batem — devolve `False`.
    """
    return any(
        fuzz.token_set_ratio(candidate, reference) >= threshold
        for reference in references
        if reference
    )


def _normalized_release(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    without_noise = _RELEASE_NOISE_RE.sub(" ", ascii_text)
    return " ".join(re.findall(r"[a-z0-9]+", without_noise.lower()))


def stream_resolution(text: str) -> int | None:
    match = _RESOLUTION_RE.search(text)
    if match:
        return int(match.group(1))
    lowered = text.lower()
    if re.search(r"(?<!\w)4k(?!\w)", lowered):
        return 2160
    if re.search(r"(?<!\w)2k(?!\w)", lowered):
        return 1440
    return None


def matches_movie_release(candidate: str, titles: Sequence[str | None], year: int | None) -> bool:
    normalized = _normalized_release(candidate)
    tokens = normalized.split()
    candidate_years = {int(value) for value in _YEAR_RE.findall(normalized)}
    if year is not None and candidate_years != {year}:
        return False
    candidate_title = " ".join(
        token
        for token in tokens
        if not _YEAR_RE.fullmatch(token) and stream_resolution(token) is None
    )
    return any(
        fuzz.ratio(candidate_title, _normalized_release(title)) >= 90 for title in titles if title
    )
