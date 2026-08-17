from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
import pytest

from evaluation.contracts.rollout import (
    ExperimentConfig,
    FairnessConfig,
    RolloutResult,
)
from evaluation.contracts.trace import RolloutTrace


def _fairness_payload() -> dict[str, Any]:
    return {
        "protocol_version": "evaluation_protocol_v1",
        "dataset_split": "protocol_check",
        "dataset_hash": "a" * 64,
        "seed": 20260806,
        "model": {
            "provider": "openai_compatible",
            "model": "test-model",
            "temperature": 0.0,
            "top_p": 1.0,
            "max_output_tokens": 512,
            "additional_parameters": {},
        },
        "retrieval_top_k": 3,
        "retry": {
            "timeout_seconds": 30.0,
            "max_retries": 3,
            "backoff_seconds": [1.0, 2.0, 4.0],
        },
    }


def _experiment_config(mode: str = "lifecycle") -> ExperimentConfig:
    return ExperimentConfig.model_validate(
        {
            "backend_mode": mode,
            "policy_version": f"{mode}_policy_v1",
            "backend_options": {},
            "fairness": _fairness_payload(),
        }
    )


def _completed_trace() -> RolloutTrace:
    empty_state = {
        "active_memory_ids": [],
        "archived_memory_ids": [],
        "invalidated_memory_ids": [],
        "contested_memory_ids": [],
        "version_relations": [],
    }
    return RolloutTrace.model_validate(
        {
            "run_id": "run_001",
            "case_id": "case_001",
            "trace_id": "trace_001",
            "step_id": "step_001",
            "step_index": 0,
            "backend_mode": "lifecycle",
            "protocol_version": "evaluation_protocol_v1",
            "policy_version": "lifecycle_policy_v1",
            "started_at": "2026-08-07T09:00:00Z",
            "completed_at": "2026-08-07T09:00:00.010Z",
            "input_event": {
                "event_id": "event_001",
                "idempotency_key": "event-001",
                "context": {
                    "user_id": "user_001",
                    "exam_id": "postgraduate_math_1",
                    "subject_id": "linear_algebra",
                },
                "session_id": "session_001",
                "question_id": "question_001",
                "knowledge_point_ids": ["linear_algebra.eigenvalue"],
                "difficulty": 0.5,
                "answer_correct": True,
                "error_type": None,
                "occurred_at": "2026-08-07T09:00:00Z",
            },
            "extracted_fields": None,
            "normalized_slot_key": None,
            "candidate_ids": [],
            "lifecycle_decision": None,
            "state_before": empty_state,
            "state_after": empty_state,
            "retrieval_ids": [],
            "recommendation": None,
            "llm_calls": [],
            "tokens": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "latency_ms": 10.0,
            "status": "completed",
            "errors": [],
        }
    )


@pytest.mark.protocol
@pytest.mark.schema
def test_backend_arms_have_different_config_hash_but_same_fairness_hash() -> None:
    lifecycle = _experiment_config("lifecycle")
    vector = _experiment_config("vector")

    assert lifecycle.canonical_hash() != vector.canonical_hash()
    assert lifecycle.fairness.canonical_hash() == vector.fairness.canonical_hash()
    lifecycle.fairness.assert_fair_with(vector.fairness)


@pytest.mark.protocol
@pytest.mark.schema
def test_fairness_comparison_rejects_different_top_k() -> None:
    baseline = FairnessConfig.model_validate(_fairness_payload())
    changed_payload = deepcopy(_fairness_payload())
    changed_payload["retrieval_top_k"] = 5
    changed = FairnessConfig.model_validate(changed_payload)

    with pytest.raises(ValueError, match="retrieval_top_k"):
        baseline.assert_fair_with(changed)


@pytest.mark.protocol
@pytest.mark.schema
def test_retry_schedule_must_match_retry_count() -> None:
    payload = _fairness_payload()
    payload["retry"]["backoff_seconds"] = [1.0]

    with pytest.raises(ValidationError, match="one delay per retry"):
        FairnessConfig.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_rollout_freezes_hashes_and_trace_aggregates() -> None:
    config = _experiment_config()
    trace = _completed_trace()

    rollout = RolloutResult.model_validate(
        {
            "run_id": "run_001",
            "case_id": "case_001",
            "config": config.model_dump(mode="json"),
            "config_hash": config.canonical_hash(),
            "fairness_hash": config.fairness.canonical_hash(),
            "code_sha": "abcdef1",
            "started_at": "2026-08-07T09:00:00Z",
            "completed_at": "2026-08-07T09:00:00.012Z",
            "initial_snapshot": {},
            "final_snapshot": {},
            "traces": [trace.model_dump(mode="json")],
            "tokens": trace.tokens.model_dump(mode="json"),
            "llm_call_count": 0,
            "latency_ms": 12.0,
            "status": "completed",
            "errors": [],
        }
    )

    assert rollout.config_hash == rollout.config.canonical_hash()
    assert rollout.fairness_hash == rollout.config.fairness.canonical_hash()


@pytest.mark.protocol
@pytest.mark.schema
def test_rollout_rejects_trace_from_another_backend() -> None:
    config = _experiment_config("vector")
    trace = _completed_trace()

    with pytest.raises(ValidationError, match="backend_mode must match"):
        RolloutResult.model_validate(
            {
                "run_id": "run_001",
                "case_id": "case_001",
                "config": config.model_dump(mode="json"),
                "config_hash": config.canonical_hash(),
                "fairness_hash": config.fairness.canonical_hash(),
                "code_sha": "abcdef1",
                "started_at": "2026-08-07T09:00:00Z",
                "completed_at": "2026-08-07T09:00:00.012Z",
                "initial_snapshot": {},
                "final_snapshot": {},
                "traces": [trace.model_dump(mode="json")],
                "tokens": trace.tokens.model_dump(mode="json"),
                "llm_call_count": 0,
                "latency_ms": 12.0,
                "status": "completed",
                "errors": [],
            }
        )
