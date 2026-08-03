"""Descoberta e carregamento dinâmico de addons via `importlib`."""

from __future__ import annotations

import contextlib
import importlib.util
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from app import __version__ as core_version
from app.addon_system.base import BaseAddon
from app.addon_system.config_loader import load_addon_config
from app.addon_system.exceptions import AddonLoadError
from app.addon_system.manifest import AddonManifest, load_manifest
from app.utils.logging import get_logger

_logger = get_logger("services")


@dataclass(slots=True)
class LoadedAddon:
    """Um addon carregado com sucesso: instância pronta para uso + metadados."""

    instance: BaseAddon
    manifest: AddonManifest
    path: Path


class AddonLoader:
    """Descobre addons em `addons_path` e os instancia dinamicamente."""

    def __init__(self, addons_path: Path, config_path: Path) -> None:
        self._addons_path = addons_path
        self._config_path = config_path

    def discover_names(self) -> list[str]:
        """Nomes das subpastas de `addons_path` que têm `manifest.json`."""
        if not self._addons_path.is_dir():
            return []
        return sorted(
            child.name
            for child in self._addons_path.iterdir()
            if child.is_dir() and (child / "manifest.json").is_file()
        )

    def load(self, name: str) -> LoadedAddon:
        """Carrega (ou recarrega) o addon `name`. Levanta `AddonManifestError`/`AddonLoadError`."""
        addon_dir = self._addons_path / name
        manifest = load_manifest(addon_dir)
        self._warn_if_incompatible(name, manifest)

        module_name, _, class_name = manifest.entrypoint.partition(":")
        if not module_name or not class_name:
            raise AddonLoadError(
                f"entrypoint inválido em {name}: {manifest.entrypoint!r} (esperado 'módulo:Classe')"
            )

        module_path = addon_dir / f"{module_name}.py"
        if not module_path.is_file():
            raise AddonLoadError(f"módulo de entrypoint não encontrado: {module_path}")

        module = self._import_fresh_module(name, module_name, module_path)

        addon_class = getattr(module, class_name, None)
        if addon_class is None:
            raise AddonLoadError(f"classe {class_name!r} não encontrada em {module_path}")
        if not (isinstance(addon_class, type) and issubclass(addon_class, BaseAddon)):
            raise AddonLoadError(
                f"{class_name!r} em {module_path} não é uma subclasse de BaseAddon"
            )

        config = load_addon_config(self._config_path, name)
        try:
            instance = addon_class(config=config)
        except Exception as exc:  # noqa: BLE001 - erro de terceiros (addon), isolar e reportar
            raise AddonLoadError(f"falha ao instanciar {class_name} de {name}: {exc}") from exc

        return LoadedAddon(instance=instance, manifest=manifest, path=addon_dir)

    @staticmethod
    def _warn_if_incompatible(name: str, manifest: AddonManifest) -> None:
        def _parse(version: str) -> tuple[int, ...] | None:
            try:
                return tuple(int(part) for part in version.split("."))
            except ValueError:
                return None

        required = _parse(manifest.min_core_version)
        current = _parse(core_version)
        if required is not None and current is not None and current < required:
            _logger.warning(
                "Addon {name} pede core >= {required}, versão atual é {current}. "
                "Pode não funcionar corretamente.",
                name=name,
                required=manifest.min_core_version,
                current=core_version,
            )

    @staticmethod
    def _import_fresh_module(addon_name: str, module_name: str, module_path: Path) -> ModuleType:
        """Importa `module_path` sob um nome único a cada chamada.

        É isso que torna `reload()` seguro: em vez de mutar um módulo já
        importado (`importlib.reload`, que deixa classes antigas penduradas em
        closures/referências alheias), cada carga cria um namespace de módulo
        totalmente novo — o antigo simplesmente vira lixo coletável.
        """
        unique_name = f"_addon_{addon_name}_{module_name}_{uuid.uuid4().hex}"
        spec = importlib.util.spec_from_file_location(unique_name, module_path)
        if spec is None or spec.loader is None:
            raise AddonLoadError(f"não foi possível carregar {module_path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[unique_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - erro de terceiros (addon), isolar e reportar
            raise AddonLoadError(f"erro ao importar {module_path}: {exc}") from exc
        finally:
            # Funções do módulo mantêm seu __globals__ vivo via referência
            # própria; não precisamos do módulo em sys.modules depois de
            # importado (ninguém mais vai importar esse nome único de novo).
            with contextlib.suppress(KeyError):
                del sys.modules[unique_name]
        return module
