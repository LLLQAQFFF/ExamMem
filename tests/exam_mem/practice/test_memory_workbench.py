from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryScope,
)
from exam_mem.lifecycle import LifecycleMemorySnapshot
from exam_mem.practice.memory_workbench import (
    LearningMemoryListRequest,
    LearningMemoryQueryService,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, 9, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="workbench_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)
SCOPE = MemoryScope(**CONTEXT.model_dump(), memory_namespace="mastery")


def _memory(
    memory_id: str,
    *,
    version: int,
    state: LifecycleState,
) -> LearningMemory:
    return LearningMemory(
        memory_id=memory_id,
        scope=SCOPE,
        slot_key="mastery:math1.probability.bayes",
        value=MasteryValue(level=MasteryLevel.LOW, score=0.2),
        confidence=0.9,
        evidence_count=1,
        lifecycle_state=state,
        version=version,
        valid_from=NOW,
        valid_to=(NOW if state is not LifecycleState.ACTIVE else None),
        superseded_by=None,
        provenance=[f"event:{version}"],
    )


class FakeMemoryRepository:
    def __init__(self, memories: list[LearningMemory]) -> None:
        self.memories = memories
        self.scopes: list[MemoryScope] = []

    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]:
        self.scopes.append(scope)
        return [memory for memory in self.memories if memory.scope == scope]

    async def get_lifecycle_snapshot(self, scope, memory_id):  # noqa: ANN001, ANN201
        self.scopes.append(scope)
        memory = next(
            (item for item in self.memories if item.scope == scope and item.memory_id == memory_id),
            None,
        )
        if memory is None:
            return None
        return LifecycleMemorySnapshot(
            memory=memory,
            row_version=memory.version,
            policy_version="lifecycle_policy_v1",
        )

    async def list_slot_snapshots(self, scope, slot_key):  # noqa: ANN001, ANN201
        self.scopes.append(scope)
        return [
            LifecycleMemorySnapshot(
                memory=memory,
                row_version=memory.version,
                policy_version="lifecycle_policy_v1",
            )
            for memory in self.memories
            if memory.scope == scope and memory.slot_key == slot_key
        ]


class FakeEventRepository:
    def __init__(self, events: list[LearningEvent]) -> None:
        self.events = events
        self.contexts: list[LearningContext] = []

    async def get_by_ids(self, context, event_ids):  # noqa: ANN001, ANN201
        self.contexts.append(context)
        selected = set(event_ids)
        return [event for event in self.events if event.event_id in selected]


def _event(event_id: str) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "context": CONTEXT,
            "session_id": "practice:workbench",
            "question_id": "question:workbench",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.5,
            "answer_correct": False,
            "occurred_at": NOW,
        }
    )


async def test_list_filters_inside_full_scope_and_exposes_terminal_rows() -> None:
    active = _memory("memory:active", version=1, state=LifecycleState.ACTIVE)
    invalidated = _memory(
        "memory:invalidated",
        version=2,
        state=LifecycleState.INVALIDATED,
    )
    repository = FakeMemoryRepository([active, invalidated])
    service = LearningMemoryQueryService(
        memory_repository=repository,
        event_repository=FakeEventRepository([]),
    )

    result = await service.list_memories(
        LearningMemoryListRequest(
            context=CONTEXT,
            memory_namespace="mastery",
            query="bayes",
        )
    )

    assert [item.memory.memory_id for item in result] == [
        "memory:active",
        "memory:invalidated",
    ]
    assert [item.correction_allowed for item in result] == [True, False]
    assert repository.scopes == [SCOPE]


async def test_detail_returns_complete_version_chain_and_evidence() -> None:
    first = _memory("memory:v1", version=1, state=LifecycleState.ARCHIVED)
    second = _memory("memory:v2", version=2, state=LifecycleState.ACTIVE)
    event = _event("event:2")
    memories = FakeMemoryRepository([first, second])
    events = FakeEventRepository([event])
    service = LearningMemoryQueryService(
        memory_repository=memories,
        event_repository=events,
    )

    detail = await service.get_detail(
        context=CONTEXT,
        memory_namespace="mastery",
        memory_id=second.memory_id,
    )
    evidence = await service.get_evidence(
        context=CONTEXT,
        memory_namespace="mastery",
        memory_id=second.memory_id,
    )

    assert detail is not None
    assert [item.memory.memory_id for item in detail.version_chain] == [
        "memory:v1",
        "memory:v2",
    ]
    assert evidence is not None
    assert evidence.events == (event,)
    assert events.contexts == [CONTEXT]


async def test_missing_id_never_falls_back_to_another_scope() -> None:
    repository = FakeMemoryRepository([])
    service = LearningMemoryQueryService(
        memory_repository=repository,
        event_repository=FakeEventRepository([]),
    )

    result = await service.get_detail(
        context=CONTEXT,
        memory_namespace="mastery",
        memory_id="memory:other-user",
    )

    assert result is None
    assert repository.scopes == [SCOPE]
