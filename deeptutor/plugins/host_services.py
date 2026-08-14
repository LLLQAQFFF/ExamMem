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
    attachments: tuple[dict[str, Any], ...] = ()
    mastery_path_id: str | None = None


class PluginTurnHost:
    """Stable plugin-facing adapter over the Host turn facade."""

    def __init__(self) -> None:
        from deeptutor.app import DeepTutorApp

        self._app = DeepTutorApp()

    async def start_turn(
        self, request: PluginTurnRequest
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = asdict(request)
        if not request.attachments:
            payload.pop("attachments")
        else:
            payload["attachments"] = list(payload["attachments"])
        if request.mastery_path_id is None:
            payload.pop("mastery_path_id")
        return await self._app.start_turn(payload)

    async def stream_turn(self, turn_id: str) -> AsyncIterator[dict[str, Any]]:
        async for event in self._app.stream_turn(turn_id):
            yield event

    async def delete_session(self, session_id: str) -> bool:
        """Remove one transient Host session and its persisted attachments."""
        from deeptutor.services.session import get_session_store
        from deeptutor.services.storage.attachment_store import get_attachment_store

        deleted = await get_session_store().delete_session(session_id)
        await get_attachment_store().delete_session(session_id)
        return deleted

    async def session_exists(self, session_id: str) -> bool:
        """Check one authenticated Host session without exposing its store."""
        from deeptutor.services.session import get_session_store

        return await get_session_store().get_session(session_id, surface="chat") is not None


@dataclass(frozen=True, slots=True)
class PluginLearningObjective:
    """One generic learning objective projected into Host Mastery Path."""

    id: str
    name: str
    type: str
    module_id: str
    module_name: str


class PluginLearningHost:
    """Domain-neutral bridge for provisioning persistent Host learning state."""

    def ensure_single_objective_path(
        self,
        *,
        path_id: str,
        objective: PluginLearningObjective,
    ) -> None:
        from deeptutor.learning.models import (
            KnowledgePoint,
            KnowledgeType,
            LearningModule,
        )
        from deeptutor.learning.service import LearningService

        service = LearningService()
        progress = service.get_or_create(path_id)
        expected = [
            LearningModule(
                id=objective.module_id,
                name=objective.module_name,
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id=objective.id,
                        name=objective.name,
                        type=KnowledgeType(objective.type),
                        module_id=objective.module_id,
                    )
                ],
            )
        ]
        if progress.modules:
            if progress.modules != expected:
                raise RuntimeError("Host learning path identity conflicts with its objective")
            return
        service.init_modules(progress, expected)
        progress.current_module_id = objective.module_id
        progress.current_kp_index = 0
        service.save(progress)

    def objective_progress(self, *, path_id: str, objective_id: str) -> dict[str, Any]:
        from deeptutor.learning.policy import (
            display_mastery,
            find_knowledge_point,
            objective_status,
        )
        from deeptutor.learning.service import LearningService

        progress = LearningService().get_or_create(path_id)
        objective, _, _ = find_knowledge_point(progress, objective_id)
        if objective is None:
            raise RuntimeError("Host learning path does not contain its linked objective")
        return {
            "status": objective_status(progress, objective),
            "mastery": round(display_mastery(progress, objective), 3),
        }


class PluginSourceHost:
    """Safe Host document/URL text extraction for first-party plugins."""

    def extract_attachment(self, *, filename: str, content: bytes) -> str:
        from pathlib import Path
        from tempfile import TemporaryDirectory

        from deeptutor.utils.document_extractor import extract_text_from_path
        from deeptutor.utils.document_validator import DocumentValidator

        safe_name = Path(filename).name
        if not safe_name or safe_name != filename:
            raise ValueError("attachment filename must be a plain basename")
        if not content or len(content) > DocumentValidator.MAX_FILE_SIZE:
            raise ValueError("attachment is empty or exceeds the Host size limit")
        with TemporaryDirectory(prefix="deeptutor-plugin-source-") as directory:
            path = Path(directory) / safe_name
            path.write_bytes(content)
            return extract_text_from_path(
                path,
                max_bytes=DocumentValidator.MAX_FILE_SIZE,
                max_chars=50_000,
            )

    async def fetch_url(self, url: str) -> dict[str, Any]:
        from deeptutor.tools.web_fetch import fetch_url_as_markdown

        outcome = await fetch_url_as_markdown(url, max_chars=50_000)
        if not outcome.ok:
            raise ValueError(outcome.error or "URL extraction failed")
        return {
            "text": outcome.markdown,
            "url": outcome.url,
            "title": outcome.title,
            "truncated": outcome.truncated,
        }


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
    "PluginLearningHost",
    "PluginLearningObjective",
    "PluginSourceHost",
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
