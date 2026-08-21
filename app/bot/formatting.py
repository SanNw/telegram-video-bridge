"""Formatação de respostas do bot. Só lê dados de `services/`; nenhuma lógica de negócio."""

from __future__ import annotations

from datetime import datetime, timedelta

from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.addon_system.base import AddonHealth, SearchResult, StreamCandidate
from app.addon_system.manager import AddonInfo
from app.player.models import PlaybackState, QueueItem
from app.services.models import ServiceStatus
from app.services.tmdb_service import TMDBMetadata, TMDBMovie
from app.utils.language_detection import detect_language_flag


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


def format_addons_list(addons: list[AddonInfo]) -> str:
    """Formata a resposta de `/addons`."""
    if not addons:
        return "Nenhum addon instalado."
    lines = ["*Addons instalados*"]
    lines.extend(
        f"{'✅' if addon.enabled else '⛔'} `{addon.name}` v{addon.version}" for addon in addons
    )
    return "\n".join(lines)


def format_addon_info(info: AddonInfo, health: AddonHealth) -> str:
    """Formata a resposta de `/addon info <nome>`."""
    status = "habilitado" if info.enabled else "desabilitado"
    health_text = "saudável" if health.healthy else f"com problema: {health.detail}"
    lines = [
        f"*{info.name}* v{info.version}",
        info.description or "_sem descrição_",
        f"Estado: {status}",
        f"Health: {health_text}",
    ]
    return "\n".join(lines)


def format_search_results(results: list[SearchResult]) -> str:
    """Formata a resposta de `/find`."""
    if not results:
        return "Nenhum resultado encontrado."
    lines = ["*Resultados*"]
    for position, result in enumerate(results, start=1):
        year_suffix = f" ({result.year})" if result.year else ""
        flag = detect_language_flag(result.title)
        flag_prefix = f"{flag} " if flag else ""
        lines.append(
            f"{position}. {flag_prefix}{result.title}{year_suffix} — _{result.addon_name}_"
        )
    lines.append("\nUse `/pick <número>` para adicionar à fila.")
    return "\n".join(lines)


def format_catalog_page(movies: list[TMDBMovie], page: int, page_size: int = 5) -> str:
    """Legenda compacta da seleção de filmes do TMDB."""
    total_pages = max(1, (len(movies) + page_size - 1) // page_size)
    start = page * page_size
    visible = movies[start : start + page_size]
    lines = ["🎬 *Escolha o filme*", f"Resultados do TMDB · página {page + 1}/{total_pages}"]
    for offset, movie in enumerate(visible, start=1):
        year = movie.release_date[:4] if movie.release_date else "—"
        rating = f"{movie.vote_average:.1f}" if movie.vote_average else "—"
        lines.append(f"\n{offset}. *{movie.title}* ({year}) · ⭐ {rating}")
    return "\n".join(lines)


def format_catalog_buttons(
    movies: list[TMDBMovie], page: int, page_size: int = 5
) -> InlineKeyboardMarkup:
    """Botões de seleção e paginação do catálogo TMDB."""
    start = page * page_size
    rows = [
        [InlineKeyboardButton(f"{offset}. {movie.title[:40]}", callback_data=f"movie:{index}")]
        for offset, (index, movie) in enumerate(
            zip(range(start, start + page_size), movies[start : start + page_size], strict=False),
            start=1,
        )
    ]
    navigation: list[InlineKeyboardButton] = []
    if page > 0:
        navigation.append(InlineKeyboardButton("◀ Anterior", callback_data=f"catalog:{page - 1}"))
    if start + page_size < len(movies):
        navigation.append(InlineKeyboardButton("Próxima ▶", callback_data=f"catalog:{page + 1}"))
    if navigation:
        rows.append(navigation)
    return InlineKeyboardMarkup(rows)


def format_movie_card(movie: TMDBMovie, metadata: TMDBMetadata) -> str:
    """Legenda rica exibida depois da escolha do filme."""
    year = metadata.release_date[:4] if metadata.release_date else "—"
    rating = f"{metadata.vote_average:.1f}" if metadata.vote_average else "—"
    genres = ", ".join(metadata.genres) or "—"
    overview = metadata.overview or "Sinopse não disponível em português."
    if len(overview) > 650:
        overview = overview[:647].rstrip() + "..."
    return (
        f"🎞 *{metadata.title}* ({year})\n"
        f"⭐ {rating} · {genres}\n\n{overview}\n\n"
        "Escolha uma fonte abaixo. A melhor opção aparece primeiro."
    )


def format_tmdb_rich_message(result: SearchResult, metadata: TMDBMetadata) -> str:
    """Formata o HTML de uma Rich Message (`InputRichMessage(html=...)`) com metadados do TMDB.

    Destaque do primeiro resultado de `/find`: título, pôster (`<img>`, mídia
    sempre como bloco separado — o protocolo de Rich Message não permite
    mídia inline), sinopse, carrossel de fotos do filme (`<tg-slideshow>`),
    carrossel de fotos do elenco + lista de nomes, e uma tabela com
    nota/gêneros/lançamento.

    O título e o ano exibidos vêm do próprio TMDB (`metadata.title` e
    `metadata.release_date`), não do `result` do addon — `result` já serve
    apenas de fallback de ano quando o TMDB não traz `release_date`.
    """
    year = (
        metadata.release_date[:4]
        if metadata.release_date
        else (str(result.year) if result.year else "")
    )
    year_suffix = f" ({year})" if year else ""
    lines = [f"<h2>{_escape_html(metadata.title)}{year_suffix}</h2>"]
    if metadata.poster_url:
        lines.append(f'<img src="{_escape_html(metadata.poster_url)}"/>')
    if metadata.overview:
        lines.append(f"<p>{_escape_html(metadata.overview)}</p>")

    if metadata.backdrop_urls:
        images = "".join(f'<img src="{_escape_html(url)}"/>' for url in metadata.backdrop_urls)
        lines.append(f"<tg-slideshow>{images}</tg-slideshow>")

    if metadata.cast:
        photo_urls = [member.profile_url for member in metadata.cast if member.profile_url]
        if photo_urls:
            images = "".join(f'<img src="{_escape_html(url)}"/>' for url in photo_urls)
            lines.append(f"<tg-slideshow>{images}</tg-slideshow>")
        names = ", ".join(_escape_html(member.name) for member in metadata.cast)
        lines.append(f"<p>Elenco: {names}</p>")

    rating = f"{metadata.vote_average:.1f}" if metadata.vote_average else "—"
    genres = ", ".join(metadata.genres) if metadata.genres else "—"
    release = metadata.release_date or "—"
    lines.append(
        "<table bordered>"
        "<tr><th>Nota</th><th>Gêneros</th><th>Lançamento</th></tr>"
        f"<tr><td>{_escape_html(rating)}</td>"
        f"<td>{_escape_html(genres)}</td>"
        f"<td>{_escape_html(release)}</td></tr>"
        "</table>"
    )
    return "".join(lines)


def format_stream_buttons(
    candidates: list[tuple[str, SearchResult, StreamCandidate]],
) -> InlineKeyboardMarkup:
    """Monta os botões inline da Rich Message de `/find`, um por addon resolvido.

    `candidates` vem de `AddonService.resolve_top_candidates()`: tokens curtos
    que identificam cada `(SearchResult, StreamCandidate)` para o handler de
    callback query resolver de volta em `AddonService.pick_candidate`.
    """
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    text=_format_stream_button_label(result.addon_name, candidate),
                    callback_data=f"play:{token}",
                )
            ]
            for token, result, candidate in candidates
        ]
    )


def _format_stream_button_label(addon_name: str, candidate: StreamCandidate) -> str:
    flag = detect_language_flag(f"{candidate.title} {candidate.quality or ''}")
    quality = f" ({candidate.quality})" if candidate.quality else ""
    label = f"▶️ {f'{flag} ' if flag else ''}{addon_name}{quality}"
    details: list[str] = []
    if candidate.size_bytes:
        details.append(f"{candidate.size_bytes / 1024**3:.1f} GB")
    if candidate.seeds is not None:
        details.append(f"{candidate.seeds} seeders")
    if details:
        label += " · " + " · ".join(details)
    return label[:64]


def _escape_html(text: str) -> str:
    """Escapa manualmente `&`, `<`, `>` — `InputRichMessage(html=...)` não escapa automaticamente."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
