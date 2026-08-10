"""Testes de `app/utils/title_matching.py` (função pura)."""

from __future__ import annotations

from app.utils.title_matching import matches_any


def test_matches_any_true_when_candidate_matches_reference() -> None:
    assert matches_any(
        "O Espetacular Homem-Aranha (2008) 1080p Dublado",
        ["O Espetacular Homem-Aranha"],
        65.0,
    )


def test_matches_any_false_for_unrelated_title() -> None:
    assert not matches_any(
        "Doblajes de Clásicos de la Diversión",
        ["O Espetacular Homem-Aranha"],
        65.0,
    )


def test_matches_any_checks_every_reference() -> None:
    assert matches_any(
        "The Amazing Spider-Man 1080p",
        ["O Espetacular Homem-Aranha", "The Amazing Spider-Man"],
        65.0,
    )


def test_matches_any_false_when_no_reference_matches() -> None:
    assert not matches_any(
        "The Amazing Spider-Man 1080p",
        ["Nada a Ver", "Outro Filme Qualquer"],
        65.0,
    )


def test_matches_any_ignores_empty_references() -> None:
    assert not matches_any("Qualquer Título", ["", None], 55.0)


def test_matches_any_empty_references_list() -> None:
    assert not matches_any("Qualquer Título", [], 55.0)
