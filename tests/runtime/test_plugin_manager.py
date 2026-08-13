from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Depends, FastAPI, HTTPException
from fastapi.testclient import TestClient
import pytest

from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolResult
from deeptutor.plugins import (
    BaseFullStackPlugin,
    MigrationContribution,
    NavigationContribution,
    PluginLoadError,
    PluginManager,
    PluginManifest,
    RouterContribution,
    SettingsContribution,
    mount_plugin_routers,
)
from deeptutor.runtime.registry.capability_registry import CapabilityRegistry
from deeptutor.runtime.registry.tool_registry import ToolRegistry


class _Capability(BaseCapability):
    manifest = CapabilityManifest(name="fake_capability", description="fake")

    async def run(self, context, stream) -> None:
        return None


class _Tool(BaseTool):
    def __init__(self, name: str = "fake_tool") -> None:
        self._name = name

    def get_definition(self) -> ToolDefinition:
        return ToolDefinition(name=self._name, description="fake")

    async def execute(self, **kwargs: Any) -> ToolResult:
        return ToolResult(content="ok")


def _normalize_settings(settings: Mapping[str, Any]) -> dict[str, Any]:
    return {"enabled": bool(settings.get("enabled", True))}


def _router(path: str = "/ping") -> APIRouter:
    router = APIRouter()

    @router.get(path)
    async def ping() -> dict[str, bool]:
        return {"ok": True}

    return router


class _Plugin(BaseFullStackPlugin):
    def __init__(self, manifest: PluginManifest, events: list[str] | None = None) -> None:
        self.manifest = manifest
        self._events = events

    async def startup(self) -> None:
        if self._events is not None:
            self._events.append(f"start:{self.manifest.name}")

    async def shutdown(self) -> None:
        if self._events is not None:
            self._events.append(f"stop:{self.manifest.name}")


def _manifest(name: str = "fake") -> PluginManifest:
    async def health() -> dict[str, str]:
        return {"status": "ready", "database": "isolated"}

    return PluginManifest(
        name=name,
        version="1.0.0",
        description="fake plugin",
        capability_factories=(_Capability,),
        tool_factories=(_Tool,),
        routers=(RouterContribution(_router(), "/api/fake", ("fake",)),),
        navigation=(NavigationContribution("/fake", "Fake", "Box", "learn", 20),),
        settings=SettingsContribution("fake", {"enabled": True}, _normalize_settings),
        migration=MigrationContribution("alembic.ini", "migrations", "0001"),
        health_check=health,
        metadata={"owner": "test"},
    )


def test_manager_materializes_and_describes_contributions_once() -> None:
    calls = 0

    def factory() -> _Plugin:
        nonlocal calls
        calls += 1
        return _Plugin(_manifest())

    manager = PluginManager(factories={"fake": factory})

    assert [item.name for item in manager.capabilities()] == ["fake_capability"]
    assert [item.name for item in manager.tools()] == ["fake_tool"]
    assert [item.href for item in manager.navigation()] == ["/fake"]
    assert manager.settings()[0].namespace == "fake"
    assert manager.describe() == [
        {
            "name": "fake",
            "version": "1.0.0",
            "description": "fake plugin",
            "capabilities": ["fake_capability"],
            "tools": ["fake_tool"],
            "navigation": [
                {
                    "href": "/fake",
                    "label": "Fake",
                    "icon": "Box",
                    "section": "learn",
                    "order": 20,
                }
            ],
            "settings_namespace": "fake",
            "migration": {
                "config_path": "alembic.ini",
                "versions_path": "migrations",
                "expected_head": "0001",
            },
            "metadata": {"owner": "test"},
        }
    ]
    assert calls == 1


def test_disabled_plugin_factory_is_never_called() -> None:
    def unexpected_factory() -> _Plugin:
        raise AssertionError("disabled plugin was imported")

    manager = PluginManager(factories={"fake": unexpected_factory}, disabled=("fake",))

    assert manager.plugins == ()
    assert manager.capabilities() == ()
    assert manager.tools() == ()
    assert manager.routers() == ()


def test_enabled_plugin_load_failure_is_explicit() -> None:
    def broken_factory() -> _Plugin:
        raise ValueError("broken import")

    manager = PluginManager(factories={"broken": broken_factory})

    with pytest.raises(PluginLoadError, match="broken import"):
        manager.load()


def test_duplicate_plugin_contributions_are_rejected() -> None:
    first = _Plugin(_manifest("first"))
    second = _Plugin(
        PluginManifest(
            name="second",
            version="1",
            description="duplicate tool",
            tool_factories=(_Tool,),
        )
    )
    manager = PluginManager(factories={"first": lambda: first, "second": lambda: second})

    with pytest.raises(PluginLoadError, match="duplicate plugin tool"):
        manager.load()


@pytest.mark.asyncio
async def test_lifecycle_health_and_reverse_shutdown_order() -> None:
    events: list[str] = []
    first = _Plugin(PluginManifest("first", "1", "first"), events)
    second = _Plugin(PluginManifest("second", "1", "second"), events)
    manager = PluginManager(factories={"first": lambda: first, "second": lambda: second})

    await manager.startup()
    await manager.startup()
    assert await manager.health() == [
        {"name": "first", "status": "ready"},
        {"name": "second", "status": "ready"},
    ]
    await manager.shutdown()

    assert events == ["start:first", "start:second", "stop:second", "stop:first"]


@pytest.mark.asyncio
async def test_startup_failure_rolls_back_started_plugins() -> None:
    events: list[str] = []
    first = _Plugin(PluginManifest("first", "1", "first"), events)

    class BrokenPlugin(_Plugin):
        async def startup(self) -> None:
            raise ValueError("startup broke")

    broken = BrokenPlugin(PluginManifest("second", "1", "second"), events)
    manager = PluginManager(factories={"first": lambda: first, "second": lambda: broken})

    with pytest.raises(PluginLoadError, match="startup broke"):
        await manager.startup()

    assert events == ["start:first", "stop:first"]


def test_mount_router_applies_declared_access_dependencies() -> None:
    plugin = _Plugin(
        PluginManifest(
            name="routes",
            version="1",
            description="routes",
            routers=(
                RouterContribution(_router(), "/public", ("public",), "public"),
                RouterContribution(_router(), "/user", ("user",), "authenticated"),
                RouterContribution(_router(), "/admin", ("admin",), "admin"),
            ),
        )
    )
    manager = PluginManager(factories={"routes": lambda: plugin})
    app = FastAPI()

    async def authenticated() -> None:
        raise HTTPException(status_code=401)

    async def admin() -> None:
        raise HTTPException(status_code=403)

    mount_plugin_routers(
        app,
        manager,
        authenticated_dependencies=(Depends(authenticated),),
        admin_dependencies=(Depends(admin),),
    )

    client = TestClient(app)
    assert client.get("/public/ping").status_code == 200
    assert client.get("/user/ping").status_code == 401
    assert client.get("/admin/ping").status_code == 403


def test_registries_accept_plugin_contributions_and_reject_host_conflicts(
    monkeypatch,
) -> None:
    manager = PluginManager(factories={"fake": lambda: _Plugin(_manifest())})
    monkeypatch.setattr("deeptutor.plugins.get_plugin_manager", lambda: manager)

    capability_registry = CapabilityRegistry()
    tool_registry = ToolRegistry()
    capability_registry.load_plugins()
    tool_registry.load_plugins()

    assert capability_registry.get("fake_capability") is manager.capabilities()[0]
    assert tool_registry.get("fake_tool") is manager.tools()[0]

    conflicting_tools = ToolRegistry()
    conflicting_tools.register(_Tool())
    with pytest.raises(PluginLoadError, match="conflicts with registered tool"):
        conflicting_tools.load_plugins()
