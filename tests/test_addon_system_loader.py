"""Testes de `app/addon_system/loader.py` (AddonLoader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.addon_system.exceptions import AddonLoadError, AddonManifestError
from app.addon_system.loader import AddonLoader

_VALID_PLUGIN = """
from app.addon_system.base import BaseAddon, Metadata


class Addon(BaseAddon):
    name = "fake"
    version = "1.0.0"

    async def search(self, query):
        return []

    async def get_metadata(self, media_id):
        return Metadata(media_id=media_id, title="t")

    async def get_streams(self, media_id):
        return []
"""


def _write_manifest(addon_dir: Path, **overrides: object) -> None:
    manifest = {
        "name": addon_dir.name,
        "version": "1.0.0",
        "description": "",
        "entrypoint": "plugin:Addon",
        "min_core_version": "0.1.0",
    }
    manifest.update(overrides)
    addon_dir.mkdir(parents=True, exist_ok=True)
    (addon_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def _make_loader(tmp_path: Path) -> AddonLoader:
    addons_path = tmp_path / "addons"
    addons_path.mkdir(exist_ok=True)
    config_path = tmp_path / "config" / "addons"
    return AddonLoader(addons_path, config_path)


def test_discover_names_empty_when_dir_missing(tmp_path: Path) -> None:
    loader = AddonLoader(tmp_path / "no_such_dir", tmp_path / "config")
    assert loader.discover_names() == []


def test_discover_names_finds_addons_with_manifest(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addons_path = tmp_path / "addons"
    _write_manifest(addons_path / "alpha")
    _write_manifest(addons_path / "beta")
    (addons_path / "no_manifest_here").mkdir()

    assert loader.discover_names() == ["alpha", "beta"]


def test_load_success(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "fake"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text(_VALID_PLUGIN, encoding="utf-8")

    loaded = loader.load("fake")

    assert loaded.manifest.name == "fake"
    assert loaded.instance.name == "fake"
    assert loaded.path == addon_dir


def test_load_twice_produces_independent_module_instances(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "fake"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text(_VALID_PLUGIN, encoding="utf-8")

    first = loader.load("fake")
    second = loader.load("fake")

    assert type(first.instance) is not type(second.instance)
    assert type(first.instance).__module__ != type(second.instance).__module__


def test_load_missing_manifest_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    with pytest.raises(AddonManifestError):
        loader.load("nonexistent")


def test_load_invalid_entrypoint_format_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "bad_entrypoint"
    _write_manifest(addon_dir, entrypoint="no_colon_here")

    with pytest.raises(AddonLoadError, match="entrypoint inválido"):
        loader.load("bad_entrypoint")


def test_load_missing_module_file_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "no_module"
    _write_manifest(addon_dir)

    with pytest.raises(AddonLoadError, match="não encontrado"):
        loader.load("no_module")


def test_load_class_not_in_module_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "no_class"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text("x = 1\n", encoding="utf-8")

    with pytest.raises(AddonLoadError, match="não encontrada"):
        loader.load("no_class")


def test_load_class_not_subclass_of_base_addon_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "wrong_base"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text("class Addon:\n    pass\n", encoding="utf-8")

    with pytest.raises(AddonLoadError, match="não é uma subclasse"):
        loader.load("wrong_base")


def test_load_module_with_import_error_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "broken_import"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text("import this_module_does_not_exist\n", encoding="utf-8")

    with pytest.raises(AddonLoadError, match="erro ao importar"):
        loader.load("broken_import")


def test_load_instantiation_failure_raises(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "bad_init"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text(
        "from app.addon_system.base import BaseAddon\n\n\n"
        "class Addon(BaseAddon):\n"
        "    name = 'bad_init'\n"
        "    version = '1.0.0'\n\n"
        "    def __init__(self, config=None):\n"
        "        raise RuntimeError('boom')\n\n"
        "    async def search(self, query):\n"
        "        return []\n\n"
        "    async def get_metadata(self, media_id):\n"
        "        raise NotImplementedError\n\n"
        "    async def get_streams(self, media_id):\n"
        "        return []\n",
        encoding="utf-8",
    )

    with pytest.raises(AddonLoadError, match="falha ao instanciar"):
        loader.load("bad_init")


def test_load_passes_addon_config_to_constructor(tmp_path: Path) -> None:
    addons_path = tmp_path / "addons"
    addons_path.mkdir()
    config_path = tmp_path / "config" / "addons"
    config_path.mkdir(parents=True)
    (config_path / "fake.json").write_text('{"custom_key": "custom_value"}', encoding="utf-8")

    loader = AddonLoader(addons_path, config_path)
    addon_dir = addons_path / "fake"
    _write_manifest(addon_dir)
    (addon_dir / "plugin.py").write_text(_VALID_PLUGIN, encoding="utf-8")

    loaded = loader.load("fake")

    assert loaded.instance.config == {"custom_key": "custom_value"}


def test_load_incompatible_min_core_version_does_not_raise(tmp_path: Path) -> None:
    loader = _make_loader(tmp_path)
    addon_dir = tmp_path / "addons" / "future_addon"
    _write_manifest(addon_dir, min_core_version="999.0.0")
    (addon_dir / "plugin.py").write_text(_VALID_PLUGIN, encoding="utf-8")

    loaded = loader.load("future_addon")

    assert loaded.instance.name == "fake"
