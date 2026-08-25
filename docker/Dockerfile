# syntax=docker/dockerfile:1

# --- Stage 1: build o venv com uv (dependências travadas via uv.lock) ---
FROM python:3.13-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.8.17 /uv /uvx /usr/local/bin/

# tgcrypto (dependência do Pyrogram) não publica wheel pré-compilada para
# todo ambiente e precisa compilar sua extensão C — build-essential fica só
# neste estágio, não vai para a imagem final.
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends build-essential

WORKDIR /build
COPY pyproject.toml uv.lock README.md LICENSE ./
# --no-install-project: só as dependências de terceiros vão para o venv. O
# código da aplicação (app/) é copiado direto na imagem final e executado via
# `python -m app.main` (CWD entra no sys.path) — não precisamos instalar o
# próprio pacote, o que evita um link de editable-install apontando de volta
# para este estágio de build (que não existe na imagem final).
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- Stage 2: imagem final, só com o venv pronto + FFmpeg ---
FROM python:3.13-slim AS runtime

RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt/lists,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg

RUN useradd --create-home --uid 1000 bridge

WORKDIR /app

COPY --chown=bridge:bridge --from=builder /build/.venv /app/.venv
COPY --chown=bridge:bridge app ./app
COPY --chown=bridge:bridge addons ./addons
COPY --chown=bridge:bridge config ./config

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Caminhos padrão em Settings (media/, logs/, data/queue.json) são relativos ao
# diretório de trabalho — batem com os pontos de montagem dos volumes abaixo
# sem precisar de override aqui, mantendo "tudo via .env" (ver .env.example).
RUN mkdir -p /app/media /app/logs /app/data \
    && chown bridge:bridge /app/media /app/logs /app/data

USER bridge

VOLUME ["/app/logs", "/app/media", "/app/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os; os.kill(1, 0)"]

CMD ["python", "-m", "app.main"]
