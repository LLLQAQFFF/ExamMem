"""Versioned, domain-neutral Host services available to first-party plugins."""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path
import re
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
    context_sources: tuple[str, ...] = ()
    knowledge_bases: tuple[str, ...] = ()
    knowledge_source_filters: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PluginConversationSummary:
    """Authenticated, storage-neutral conversation metadata for a plugin picker."""

    session_id: str
    title: str
    message_count: int
    updated_at: str


@dataclass(frozen=True, slots=True)
class PluginConversationTranscript:
    """Bounded conversation content without exposing the Host session store."""

    session_id: str
    title: str
    messages: tuple[dict[str, str], ...]


class PluginTurnHost:
    """Stable plugin-facing adapter over the Host turn facade."""

    def __init__(self) -> None:
        from deeptutor.app import DeepTutorApp

        self._app = DeepTutorApp()

    async def start_turn(self, request: PluginTurnRequest) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = asdict(request)
        if not request.attachments:
            payload.pop("attachments")
        else:
            payload["attachments"] = list(payload["attachments"])
        if request.mastery_path_id is None:
            payload.pop("mastery_path_id")
        if not request.context_sources:
            payload.pop("context_sources")
        else:
            payload["context_sources"] = list(request.context_sources)
        if not request.knowledge_bases:
            payload.pop("knowledge_bases")
        else:
            payload["knowledge_bases"] = list(request.knowledge_bases)
        if request.knowledge_source_filters:
            payload["config"] = {
                **payload["config"],
                "knowledge_source_filters": request.knowledge_source_filters,
            }
        payload.pop("knowledge_source_filters")
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

    async def bind_session_context_sources(
        self, session_id: str, source_names: tuple[str, ...]
    ) -> bool:
        """Bind named plugin context sources to one authenticated Chat session."""
        from deeptutor.services.session import get_session_store

        store = get_session_store()
        if await store.get_session(session_id, surface="chat") is None:
            return False
        names = list(dict.fromkeys(name.strip() for name in source_names if name.strip()))
        return await store.update_session_preferences(session_id, {"context_sources": names})

    async def bind_session_knowledge_sources(
        self,
        session_id: str,
        source_names: tuple[str, ...],
        *,
        filters: dict[str, dict[str, tuple[str, ...]]] | None = None,
    ) -> bool:
        """Rebind an owned session to permission-checked opaque knowledge sources."""
        from deeptutor.services.session import get_session_store

        store = get_session_store()
        if await store.get_session(session_id, surface="chat") is None:
            return False
        names = list(dict.fromkeys(name.strip() for name in source_names if name.strip()))
        return await store.update_session_preferences(
            session_id,
            {
                "knowledge_bases": names,
                "knowledge_source_filters": filters or {},
            },
        )

    async def list_conversations(self, *, limit: int = 50) -> tuple[PluginConversationSummary, ...]:
        """List the authenticated user's native Chat sessions through a neutral seam."""
        from deeptutor.services.session import get_session_store

        bounded = max(1, min(limit, 100))
        sessions = await get_session_store().list_sessions(
            limit=bounded,
            offset=0,
            surface="chat",
        )
        return tuple(
            PluginConversationSummary(
                session_id=str(item.get("session_id") or item.get("id") or ""),
                title=str(item.get("title") or "Untitled conversation"),
                message_count=int(item.get("message_count") or 0),
                updated_at=str(item.get("updated_at") or ""),
            )
            for item in sessions
            if str(item.get("session_id") or item.get("id") or "").strip()
        )

    async def read_conversation(
        self,
        session_id: str,
        *,
        maximum_messages: int = 120,
        maximum_characters: int = 40_000,
    ) -> PluginConversationTranscript | None:
        """Read one bounded Chat transcript owned by the authenticated user."""
        from deeptutor.services.session import get_session_store

        store = get_session_store()
        session = await store.get_session(session_id, surface="chat")
        if session is None:
            return None
        messages = await store.get_messages_for_context(session_id)
        bounded_messages = messages[-max(1, min(maximum_messages, 200)) :]
        output: list[dict[str, str]] = []
        used = 0
        for message in reversed(bounded_messages):
            role = str(message.get("role") or "")
            if role not in {"user", "assistant"}:
                continue
            content = str(message.get("content") or "").strip()
            if not content:
                continue
            remaining = maximum_characters - used
            if remaining <= 0:
                break
            clipped = content[-remaining:]
            output.append(
                {
                    "id": str(message.get("id") or ""),
                    "role": role,
                    "content": clipped,
                }
            )
            used += len(clipped)
        output.reverse()
        return PluginConversationTranscript(
            session_id=session_id,
            title=str(session.get("title") or "Untitled conversation"),
            messages=tuple(output),
        )


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

    def save_attachment(self, *, filename: str, content: bytes) -> dict[str, Any]:
        """Persist one user-owned source and return an opaque content-addressed ref."""
        from deeptutor.services.path_service import get_path_service
        from deeptutor.utils.document_validator import DocumentValidator

        safe_name = Path(filename).name
        if not safe_name or safe_name != filename:
            raise ValueError("attachment filename must be a plain basename")
        suffix = Path(safe_name).suffix.lower()
        if suffix not in {".pdf", ".txt", ".md"}:
            raise ValueError("structured sources support only PDF, TXT and Markdown")
        if not content or len(content) > DocumentValidator.MAX_FILE_SIZE:
            raise ValueError("attachment is empty or exceeds the Host size limit")
        source_hash = hashlib.sha256(content).hexdigest()
        source_ref = f"source:{source_hash}"
        root = get_path_service().workspace_root / "structured_sources" / source_hash
        root.mkdir(parents=True, exist_ok=True)
        path = root / f"original{suffix}"
        if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() != source_hash:
            raise PluginDataConflict("structured source ref conflicts with stored bytes")
        if not path.exists():
            path.write_bytes(content)
        manifest = root / "manifest.json"
        if not manifest.exists():
            manifest.write_text(
                json.dumps(
                    {"source_ref": source_ref, "source_hash": source_hash, "filename": safe_name},
                    ensure_ascii=False,
                    sort_keys=True,
                ),
                encoding="utf-8",
            )
        return {
            "source_ref": source_ref,
            "source_hash": source_hash,
            "filename": safe_name,
            "size_bytes": len(content),
        }

    async def parse_saved_source(self, source_ref: str) -> dict[str, Any]:
        """Parse a previously saved source through the shared ParseService."""
        from deeptutor.services.parsing import get_parse_service

        root = self._source_root(source_ref)
        paths = [path for path in root.glob("original.*") if path.is_file()]
        if len(paths) != 1:
            raise FileNotFoundError("structured source is unavailable")
        parsed = await asyncio.to_thread(get_parse_service().parse, paths[0])
        return {
            "source_ref": source_ref,
            "source_hash": parsed.source_hash,
            "parser_signature": parsed.parser_signature,
            "engine": parsed.engine,
            "markdown": parsed.markdown,
            "blocks": parsed.blocks or [],
            "asset_ref": None if parsed.asset_dir is None else str(parsed.asset_dir),
        }

    @staticmethod
    def _source_root(source_ref: str) -> Path:
        from deeptutor.services.path_service import get_path_service

        match = re.fullmatch(r"source:([0-9a-f]{64})", source_ref)
        if match is None:
            raise ValueError("invalid structured source ref")
        return get_path_service().workspace_root / "structured_sources" / match.group(1)


class PluginKnowledgeIndexHost:
    """Domain-neutral owner-scoped structured-document index bridge."""

    @staticmethod
    def index_ref(identity: str) -> str:
        return f"structured-{hashlib.sha256(identity.encode()).hexdigest()[:32]}"

    async def build(
        self,
        *,
        index_ref: str,
        documents: tuple[dict[str, Any], ...],
        progress_callback: Any = None,
    ) -> dict[str, Any]:
        from deeptutor.knowledge.manager import KnowledgeBaseManager
        from deeptutor.services.path_service import get_path_service
        from deeptutor.services.rag.service import RAGService

        name = self._index_name(index_ref)
        base = get_path_service().get_knowledge_bases_root()
        kb_dir = base / name
        inputs = kb_dir / "structured_inputs"
        inputs.mkdir(parents=True, exist_ok=True)
        paths: list[str] = []
        for position, document in enumerate(documents):
            text = str(document.get("text") or "").strip()
            if not text:
                continue
            path = inputs / f"document-{position:06d}.md"
            path.write_text(text, encoding="utf-8")
            path.with_suffix(path.suffix + ".metadata.json").write_text(
                json.dumps(
                    dict(document.get("metadata") or {}), ensure_ascii=False, sort_keys=True
                ),
                encoding="utf-8",
            )
            paths.append(str(path))
        if not paths:
            raise ValueError("knowledge index requires at least one non-empty document")
        manager = KnowledgeBaseManager(base)
        manager.update_kb_status(name, "processing", {"stage": "indexing", "percent": 0})
        try:
            built = await RAGService(kb_base_dir=str(base)).initialize(
                kb_name=name, file_paths=paths, progress_callback=progress_callback
            )
        except Exception:
            manager.update_kb_status(name, "error", {"stage": "indexing", "percent": 0})
            raise
        if not built:
            raise RuntimeError("Host knowledge index produced no searchable content")
        manager.update_kb_status(
            name, "ready", {"stage": "complete", "percent": 100, "indexed_count": len(paths)}
        )
        return {"index_ref": name, "index_version": self._index_version(kb_dir)}

    async def rebuild(self, index_ref: str) -> dict[str, Any]:
        from deeptutor.services.path_service import get_path_service

        name = self._index_name(index_ref)
        inputs = get_path_service().get_knowledge_bases_root() / name / "structured_inputs"
        documents: list[dict[str, Any]] = []
        for path in sorted(inputs.glob("*.md")):
            sidecar = path.with_suffix(path.suffix + ".metadata.json")
            documents.append(
                {
                    "text": path.read_text(encoding="utf-8"),
                    "metadata": json.loads(sidecar.read_text(encoding="utf-8")),
                }
            )
        return await self.build(index_ref=name, documents=tuple(documents))

    async def search(
        self,
        *,
        index_ref: str,
        query: str,
        metadata_filters: dict[str, tuple[str, ...]] | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        from deeptutor.services.path_service import get_path_service
        from deeptutor.services.rag.service import RAGService

        name = self._index_name(index_ref)
        base = get_path_service().get_knowledge_bases_root()
        if not (base / name).is_dir():
            raise FileNotFoundError("knowledge index is unavailable")
        result = await RAGService(kb_base_dir=str(base)).search(
            query=query, kb_name=name, top_k=top_k, metadata_filters=metadata_filters or {}
        )
        result["index_ref"] = name
        result["index_version"] = self._index_version(base / name)
        return result

    def status(self, index_ref: str) -> dict[str, Any]:
        from deeptutor.services.path_service import get_path_service

        name = self._index_name(index_ref)
        path = get_path_service().get_knowledge_bases_root() / name
        return {
            "index_ref": name,
            "available": path.is_dir(),
            "index_version": self._index_version(path),
        }

    @staticmethod
    def _index_name(index_ref: str) -> str:
        if re.fullmatch(r"structured-[0-9a-f]{32}", index_ref) is None:
            raise ValueError("invalid knowledge index ref")
        return index_ref

    @staticmethod
    def _index_version(path: Path) -> str:
        versions = sorted(item.name for item in path.glob("version-*") if item.is_dir())
        return versions[-1] if versions else "unavailable"


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
                raise PluginDataConflict("Native Memory event identity conflicts with stored trace")
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
    "PluginKnowledgeIndexHost",
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
