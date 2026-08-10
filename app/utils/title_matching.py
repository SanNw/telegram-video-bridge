"""Comparação difusa (fuzzy) de títulos, usada pelo filtro de `/find`.

Função pura, sem dependência de TMDB/Pyrogram — testável isoladamente. Usa
`token_set_ratio` (em vez de `ratio` simples) porque tolera ruído comum em
títulos vindos de addons: sufixos como "1080p Dublado" ou "(2008)", e ordem
de palavras trocada, sem penalizar o score tanto quanto uma comparação
caractere-a-caractere.
"""

from __future__ import annotations

from collections.abc import Sequence

from rapidfuzz import fuzz


def matches_any(candidate: str, references: Sequence[str | None], threshold: float) -> bool:
    """`True` se `candidate` bate com qualquer string em `references` (score >= `threshold`).

    `references` vazias (ou só com strings vazias/`None`) nunca batem — devolve `False`.
    """
    return any(
        fuzz.token_set_ratio(candidate, reference) >= threshold
        for reference in references
        if reference
    )
