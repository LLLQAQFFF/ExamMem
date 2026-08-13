"""Scope-safe read models for the Stage 07 Learning Memory workbench."""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Annotated, Protocol

from pydantic import BaseModel, ConfigDict, StringConstraints

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MemoryNamespace,
    MemoryScope,
)
from exam_mem.lifecycle import LifecycleMemorySnapshot

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictWorkbenchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LearningMemoryListRequest(StrictWorkbenchModel):
    """One authenticated, four-dimensional Learning Memory list query."""

    context: LearningContext
    memory_namespace: MemoryNamespace
    lifecycle_states: tuple[LifecycleState, ...] = ()
    query: NonEmptyString | None = None


class LearningMemorySummary(StrictWorkbenchModel):
    memory: LearningMemory
    correction_allowed: bool


class LearningMemoryDetail(StrictWorkbenchModel):
    snapshot: LifecycleMemorySnapshot
    version_chain: tuple[LifecycleMemorySnapshot, ...]
    correction_allowed: bool


class LearningMemoryEvidence(StrictWorkbenchModel):
    memory: LearningMemory
    events: tuple[LearningEvent, ...]


class WorkbenchMemoryRepository(Protocol):
    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]: ...

    async def get_lifecycle_snapshot(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> LifecycleMemorySnapshot | None: ...

    async def list_slot_snapshots(
        self,
        scope: MemoryScope,
        slot_key: str,
    ) -> list[LifecycleMemorySnapshot]: ...


class WorkbenchEventRepository(Protocol):
    async def get_by_ids(
        self,
        context: LearningContext,
        event_ids: Sequence[str],
    ) -> list[LearningEvent]: ...


class LearningMemoryQueryService:
    """Expose L2 state, version chains, and L1 evidence without bypassing Scope."""

    def __init__(
        self,
        *,
        memory_repository: WorkbenchMemoryRepository,
        event_repository: WorkbenchEventRepository,
    ) -> None:
        self._memories = memory_repository
        self._events = event_repository

    async def list_memories(
        self,
        request: LearningMemoryListRequest,
    ) -> tuple[LearningMemorySummary, ...]:
        scope = _scope(request.context, request.memory_namespace)
        memories = await self._memories.snapshot(scope)
        states = set(request.lifecycle_states)
        query = request.query.casefold() if request.query is not None else None
        selected = [
            memory
            for memory in memories
            if (not states or memory.lifecycle_state in states)
            and (query is None or query in _search_text(memory))
        ]
        return tuple(
            LearningMemorySummary(
                memory=memory,
                correction_allowed=_correction_allowed(memory),
            )
            for memory in selected
        )

    async def get_detail(
        self,
        *,
        context: LearningContext,
        memory_namespace: MemoryNamespace,
        memory_id: str,
    ) -> LearningMemoryDetail | None:
        scope = _scope(context, memory_namespace)
        snapshot = await self._memories.get_lifecycle_snapshot(scope, memory_id)
        if snapshot is None:
            return None
        version_chain = await self._memories.list_slot_snapshots(
            scope,
            snapshot.memory.slot_key,
        )
        return LearningMemoryDetail(
            snapshot=snapshot,
            version_chain=tuple(version_chain),
            correction_allowed=_correction_allowed(snapshot.memory),
        )

    async def get_evidence(
        self,
        *,
        context: LearningContext,
        memory_namespace: MemoryNamespace,
        memory_id: str,
    ) -> LearningMemoryEvidence | None:
        detail = await self.get_detail(
            context=context,
            memory_namespace=memory_namespace,
            memory_id=memory_id,
        )
        if detail is None:
            return None
        memory = detail.snapshot.memory
        events = await self._events.get_by_ids(context, memory.provenance)
        return LearningMemoryEvidence(memory=memory, events=tuple(events))

    async def recommendation_inputs(
        self,
        context: LearningContext,
    ) -> tuple[str, ...]:
        """Re-read the exact L2 rows eligible for the next recommendation."""
        source_ids: list[str] = []
        for namespace in (
            MemoryNamespace.MASTERY,
            MemoryNamespace.ERROR_PATTERN,
            MemoryNamespace.PLAN,
        ):
            memories = await self._memories.snapshot(_scope(context, namespace))
            source_ids.extend(
                memory.memory_id
                for memory in memories
                if memory.lifecycle_state
                not in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
            )
        return tuple(sorted(source_ids))


def _scope(context: LearningContext, namespace: MemoryNamespace) -> MemoryScope:
    return MemoryScope(
        **context.model_dump(),
        memory_namespace=namespace,
    )


def _correction_allowed(memory: LearningMemory) -> bool:
    return memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}


def _search_text(memory: LearningMemory) -> str:
    return " ".join(
        (
            memory.memory_id,
            memory.slot_key,
            json.dumps(
                memory.value.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
    ).casefold()


__all__ = [
    "LearningMemoryDetail",
    "LearningMemoryEvidence",
    "LearningMemoryListRequest",
    "LearningMemoryQueryService",
    "LearningMemorySummary",
    "WorkbenchEventRepository",
    "WorkbenchMemoryRepository",
]
