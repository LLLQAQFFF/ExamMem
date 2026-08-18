"""Neutral full-stack plugin contracts and runtime manager."""

from .api import mount_plugin_routers
from .contracts import (
    BaseFullStackPlugin,
    FullStackPlugin,
    MigrationContribution,
    NavigationContribution,
    PluginManifest,
    RouterContribution,
    SessionContextBlock,
    SessionContextContributor,
    SettingsContribution,
)
from .manager import (
    PluginLoadError,
    PluginManager,
    get_plugin_manager,
    reset_plugin_manager,
)
from .settings import load_plugin_settings, save_plugin_settings

__all__ = [
    "BaseFullStackPlugin",
    "FullStackPlugin",
    "MigrationContribution",
    "NavigationContribution",
    "PluginLoadError",
    "PluginManager",
    "PluginManifest",
    "RouterContribution",
    "SessionContextBlock",
    "SessionContextContributor",
    "SettingsContribution",
    "get_plugin_manager",
    "load_plugin_settings",
    "mount_plugin_routers",
    "reset_plugin_manager",
    "save_plugin_settings",
]
