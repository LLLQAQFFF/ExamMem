from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.backends.native import NativeMemoryBackend, NativeMemoryEvent
from exam_mem.contracts import LearningEvent, MemoryScope, MemoryUpdateCandidate

pytestmark = [pytest.mark.asyncio, pytest.mark.backend_mode]

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="native_backend_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "native_backend_event_001",
            "idempotency_key": "native-backend-idempotency-001",
            "event_type": "answer_attempt",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
            "session_id": "native_backend_session",
            "question_id": "native_backend_question",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "difficulty": 0.5,
            "answer_correct": True,
            "occurred_at": NOW,
        }
    )


def _candidate() -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate(
        event_id=_event().event_id,
        scope=SCOPE,
        slot_key="mastery:math1.linear_algebra.matrix_rank",
        proposed_value={"type": "mastery", "level": "high", "score": 1.0},
        evidence={"source": "native_backend_test"},
    )


class FakeNativeMemoryClient:
    def __init__(self) -> None:
        self.events: dict[str, NativeMemoryEvent] = {}
        self.consolidations = 0

    async def append_once(self, event: NativeMemoryEvent) -> bool:
        if event.id in self.events:
            assert self.events[event.id] == event
            return False
        self.events[event.id] = event
        return True

    async def consolidate_quiz(self) -> None:
        self.consolidations += 1

    def snapshot(self):  # noqa: ANN201
        return {"backend_mode": "native", "quiz_l2": "native", "l3": "snapshot"}


async def test_native_backend_uses_only_the_host_neutral_memory_port() -> None:
    client = FakeNativeMemoryClient()
    backend = NativeMemoryBackend(client)
    event = _event()

    await backend.record_event(event)
    decisions = await backend.update(event, [_candidate()])
    snapshot = await backend.snapshot(event.context)

    assert decisions == []
    assert client.consolidations == 1
    assert list(client.events) == [
        f"quiz:exam_mem:{event.event_id}:learning_event",
        f"quiz:exam_mem:{event.event_id}:memory_candidates",
    ]
    assert all(item.surface == "quiz" for item in client.events.values())
    assert snapshot == {
        "backend_mode": "native",
        "quiz_l2": "native",
        "l3": "snapshot",
    }


async def test_native_replay_does_not_reconsolidate_or_emit_duplicate_trace() -> None:
    client = FakeNativeMemoryClient()
    event = _event()

    first = NativeMemoryBackend(client)
    await first.record_event(event)
    await first.update(event, [_candidate()])

    replay = NativeMemoryBackend(client)
    await replay.record_event(event)
    await replay.update(event, [_candidate()])

    assert len(client.events) == 2
    assert client.consolidations == 1


async def test_native_backend_does_not_invent_typed_learning_memories() -> None:
    backend = NativeMemoryBackend(FakeNativeMemoryClient())

    assert await backend.query_state(_event().context) is None
    assert await backend.retrieve(SCOPE, "query", 5) == []
