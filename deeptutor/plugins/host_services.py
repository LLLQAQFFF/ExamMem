"""Versioned, domain-neutral Host services available to first-party plugins."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, AsyncIterator

from deeptutor.agents._shared.capability_result import emit_capability_result
from deeptutor.agents._shared.json_output import extract_json_object
from deeptutor.core.capability_protocol import BaseCapability, CapabilityManifest
from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream_bus import StreamBus
from deeptutor.core.tool_protocol import BaseTool, ToolDefinition, ToolResult
from deeptutor.services.embedding.validation import validate_embedding_batch


class PluginDataConflict(RuntimeError):
    """Raised when a plugin event identity already exists with other content."""


@dataclass(frozen=True, slots=True)
class PluginMemoryEvent:
    """Domain-neutral event accepted by the Host Native Memory boundary."""

    id: str
    ts: str
    surface: str
    kind: str
    payload: dict[str, Any]
    session_id: str | None
    turn_id: str


@dataclass(frozen=True, slots=True)
class PluginTurnRequest:
    """Domain-neutral turn request accepted by the Host runtime."""

    content: str
    capability: str
    session_id: str | None = None
    language: str = "en"
    config: dict[str, Any] = field(default_factory=dict)


class PluginTurnHost:
    """Stable plugin-facing adapter over the Host turn facade."""

    def __init__(self) -> None:
        from deeptutor.app import DeepTutorApp

        self._app = DeepTutorApp()

    async def start_turn(
        self, request: PluginTurnRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        return await self._app.start_turn(asdict(request))

    async def stream_turn(self, turn_id: str) -> AsyncIterator[dict[str, Any]]:
        async for event in self._app.stream_turn(turn_id):
            yield event


class NativeMemoryHost:
    """Stable plugin-facing adapter over Host-owned Native Memory formats."""

    def __init__(self) -> None:
        from deeptutor.services.memory import get_memory_store

        self._store = get_memory_store()

    async def append_once(self, event: PluginMemoryEvent) -> bool:
        from deeptutor.services.memory import TraceEvent
        from deeptutor.services.memory.trace import iter_by_ids

        native_event = TraceEvent(**asdict(event))
        existing = next(iter_by_ids([native_event.id]), None)
        if existing is not None:
            if asdict(existing) != asdict(native_event):
                raise PluginDataConflict(
                    "Native Memory event identity conflicts with stored trace"
                )
            return False
        await self._store.emit(native_event)
        return True

    async def consolidate(self, surface: str) -> None:
        await self._store.update_l2(surface)
        await self._store.update_l3("recent")
        await self._store.update_l3("scope")

    def snapshot(self, surface: str) -> dict[str, Any]:
        return {
            "backend_mode": "native",
            "l2": self._store.read_raw("L2", surface),
            "l3": self._store.read_l3_concat(),
        }


async def complete(
    *,
    prompt: str,
    system_prompt: str,
    response_format: dict[str, object],
    temperature: float,
) -> str:
    """Call the configured non-streaming Host LLM through a stable plugin seam."""

    from deeptutor.services.llm import complete as host_complete

    return await host_complete(
        prompt=prompt,
        system_prompt=system_prompt,
        response_format=response_format,
        temperature=temperature,
    )


def current_user_id() -> str:
    """Return the authenticated Host identity for the current request."""

    from deeptutor.multi_user.context import get_current_user

    return get_current_user().id


def current_user_is_admin() -> bool:
    """Return whether the authenticated Host identity is an administrator."""

    from deeptutor.multi_user.context import get_current_user

    return get_current_user().is_admin


def get_embedding_client() -> Any:
    """Return the configured Host embedding client without exposing its implementation."""

    from deeptutor.services.embedding import get_embedding_client as host_embedding_client

    return host_embedding_client()


__all__ = [
    "BaseCapability",
    "BaseTool",
    "CapabilityManifest",
    "NativeMemoryHost",
    "PluginDataConflict",
    "PluginMemoryEvent",
    "PluginTurnHost",
    "PluginTurnRequest",
    "StreamBus",
    "ToolDefinition",
    "ToolResult",
    "UnifiedContext",
    "complete",
    "current_user_id",
    "current_user_is_admin",
    "emit_capability_result",
    "extract_json_object",
    "get_embedding_client",
    "validate_embedding_batch",
]
