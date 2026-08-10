"""Detecção heurística do idioma de um título/stream, para exibir como bandeira.

Nenhum addon expõe idioma como campo estruturado — a informação só existe
como texto solto no título ou na qualidade do stream (ex.: "Dublado",
"Legendado", "Dual Áudio", ou já como emoji de bandeira, convenção comum em
streams do Torrentio). `detect_language_flag` extrai essa informação por
palavra-chave, best-effort — quando nada bate, `None` (nenhuma bandeira
exibida, nunca um palpite errado).
"""

from __future__ import annotations

import re

_FLAG_BR = "🇧🇷"
_FLAG_US = "🇺🇸"

# Emoji de bandeira = par de "regional indicator symbols" (U+1F1E6-U+1F1FF).
# Streams do Torrentio às vezes já vêm com a bandeira embutida no nome —
# nesse caso não há o que adivinhar, só extrair.
_FLAG_EMOJI_RE = re.compile("[\U0001f1e6-\U0001f1ff]{2}")

# "Legendado"/"legenda" indica áudio original (majoritariamente inglês nos
# catálogos deste projeto) com legenda em português — por isso mapeia para
# a bandeira do idioma de ÁUDIO (US), não da legenda.
_DUAL_AUDIO_KEYWORDS = ("dual áudio", "dual audio", "dual-áudio", "dual-audio")
_DUBBED_KEYWORDS = ("dublado", "dublagem", "nacional")
_SUBTITLED_KEYWORDS = ("legendado", "legenda")


def detect_language_flag(text: str) -> str | None:
    """Devolve o emoji de bandeira do idioma detectado em `text`, ou `None`.

    Ordem de checagem: bandeira já embutida no texto > dual áudio > dublado >
    legendado. Nunca lança; entrada sem nenhum marcador conhecido devolve
    `None` (melhor omitir do que arriscar uma bandeira errada).
    """
    existing = _FLAG_EMOJI_RE.search(text)
    if existing is not None:
        return existing.group(0)

    lowered = text.lower()
    if any(keyword in lowered for keyword in _DUAL_AUDIO_KEYWORDS):
        return f"{_FLAG_BR}{_FLAG_US}"
    if any(keyword in lowered for keyword in _DUBBED_KEYWORDS):
        return _FLAG_BR
    if any(keyword in lowered for keyword in _SUBTITLED_KEYWORDS):
        return _FLAG_US
    return None
