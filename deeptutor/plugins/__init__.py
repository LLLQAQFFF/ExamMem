"""Neutral full-stack plugin contracts and runtime manager."""

from .api import mount_plugin_routers
from .contracts import (
    BaseFullStackPlugin,
    FullStackPlugin,
    MigrationContribution,
    NavigationContribution,
    PluginManifest,
    RouterContribution,
    SettingsContribution,
)
from .manager import (
    PluginLoadError,
    PluginManager,
    get_plugin_manager,
    reset_plugin_manager,
)

__all__ = [
    "BaseFullStackPlugin",
    "FullStackPlugin",
    "MigrationContribution",
    "NavigationContribution",
    "PluginLoadError",
    "PluginManager",
    "PluginManifest",
    "RouterContribution",
    "SettingsContribution",
    "get_plugin_manager",
    "mount_plugin_routers",
    "reset_plugin_manager",
]
