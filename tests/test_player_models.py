"""Testes dos modelos persistidos da fila de reproducao."""

from datetime import UTC, datetime

from app.player.models import QueueItem
from app.utils.sanitize import MediaSource, SourceType


def _item() -> QueueItem:
    return QueueItem(
        source=MediaSource(raw="/media/a.mp4", type=SourceType.LOCAL_FILE),
        requested_by=1,
    )


def test_queue_item_from_dict_accepts_legacy_payload() -> None:
    item = QueueItem.from_dict(
        {
            "source_raw": "/media/a.mp4",
            "source_type": "local_file",
            "requested_by": 1,
            "added_at": datetime.now(UTC).isoformat(),
        }
    )

    assert item.media_id is None
    assert item.display_title is None


def test_queue_item_roundtrip_preserves_movie_context() -> None:
    item = _item()
    item.media_id = "tt0133093"
    item.display_title = "The Matrix"

    restored = QueueItem.from_dict(item.to_dict())

    assert (restored.media_id, restored.display_title) == ("tt0133093", "The Matrix")
