from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
import pytest

from evaluation.contracts.case import EvaluationCase
from exam_mem.contracts import EvidenceQuality, LearningEvent


@pytest.fixture
def stale_state_case_payload() -> dict[str, Any]:
    """A correct downstream action must not hide a stale active memory."""
    return {
        "protocol_version": "evaluation_protocol_v1",
        "case_id": "mastery_improvement_stale_state_001",
        "scenario_type": "mastery_improvement",
        "initial_memory": [
            {
                "memory_id": "memory_mastery_low_v1",
                "scope": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "linear_algebra",
                    "memory_namespace": "mastery",
                },
                "slot_key": "mastery:linear_algebra:eigenvalue",
                "value": {
                    "type": "mastery",
                    "level": "low",
                    "score": 0.35,
                },
                "confidence": 0.82,
                "evidence_count": 4,
                "lifecycle_state": "active",
                "version": 1,
                "valid_from": "2026-08-01T08:00:00Z",
                "valid_to": None,
                "superseded_by": None,
                "provenance": ["event_before_case_001"],
            }
        ],
        "events": [
            {
                "event_id": "event_001",
                "idempotency_key": "mastery-improvement-001",
                "context": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "linear_algebra",
                },
                "session_id": "session_002",
                "question_id": "question_eigenvalue_007",
                "knowledge_point_ids": ["linear_algebra.eigenvalue"],
                "difficulty": 0.7,
                "answer_correct": True,
                "error_type": None,
                "occurred_at": "2026-08-07T09:00:00Z",
            }
        ],
        "gold_operations": [
            {
                "operation_id": "step_001:mastery",
                "step_id": "step_001",
                "event_id": "event_001",
                "extracted_fields": {
                    "knowledge_point_ids": ["linear_algebra.eigenvalue"],
                    "answer_correct": True,
                    "error_type": None,
                },
                "canonical_knowledge_point_ids": ["linear_algebra.eigenvalue"],
                "slot_key": "mastery:linear_algebra:eigenvalue",
                "candidate_memory_ids": ["memory_mastery_low_v1"],
                "operation": "SUPERSEDE",
                "target_memory_ids": ["memory_mastery_low_v1"],
                "result_memory_id": "memory_mastery_high_v2",
                "reason_code": "sustained_correct_evidence",
                "evidence_event_ids": ["event_001"],
            }
        ],
        "gold_states": [
            {
                "step_id": "step_001",
                "active_memory_ids": ["memory_mastery_high_v2"],
                "archived_memory_ids": ["memory_mastery_low_v1"],
                "invalidated_memory_ids": [],
                "contested_memory_ids": [],
                "version_relations": [
                    {
                        "predecessor_memory_id": "memory_mastery_low_v1",
                        "successor_memory_id": "memory_mastery_high_v2",
                        "relation": "superseded_by",
                    }
                ],
            }
        ],
        "queries": [
            {
                "query_id": "query_001",
                "after_step_id": "step_001",
                "scope": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "linear_algebra",
                    "memory_namespace": "mastery",
                },
                "text": "What should the learner review next?",
                "top_k": 3,
            }
        ],
        "gold_actions": [
            {
                "step_id": "step_001",
                "action_type": "avoid_over_review",
                "knowledge_point_ids": ["linear_algebra.eigenvalue"],
                "reason_code": "mastery_now_high",
            }
        ],
        "metadata": {
            "split": "protocol_check",
            "seed": 20260806,
            "gold_revision": 1,
        },
    }


@pytest.mark.protocol
@pytest.mark.schema
def test_complete_case_captures_operation_state_and_downstream_action(
    stale_state_case_payload: dict[str, Any],
) -> None:
    case = EvaluationCase.model_validate(stale_state_case_payload)

    assert case.gold_operations[0].operation == "SUPERSEDE"
    assert case.gold_states[0].active_memory_ids == ["memory_mastery_high_v2"]
    assert case.gold_actions[0].action_type == "avoid_over_review"


@pytest.mark.protocol
@pytest.mark.schema
def test_case_rejects_missing_active_state_even_when_action_is_correct(
    stale_state_case_payload: dict[str, Any],
) -> None:
    incomplete_payload = deepcopy(stale_state_case_payload)
    del incomplete_payload["gold_states"][0]["active_memory_ids"]

    with pytest.raises(ValidationError) as exc_info:
        EvaluationCase.model_validate(incomplete_payload)

    error_locations = {error["loc"] for error in exc_info.value.errors()}
    assert ("gold_states", 0, "active_memory_ids") in error_locations


@pytest.mark.protocol
@pytest.mark.schema
def test_case_rejects_memory_in_multiple_lifecycle_states(
    stale_state_case_payload: dict[str, Any],
) -> None:
    invalid_payload = deepcopy(stale_state_case_payload)
    invalid_payload["gold_states"][0]["contested_memory_ids"] = ["memory_mastery_high_v2"]

    with pytest.raises(ValidationError, match="exactly one lifecycle state"):
        EvaluationCase.model_validate(invalid_payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_one_event_step_can_have_multiple_namespace_operations(
    stale_state_case_payload: dict[str, Any],
) -> None:
    payload = deepcopy(stale_state_case_payload)
    error_pattern_operation = deepcopy(payload["gold_operations"][0])
    error_pattern_operation.update(
        {
            "operation_id": "step_001:error_pattern",
            "slot_key": "error_pattern:linear_algebra:eigenvalue:concept_confusion",
            "candidate_memory_ids": [],
            "operation": "NO_OP",
            "target_memory_ids": [],
            "result_memory_id": None,
            "reason_code": "no_stable_error_pattern_from_correct_answer",
        }
    )
    payload["gold_operations"].append(error_pattern_operation)

    case = EvaluationCase.model_validate(payload)

    assert [operation.operation_id for operation in case.gold_operations] == [
        "step_001:mastery",
        "step_001:error_pattern",
    ]


@pytest.mark.protocol
@pytest.mark.schema
def test_explicit_correction_is_a_typed_replayable_event() -> None:
    event = LearningEvent.model_validate(
        {
            "event_id": "correction_001",
            "idempotency_key": "correction-001",
            "event_type": "explicit_correction",
            "context": {
                "user_id": "user_001",
                "exam_id": "postgraduate_math_1",
                "subject_id": "probability_theory",
            },
            "session_id": "session_040",
            "knowledge_point_ids": ["probability.bayes_formula"],
            "correction": {
                "target_memory_ids": ["error_bayes_v1"],
                "source": "teacher",
                "statement": "该错误诊断来自评分解析错误",
            },
            "occurred_at": "2026-08-10T09:00:00Z",
        }
    )

    assert event.event_type == "explicit_correction"
    assert event.correction is not None
    assert event.correction.target_memory_ids == ["error_bayes_v1"]


@pytest.mark.protocol
@pytest.mark.schema
def test_explicit_correction_rejects_missing_correction_payload() -> None:
    with pytest.raises(ValidationError, match="requires correction"):
        LearningEvent.model_validate(
            {
                "event_id": "correction_001",
                "idempotency_key": "correction-001",
                "event_type": "explicit_correction",
                "context": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "probability_theory",
                },
                "session_id": "session_040",
                "knowledge_point_ids": ["probability.bayes_formula"],
                "occurred_at": "2026-08-10T09:00:00Z",
            }
        )


@pytest.mark.protocol
@pytest.mark.schema
def test_temporary_evidence_quality_is_machine_readable() -> None:
    quality = EvidenceQuality.model_validate(
        {
            "confidence": 0.45,
            "is_temporary_exception": True,
            "reasons": ["low_grader_confidence", "external_disruption"],
        }
    )

    assert quality.confidence == 0.45
    assert quality.is_temporary_exception is True
    assert [reason.value for reason in quality.reasons] == [
        "low_grader_confidence",
        "external_disruption",
    ]


@pytest.mark.protocol
@pytest.mark.schema
def test_non_default_evidence_quality_requires_a_reason() -> None:
    with pytest.raises(ValidationError, match="requires at least one reason"):
        EvidenceQuality.model_validate(
            {
                "confidence": 0.45,
                "is_temporary_exception": False,
                "reasons": [],
            }
        )


@pytest.mark.protocol
@pytest.mark.schema
def test_plan_transition_is_a_typed_non_answer_event() -> None:
    event = LearningEvent.model_validate(
        {
            "event_id": "plan_transition_001",
            "idempotency_key": "plan-transition-001",
            "event_type": "plan_transition",
            "context": {
                "user_id": "user_001",
                "exam_id": "postgraduate_math_1",
                "subject_id": "probability_theory",
            },
            "session_id": "session_060",
            "knowledge_point_ids": ["probability.conditional_probability"],
            "plan_transition": {
                "target_memory_id": "plan_probability_v1",
                "to_status": "completed",
                "source": "practice_progress",
                "reason": "deterministic progress reached the plan goal",
            },
            "occurred_at": "2026-08-10T11:00:00Z",
        }
    )

    assert event.event_type == "plan_transition"
    assert event.plan_transition is not None
    assert event.plan_transition.to_status == "completed"
    assert event.question_id is None


@pytest.mark.protocol
@pytest.mark.schema
def test_plan_transition_rejects_answer_payload() -> None:
    with pytest.raises(ValidationError, match="must not contain answer-attempt fields"):
        LearningEvent.model_validate(
            {
                "event_id": "plan_transition_002",
                "idempotency_key": "plan-transition-002",
                "event_type": "plan_transition",
                "context": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "probability_theory",
                },
                "session_id": "session_061",
                "question_id": "must_not_exist",
                "knowledge_point_ids": ["probability.core_review"],
                "difficulty": 0.5,
                "answer_correct": True,
                "plan_transition": {
                    "target_memory_id": "plan_probability_v1",
                    "to_status": "cancelled",
                    "source": "user",
                    "reason": "cancel this plan",
                },
                "occurred_at": "2026-08-10T11:30:00Z",
            }
        )
