from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.backends.baseline import (
    AppendOnlyMemoryBackend,
    BackendWriteConflict,
    VectorMemoryBackend,
)
from exam_mem.backends.protocol import BackendMode
from exam_mem.contracts import LearningEvent, MemoryScope, MemoryUpdateCandidate
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    AppendResult,
    AppendStatus,
    BaselineFactAppendResult,
    BaselineFactRecord,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.backend_mode]

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="baseline_backend_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "baseline_backend_event_001",
            "idempotency_key": "baseline-backend-idempotency-001",
            "event_type": "answer_attempt",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
            "session_id": "baseline_backend_session",
            "question_id": "baseline_backend_question",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "difficulty": 0.5,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "rank conditions were confused",
            "occurred_at": NOW,
        }
    )


def _candidate() -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate(
        event_id=_event().event_id,
        scope=SCOPE,
        slot_key="mastery:math1.linear_algebra.matrix_rank",
        proposed_value={"type": "mastery", "level": "low", "score": 0.0},
        evidence={"source": "baseline_backend_test"},
    )


def _basis_vector() -> list[float]:
    return [1.0, *([0.0] * (LEARNING_MEMORY_EMBEDDING_DIMENSION - 1))]


class FakeEventRepository:
    def __init__(self, status: AppendStatus = AppendStatus.CREATED) -> None:
        self.status = status
        self.events: list[LearningEvent] = []
        self.trace_ids: list[str | None] = []

    async def append(
        self,
        event: LearningEvent,
        *,
        trace_id: str | None = None,
    ) -> AppendResult:
        self.events.append(event)
        self.trace_ids.append(trace_id)
        return AppendResult(status=self.status, event=event)


class FakeFactRepository:
    def __init__(self, status: AppendStatus = AppendStatus.CREATED) -> None:
        self.status = status
        self.records: list[BaselineFactRecord] = []
        self.similar_calls: list[tuple[MemoryScope, list[float], int]] = []

    async def append(self, record: BaselineFactRecord) -> BaselineFactAppendResult:
        self.records.append(record)
        return BaselineFactAppendResult(status=self.status, record=record)

    async def list_scope(
        self,
        backend_mode: BackendMode,
        scope: MemoryScope,
        limit: int,
    ) -> list[BaselineFactRecord]:
        return [
            record
            for record in self.records
            if record.backend_mode is backend_mode and record.candidate.scope == scope
        ][:limit]

    async def find_similar(
        self,
        scope: MemoryScope,
        query_embedding: list[float],
        limit: int,
    ) -> list[BaselineFactRecord]:
        self.similar_calls.append((scope, query_embedding, limit))
        return [record for record in self.records if record.candidate.scope == scope][:limit]

    async def snapshot(self, backend_mode, context):  # noqa: ANN001, ANN201
        return [record for record in self.records if record.backend_mode is backend_mode]


class FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]:
        self.calls.append((texts, input_type))
        return [_basis_vector() for _ in texts]


async def test_append_only_writes_l1_and_fact_without_lifecycle_decisions() -> None:
    events = FakeEventRepository()
    facts = FakeFactRepository()
    backend = AppendOnlyMemoryBackend(
        event_repository=events,
        fact_repository=facts,
    )
    event = _event()

    await backend.record_event(event)
    decisions = await backend.update(event, [_candidate()])
    retrieved = await backend.retrieve(SCOPE, "ignored for append order", 5)

    assert events.events == [event]
    assert decisions == []
    assert len(facts.records) == 1
    assert facts.records[0].backend_mode is BackendMode.APPEND_ONLY
    assert facts.records[0].content_embedding is None
    assert [memory.provenance for memory in retrieved] == [[event.event_id]]
    assert all(memory.version == 1 for memory in retrieved)


async def test_vector_embeds_documents_and_queries_through_the_same_client() -> None:
    facts = FakeFactRepository()
    embeddings = FakeEmbeddingClient()
    backend = VectorMemoryBackend(
        event_repository=FakeEventRepository(),
        fact_repository=facts,
        embedding_client=embeddings,
    )
    event = _event()

    await backend.record_event(event)
    assert await backend.update(event, [_candidate()]) == []
    retrieved = await backend.retrieve(SCOPE, "matrix rank weakness", 3)

    assert [call[1] for call in embeddings.calls] == ["search_document", "search_query"]
    assert facts.records[0].backend_mode is BackendMode.VECTOR
    assert facts.records[0].content_embedding == tuple(_basis_vector())
    assert facts.similar_calls == [(SCOPE, _basis_vector(), 3)]
    assert len(retrieved) == 1


async def test_baseline_conflicts_fail_instead_of_switching_modes() -> None:
    event = _event()
    event_conflict = AppendOnlyMemoryBackend(
        event_repository=FakeEventRepository(AppendStatus.CONFLICT),
        fact_repository=FakeFactRepository(),
    )
    with pytest.raises(BackendWriteConflict, match="stored L1"):
        await event_conflict.record_event(event)

    fact_conflict = AppendOnlyMemoryBackend(
        event_repository=FakeEventRepository(),
        fact_repository=FakeFactRepository(AppendStatus.CONFLICT),
    )
    with pytest.raises(BackendWriteConflict, match="stored candidate"):
        await fact_conflict.update(event, [_candidate()])


async def test_baseline_rejects_cross_event_candidates_before_writing() -> None:
    facts = FakeFactRepository()
    backend = AppendOnlyMemoryBackend(
        event_repository=FakeEventRepository(),
        fact_repository=facts,
    )
    candidate = _candidate().model_copy(update={"event_id": "other_event"})

    with pytest.raises(ValueError, match="event_id"):
        await backend.update(_event(), [candidate])

    assert facts.records == []
