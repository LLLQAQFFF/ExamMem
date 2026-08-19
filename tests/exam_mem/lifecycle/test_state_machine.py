from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exam_mem.contracts import (
    EvidenceQualityReason,
    LearningEvent,
    LearningMemory,
    LifecycleOperation,
    MemoryScope,
    MemoryUpdateCandidate,
    PlanStatus,
    PlanTransitionSource,
)
from exam_mem.lifecycle import (
    LifecycleCandidateSnapshot,
    LifecyclePolicyInput,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    decide_lifecycle,
)

pytestmark = pytest.mark.lifecycle

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
CONTEXT = {
    "user_id": "stage06_user",
    "exam_id": "postgraduate_entrance_exam",
    "subject_id": "math_1",
}
ERROR_SCOPE = MemoryScope(**CONTEXT, memory_namespace="error_pattern")
ERROR_SLOT = "error_pattern:math1.probability.bayes:concept_confusion"
PLAN_SCOPE = MemoryScope(**CONTEXT, memory_namespace="plan")
PLAN_SLOT = "plan:postgraduate_entrance_exam:math_1"


def _answer_event(
    *,
    event_id: str = "stage06_state_event_002",
    idempotency_key: str | None = None,
    error_type: str = "concept_confusion",
    confidence: float = 1.0,
    is_temporary_exception: bool = False,
) -> LearningEvent:
    reasons = []
    if is_temporary_exception:
        reasons.append(EvidenceQualityReason.EXTERNAL_DISRUPTION)
    elif confidence < 1.0:
        reasons.append(EvidenceQualityReason.AMBIGUOUS_RESPONSE)
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": idempotency_key or f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": CONTEXT,
            "session_id": "stage06_state_session_002",
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": error_type,
            "error_detail": "reverses the conditional direction",
            "evidence_quality": {
                "confidence": confidence,
                "is_temporary_exception": is_temporary_exception,
                "reasons": reasons,
            },
            "occurred_at": NOW,
        }
    )


def _correction_event(
    *,
    target_ids: list[str] | None = None,
    confidence: float = 1.0,
) -> LearningEvent:
    reasons = [EvidenceQualityReason.AMBIGUOUS_RESPONSE] if confidence < 1.0 else []
    return LearningEvent.model_validate(
        {
            "event_id": "stage06_correction_event_002",
            "idempotency_key": "idem:stage06_correction_event_002",
            "event_type": "explicit_correction",
            "context": CONTEXT,
            "session_id": "stage06_state_session_002",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "evidence_quality": {
                "confidence": confidence,
                "is_temporary_exception": False,
                "reasons": reasons,
            },
            "correction": {
                "target_memory_ids": target_ids or ["stage06_error_memory_v1"],
                "source": "grader_audit",
                "statement": "The stored diagnosis is incorrect.",
            },
            "occurred_at": NOW,
        }
    )


def _plan_event(
    *,
    status: PlanStatus,
    source: PlanTransitionSource,
    confidence: float = 1.0,
    target_id: str = "stage06_plan_memory_v1",
) -> LearningEvent:
    reasons = [EvidenceQualityReason.AMBIGUOUS_RESPONSE] if confidence < 1.0 else []
    return LearningEvent.model_validate(
        {
            "event_id": f"stage06_plan_{status.value}_{source.value}",
            "idempotency_key": f"idem:plan:{status.value}:{source.value}",
            "event_type": "plan_transition",
            "context": CONTEXT,
            "session_id": "stage06_state_session_002",
            "evidence_quality": {
                "confidence": confidence,
                "is_temporary_exception": False,
                "reasons": reasons,
            },
            "plan_transition": {
                "target_memory_id": target_id,
                "to_status": status,
                "source": source,
                "reason": f"controlled {status.value} transition",
            },
            "occurred_at": NOW,
        }
    )


def _error_value(*, replacement: bool = False) -> dict:
    return {
        "type": "error_pattern",
        "error_type": "concept_confusion",
        "summary": (
            "Confuses conditional direction only under time pressure"
            if replacement
            else "Confuses conditional direction"
        ),
        "details": ["reverses prior and posterior"],
    }


def _plan_value(
    *,
    status: PlanStatus = PlanStatus.IN_PROGRESS,
    progress: float = 0.2,
    goal: str = "Complete probability review",
    due_at: datetime | None = None,
) -> dict:
    return {
        "type": "plan",
        "goal": goal,
        "status": status,
        "progress": progress,
        "due_at": due_at,
    }


def _snapshot(
    *,
    memory_id: str = "stage06_error_memory_v1",
    scope: MemoryScope = ERROR_SCOPE,
    slot_key: str = ERROR_SLOT,
    value: dict | None = None,
    provenance: list[str] | None = None,
    row_version: int = 4,
) -> LifecycleCandidateSnapshot:
    memory = LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "value": value or _error_value(),
            "confidence": 0.9,
            "evidence_count": len(provenance or ["stage06_state_event_001"]),
            "lifecycle_state": "active",
            "version": 1,
            "valid_from": NOW - timedelta(days=10),
            "valid_to": None,
            "superseded_by": None,
            "provenance": provenance or ["stage06_state_event_001"],
        }
    )
    return LifecycleCandidateSnapshot(
        memory=memory,
        row_version=row_version,
        policy_version="lifecycle_policy_v1",
    )


def _candidate(
    event: LearningEvent,
    *,
    scope: MemoryScope = ERROR_SCOPE,
    slot_key: str = ERROR_SLOT,
    value: dict | None = None,
) -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "proposed_value": value or _error_value(),
            "evidence": {"source_event": event.event_id},
        }
    )


def _relation(
    *,
    relation: MemoryRelation,
    target_id: str = "stage06_error_memory_v1",
    confidence: float = 0.9,
) -> ResolvedRelationClassification:
    return ResolvedRelationClassification(
        target_memory_id=target_id,
        classification=RelationClassifierOutput(
            candidate_display_number=1,
            relation=relation,
            canonical_knowledge_point_id="math1.probability.bayes",
            error_type="concept_confusion",
            error_summary="Controlled state-machine classification",
            confidence=confidence,
            reason="Controlled relation for deterministic policy test.",
        ),
    )


def _policy_input(
    event: LearningEvent,
    *,
    candidate: MemoryUpdateCandidate | None = None,
    snapshots: tuple[LifecycleCandidateSnapshot, ...] = (),
    relation: ResolvedRelationClassification | None = None,
    history: tuple[LearningEvent, ...] = (),
) -> LifecyclePolicyInput:
    return LifecyclePolicyInput(
        event=event,
        candidate=candidate or _candidate(event),
        candidate_snapshots=snapshots,
        relation=relation,
        historical_events=history,
        evaluated_at=NOW,
    )


def _plan_snapshot() -> LifecycleCandidateSnapshot:
    return _snapshot(
        memory_id="stage06_plan_memory_v1",
        scope=PLAN_SCOPE,
        slot_key=PLAN_SLOT,
        value=_plan_value(),
        row_version=7,
    )


def _plan_candidate(event: LearningEvent, **value_changes: object) -> MemoryUpdateCandidate:
    value = _plan_value()
    value.update(value_changes)
    return _candidate(event, scope=PLAN_SCOPE, slot_key=PLAN_SLOT, value=value)


def test_s01_new_slot_adds_without_target_or_expected_version() -> None:
    event = _answer_event()

    result = decide_lifecycle(_policy_input(event))

    assert result.decision.operation is LifecycleOperation.ADD
    assert result.decision.target_memory_ids == []
    assert result.expected_row_versions == {}


@pytest.mark.parametrize(
    ("event", "candidate", "reason_code"),
    [
        (
            _answer_event(is_temporary_exception=True),
            None,
            "temporary_exception_no_change",
        ),
        (
            _answer_event(confidence=0.32),
            None,
            "isolated_low_confidence_no_change",
        ),
        (
            _answer_event(error_type="careless_error"),
            None,
            "isolated_careless_error_no_pattern",
        ),
    ],
)
def test_s05_low_quality_new_slot_remains_l1_only(
    event: LearningEvent,
    candidate: MemoryUpdateCandidate | None,
    reason_code: str,
) -> None:
    result = decide_lifecycle(_policy_input(event, candidate=candidate))

    assert result.decision.operation is LifecycleOperation.NO_OP
    assert result.decision.reason_code == reason_code
    assert result.decision.target_memory_ids == []
    assert result.expected_row_versions == {}


def test_careless_error_does_not_change_mastery() -> None:
    event = _answer_event(error_type="careless_error")
    mastery_scope = ERROR_SCOPE.model_copy(update={"memory_namespace": "mastery"})
    mastery_slot = "mastery:math1.probability.bayes"
    candidate = _candidate(
        event,
        scope=mastery_scope,
        slot_key=mastery_slot,
        value={"type": "mastery", "level": "low", "score": 0.0},
    )
    current = _snapshot(
        scope=mastery_scope,
        slot_key=mastery_slot,
        value={"type": "mastery", "level": "high", "score": 1.0},
    )

    result = decide_lifecycle(_policy_input(event, candidate=candidate, snapshots=(current,)))

    assert result.decision.operation is LifecycleOperation.NO_OP
    assert result.decision.reason_code == "careless_error_does_not_change_mastery"


def test_s02_same_event_replay_wins_before_aggressive_relation() -> None:
    event = _answer_event()
    snapshot = _snapshot(provenance=[event.event_id])

    result = decide_lifecycle(
        _policy_input(
            event,
            snapshots=(snapshot,),
            relation=_relation(relation=MemoryRelation.CONTRADICTORY),
        )
    )

    assert result.decision.operation is LifecycleOperation.NO_OP
    assert result.decision.reason_code == "already_applied_replay"
    assert result.expected_row_versions == {}


def test_s02_same_idempotency_key_replay_uses_authoritative_history() -> None:
    original = _answer_event(event_id="stage06_original", idempotency_key="same-key")
    replay = _answer_event(event_id="stage06_replay", idempotency_key="same-key")
    snapshot = _snapshot(provenance=[original.event_id])

    result = decide_lifecycle(_policy_input(replay, snapshots=(snapshot,), history=(original,)))

    assert result.decision.operation is LifecycleOperation.NO_OP


def test_s03_independent_same_direction_mastery_evidence_merges() -> None:
    event = _answer_event()
    scope = ERROR_SCOPE.model_copy(update={"memory_namespace": "mastery"})
    slot_key = "mastery:math1.probability.bayes"
    candidate = _candidate(
        event,
        scope=scope,
        slot_key=slot_key,
        value={"type": "mastery", "level": "low", "score": 0.0},
    )
    snapshot = _snapshot(
        memory_id="stage06_mastery_low_v1",
        scope=scope,
        slot_key=slot_key,
        value={"type": "mastery", "level": "low", "score": 0.0},
    )
    relation = _relation(
        relation=MemoryRelation.DUPLICATE,
        target_id=snapshot.memory.memory_id,
    )

    result = decide_lifecycle(
        _policy_input(
            event,
            candidate=candidate,
            snapshots=(snapshot,),
            relation=relation,
        )
    )

    assert result.decision.operation is LifecycleOperation.MERGE
    assert result.decision.reason_code == "independent_duplicate_evidence"
    assert result.expected_row_versions == {snapshot.memory.memory_id: snapshot.row_version}


@pytest.mark.parametrize(
    ("relation", "reason_code"),
    [
        (MemoryRelation.DUPLICATE, "independent_duplicate_evidence"),
        (MemoryRelation.COMPLEMENTARY, "complementary_error_detail"),
    ],
)
def test_s03_s04_independent_error_evidence_merges(
    relation: MemoryRelation,
    reason_code: str,
) -> None:
    event = _answer_event()
    snapshot = _snapshot()

    result = decide_lifecycle(
        _policy_input(
            event,
            snapshots=(snapshot,),
            relation=_relation(relation=relation),
        )
    )

    assert result.decision.operation is LifecycleOperation.MERGE
    assert result.decision.reason_code == reason_code
    assert result.expected_row_versions == {snapshot.memory.memory_id: 4}


def test_error_pattern_rejects_relation_outside_s03_s04() -> None:
    event = _answer_event()
    snapshot = _snapshot()

    with pytest.raises(ValueError, match="duplicate or complementary"):
        decide_lifecycle(
            _policy_input(
                event,
                snapshots=(snapshot,),
                relation=_relation(relation=MemoryRelation.UNRELATED),
            )
        )


@pytest.mark.parametrize(
    ("replacement", "confidence", "operation", "reason_code"),
    [
        (False, 1.0, LifecycleOperation.INVALIDATE, "correction_invalidates_false_memory"),
        (True, 1.0, LifecycleOperation.SUPERSEDE, "correction_supplies_replacement"),
        (True, 0.6, LifecycleOperation.CONTESTED, "uncertain_explicit_correction"),
    ],
)
def test_s10_s11_explicit_correction_branches(
    replacement: bool,
    confidence: float,
    operation: LifecycleOperation,
    reason_code: str,
) -> None:
    event = _correction_event(confidence=confidence)
    snapshot = _snapshot()
    candidate = _candidate(event, value=_error_value(replacement=replacement))

    result = decide_lifecycle(
        _policy_input(
            event,
            candidate=candidate,
            snapshots=(snapshot,),
            relation=_relation(
                relation=MemoryRelation.CONTRADICTORY,
                confidence=confidence,
            ),
        )
    )

    assert result.decision.operation is operation
    assert result.decision.reason_code == reason_code
    assert result.expected_row_versions == {snapshot.memory.memory_id: 4}


def test_explicit_correction_rejects_ambiguous_or_missing_target() -> None:
    ambiguous = _correction_event(target_ids=["stage06_error_memory_v1", "stage06_error_memory_v2"])
    snapshot = _snapshot()
    with pytest.raises(ValueError, match="exactly one target"):
        decide_lifecycle(
            _policy_input(
                ambiguous,
                snapshots=(snapshot,),
                relation=_relation(relation=MemoryRelation.CONTRADICTORY),
            )
        )

    missing = _correction_event(target_ids=["missing_memory"])
    with pytest.raises(ValueError, match="not an authoritative candidate"):
        decide_lifecycle(
            _policy_input(
                missing,
                snapshots=(snapshot,),
                relation=_relation(relation=MemoryRelation.CONTRADICTORY),
            )
        )


def test_s12_new_plan_without_active_memory_adds() -> None:
    event = _answer_event(event_id="stage06_new_plan")
    candidate = _plan_candidate(event, status=PlanStatus.PLANNED, progress=0.0)

    result = decide_lifecycle(_policy_input(event, candidate=candidate))

    assert result.decision.operation is LifecycleOperation.ADD


@pytest.mark.parametrize(
    ("value_changes", "operation"),
    [
        ({"progress": 0.6}, LifecycleOperation.MERGE),
        ({"goal": "Replace probability review goal"}, LifecycleOperation.SUPERSEDE),
        ({"due_at": NOW + timedelta(days=14)}, LifecycleOperation.SUPERSEDE),
    ],
)
def test_s12_active_plan_merge_or_supersede(
    value_changes: dict[str, object],
    operation: LifecycleOperation,
) -> None:
    event = _plan_event(
        status=PlanStatus.IN_PROGRESS,
        source=PlanTransitionSource.PRACTICE_PROGRESS,
    )
    snapshot = _plan_snapshot()

    result = decide_lifecycle(
        _policy_input(
            event,
            candidate=_plan_candidate(event, **value_changes),
            snapshots=(snapshot,),
        )
    )

    assert result.decision.operation is operation
    assert result.expected_row_versions == {snapshot.memory.memory_id: 7}


def test_s12_ambiguous_user_cancellation_is_contested() -> None:
    event = _plan_event(
        status=PlanStatus.CANCELLED,
        source=PlanTransitionSource.USER,
        confidence=0.6,
    )

    result = decide_lifecycle(
        _policy_input(
            event,
            candidate=_plan_candidate(event, status=PlanStatus.CANCELLED),
            snapshots=(_plan_snapshot(),),
        )
    )

    assert result.decision.operation is LifecycleOperation.CONTESTED
    assert result.decision.reason_code == "ambiguous_user_plan_cancellation"


@pytest.mark.parametrize(
    ("status", "source"),
    [
        (PlanStatus.COMPLETED, PlanTransitionSource.PRACTICE_PROGRESS),
        (PlanStatus.CANCELLED, PlanTransitionSource.USER),
        (PlanStatus.EXPIRED, PlanTransitionSource.SYSTEM),
    ],
)
def test_s12_controlled_terminal_paths_invalidate(
    status: PlanStatus,
    source: PlanTransitionSource,
) -> None:
    event = _plan_event(status=status, source=source)

    result = decide_lifecycle(
        _policy_input(
            event,
            candidate=_plan_candidate(event, status=status),
            snapshots=(_plan_snapshot(),),
        )
    )

    assert result.decision.operation is LifecycleOperation.INVALIDATE
    assert result.decision.reason_code == f"plan_{status.value}"


def test_s12_rejects_wrong_terminal_source_and_missing_target() -> None:
    wrong_source = _plan_event(
        status=PlanStatus.COMPLETED,
        source=PlanTransitionSource.USER,
    )
    with pytest.raises(ValueError, match="does not match transition source"):
        decide_lifecycle(
            _policy_input(
                wrong_source,
                candidate=_plan_candidate(wrong_source, status=PlanStatus.COMPLETED),
                snapshots=(_plan_snapshot(),),
            )
        )

    missing_target = _plan_event(
        status=PlanStatus.IN_PROGRESS,
        source=PlanTransitionSource.PRACTICE_PROGRESS,
        target_id="missing_plan",
    )
    with pytest.raises(ValueError, match="not an authoritative candidate"):
        decide_lifecycle(
            _policy_input(
                missing_target,
                candidate=_plan_candidate(missing_target),
                snapshots=(_plan_snapshot(),),
            )
        )
