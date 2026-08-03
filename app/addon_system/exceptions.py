"""Exceções do sistema de addons."""

from __future__ import annotations


class AddonError(Exception):
    """Erro genérico do sistema de addons."""


class AddonNotFoundError(AddonError):
    """Addon referenciado por nome não está carregado."""


class AddonManifestError(AddonError):
    """`manifest.json` ausente, malformado ou com campos obrigatórios faltando."""


class AddonLoadError(AddonError):
    """Falha ao importar `plugin.py` ou instanciar a classe do addon."""


class AddonTimeoutError(AddonError):
    """Uma chamada ao addon (search/get_metadata/get_streams) excedeu o timeout."""
