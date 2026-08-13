"""
Capability Registry
===================

Central registry for all capabilities (built-in and plugin).
"""

from __future__ import annotations

import importlib
import logging
from typing import Any

from deeptutor.core.capability_protocol import BaseCapability
from deeptutor.i18n.metadata_i18n import capability_description_i18n
from deeptutor.runtime.bootstrap.builtin_capabilities import BUILTIN_CAPABILITY_CLASSES

logger = logging.getLogger(__name__)


def _import_capability_class(path: str) -> type[BaseCapability]:
    module_path, class_name = path.rsplit(":", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


class CapabilityRegistry:
    """Registry of available capabilities."""

    def __init__(self) -> None:
        self._capabilities: dict[str, BaseCapability] = {}

    def register(self, capability: BaseCapability) -> None:
        self._capabilities[capability.name] = capability
        logger.debug("Registered capability: %s", capability.name)

    def load_builtins(self) -> None:
        for name, class_path in BUILTIN_CAPABILITY_CLASSES.items():
            if name in self._capabilities:
                continue
            try:
                cls = _import_capability_class(class_path)
                self.register(cls())
            except Exception:
                logger.warning("Failed to load capability %s", name, exc_info=True)

    def load_plugins(self) -> None:
        from deeptutor.plugins import PluginLoadError, get_plugin_manager

        for capability in get_plugin_manager().capabilities():
            current = self._capabilities.get(capability.name)
            if current is capability:
                continue
            if current is not None:
                raise PluginLoadError(
                    f"plugin capability conflicts with registered capability: {capability.name}"
                )
            self.register(capability)

    def get(self, name: str) -> BaseCapability | None:
        return self._capabilities.get(name)

    def list_capabilities(self) -> list[str]:
        return list(self._capabilities.keys())

    def get_manifests(self) -> list[dict[str, Any]]:
        return [
            {
                "name": c.manifest.name,
                "description": c.manifest.description,
                "description_i18n": capability_description_i18n(
                    c.manifest.name,
                    c.manifest.description,
                ),
                "stages": c.manifest.stages,
                "tools_used": c.manifest.tools_used,
                "cli_aliases": c.manifest.cli_aliases,
                "request_schema": c.manifest.request_schema,
                "config_defaults": c.manifest.config_defaults,
                "session_surface": c.manifest.session_surface,
            }
            for c in self._capabilities.values()
        ]


_default_registry: CapabilityRegistry | None = None


def get_capability_registry() -> CapabilityRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = CapabilityRegistry()
        _default_registry.load_builtins()
        _default_registry.load_plugins()
    return _default_registry
