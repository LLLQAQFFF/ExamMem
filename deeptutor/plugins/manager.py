"""Load, validate, and expose full-stack plugin contributions."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any

from deeptutor.core.capability_protocol import BaseCapability
from deeptutor.core.tool_protocol import BaseTool

from .contracts import (
    FullStackPlugin,
    NavigationContribution,
    RouterContribution,
    SettingsContribution,
)
from .discovery import PluginFactory, discover_plugin_factories

logger = logging.getLogger(__name__)


class PluginLoadError(RuntimeError):
    """Raised when an enabled compile-time plugin cannot be loaded safely."""


class PluginManager:
    """Process-scoped, immutable set of validated plugin contributions."""

    def __init__(
        self,
        *,
        factories: Mapping[str, PluginFactory] | None = None,
        disabled: Iterable[str] = (),
    ) -> None:
        self._provided_factories = None if factories is None else dict(factories)
        self._disabled = frozenset(str(name).strip() for name in disabled if str(name).strip())
        self._plugins: dict[str, FullStackPlugin] = {}
        self._plugin_capabilities: dict[str, tuple[str, ...]] = {}
        self._plugin_tools: dict[str, tuple[str, ...]] = {}
        self._capabilities: tuple[BaseCapability, ...] = ()
        self._tools: tuple[BaseTool, ...] = ()
        self._routers: tuple[RouterContribution, ...] = ()
        self._navigation: tuple[NavigationContribution, ...] = ()
        self._settings: tuple[SettingsContribution, ...] = ()
        self._loaded = False
        self._started: tuple[FullStackPlugin, ...] = ()

    def load(self) -> None:
        if self._loaded:
            return
        factories = (
            dict(self._provided_factories)
            if self._provided_factories is not None
            else discover_plugin_factories()
        )
        plugins: dict[str, FullStackPlugin] = {}
        plugin_capabilities_by_name: dict[str, tuple[str, ...]] = {}
        plugin_tools_by_name: dict[str, tuple[str, ...]] = {}
        capabilities: list[BaseCapability] = []
        tools: list[BaseTool] = []
        routers: list[RouterContribution] = []
        navigation: list[NavigationContribution] = []
        settings: list[SettingsContribution] = []
        seen_capabilities: set[str] = set()
        seen_tools: set[str] = set()
        seen_router_prefixes: set[str] = set()
        seen_settings: set[str] = set()

        for discovered_name in sorted(factories):
            if discovered_name in self._disabled:
                continue
            try:
                plugin = factories[discovered_name]()
                manifest = plugin.manifest
                if manifest.name != discovered_name:
                    raise PluginLoadError(
                        f"discovered plugin {discovered_name!r} declares {manifest.name!r}"
                    )
                if manifest.name in plugins:
                    raise PluginLoadError(f"duplicate plugin name: {manifest.name}")
                plugin_capabilities = tuple(factory() for factory in manifest.capability_factories)
                plugin_tools = tuple(factory() for factory in manifest.tool_factories)
                for capability in plugin_capabilities:
                    if capability.name in seen_capabilities:
                        raise PluginLoadError(f"duplicate plugin capability: {capability.name}")
                    seen_capabilities.add(capability.name)
                for tool in plugin_tools:
                    if tool.name in seen_tools:
                        raise PluginLoadError(f"duplicate plugin tool: {tool.name}")
                    seen_tools.add(tool.name)
                for contribution in manifest.routers:
                    if contribution.prefix in seen_router_prefixes:
                        raise PluginLoadError(
                            f"duplicate plugin router prefix: {contribution.prefix}"
                        )
                    seen_router_prefixes.add(contribution.prefix)
                if manifest.settings is not None:
                    if manifest.settings.namespace in seen_settings:
                        raise PluginLoadError(
                            f"duplicate plugin settings namespace: {manifest.settings.namespace}"
                        )
                    seen_settings.add(manifest.settings.namespace)
                    settings.append(manifest.settings)
                plugins[manifest.name] = plugin
                plugin_capabilities_by_name[manifest.name] = tuple(
                    capability.name for capability in plugin_capabilities
                )
                plugin_tools_by_name[manifest.name] = tuple(tool.name for tool in plugin_tools)
                capabilities.extend(plugin_capabilities)
                tools.extend(plugin_tools)
                routers.extend(manifest.routers)
                navigation.extend(manifest.navigation)
            except PluginLoadError:
                raise
            except Exception as exc:
                raise PluginLoadError(
                    f"failed to load enabled plugin {discovered_name!r}: {exc}"
                ) from exc

        self._plugins = plugins
        self._plugin_capabilities = plugin_capabilities_by_name
        self._plugin_tools = plugin_tools_by_name
        self._capabilities = tuple(capabilities)
        self._tools = tuple(tools)
        self._routers = tuple(routers)
        self._navigation = tuple(sorted(navigation, key=lambda item: (item.section, item.order)))
        self._settings = tuple(settings)
        self._loaded = True

    @property
    def plugins(self) -> tuple[FullStackPlugin, ...]:
        self.load()
        return tuple(self._plugins.values())

    def capabilities(self) -> tuple[BaseCapability, ...]:
        self.load()
        return self._capabilities

    def tools(self) -> tuple[BaseTool, ...]:
        self.load()
        return self._tools

    def routers(self) -> tuple[RouterContribution, ...]:
        self.load()
        return self._routers

    def navigation(self) -> tuple[NavigationContribution, ...]:
        self.load()
        return self._navigation

    def settings(self) -> tuple[SettingsContribution, ...]:
        self.load()
        return self._settings

    async def startup(self) -> None:
        if self._started:
            return
        started: list[FullStackPlugin] = []
        for plugin in self.plugins:
            try:
                await plugin.startup()
                started.append(plugin)
            except Exception as exc:
                for active_plugin in reversed(started):
                    try:
                        await active_plugin.shutdown()
                    except Exception:
                        logger.exception(
                            "Plugin %s rollback shutdown failed",
                            active_plugin.manifest.name,
                        )
                raise PluginLoadError(
                    f"plugin {plugin.manifest.name!r} startup failed: {exc}"
                ) from exc
        self._started = tuple(started)

    async def shutdown(self) -> None:
        for plugin in reversed(self._started):
            try:
                await plugin.shutdown()
            except Exception:
                logger.exception("Plugin %s shutdown failed", plugin.manifest.name)
        self._started = ()

    async def health(self) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for plugin in self.plugins:
            check = plugin.manifest.health_check
            try:
                payload = dict(await check()) if check is not None else {"status": "ready"}
            except Exception as exc:
                payload = {"status": "error", "detail": str(exc)}
            results.append({"name": plugin.manifest.name, **payload})
        return results

    def describe(self) -> list[dict[str, Any]]:
        descriptions: list[dict[str, Any]] = []
        for plugin in self.plugins:
            manifest = plugin.manifest
            descriptions.append(
                {
                    "name": manifest.name,
                    "version": manifest.version,
                    "description": manifest.description,
                    "capabilities": list(self._plugin_capabilities[manifest.name]),
                    "tools": list(self._plugin_tools[manifest.name]),
                    "navigation": [
                        {
                            "href": item.href,
                            "label": item.label,
                            "icon": item.icon,
                            "section": item.section,
                            "order": item.order,
                        }
                        for item in manifest.navigation
                    ],
                    "settings_namespace": (
                        None if manifest.settings is None else manifest.settings.namespace
                    ),
                    "migration": (
                        None
                        if manifest.migration is None
                        else {
                            "config_path": manifest.migration.config_path,
                            "versions_path": manifest.migration.versions_path,
                            "expected_head": manifest.migration.expected_head,
                        }
                    ),
                    "metadata": dict(manifest.metadata),
                }
            )
        return descriptions


_default_manager: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _default_manager
    if _default_manager is None:
        from deeptutor.services.config.runtime_settings import load_plugins_settings

        settings = load_plugins_settings()
        _default_manager = PluginManager(disabled=settings["disabled"])
        _default_manager.load()
    return _default_manager


def reset_plugin_manager() -> None:
    """Drop the process cache after an explicit configuration reload."""

    global _default_manager
    _default_manager = None


__all__ = ["PluginLoadError", "PluginManager", "get_plugin_manager", "reset_plugin_manager"]
