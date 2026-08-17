from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
import pytest

from evaluation.contracts.trace import RolloutTrace


def _state(
    *, active: list[str], archived: list[str]
) -> dict[str, list[dict[str, str]] | list[str]]:
    return {
        "active_memory_ids": active,
        "archived_memory_ids": archived,
        "invalidated_memory_ids": [],
        "contested_memory_ids": [],
        "version_relations": [],
    }


@pytest.fixture
def completed_trace_payload() -> dict[str, Any]:
    return {
        "run_id": "run_001",
        "case_id": "mastery_improvement_stale_state_001",
        "trace_id": "trace_001",
        "step_id": "step_001",
        "step_index": 0,
        "backend_mode": "lifecycle",
        "protocol_version": "evaluation_protocol_v1",
        "policy_version": "lifecycle_policy_v1",
        "started_at": "2026-08-07T09:00:00Z",
        "completed_at": "2026-08-07T09:00:00.120Z",
        "input_event": {
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
        },
        "extracted_fields": {
            "knowledge_point_ids": ["linear_algebra.eigenvalue"],
            "answer_correct": True,
            "error_type": None,
        },
        "normalized_slot_key": "mastery:linear_algebra:eigenvalue",
        "candidate_ids": ["memory_mastery_low_v1"],
        "lifecycle_decision": {
            "operation": "SUPERSEDE",
            "target_memory_ids": ["memory_mastery_low_v1"],
            "reason_code": "sustained_correct_evidence",
            "confidence": 0.91,
            "policy_version": "lifecycle_policy_v1",
        },
        "state_before": _state(active=["memory_mastery_low_v1"], archived=[]),
        "state_after": _state(
            active=["memory_mastery_high_v2"],
            archived=["memory_mastery_low_v1"],
        ),
        "retrieval_ids": ["memory_mastery_high_v2"],
        "recommendation": {
            "action_type": "avoid_over_review",
            "knowledge_point_ids": ["linear_algebra.eigenvalue"],
            "difficulty": 0.8,
            "reason_code": "mastery_now_high",
        },
        "llm_calls": [
            {
                "call_id": "llm_call_001",
                "purpose": "extract_learning_event",
                "provider": "openai_compatible",
                "model": "test-model",
                "token_usage": {
                    "prompt_tokens": 80,
                    "completion_tokens": 20,
                    "total_tokens": 100,
                },
                "latency_ms": 75.0,
                "succeeded": True,
                "error": None,
            }
        ],
        "tokens": {
            "prompt_tokens": 80,
            "completion_tokens": 20,
            "total_tokens": 100,
        },
        "latency_ms": 120.0,
        "status": "completed",
        "errors": [],
    }


@pytest.mark.protocol
@pytest.mark.schema
def test_completed_trace_captures_internal_state_not_gold(
    completed_trace_payload: dict[str, Any],
) -> None:
    trace = RolloutTrace.model_validate(completed_trace_payload)
    serialized = trace.model_dump(mode="json")

    assert trace.state_after is not None
    assert trace.state_after.archived_memory_ids == ["memory_mastery_low_v1"]
    assert "gold_operations" not in serialized
    assert "gold_states" not in serialized


@pytest.mark.protocol
@pytest.mark.schema
def test_failed_trace_requires_a_visible_error(
    completed_trace_payload: dict[str, Any],
) -> None:
    payload = deepcopy(completed_trace_payload)
    payload["status"] = "failed"
    payload["state_after"] = None

    with pytest.raises(ValidationError, match="must contain at least one error"):
        RolloutTrace.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_trace_rejects_inconsistent_aggregate_tokens(
    completed_trace_payload: dict[str, Any],
) -> None:
    payload = deepcopy(completed_trace_payload)
    payload["tokens"] = {
        "prompt_tokens": 90,
        "completion_tokens": 20,
        "total_tokens": 110,
    }

    with pytest.raises(ValidationError, match="sum of LLM call usage"):
        RolloutTrace.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_memory_id_cannot_appear_in_two_lifecycle_states(
    completed_trace_payload: dict[str, Any],
) -> None:
    payload = deepcopy(completed_trace_payload)
    payload["state_after"]["active_memory_ids"] = ["memory_mastery_low_v1"]

    with pytest.raises(ValidationError, match="exactly one lifecycle state"):
        RolloutTrace.model_validate(payload)
