"""Formatação de respostas do bot. Só lê dados de `services/`; nenhuma lógica de negócio."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.player.models import PlaybackState, QueueItem
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
        lines.extend(
            f"{position}. `{item.source.raw}`" for position, item in enumerate(state.items, start=1)
        )
    return "\n".join(lines)


def format_now_playing(item: QueueItem, started_at: datetime) -> str:
    """Formata a resposta de `/nowplaying`."""
    elapsed = datetime.now(started_at.tzinfo) - started_at
    return (
        f"▶️ Tocando agora: `{item.source.raw}`\n"
        f"Pedido por: `{item.requested_by}`\n"
        f"Tocando há: {format_timedelta(elapsed)}"
    )


def format_uptime(uptime: timedelta) -> str:
    """Formata a resposta de `/uptime`."""
    return f"Em execução há {format_timedelta(uptime)}."


def format_timedelta(delta: timedelta) -> str:
    """Formata uma duração como `1h 02m 03s` (omite unidades zeradas à esquerda)."""
    total_seconds = int(delta.total_seconds())
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {seconds:02d}s"
    if minutes:
        return f"{minutes}m {seconds:02d}s"
    return f"{seconds}s"
