"""Domain-neutral contribution contracts for compile-time full-stack plugins."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable

from fastapi import APIRouter

from deeptutor.core.capability_protocol import BaseCapability
from deeptutor.core.tool_protocol import BaseTool

PluginAccess = Literal["public", "authenticated", "admin"]
CapabilityFactory = Callable[[], BaseCapability]
ToolFactory = Callable[[], BaseTool]
SettingsNormalizer = Callable[[Mapping[str, Any]], dict[str, Any]]
HealthCheck = Callable[[], Awaitable[Mapping[str, Any]]]
RuntimeSnapshotFactory = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class RouterContribution:
    """One FastAPI router owned by a plugin."""

    router: APIRouter
    prefix: str
    tags: tuple[str, ...]
    access: PluginAccess = "authenticated"


@dataclass(frozen=True, slots=True)
class NavigationContribution:
    """Compile-time Web route metadata exposed by the Host API."""

    href: str
    label: str
    icon: str
    section: str
    order: int = 100


@dataclass(frozen=True, slots=True)
class SettingsContribution:
    """A non-sensitive, namespaced runtime-settings contract."""

    namespace: str
    defaults: Mapping[str, Any]
    normalize: SettingsNormalizer


@dataclass(frozen=True, slots=True)
class MigrationContribution:
    """Plugin-owned Alembic location; the Host only discovers and reports it."""

    config_path: str
    versions_path: str
    expected_head: str


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Static contributions exported by one full-stack plugin."""

    name: str
    version: str
    description: str
    capability_factories: tuple[CapabilityFactory, ...] = ()
    tool_factories: tuple[ToolFactory, ...] = ()
    routers: tuple[RouterContribution, ...] = ()
    navigation: tuple[NavigationContribution, ...] = ()
    settings: SettingsContribution | None = None
    migration: MigrationContribution | None = None
    health_check: HealthCheck | None = None
    runtime_snapshot: RuntimeSnapshotFactory | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@runtime_checkable
class FullStackPlugin(Protocol):
    """Minimal lifecycle surface implemented by plugin packages."""

    manifest: PluginManifest

    async def startup(self) -> None: ...

    async def shutdown(self) -> None: ...


class BaseFullStackPlugin:
    """Convenience base for plugins without custom process lifecycle work."""

    manifest: PluginManifest

    async def startup(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None


__all__ = [
    "BaseFullStackPlugin",
    "FullStackPlugin",
    "MigrationContribution",
    "NavigationContribution",
    "PluginManifest",
    "RouterContribution",
    "SettingsContribution",
]
