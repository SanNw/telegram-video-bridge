"""Testes de `app/utils/title_matching.py` (função pura)."""

from __future__ import annotations

import pytest

from app.utils.title_matching import matches_any, matches_movie_release, stream_resolution


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


def test_matches_movie_release_accepts_release_tags_and_accents() -> None:
    assert matches_movie_release(
        "O.Fabuloso.Destino.de.Amelie.Poulain.2001.1080p.BluRay.x264.DTS",
        ["O Fabuloso Destino de Amélie Poulain", None],
        2001,
    )


def test_matches_movie_release_rejects_wrong_sequel() -> None:
    assert not matches_movie_release("The Matrix Reloaded 1999 1080p", ["The Matrix"], 1999)


def test_matches_movie_release_rejects_wrong_year() -> None:
    assert not matches_movie_release("The Matrix 2021 1080p", ["The Matrix"], 1999)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1080p BluRay", 1080),
        ("720p WEB-DL", 720),
        ("1440p WEB", 1440),
        ("2160p 4K", 2160),
        ("4320p 8K", 4320),
        ("release 4K HDR", 2160),
        ("release 2K HDR", 1440),
        ("no quality", None),
    ],
)
def test_stream_resolution(text: str, expected: int | None) -> None:
    assert stream_resolution(text) == expected
