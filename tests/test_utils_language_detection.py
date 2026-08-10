"""Testes de `app/utils/language_detection.py` (função pura)."""

from __future__ import annotations

from app.utils.language_detection import detect_language_flag


def test_detect_language_flag_dual_audio() -> None:
    assert detect_language_flag("Filme Dual Áudio 1080p") == "🇧🇷🇺🇸"


def test_detect_language_flag_dual_audio_hyphenated() -> None:
    assert detect_language_flag("Filme Dual-Audio") == "🇧🇷🇺🇸"


def test_detect_language_flag_dubbed() -> None:
    assert detect_language_flag("Filme Dublado 1080p") == "🇧🇷"


def test_detect_language_flag_dubbing_keyword() -> None:
    assert detect_language_flag("Dublagem Nacional") == "🇧🇷"


def test_detect_language_flag_nacional_keyword() -> None:
    assert detect_language_flag("Filme Nacional") == "🇧🇷"


def test_detect_language_flag_subtitled() -> None:
    assert detect_language_flag("Movie Legendado") == "🇺🇸"


def test_detect_language_flag_legenda_keyword() -> None:
    assert detect_language_flag("Movie com Legenda") == "🇺🇸"


def test_detect_language_flag_extracts_embedded_emoji() -> None:
    assert detect_language_flag("Movie 🇵🇹 1080p") == "🇵🇹"


def test_detect_language_flag_embedded_emoji_takes_priority() -> None:
    assert detect_language_flag("Filme Dublado 🇺🇸") == "🇺🇸"


def test_detect_language_flag_none_when_no_marker() -> None:
    assert detect_language_flag("Movie 1080p") is None


def test_detect_language_flag_case_insensitive() -> None:
    assert detect_language_flag("FILME DUBLADO") == "🇧🇷"
