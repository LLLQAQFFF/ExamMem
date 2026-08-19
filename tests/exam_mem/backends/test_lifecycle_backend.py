from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.backends.lifecycle import CorrectionTargetNotCurrent, LifecycleMemoryBackend
from exam_mem.contracts import (
    LearningEvent,
    LearningMemory,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import (
    LifecycleApplicationResult,
    LifecycleApplyState,
    LifecycleCandidateSnapshot,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    MemoryRelation,
)
from exam_mem.storage import AppendResult, AppendStatus

pytestmark = [pytest.mark.asyncio, pytest.mark.backend_mode, pytest.mark.lifecycle]

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="lifecycle_backend_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"


def _event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "lifecycle_backend_event_001",
            "idempotency_key": "lifecycle-backend-idempotency-001",
            "event_type": "answer_attempt",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
            "session_id": "lifecycle_backend_session",
            "question_id": "lifecycle_backend_question",
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
        slot_key=SLOT_KEY,
        proposed_value={"type": "mastery", "level": "low", "score": 0.0},
        evidence={"source": "lifecycle_backend_test"},
    )


def _correction_event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "lifecycle_backend_correction_001",
            "idempotency_key": "lifecycle-backend-correction-001",
            "event_type": "explicit_correction",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
            "session_id": "lifecycle_backend_session",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "correction": {
                "target_memory_ids": ["terminal_memory_v1"],
                "source": "user",
                "statement": "This stored mastery is inaccurate.",
            },
            "occurred_at": NOW,
        }
    )


def _correction_candidate() -> MemoryUpdateCandidate:
    event = _correction_event()
    return MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value={"type": "mastery", "level": "low", "score": 0.0},
        evidence={"target_memory_id": "terminal_memory_v1"},
    )


class FakeEventRepository:
    def __init__(self) -> None:
        self.events: list[LearningEvent] = []

    async def append(
        self,
        event: LearningEvent,
        *,
        trace_id: str | None = None,
    ) -> AppendResult:
        del trace_id
        self.events.append(event)
        return AppendResult(status=AppendStatus.CREATED, event=event)

    async def list_after(self, context, watermark, limit):  # noqa: ANN001, ANN201
        del context, watermark, limit
        return list(self.events)


class FakeMemoryRepository:
    def __init__(self) -> None:
        self.inserted: list[LearningMemory] = []

    async def find_candidate_snapshots(self, query, *, for_update=False):  # noqa: ANN001, ANN201
        del query, for_update
        return []

    async def find_candidates(self, query, *, for_update=False):  # noqa: ANN001, ANN201
        del query, for_update
        return list(self.inserted)

    async def snapshot(self, scope):  # noqa: ANN001, ANN201
        return [memory for memory in self.inserted if memory.scope == scope]

    async def find_similar(self, scope, query_embedding, limit):  # noqa: ANN001, ANN201
        del query_embedding
        return [memory for memory in self.inserted if memory.scope == scope][:limit]


class ExistingMasteryMemoryRepository(FakeMemoryRepository):
    def __init__(self, *, event_id: str, score: float) -> None:
        super().__init__()
        level = "high" if score == 1.0 else "low"
        memory = LearningMemory.model_validate(
            {
                "memory_id": "existing_mastery_v1",
                "scope": SCOPE.model_dump(mode="json"),
                "slot_key": SLOT_KEY,
                "value": {"type": "mastery", "level": level, "score": score},
                "confidence": 0.9,
                "evidence_count": 1,
                "lifecycle_state": "active",
                "version": 1,
                "valid_from": NOW,
                "valid_to": None,
                "superseded_by": None,
                "provenance": [event_id],
            }
        )
        self.snapshot = LifecycleCandidateSnapshot(
            memory=memory,
            row_version=1,
            policy_version="lifecycle_policy_v1",
        )

    async def find_candidate_snapshots(self, query, *, for_update=False):  # noqa: ANN001, ANN201
        del query, for_update
        return [self.snapshot]


class ExistingErrorMemoryRepository(FakeMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        memory = LearningMemory.model_validate(
            {
                "memory_id": "existing_error_v1",
                "scope": SCOPE.model_copy(update={"memory_namespace": "error_pattern"}).model_dump(
                    mode="json"
                ),
                "slot_key": ("error_pattern:math1.linear_algebra.matrix_rank:concept_confusion"),
                "value": {
                    "type": "error_pattern",
                    "error_type": "concept_confusion",
                    "summary": "confuses matrix rank conditions",
                    "details": ["confuses matrix rank conditions"],
                },
                "confidence": 0.9,
                "evidence_count": 1,
                "lifecycle_state": "active",
                "version": 1,
                "valid_from": NOW,
                "valid_to": None,
                "superseded_by": None,
                "provenance": ["prior_error_event"],
            }
        )
        self.snapshot = LifecycleCandidateSnapshot(
            memory=memory,
            row_version=1,
            policy_version="lifecycle_policy_v1",
        )

    async def find_candidate_snapshots(self, query, *, for_update=False):  # noqa: ANN001, ANN201
        del query, for_update
        return [self.snapshot]


class ExistingCorrectionEventRepository(FakeEventRepository):
    async def append(
        self,
        event: LearningEvent,
        *,
        trace_id: str | None = None,
    ) -> AppendResult:
        del trace_id
        self.events.append(event)
        return AppendResult(status=AppendStatus.EXISTING, event=event)


class HistoricalCorrectionMemoryRepository(FakeMemoryRepository):
    def __init__(self, event: LearningEvent) -> None:
        super().__init__()
        self.historical = LifecycleMemorySnapshot(
            memory=LearningMemory(
                memory_id="terminal_memory_v1",
                scope=SCOPE,
                slot_key=SLOT_KEY,
                value={"type": "mastery", "level": "high", "score": 0.9},
                confidence=0.9,
                evidence_count=2,
                lifecycle_state=LifecycleState.INVALIDATED,
                version=1,
                valid_from=NOW,
                valid_to=NOW,
                superseded_by=None,
                provenance=[event.event_id],
            ),
            row_version=2,
            policy_version="lifecycle_policy_v1",
        )

    async def event_was_applied(self, scope, slot_key, event_id):  # noqa: ANN001, ANN201
        return (
            scope == SCOPE
            and slot_key == SLOT_KEY
            and event_id in self.historical.memory.provenance
        )

    async def list_slot_snapshots(self, scope, slot_key):  # noqa: ANN001, ANN201
        assert scope == SCOPE
        assert slot_key == SLOT_KEY
        return [self.historical]


class FakeStudentModelRepository:
    async def get_latest(self, context):  # noqa: ANN001, ANN201
        del context
        return None


class FailingRelationClassifier:
    async def classify(self, candidate, candidate_snapshots):  # noqa: ANN001, ANN201
        del candidate, candidate_snapshots
        raise AssertionError("new mastery slot must not call the relation classifier")


class FakeApplier:
    def __init__(self, memory_repository: FakeMemoryRepository) -> None:
        self._memory_repository = memory_repository
        self.calls: list[tuple[str, str]] = []
        self.policy_inputs = []

    async def apply(
        self,
        policy_input,
        policy_result,
        *,
        decision_id,
        trace_id,
        applied_at,
    ):  # noqa: ANN001, ANN201
        self.calls.append((decision_id, trace_id))
        self.policy_inputs.append(policy_input)
        memory = LearningMemory(
            memory_id=f"{decision_id}:memory:v1",
            scope=policy_input.candidate.scope,
            slot_key=policy_input.candidate.slot_key,
            value=policy_input.candidate.proposed_value,
            confidence=policy_result.decision.confidence,
            evidence_count=1,
            lifecycle_state=LifecycleState.ACTIVE,
            version=1,
            valid_from=applied_at,
            valid_to=None,
            superseded_by=None,
            provenance=[policy_input.event.event_id],
        )
        self._memory_repository.inserted.append(memory)
        after = LifecycleMemorySnapshot(
            memory=memory,
            row_version=1,
            policy_version=policy_result.decision.policy_version,
        )
        decision = LifecycleDecisionAuditRecord(
            decision_id=decision_id,
            trace_id=trace_id,
            policy_input=policy_input,
            policy_result=policy_result,
            created_at=applied_at,
        )
        change = LifecycleChangeAuditRecord(
            change_id=f"{decision_id}:applied",
            decision_id=decision_id,
            trace_id=trace_id,
            apply_state=LifecycleApplyState.APPLIED,
            memory_id=memory.memory_id,
            after_state=after,
            actual_row_version=1,
            recorded_at=applied_at,
        )
        return LifecycleApplicationResult(decision=decision, changes=(change,))


async def test_lifecycle_backend_reuses_policy_applier_and_emits_projection_request() -> None:
    events = FakeEventRepository()
    memories = FakeMemoryRepository()
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=events,
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
        trace_id="practice_trace_001",
    )
    event = _event()

    await backend.record_event(event)
    decisions = await backend.update(event, [_candidate()])
    requests = backend.take_projection_requests()

    assert [decision.operation for decision in decisions] == [LifecycleOperation.ADD]
    assert len(memories.inserted) == 1
    assert memories.inserted[0].provenance == [event.event_id]
    assert len(requests) == 1
    assert requests[0].context == event.context
    assert applier.calls[0][1] == "practice_trace_001"
    assert backend.take_projection_requests() == ()


async def test_existing_equal_mastery_uses_typed_duplicate_without_llm() -> None:
    event = _event()
    memories = ExistingMasteryMemoryRepository(event_id="prior_mastery_event", score=0.0)
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=FakeEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )

    await backend.record_event(event)
    decisions = await backend.update(event, [_candidate()])

    assert decisions[0].operation is LifecycleOperation.MERGE
    relation = applier.policy_inputs[0].relation
    assert relation is not None
    assert relation.classification.relation is MemoryRelation.DUPLICATE


async def test_existing_opposite_mastery_uses_typed_contradiction_without_llm() -> None:
    prior = _event().model_copy(
        update={
            "event_id": "prior_mastery_event",
            "idempotency_key": "prior-mastery-event",
        }
    )
    current = _event().model_copy(
        update={
            "event_id": "current_mastery_event",
            "idempotency_key": "current-mastery-event",
            "answer_correct": True,
            "error_type": None,
            "error_detail": None,
        }
    )
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": current.event_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": {"type": "mastery", "level": "high", "score": 1.0},
            "evidence": {"source": "lifecycle_backend_test"},
        }
    )
    events = FakeEventRepository()
    events.events.append(prior)
    memories = ExistingMasteryMemoryRepository(event_id=prior.event_id, score=0.0)
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=events,
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )

    await backend.record_event(current)
    await backend.update(current, [candidate])

    relation = applier.policy_inputs[0].relation
    assert relation is not None
    assert relation.classification.relation is MemoryRelation.CONTRADICTORY


async def test_temporary_error_evidence_bypasses_semantic_relation_classifier() -> None:
    event_payload = _event().model_dump(mode="json")
    event_payload["evidence_quality"] = {
        "confidence": 0.1,
        "is_temporary_exception": True,
        "reasons": ["external_disruption"],
    }
    event = LearningEvent.model_validate(event_payload)
    scope = SCOPE.model_copy(update={"memory_namespace": "error_pattern"})
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": "error_pattern:math1.linear_algebra.matrix_rank:concept_confusion",
            "proposed_value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "temporary interrupted answer",
                "details": ["temporary interrupted answer"],
            },
            "evidence": {"source": "lifecycle_backend_test"},
        }
    )
    memories = ExistingErrorMemoryRepository()
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=FakeEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )

    await backend.record_event(event)
    decisions = await backend.update(event, [candidate])

    assert decisions[0].operation is LifecycleOperation.NO_OP
    assert applier.policy_inputs[0].relation is None


async def test_lifecycle_exact_retrieval_uses_active_candidate_query() -> None:
    memories = FakeMemoryRepository()
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=FakeEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )
    event = _event()
    await backend.record_event(event)
    await backend.update(event, [_candidate()])

    assert await backend.retrieve(SCOPE, SLOT_KEY, 5) == memories.inserted


async def test_lifecycle_backend_rejects_cross_context_candidate_before_policy() -> None:
    memories = FakeMemoryRepository()
    backend = LifecycleMemoryBackend(
        event_repository=FakeEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=FakeApplier(memories),
    )
    other_scope = SCOPE.model_copy(update={"user_id": "other_user"})
    candidate = _candidate().model_copy(update={"scope": other_scope})

    with pytest.raises(ValueError, match="scope"):
        await backend.update(_event(), [candidate])

    assert memories.inserted == []


async def test_new_explicit_correction_requires_current_target() -> None:
    memories = FakeMemoryRepository()
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=FakeEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )
    event = _correction_event()

    await backend.record_event(event)
    with pytest.raises(CorrectionTargetNotCurrent, match="historical terminal"):
        await backend.update(event, [_correction_candidate()])

    assert applier.calls == []


async def test_applied_correction_replay_precedes_current_target_guard() -> None:
    event = _correction_event()
    memories = HistoricalCorrectionMemoryRepository(event)
    applier = FakeApplier(memories)
    backend = LifecycleMemoryBackend(
        event_repository=ExistingCorrectionEventRepository(),
        memory_repository=memories,
        student_model_repository=FakeStudentModelRepository(),
        relation_classifier=FailingRelationClassifier(),
        applier=applier,
    )

    await backend.record_event(event)
    decisions = await backend.update(event, [_correction_candidate()])

    assert decisions[0].operation is LifecycleOperation.NO_OP
    assert decisions[0].reason_code == "already_applied_replay"
    assert decisions[0].target_memory_ids == ["terminal_memory_v1"]
    assert applier.calls == []
