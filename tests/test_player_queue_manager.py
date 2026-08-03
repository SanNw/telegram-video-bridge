"""Testes de `app/player/queue_manager.py`, incluindo persistência e fila corrompida."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.player.exceptions import InvalidQueueIndexError, QueueFullError
from app.player.models import LoopMode, QueueItem
from app.player.queue_manager import QueueManager
from app.utils.sanitize import MediaSource, SourceType


def _make_item(name: str = "video.mp4", user_id: int = 1) -> QueueItem:
    return QueueItem(
        source=MediaSource(raw=f"/media/{name}", type=SourceType.LOCAL_FILE), requested_by=user_id
    )


async def test_add_increments_position_and_returns_it(
    make_settings: Callable[..., Settings],
) -> None:
    manager = QueueManager(make_settings())
    position = await manager.add(_make_item("a.mp4"))
    assert position == 1
    position = await manager.add(_make_item("b.mp4"))
    assert position == 2


async def test_add_raises_when_queue_full(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings(queue_max_items=1))
    await manager.add(_make_item("a.mp4"))
    with pytest.raises(QueueFullError):
        await manager.add(_make_item("b.mp4"))


async def test_advance_pops_fifo_order(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))

    first = await manager.advance()
    second = await manager.advance()

    assert first is not None and first.source.raw.endswith("a.mp4")
    assert second is not None and second.source.raw.endswith("b.mp4")


async def test_advance_returns_none_when_empty(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings())
    assert await manager.advance() is None


async def test_remove_by_position(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))

    removed = await manager.remove(1)
    assert removed.source.raw.endswith("a.mp4")
    snapshot = manager.snapshot()
    assert len(snapshot.items) == 1
    assert snapshot.items[0].source.raw.endswith("b.mp4")


async def test_remove_invalid_position_raises(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings())
    with pytest.raises(InvalidQueueIndexError):
        await manager.remove(1)
    await manager.add(_make_item())
    with pytest.raises(InvalidQueueIndexError):
        await manager.remove(0)
    with pytest.raises(InvalidQueueIndexError):
        await manager.remove(99)


async def test_clear_empties_pending_but_keeps_current(
    make_settings: Callable[..., Settings],
) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))
    await manager.advance()  # a.mp4 vira current

    await manager.clear()

    snapshot = manager.snapshot()
    assert snapshot.items == []
    assert snapshot.current is not None
    assert snapshot.current.source.raw.endswith("a.mp4")


async def test_loop_item_replays_same_item_on_advance(
    make_settings: Callable[..., Settings],
) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.set_loop_mode(LoopMode.ITEM)

    first = await manager.advance()
    second = await manager.advance()

    assert first is second is not None
    assert first.source.raw.endswith("a.mp4")


async def test_skip_ignores_item_loop(make_settings: Callable[..., Settings]) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))
    await manager.set_loop_mode(LoopMode.ITEM)

    await manager.advance()  # current = a.mp4
    next_item = await manager.skip()  # deve ir para b.mp4, ignorando loop de item

    assert next_item is not None
    assert next_item.source.raw.endswith("b.mp4")


async def test_loop_queue_requeues_current_at_the_back(
    make_settings: Callable[..., Settings],
) -> None:
    manager = QueueManager(make_settings())
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))
    await manager.set_loop_mode(LoopMode.QUEUE)

    first = await manager.advance()  # current = a.mp4
    second = await manager.advance()  # current = b.mp4, a.mp4 volta pro fim
    third = await manager.advance()  # current = a.mp4 de novo

    assert first is not None and first.source.raw.endswith("a.mp4")
    assert second is not None and second.source.raw.endswith("b.mp4")
    assert third is not None and third.source.raw.endswith("a.mp4")


async def test_persistence_roundtrip_survives_new_manager_instance(
    make_settings: Callable[..., Settings],
) -> None:
    settings = make_settings()
    manager = QueueManager(settings)
    await manager.add(_make_item("a.mp4"))
    await manager.add(_make_item("b.mp4"))
    await manager.advance()
    await manager.set_loop_mode(LoopMode.QUEUE)

    restarted = QueueManager(settings)
    await restarted.load()

    snapshot = restarted.snapshot()
    assert snapshot.loop_mode is LoopMode.QUEUE
    assert snapshot.current is not None and snapshot.current.source.raw.endswith("a.mp4")
    assert len(snapshot.items) == 1
    assert snapshot.items[0].source.raw.endswith("b.mp4")


async def test_load_with_no_file_on_disk_starts_empty(
    make_settings: Callable[..., Settings],
) -> None:
    manager = QueueManager(make_settings())
    await manager.load()
    snapshot = manager.snapshot()
    assert snapshot.items == []
    assert snapshot.current is None


async def test_load_with_corrupted_json_falls_back_to_empty_queue(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    queue_path = tmp_path / "data" / "queue.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text("{ isso nao é json válido ]]]")

    settings = make_settings(queue_data_path=queue_path)
    manager = QueueManager(settings)
    await manager.load()  # não deve levantar

    snapshot = manager.snapshot()
    assert snapshot.items == []
    assert snapshot.current is None


async def test_load_with_wrong_schema_falls_back_to_empty_queue(
    make_settings: Callable[..., Settings], tmp_path: Path
) -> None:
    queue_path = tmp_path / "data" / "queue.json"
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    queue_path.write_text('{"items": "isso deveria ser uma lista"}')

    settings = make_settings(queue_data_path=queue_path)
    manager = QueueManager(settings)
    await manager.load()

    snapshot = manager.snapshot()
    assert snapshot.items == []
