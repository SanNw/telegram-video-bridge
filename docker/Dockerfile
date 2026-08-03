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
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY pyproject.toml uv.lock README.md ./
# --no-install-project: só as dependências de terceiros vão para o venv. O
# código da aplicação (app/) é copiado direto na imagem final e executado via
# `python -m app.main` (CWD entra no sys.path) — não precisamos instalar o
# próprio pacote, o que evita um link de editable-install apontando de volta
# para este estágio de build (que não existe na imagem final).
RUN uv sync --frozen --no-dev --no-install-project

# --- Stage 2: imagem final, só com o venv pronto + FFmpeg ---
FROM python:3.13-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1000 bridge

WORKDIR /app

COPY --from=builder /build/.venv /app/.venv
COPY app ./app

ENV PATH="/app/.venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1

# Caminhos padrão em Settings (media/, logs/, data/queue.json) são relativos ao
# diretório de trabalho — batem com os pontos de montagem dos volumes abaixo
# sem precisar de override aqui, mantendo "tudo via .env" (ver .env.example).
RUN mkdir -p /app/media /app/logs /app/data \
    && chown -R bridge:bridge /app

USER bridge

VOLUME ["/app/logs", "/app/media", "/app/data"]

CMD ["python", "-m", "app.main"]
