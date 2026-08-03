"""Testes de `app/addon_system/manifest.py`."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.addon_system.exceptions import AddonManifestError
from app.addon_system.manifest import load_manifest


def test_load_manifest_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(AddonManifestError, match="ausente"):
        load_manifest(tmp_path / "does_not_exist")


def test_load_manifest_invalid_json_raises(tmp_path: Path) -> None:
    addon_dir = tmp_path / "broken"
    addon_dir.mkdir()
    (addon_dir / "manifest.json").write_text("{not valid json", encoding="utf-8")

    with pytest.raises(AddonManifestError, match="inválido"):
        load_manifest(addon_dir)


def test_load_manifest_missing_required_field_raises(tmp_path: Path) -> None:
    addon_dir = tmp_path / "incomplete"
    addon_dir.mkdir()
    (addon_dir / "manifest.json").write_text('{"version": "1.0.0"}', encoding="utf-8")

    with pytest.raises(AddonManifestError, match="inválidos"):
        load_manifest(addon_dir)


def test_load_manifest_success_with_defaults(tmp_path: Path) -> None:
    addon_dir = tmp_path / "minimal"
    addon_dir.mkdir()
    (addon_dir / "manifest.json").write_text(
        '{"name": "minimal", "version": "1.0.0"}', encoding="utf-8"
    )

    manifest = load_manifest(addon_dir)

    assert manifest.name == "minimal"
    assert manifest.version == "1.0.0"
    assert manifest.description == ""
    assert manifest.entrypoint == "plugin:Addon"
    assert manifest.min_core_version == "0.1.0"


def test_load_manifest_success_with_all_fields(tmp_path: Path) -> None:
    addon_dir = tmp_path / "full"
    addon_dir.mkdir()
    (addon_dir / "manifest.json").write_text(
        '{"name": "full", "version": "2.0.0", "description": "desc", '
        '"entrypoint": "custom:MyAddon", "min_core_version": "0.5.0"}',
        encoding="utf-8",
    )

    manifest = load_manifest(addon_dir)

    assert manifest.description == "desc"
    assert manifest.entrypoint == "custom:MyAddon"
    assert manifest.min_core_version == "0.5.0"
