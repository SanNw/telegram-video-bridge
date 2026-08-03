"""Testes de `app/addon_system/config_loader.py`."""

from __future__ import annotations

from pathlib import Path

from app.addon_system.config_loader import load_addon_config


def test_no_config_file_returns_empty_dict(tmp_path: Path) -> None:
    assert load_addon_config(tmp_path, "missing_addon") == {}


def test_loads_json_config(tmp_path: Path) -> None:
    (tmp_path / "my_addon.json").write_text('{"timeout_seconds": 5.0}', encoding="utf-8")
    assert load_addon_config(tmp_path, "my_addon") == {"timeout_seconds": 5.0}


def test_loads_yaml_config(tmp_path: Path) -> None:
    (tmp_path / "my_addon.yaml").write_text("timeout_seconds: 5.0\n", encoding="utf-8")
    assert load_addon_config(tmp_path, "my_addon") == {"timeout_seconds": 5.0}


def test_loads_yml_config(tmp_path: Path) -> None:
    (tmp_path / "my_addon.yml").write_text("key: value\n", encoding="utf-8")
    assert load_addon_config(tmp_path, "my_addon") == {"key": "value"}


def test_json_takes_priority_over_yaml(tmp_path: Path) -> None:
    (tmp_path / "my_addon.json").write_text('{"source": "json"}', encoding="utf-8")
    (tmp_path / "my_addon.yaml").write_text("source: yaml\n", encoding="utf-8")
    assert load_addon_config(tmp_path, "my_addon") == {"source": "json"}


def test_invalid_json_returns_empty_dict(tmp_path: Path) -> None:
    (tmp_path / "broken.json").write_text("{not valid", encoding="utf-8")
    assert load_addon_config(tmp_path, "broken") == {}


def test_invalid_yaml_returns_empty_dict(tmp_path: Path) -> None:
    (tmp_path / "broken.yaml").write_text("key: [unterminated\n", encoding="utf-8")
    assert load_addon_config(tmp_path, "broken") == {}


def test_non_dict_json_returns_empty_dict(tmp_path: Path) -> None:
    (tmp_path / "list.json").write_text("[1, 2, 3]", encoding="utf-8")
    assert load_addon_config(tmp_path, "list") == {}
