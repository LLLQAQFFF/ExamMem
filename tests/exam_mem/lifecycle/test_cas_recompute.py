from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from types import TracebackType

import pytest

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.domain.candidate_query import CandidateQuery
from exam_mem.lifecycle import (
    AuditAppendStatus,
    LifecycleApplier,
    LifecycleApplyState,
    LifecycleCandidateSnapshot,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    decide_lifecycle,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle, pytest.mark.cas]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_cas_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


class _Savepoint(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _Connection:
    def begin_nested(self) -> _Savepoint:
        return _Savepoint()


class _AuditRepository:
    def __init__(self) -> None:
        self.decisions: dict[str, LifecycleDecisionAuditRecord] = {}
        self.changes: dict[str, LifecycleChangeAuditRecord] = {}

    async def append_decision(
        self,
        record: LifecycleDecisionAuditRecord,
    ) -> AuditAppendStatus:
        existing = self.decisions.get(record.decision_id)
        if existing is None:
            self.decisions[record.decision_id] = record
            return AuditAppendStatus.CREATED
        return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus:
        existing = self.changes.get(record.change_id)
        if existing is None:
            self.changes[record.change_id] = record
            return AuditAppendStatus.CREATED
        return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def get_decision(
        self,
        decision_id: str,
    ) -> LifecycleDecisionAuditRecord | None:
        return self.decisions.get(decision_id)

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]:
        return [change for change in self.changes.values() if change.decision_id == decision_id]


class _ConflictingMemoryRepository:
    def __init__(
        self,
        snapshots: tuple[LifecycleMemorySnapshot, ...],
        *,
        forced_conflicts: int = 0,
    ) -> None:
        self.snapshots = {snapshot.memory.memory_id: snapshot for snapshot in snapshots}
        self.forced_conflicts = forced_conflicts
        self.cas_calls = 0

    async def next_version(self, scope: MemoryScope, slot_key: str) -> int:
        versions = [
            snapshot.memory.version
            for snapshot in self.snapshots.values()
            if snapshot.memory.scope == scope and snapshot.memory.slot_key == slot_key
        ]
        return max(versions, default=0) + 1

    async def get_lifecycle_snapshot(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> LifecycleMemorySnapshot | None:
        snapshot = self.snapshots.get(memory_id)
        return snapshot if snapshot is not None and snapshot.memory.scope == scope else None

    async def find_candidate_snapshots(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LifecycleCandidateSnapshot]:
        del for_update
        return [
            LifecycleCandidateSnapshot.model_validate(snapshot.model_dump())
            for snapshot in self.snapshots.values()
            if snapshot.memory.scope == query.scope
            and snapshot.memory.slot_key == query.slot_key
            and snapshot.memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        ]

    async def event_was_applied(
        self,
        scope: MemoryScope,
        slot_key: str,
        event_id: str,
    ) -> bool:
        return any(
            snapshot.memory.scope == scope
            and snapshot.memory.slot_key == slot_key
            and event_id in snapshot.memory.provenance
            for snapshot in self.snapshots.values()
        )

    async def cas_transition(
        self,
        scope: MemoryScope,
        slot_key: str,
        memory_id: str,
        *,
        expected_row_version: int,
        **_: object,
    ) -> LifecycleMemorySnapshot | None:
        self.cas_calls += 1
        current = self.snapshots.get(memory_id)
        if (
            current is None
            or current.memory.scope != scope
            or current.memory.slot_key != slot_key
            or current.row_version != expected_row_version
            or current.memory.lifecycle_state
            not in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        ):
            return None
        if self.forced_conflicts > 0:
            self.forced_conflicts -= 1
            self.snapshots[memory_id] = current.model_copy(
                update={"row_version": current.row_version + 1}
            )
            return None
        raise AssertionError("test expected every attempted CAS to conflict")

    async def insert_version(self, *_: object, **__: object) -> LifecycleMemorySnapshot:
        raise AssertionError("a stale attempt must not insert a new version")


class _EventRepository:
    def __init__(self, events: tuple[LearningEvent, ...] = ()) -> None:
        self.events = {event.event_id: event for event in events}
        self.calls: list[tuple[str, ...]] = []

    async def get_by_ids(
        self,
        _context: object,
        event_ids: tuple[str, ...] | list[str],
    ) -> list[LearningEvent]:
        ordered = tuple(sorted(event_ids))
        self.calls.append(ordered)
        return [self.events[event_id] for event_id in ordered]


def _event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "stage06_cas_event_002",
            "idempotency_key": "idem:stage06_cas_event_002",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_cas_session",
            "question_id": "stage06_cas_question",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.7,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "new detail",
            "occurred_at": NOW,
        }
    )


def _memory(
    *,
    memory_id: str = "stage06_cas_memory_v1",
    state: LifecycleState = LifecycleState.ACTIVE,
    version: int = 1,
    provenance: list[str] | None = None,
    valid_to: datetime | None = None,
    superseded_by: str | None = None,
) -> LearningMemory:
    events = provenance or ["stage06_cas_event_001"]
    return LearningMemory(
        memory_id=memory_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        value=ErrorPatternValue(
            error_type="concept_confusion",
            summary="Confuses conditional probability",
            details=["old detail"],
        ),
        confidence=0.8,
        evidence_count=len(events),
        lifecycle_state=state,
        version=version,
        valid_from=NOW - timedelta(days=1),
        valid_to=valid_to,
        superseded_by=superseded_by,
        provenance=events,
    )


def _snapshot(
    *,
    memory: LearningMemory | None = None,
    row_version: int = 1,
) -> LifecycleMemorySnapshot:
    return LifecycleMemorySnapshot(
        memory=memory or _memory(),
        row_version=row_version,
        policy_version="lifecycle_policy_v1",
    )


def _policy(
    snapshot: LifecycleMemorySnapshot,
) -> tuple[LifecyclePolicyInput, LifecyclePolicyResult]:
    candidate_snapshot = LifecycleCandidateSnapshot.model_validate(snapshot.model_dump())
    event = _event()
    candidate = MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value=ErrorPatternValue(
            error_type="concept_confusion",
            summary="Confuses conditional probability",
            details=["new detail"],
        ),
        evidence={"source": "cas_recompute_test"},
    )
    relation = ResolvedRelationClassification(
        target_memory_id=candidate_snapshot.memory.memory_id,
        classification=RelationClassifierOutput(
            candidate_display_number=1,
            relation=MemoryRelation.COMPLEMENTARY,
            canonical_knowledge_point_id="math1.probability.bayes",
            error_type="concept_confusion",
            error_summary="Adds another manifestation",
            confidence=0.8,
            reason="The new evidence adds a distinct detail.",
        ),
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        candidate_snapshots=(candidate_snapshot,),
        relation=relation,
        evaluated_at=NOW,
    )
    return policy_input, decide_lifecycle(policy_input)


def _applier(
    memory_repository: _ConflictingMemoryRepository,
    event_repository: _EventRepository | None = None,
) -> tuple[LifecycleApplier, _AuditRepository]:
    audit = _AuditRepository()
    return (
        LifecycleApplier(
            _Connection(),  # type: ignore[arg-type]
            memory_repository=memory_repository,
            audit_repository=audit,
            event_repository=event_repository or _EventRepository(),
        ),
        audit,
    )


async def test_competing_replay_recomputes_to_idempotent_no_op() -> None:
    policy_snapshot = _snapshot(row_version=1)
    policy_input, policy_result = _policy(policy_snapshot)
    authoritative = _snapshot(
        memory=_memory(provenance=["stage06_cas_event_001", _event().event_id]),
        row_version=2,
    )
    repository = _ConflictingMemoryRepository((authoritative,))
    applier, audit = _applier(repository)

    result = await applier.apply(
        policy_input,
        policy_result,  # type: ignore[arg-type]
        decision_id="stage06_cas_replay_decision",
        trace_id="stage06_cas_replay_trace",
        applied_at=NOW,
    )

    assert result.apply_state is LifecycleApplyState.IDEMPOTENT
    assert result.decision.decision_id == "stage06_cas_replay_decision:recompute:1"
    assert len(audit.decisions) == 2
    assert repository.cas_calls == 1


async def test_three_conflicts_stop_after_two_recomputations() -> None:
    snapshot = _snapshot()
    policy_input, policy_result = _policy(snapshot)
    repository = _ConflictingMemoryRepository((snapshot,), forced_conflicts=3)
    applier, audit = _applier(repository)

    result = await applier.apply(
        policy_input,
        policy_result,  # type: ignore[arg-type]
        decision_id="stage06_cas_exhausted_decision",
        trace_id="stage06_cas_exhausted_trace",
        applied_at=NOW,
    )

    assert result.apply_state is LifecycleApplyState.FAILED
    assert result.changes[0].error_code == "cas_recompute_exhausted"
    assert repository.cas_calls == 3
    assert list(audit.decisions) == [
        "stage06_cas_exhausted_decision",
        "stage06_cas_exhausted_decision:recompute:1",
        "stage06_cas_exhausted_decision:recompute:2",
    ]
    assert (
        sum(change.apply_state is LifecycleApplyState.STALE for change in audit.changes.values())
        == 3
    )

    replay = await applier.apply(
        policy_input,
        policy_result,  # type: ignore[arg-type]
        decision_id="stage06_cas_exhausted_decision",
        trace_id="stage06_cas_exhausted_trace",
        applied_at=NOW,
    )
    assert replay == result
    assert repository.cas_calls == 3


async def test_recompute_refuses_to_reuse_relation_for_replaced_target() -> None:
    policy_snapshot = _snapshot(row_version=1)
    policy_input, policy_result = _policy(policy_snapshot)
    archived = _snapshot(
        memory=_memory(
            state=LifecycleState.ARCHIVED,
            valid_to=NOW,
            superseded_by="stage06_cas_memory_v2",
        ),
        row_version=2,
    )
    successor = _snapshot(
        memory=_memory(memory_id="stage06_cas_memory_v2", version=2),
        row_version=1,
    )
    repository = _ConflictingMemoryRepository((archived, successor))
    applier, _ = _applier(repository)

    result = await applier.apply(
        policy_input,
        policy_result,  # type: ignore[arg-type]
        decision_id="stage06_relation_stale_decision",
        trace_id="stage06_relation_stale_trace",
        applied_at=NOW,
    )

    assert result.apply_state is LifecycleApplyState.FAILED
    assert result.changes[0].error_code == "relation_reclassification_required"
    assert repository.cas_calls == 1


def _mastery_event(event_id: str, *, answer_correct: bool) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": {
                "user_id": "stage06_mastery_cas_user",
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            "session_id": f"session:{event_id}",
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.7,
            "answer_correct": answer_correct,
            "error_type": None if answer_correct else "concept_confusion",
            "error_detail": None if answer_correct else "reversed conditional direction",
            "occurred_at": NOW - timedelta(minutes=1),
        }
    )


async def test_mastery_recompute_hydrates_new_authoritative_provenance() -> None:
    mastery_scope = MemoryScope(
        user_id="stage06_mastery_cas_user",
        exam_id="postgraduate_entrance_exam",
        subject_id="math_1",
        memory_namespace="mastery",
    )
    mastery_slot = "mastery:math1.probability.bayes"
    old_event = _mastery_event("stage06_mastery_old_event", answer_correct=True)
    competing_event = _mastery_event(
        "stage06_mastery_competing_event",
        answer_correct=True,
    )
    current_event = _mastery_event("stage06_mastery_current_event", answer_correct=False)
    memory = LearningMemory(
        memory_id="stage06_mastery_memory_v1",
        scope=mastery_scope,
        slot_key=mastery_slot,
        value=MasteryValue(level=MasteryLevel.HIGH, score=0.9),
        confidence=0.9,
        evidence_count=2,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        superseded_by=None,
        provenance=[old_event.event_id, competing_event.event_id],
    )
    authoritative = LifecycleMemorySnapshot(
        memory=memory,
        row_version=2,
        policy_version="lifecycle_policy_v1",
    )
    original_snapshot = LifecycleCandidateSnapshot(
        memory=memory.model_copy(
            update={
                "evidence_count": 1,
                "provenance": [old_event.event_id],
            }
        ),
        row_version=1,
        policy_version="lifecycle_policy_v1",
    )
    candidate = MemoryUpdateCandidate(
        event_id=current_event.event_id,
        scope=mastery_scope,
        slot_key=mastery_slot,
        proposed_value=MasteryValue(level=MasteryLevel.LOW, score=0.3),
        evidence={"source": "mastery_cas_recompute_test"},
    )
    policy_input = LifecyclePolicyInput(
        event=current_event,
        candidate=candidate,
        candidate_snapshots=(original_snapshot,),
        relation=ResolvedRelationClassification(
            target_memory_id=memory.memory_id,
            classification=RelationClassifierOutput(
                candidate_display_number=1,
                relation=MemoryRelation.CONTRADICTORY,
                canonical_knowledge_point_id="math1.probability.bayes",
                error_type="concept_confusion",
                error_summary="The answer contradicts the current mastery state",
                confidence=0.9,
                reason="An incorrect conceptual answer supports the lower direction.",
            ),
        ),
        historical_events=(old_event,),
        evaluated_at=NOW,
    )
    event_repository = _EventRepository((competing_event,))
    applier, _ = _applier(
        _ConflictingMemoryRepository((authoritative,)),
        event_repository,
    )

    rebased_input, result = await applier._recompute(policy_input, applied_at=NOW)

    assert event_repository.calls == [(competing_event.event_id,)]
    assert [event.event_id for event in rebased_input.historical_events] == [
        competing_event.event_id,
        old_event.event_id,
    ]
    assert result.expected_row_versions == {memory.memory_id: 2}
