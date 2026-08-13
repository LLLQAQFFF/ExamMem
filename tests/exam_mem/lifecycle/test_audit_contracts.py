from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from exam_mem.contracts import LearningEvent, LearningMemory, MemoryScope, MemoryUpdateCandidate
from exam_mem.lifecycle import (
    LifecycleApplyState,
    LifecycleAuditTrail,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    decide_lifecycle,
)

pytestmark = pytest.mark.lifecycle

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_audit_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


def _policy_input() -> LifecyclePolicyInput:
    event = LearningEvent.model_validate(
        {
            "event_id": "stage06_audit_event_001",
            "idempotency_key": "idem:stage06_audit_event_001",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_audit_session",
            "question_id": "stage06_audit_question",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "reversed conditional direction",
            "occurred_at": NOW,
        }
    )
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses conditional direction",
                "details": ["reverses prior and posterior"],
            },
            "evidence": {"source": "controlled_audit_test"},
        }
    )
    return LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        evaluated_at=NOW,
    )


def _decision_record() -> LifecycleDecisionAuditRecord:
    policy_input = _policy_input()
    return LifecycleDecisionAuditRecord(
        decision_id="stage06_decision_001",
        trace_id="stage06_trace_001",
        policy_input=policy_input,
        policy_result=decide_lifecycle(policy_input),
        created_at=NOW,
    )


def _memory() -> LearningMemory:
    return LearningMemory.model_validate(
        {
            "memory_id": "stage06_audit_memory_v1",
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses conditional direction",
                "details": ["reverses prior and posterior"],
            },
            "confidence": 0.8,
            "evidence_count": 1,
            "lifecycle_state": "active",
            "version": 1,
            "valid_from": NOW,
            "valid_to": None,
            "superseded_by": None,
            "provenance": ["stage06_audit_event_001"],
        }
    )


def _snapshot() -> LifecycleMemorySnapshot:
    return LifecycleMemorySnapshot(
        memory=_memory(),
        row_version=1,
        policy_version="lifecycle_policy_v1",
    )


def test_decision_audit_record_keeps_reproducible_input_and_result() -> None:
    record = _decision_record()

    assert record.policy_result.event_id == record.policy_input.event.event_id
    assert record.policy_result.decision.policy_version == "lifecycle_policy_v1"
    assert record.policy_result.decision.operation.value == "ADD"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("event_id", "other_event", "event identity must match"),
        ("slot_key", "error_pattern:math1.calculus.limit:concept_confusion", "slot_key must match"),
    ],
)
def test_decision_audit_rejects_identity_drift(
    field: str,
    value: str,
    message: str,
) -> None:
    valid = _decision_record()
    changed_result = valid.policy_result.model_copy(update={field: value})

    with pytest.raises(ValidationError, match=message):
        LifecycleDecisionAuditRecord(
            decision_id=valid.decision_id,
            trace_id=valid.trace_id,
            policy_input=valid.policy_input,
            policy_result=changed_result,
            created_at=valid.created_at,
        )


def test_decision_audit_rejects_target_outside_authoritative_snapshots() -> None:
    valid = _decision_record()
    changed_decision = valid.policy_result.decision.model_copy(
        update={"target_memory_ids": ["arbitrary_memory"]}
    )
    changed_result = valid.policy_result.model_copy(update={"decision": changed_decision})

    with pytest.raises(ValidationError, match="must come from candidate snapshots"):
        LifecycleDecisionAuditRecord(
            decision_id=valid.decision_id,
            trace_id=valid.trace_id,
            policy_input=valid.policy_input,
            policy_result=changed_result,
            created_at=valid.created_at,
        )


@pytest.mark.parametrize(
    "record",
    [
        LifecycleChangeAuditRecord(
            change_id="planned_change",
            decision_id="stage06_decision_001",
            trace_id="stage06_trace_001",
            apply_state=LifecycleApplyState.PLANNED,
            recorded_at=NOW,
        ),
        LifecycleChangeAuditRecord(
            change_id="stale_change",
            decision_id="stage06_decision_001",
            trace_id="stage06_trace_001",
            apply_state=LifecycleApplyState.STALE,
            memory_id="stage06_audit_memory_v1",
            expected_row_version=1,
            actual_row_version=2,
            recorded_at=NOW,
        ),
        LifecycleChangeAuditRecord(
            change_id="failed_change",
            decision_id="stage06_decision_001",
            trace_id="stage06_trace_001",
            apply_state=LifecycleApplyState.FAILED,
            error_code="transaction_failure",
            recorded_at=NOW,
        ),
    ],
)
def test_non_success_change_states_are_strict_and_auditable(
    record: LifecycleChangeAuditRecord,
) -> None:
    assert record.recorded_at == NOW


def test_applied_and_idempotent_snapshots_have_controlled_shapes() -> None:
    snapshot = _snapshot()
    applied = LifecycleChangeAuditRecord(
        change_id="applied_change",
        decision_id="stage06_decision_001",
        trace_id="stage06_trace_001",
        apply_state=LifecycleApplyState.APPLIED,
        memory_id=snapshot.memory.memory_id,
        after_state=snapshot,
        recorded_at=NOW,
    )
    idempotent = LifecycleChangeAuditRecord(
        change_id="idempotent_change",
        decision_id="stage06_decision_001",
        trace_id="stage06_trace_001",
        apply_state=LifecycleApplyState.IDEMPOTENT,
        memory_id=snapshot.memory.memory_id,
        before_state=snapshot,
        after_state=snapshot,
        recorded_at=NOW,
    )

    assert applied.after_state == snapshot
    assert idempotent.before_state == idempotent.after_state


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (
            {"apply_state": "STALE", "expected_row_version": 1},
            "expected and actual row versions",
        ),
        (
            {"apply_state": "FAILED"},
            "failed change requires error_code",
        ),
        (
            {"apply_state": "PLANNED", "error_code": "not_allowed"},
            "allowed only for failed",
        ),
        (
            {"apply_state": "APPLIED", "memory_id": "missing_after"},
            "requires memory_id and after_state",
        ),
    ],
)
def test_change_audit_rejects_incomplete_apply_observations(
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValidationError, match=message):
        LifecycleChangeAuditRecord.model_validate(
            {
                "change_id": "invalid_change",
                "decision_id": "stage06_decision_001",
                "trace_id": "stage06_trace_001",
                "recorded_at": NOW,
                **payload,
            }
        )


def test_audit_trail_rejects_change_from_another_decision_or_trace() -> None:
    decision = _decision_record()
    valid_change = LifecycleChangeAuditRecord(
        change_id="planned_change",
        decision_id=decision.decision_id,
        trace_id=decision.trace_id,
        apply_state=LifecycleApplyState.PLANNED,
        recorded_at=NOW,
    )
    trail = LifecycleAuditTrail(
        trace_id=decision.trace_id,
        decisions=(decision,),
        changes=(valid_change,),
    )
    assert trail.changes == (valid_change,)

    foreign_change = valid_change.model_copy(update={"decision_id": "another_decision"})
    with pytest.raises(ValidationError, match="must reference a returned decision"):
        LifecycleAuditTrail(
            trace_id=decision.trace_id,
            decisions=(decision,),
            changes=(foreign_change,),
        )


def test_change_snapshot_rejects_wrong_memory_identity() -> None:
    snapshot = _snapshot()
    with pytest.raises(ValidationError, match="snapshot memory_id must match"):
        LifecycleChangeAuditRecord(
            change_id="wrong_memory_change",
            decision_id="stage06_decision_001",
            trace_id="stage06_trace_001",
            apply_state=LifecycleApplyState.APPLIED,
            memory_id="another_memory",
            after_state=snapshot,
            recorded_at=NOW + timedelta(seconds=1),
        )
