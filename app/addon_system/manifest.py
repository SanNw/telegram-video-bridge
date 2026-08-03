"""`manifest.json` de cada addon: metadados declarativos, validados via Pydantic."""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from app.addon_system.exceptions import AddonManifestError


class AddonManifest(BaseModel):
    """Schema de `manifest.json`. `entrypoint` é `"<módulo>:<Classe>"` dentro do addon."""

    name: str
    version: str
    description: str = ""
    entrypoint: str = Field(default="plugin:Addon")
    min_core_version: str = "0.1.0"


def load_manifest(addon_dir: Path) -> AddonManifest:
    """Lê e valida `manifest.json` de `addon_dir`. Levanta `AddonManifestError`."""
    manifest_path = addon_dir / "manifest.json"
    if not manifest_path.is_file():
        raise AddonManifestError(f"manifest.json ausente em {addon_dir}")
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AddonManifestError(f"manifest.json inválido em {addon_dir}: {exc}") from exc
    try:
        return AddonManifest.model_validate(raw)
    except ValidationError as exc:
        raise AddonManifestError(
            f"manifest.json com campos inválidos em {addon_dir}: {exc}"
        ) from exc
