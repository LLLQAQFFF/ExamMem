from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import ValidationError
import pytest

from exam_mem.contracts import (
    ErrorType,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import (
    CandidateDisplayRangeError,
    LifecycleCandidateSnapshot,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    LifecyclePolicyV1Config,
    MemoryRelation,
    RelationClassifierOutput,
    resolve_relation_output,
)

pytestmark = pytest.mark.lifecycle

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.probability.bayes"


def _event(
    *,
    event_id: str = "stage06_event_002",
    session_id: str = "stage06_session_002",
    user_id: str = SCOPE.user_id,
    occurred_at: datetime = NOW,
) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": {
                "user_id": user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": session_id,
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.7,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "reversed prior and posterior probabilities",
            "evidence_quality": {
                "confidence": 0.8,
                "is_temporary_exception": False,
                "reasons": ["insufficient_context"],
            },
            "occurred_at": occurred_at,
        }
    )


def _candidate(
    *,
    event_id: str = "stage06_event_002",
    scope: MemoryScope = SCOPE,
    slot_key: str = SLOT_KEY,
) -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate.model_validate(
        {
            "event_id": event_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "proposed_value": {
                "type": "mastery",
                "level": "low",
                "score": 0.3,
            },
            "evidence": {"answer_correct": False},
        }
    )


def _memory(
    *,
    memory_id: str = "stage06_memory_high_v1",
    scope: MemoryScope = SCOPE,
    slot_key: str = SLOT_KEY,
    state: str = "active",
    version: int = 1,
) -> LearningMemory:
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "value": {"type": "mastery", "level": "high", "score": 0.9},
            "confidence": 0.9,
            "evidence_count": 1,
            "lifecycle_state": state,
            "version": version,
            "valid_from": NOW - timedelta(days=10),
            "valid_to": None,
            "superseded_by": None,
            "provenance": ["stage06_event_001"],
        }
    )


def _snapshot(
    *,
    memory_id: str = "stage06_memory_high_v1",
    state: str = "active",
    version: int = 1,
    row_version: int = 1,
    contested_group_id: str | None = None,
    scope: MemoryScope = SCOPE,
    slot_key: str = SLOT_KEY,
) -> LifecycleCandidateSnapshot:
    return LifecycleCandidateSnapshot(
        memory=_memory(
            memory_id=memory_id,
            scope=scope,
            slot_key=slot_key,
            state=state,
            version=version,
        ),
        row_version=row_version,
        contested_group_id=contested_group_id,
        policy_version="lifecycle_policy_v1",
    )


def _relation(display_number: int = 1) -> RelationClassifierOutput:
    return RelationClassifierOutput(
        candidate_display_number=display_number,
        relation=MemoryRelation.CONTRADICTORY,
        canonical_knowledge_point_id="math1.probability.bayes",
        error_type=ErrorType.CONCEPT_CONFUSION,
        error_summary="Confuses prior and posterior probabilities",
        confidence=0.8,
        reason="The new answer reverses the conditional direction.",
    )


def test_relation_output_accepts_only_controlled_strict_fields() -> None:
    output = _relation()

    assert output.candidate_display_number == 1
    assert output.relation is MemoryRelation.CONTRADICTORY
    assert output.error_type is ErrorType.CONCEPT_CONFUSION

    payload = output.model_dump(mode="json")
    payload["memory_id"] = "llm_must_not_choose_this"
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        RelationClassifierOutput.model_validate(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("candidate_display_number", 0, "greater than or equal to 1"),
        ("relation", "similar", "Input should be"),
        ("error_type", "free_form_error", "Input should be"),
        ("confidence", 1.1, "less than or equal to 1"),
        ("reason", "   ", "at least 1 character"),
    ],
)
def test_relation_output_rejects_uncontrolled_values(
    field: str,
    value: object,
    message: str,
) -> None:
    payload = _relation().model_dump(mode="json")
    payload[field] = value

    with pytest.raises(ValidationError, match=message):
        RelationClassifierOutput.model_validate(payload)


def test_relation_display_number_resolves_against_deterministic_candidate_order() -> None:
    v2 = _snapshot(
        memory_id="stage06_memory_contested_v2",
        state="contested",
        version=2,
        contested_group_id="stage06_contested_group",
    )
    v1 = _snapshot()

    resolved = resolve_relation_output(_relation(display_number=2), [v2, v1])

    assert resolved.target_memory_id == "stage06_memory_contested_v2"
    assert resolved.classification == _relation(display_number=2)

    with pytest.raises(CandidateDisplayRangeError, match="outside candidate range 1..2"):
        resolve_relation_output(_relation(display_number=3), [v2, v1])


def test_relation_resolution_rejects_duplicate_candidate_memory_ids() -> None:
    with pytest.raises(ValueError, match="candidate memory IDs must be unique"):
        resolve_relation_output(_relation(), [_snapshot(), _snapshot()])


def test_candidate_snapshot_requires_writable_state_and_contested_group() -> None:
    with pytest.raises(ValidationError, match="active or contested"):
        _snapshot(state="archived")

    with pytest.raises(ValidationError, match="contested memory requires contested_group_id"):
        _snapshot(state="contested")

    with pytest.raises(ValidationError, match="greater than or equal to 1"):
        _snapshot(row_version=0)


def test_general_lifecycle_snapshot_accepts_terminal_state_for_audit() -> None:
    archived = _memory(state="archived").model_copy(
        update={"valid_to": NOW, "superseded_by": "stage06_memory_v2"}
    )
    snapshot = LifecycleMemorySnapshot(
        memory=archived,
        row_version=2,
        policy_version="lifecycle_policy_v1",
    )

    assert snapshot.memory.lifecycle_state.value == "archived"
    assert snapshot.row_version == 2


def test_lifecycle_policy_v1_config_uses_only_documented_defaults() -> None:
    config = LifecyclePolicyV1Config()

    assert config.model_dump(mode="json") == {
        "policy_version": "lifecycle_policy_v1",
        "minimum_directional_event_count": 3,
        "minimum_session_count": 2,
        "minimum_candidate_confidence": 0.7,
        "minimum_support_margin": 0.15,
        "maximum_cas_recomputations": 2,
        "manual_review_after_days": 30,
    }


def test_lifecycle_policy_input_accepts_one_scope_safe_snapshot() -> None:
    snapshots = [_snapshot()]
    relation = resolve_relation_output(_relation(), snapshots)
    history = [_event(event_id="stage06_event_001", session_id="stage06_session_001")]

    policy_input = LifecyclePolicyInput(
        event=_event(),
        candidate=_candidate(),
        candidate_snapshots=snapshots,
        relation=relation,
        historical_events=history,
        evaluated_at=NOW,
    )

    assert policy_input.relation is not None
    assert policy_input.relation.target_memory_id == snapshots[0].memory.memory_id
    assert policy_input.config.policy_version == "lifecycle_policy_v1"


def test_lifecycle_policy_input_rejects_event_candidate_identity_drift() -> None:
    with pytest.raises(ValidationError, match="candidate event_id must match current event"):
        LifecyclePolicyInput(
            event=_event(),
            candidate=_candidate(event_id="different_event"),
            candidate_snapshots=[],
            relation=None,
            historical_events=[],
            evaluated_at=NOW,
        )


def test_lifecycle_policy_input_rejects_cross_scope_or_slot_candidates() -> None:
    other_scope = SCOPE.model_copy(update={"user_id": "other_user"})

    with pytest.raises(ValidationError, match="candidate snapshot must match candidate scope"):
        LifecyclePolicyInput(
            event=_event(),
            candidate=_candidate(),
            candidate_snapshots=[_snapshot(scope=other_scope)],
            relation=None,
            historical_events=[],
            evaluated_at=NOW,
        )

    with pytest.raises(ValidationError, match="candidate snapshot must match slot_key"):
        LifecyclePolicyInput(
            event=_event(),
            candidate=_candidate(),
            candidate_snapshots=[
                _snapshot(slot_key="mastery:math1.probability.conditional_probability")
            ],
            relation=None,
            historical_events=[],
            evaluated_at=NOW,
        )


def test_lifecycle_policy_input_rejects_unresolved_or_foreign_relation_target() -> None:
    resolved = resolve_relation_output(_relation(), [_snapshot()])

    with pytest.raises(ValidationError, match="relation target must be in candidate snapshots"):
        LifecyclePolicyInput(
            event=_event(),
            candidate=_candidate(),
            candidate_snapshots=[],
            relation=resolved,
            historical_events=[],
            evaluated_at=NOW,
        )


@pytest.mark.parametrize(
    "history",
    [
        [_event(event_id="stage06_event_001"), _event(event_id="stage06_event_001")],
        [_event(event_id="stage06_event_001", user_id="other_user")],
        [_event()],
    ],
)
def test_lifecycle_policy_input_rejects_invalid_historical_window(
    history: list[LearningEvent],
) -> None:
    with pytest.raises(ValidationError, match="historical events"):
        LifecyclePolicyInput(
            event=_event(),
            candidate=_candidate(),
            candidate_snapshots=[],
            relation=None,
            historical_events=history,
            evaluated_at=NOW,
        )


def test_policy_result_rejects_cas_versions_for_unknown_targets() -> None:
    decision = LifecycleDecision(
        operation="CONTESTED",
        target_memory_ids=["stage06_memory_high_v1"],
        reason_code="single_concept_error_contests_stable_high",
        confidence=0.8,
        policy_version="lifecycle_policy_v1",
    )

    result = LifecyclePolicyResult(
        event_id="stage06_event_002",
        scope=SCOPE,
        slot_key=SLOT_KEY,
        decision=decision,
        expected_row_versions={"stage06_memory_high_v1": 1},
    )
    assert result.expected_row_versions == {"stage06_memory_high_v1": 1}

    with pytest.raises(ValidationError, match="expected_row_versions contains unknown target"):
        LifecyclePolicyResult(
            event_id="stage06_event_002",
            scope=SCOPE,
            slot_key=SLOT_KEY,
            decision=decision,
            expected_row_versions={"other_memory": 1},
        )
