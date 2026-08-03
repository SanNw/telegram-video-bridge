"""Formatação de respostas do bot. Só lê dados de `services/`; nenhuma lógica de negócio."""

from __future__ import annotations

from app.player.models import PlaybackState
from app.services.models import ServiceStatus


def format_status(status: ServiceStatus) -> str:
    """Formata a resposta de `/status`."""
    source_suffix = (
        f" (fonte: `{status.streaming.current_source}`)" if status.streaming.current_source else ""
    )
    lines = [
        "*Status*",
        f"Streaming: `{status.streaming.state.value}`{source_suffix}",
        f"Reinícios do FFmpeg: {status.streaming.restart_count}",
        f"Chamada: `{status.call.state.value}` (reconexões: {status.call.reconnect_count})",
        f"Fila: {status.queue_length} item(ns) pendente(s) — loop: `{status.loop_mode.value}`",
    ]
    if status.degraded:
        lines.append(f"⚠️ Degradado: {status.degraded_reason}")
    return "\n".join(lines)


def format_queue(state: PlaybackState) -> str:
    """Formata a resposta de `/queue`."""
    if state.current is None and not state.items:
        return "Fila vazia."
    lines: list[str] = []
    if state.current is not None:
        lines.append(f"▶️ Tocando agora: `{state.current.source.raw}`")
    if state.items:
        lines.append("\nPróximos:")
        lines.extend(f"{position}. `{item.source.raw}`" for position, item in enumerate(state.items, start=1))
    return "\n".join(lines)
