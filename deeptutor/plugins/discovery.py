"""Discover compile-time plugins without importing disabled plugin packages."""

from __future__ import annotations

from collections.abc import Callable
import importlib
from importlib.metadata import entry_points
import pkgutil
from typing import Any

from .contracts import FullStackPlugin

PluginFactory = Callable[[], FullStackPlugin]
ENTRY_POINT_GROUP = "deeptutor.plugins"
NAMESPACE_PACKAGE = "deeptutor_plugins"


def discover_plugin_factories() -> dict[str, PluginFactory]:
    """Return lazy factories from entry points and the compile-time namespace."""

    factories: dict[str, PluginFactory] = {}
    for entry_point in entry_points(group=ENTRY_POINT_GROUP):
        factories[entry_point.name] = entry_point.load

    try:
        namespace = importlib.import_module(NAMESPACE_PACKAGE)
    except ModuleNotFoundError as exc:
        if exc.name != NAMESPACE_PACKAGE:
            raise
        return factories

    paths = getattr(namespace, "__path__", ())
    for module_info in pkgutil.iter_modules(paths):
        name = module_info.name
        factories.setdefault(name, _namespace_factory(name))
    return factories


def _namespace_factory(name: str) -> PluginFactory:
    module_name = f"{NAMESPACE_PACKAGE}.{name}"

    def load() -> FullStackPlugin:
        module = importlib.import_module(module_name)
        factory: Any = getattr(module, "get_plugin", None)
        if callable(factory):
            return factory()
        plugin = getattr(module, "plugin", None)
        if plugin is None:
            raise TypeError(f"{module_name} exports neither get_plugin() nor plugin")
        return plugin

    return load


__all__ = ["ENTRY_POINT_GROUP", "NAMESPACE_PACKAGE", "PluginFactory", "discover_plugin_factories"]
