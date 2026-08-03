"""Testes de `app/addon_system/persistence.py` (AddonStatePersistence)."""

from __future__ import annotations

from pathlib import Path

from app.addon_system.persistence import AddonStatePersistence


async def test_load_returns_empty_dict_when_file_missing(tmp_path: Path) -> None:
    persistence = AddonStatePersistence(tmp_path / "state.json")
    assert await persistence.load() == {}


async def test_save_then_load_roundtrips(tmp_path: Path) -> None:
    persistence = AddonStatePersistence(tmp_path / "nested" / "state.json")
    await persistence.save({"archive_org": True, "other": False})

    reloaded = await persistence.load()

    assert reloaded == {"archive_org": True, "other": False}


async def test_load_returns_empty_dict_for_corrupted_file(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{not valid json", encoding="utf-8")

    persistence = AddonStatePersistence(path)

    assert await persistence.load() == {}


async def test_load_returns_empty_dict_for_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")

    persistence = AddonStatePersistence(path)

    assert await persistence.load() == {}


async def test_save_creates_parent_directories(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "c" / "state.json"
    persistence = AddonStatePersistence(path)

    await persistence.save({"x": True})

    assert path.is_file()
