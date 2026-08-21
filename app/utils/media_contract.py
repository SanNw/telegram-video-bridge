"""Contrato de formato raw entre `streaming/` (produtor) e `telegram/` (consumidor).

`FFmpegStreamer` escreve vídeo/áudio crus nestes pipes nomeados; `TelegramCallManager`
os lê via streams raw com `MediaSource.FILE` do py-tgcalls. As duas camadas não se importam entre si
(mantendo a independência exigida pela arquitetura), então estas constantes — a
única fonte de verdade do formato — vivem em `utils/`, importável por ambas.
Mudar um valor aqui sem mudar o outro lado quebraria o pipeline silenciosamente.
"""

from __future__ import annotations

VIDEO_WIDTH = 1280
VIDEO_HEIGHT = 720
VIDEO_FPS = 30
VIDEO_PIXEL_FORMAT = "yuv420p"

AUDIO_SAMPLE_RATE = 48000
AUDIO_CHANNELS = 2
AUDIO_SAMPLE_FORMAT = "s16le"
