"""Host-neutral port for the frozen Native Memory experiment arm."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timezone
from typing import Any, Protocol

from pydantic import JsonValue

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)


@dataclass(frozen=True, slots=True)
class NativeMemoryEvent:
    """ExamMem-owned event DTO converted by the plugin's Host adapter."""

    id: str
    ts: str
    surface: str
    kind: str
    payload: dict[str, Any]
    session_id: str | None
    turn_id: str


class NativeMemoryClient(Protocol):
    """Stable Host operations needed by the native comparison backend."""

    async def append_once(self, event: NativeMemoryEvent) -> bool: ...

    async def consolidate_quiz(self) -> None: ...

    def snapshot(self) -> dict[str, JsonValue]: ...


class NativeMemoryBackend:
    """Exercise a supplied Native Memory port without reading Host internals."""

    def __init__(
        self,
        client: NativeMemoryClient,
        *,
        trace_id: str | None = None,
    ) -> None:
        if trace_id is not None and not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self._client = client
        self._trace_id = trace_id
        self._new_events: set[str] = set()

    async def record_event(self, event: LearningEvent) -> None:
        native_event = _native_event(
            event,
            suffix="learning_event",
            payload=_event_payload(event, trace_id=self._trace_id or event.event_id),
            trace_id=self._trace_id or event.event_id,
        )
        if await self._client.append_once(native_event):
            self._new_events.add(event.event_id)

    async def update(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> list[LifecycleDecision]:
        _validate_candidates(event, candidates)
        candidate_event = _native_event(
            event,
            suffix="memory_candidates",
            payload={
                "event_id": event.event_id,
                "trace_id": self._trace_id or event.event_id,
                "candidates": [candidate.model_dump(mode="json") for candidate in candidates],
            },
            trace_id=self._trace_id or event.event_id,
        )
        candidate_created = await self._client.append_once(candidate_event)
        if event.event_id in self._new_events or candidate_created:
            await self._client.consolidate_quiz()
        self._new_events.discard(event.event_id)
        return []

    async def query_state(self, context: LearningContext) -> StudentModel | None:
        return None

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]:
        if top_k < 1:
            raise ValueError("top_k must be greater than or equal to 1")
        return []

    async def snapshot(self, context: LearningContext) -> dict[str, JsonValue]:
        return self._client.snapshot()


def _native_event(
    event: LearningEvent,
    *,
    suffix: str,
    payload: dict[str, Any],
    trace_id: str,
) -> NativeMemoryEvent:
    return NativeMemoryEvent(
        id=f"quiz:exam_mem:{event.event_id}:{suffix}",
        ts=event.occurred_at.astimezone(timezone.utc).isoformat(),
        surface="quiz",
        kind=f"exam_mem_{suffix}",
        payload=payload,
        session_id=event.session_id,
        turn_id=trace_id,
    )


def _event_payload(event: LearningEvent, *, trace_id: str) -> dict[str, Any]:
    return {**event.model_dump(mode="json"), "trace_id": trace_id}


def _validate_candidates(
    event: LearningEvent,
    candidates: list[MemoryUpdateCandidate],
) -> None:
    for candidate in candidates:
        if candidate.event_id != event.event_id:
            raise ValueError("candidate event_id must match the current event")
        if candidate.scope.model_dump(exclude={"memory_namespace"}) != event.context.model_dump():
            raise ValueError("candidate scope must match the current event context")


__all__ = [
    "NativeMemoryBackend",
    "NativeMemoryClient",
    "NativeMemoryEvent",
]
