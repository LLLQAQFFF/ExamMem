from __future__ import annotations

from copy import deepcopy
from typing import Any

from pydantic import ValidationError
import pytest

from evaluation.contracts.report import EvaluationReport, MetricObservation

BACKEND_MODES = ["none", "native", "append_only", "vector", "lifecycle"]


def _metric(*, applicable: bool = True) -> dict[str, Any]:
    if applicable:
        return {
            "metric_id": "lifecycle.operation_accuracy",
            "status": "measured",
            "value": 0.75,
            "numerator": 3,
            "denominator": 4,
            "sample_count": 4,
            "reason": None,
        }
    return {
        "metric_id": "lifecycle.operation_accuracy",
        "status": "not_applicable",
        "value": None,
        "numerator": None,
        "denominator": None,
        "sample_count": 0,
        "reason": "backend does not emit lifecycle decisions",
    }


def _backend_result(mode: str, index: int) -> dict[str, Any]:
    return {
        "backend_mode": mode,
        "config_hash": f"{index + 1:x}" * 64,
        "fairness_hash": "b" * 64,
        "run_ids": [f"run_{mode}_001"],
        "outcomes": {
            "total": 1,
            "completed": 1,
            "partial": 0,
            "failed": 0,
            "timeout": 0,
        },
        "metrics": [_metric(applicable=mode != "none")],
        "cost": {
            "llm_call_count": 1,
            "tokens": {
                "prompt_tokens": 80,
                "completion_tokens": 20,
                "total_tokens": 100,
            },
            "estimated_cost_usd": 0.001,
            "estimated_cost_reason": None,
            "latency": {"mean_ms": 10.0, "p95_ms": 12.0, "max_ms": 15.0},
            "memory_growth": {
                "records_before": 0,
                "records_after": 1,
                "record_growth": 1,
                "byte_growth": 256,
            },
        },
    }


@pytest.fixture
def report_payload() -> dict[str, Any]:
    return {
        "report_id": "report_protocol_check_001",
        "protocol_version": "evaluation_protocol_v1",
        "dataset_split": "protocol_check",
        "dataset_hash": "a" * 64,
        "fairness_hash": "b" * 64,
        "seed": 20260806,
        "gold_revision": 1,
        "code_sha": "abcdef1",
        "generated_at": "2026-08-07T10:00:00Z",
        "metric_definitions": [
            {
                "metric_id": "lifecycle.operation_accuracy",
                "layer": "lifecycle",
                "display_name": "Operation Accuracy",
                "formula": "correct lifecycle operations / gold operations",
                "direction": "higher_is_better",
                "target": {
                    "operator": "gte",
                    "threshold": 0.8,
                    "tolerance": 0.0,
                },
            }
        ],
        "backend_results": [
            _backend_result(mode, index) for index, mode in enumerate(BACKEND_MODES)
        ],
        "warnings": [],
    }


@pytest.mark.protocol
@pytest.mark.schema
def test_report_keeps_targets_separate_from_observations(
    report_payload: dict[str, Any],
) -> None:
    report = EvaluationReport.model_validate(report_payload)

    assert report.metric_definitions[0].target is not None
    assert report.metric_definitions[0].target.threshold == 0.8
    assert report.backend_results[-1].metrics[0].value == 0.75


@pytest.mark.protocol
@pytest.mark.schema
def test_not_applicable_metric_requires_a_reason() -> None:
    payload = _metric(applicable=False)
    payload["reason"] = None

    with pytest.raises(ValidationError, match="explain why"):
        MetricObservation.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_report_requires_all_five_backend_modes(
    report_payload: dict[str, Any],
) -> None:
    payload = deepcopy(report_payload)
    payload["backend_results"].pop()

    with pytest.raises(ValidationError, match="all five backend modes"):
        EvaluationReport.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_backend_cannot_silently_omit_a_registered_metric(
    report_payload: dict[str, Any],
) -> None:
    payload = deepcopy(report_payload)
    payload["backend_results"][0]["metrics"] = [
        {
            "metric_id": "unregistered.metric",
            "status": "not_applicable",
            "value": None,
            "sample_count": 0,
            "reason": "not emitted",
        }
    ]

    with pytest.raises(ValidationError, match="every registered metric"):
        EvaluationReport.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_run_outcome_counts_cannot_hide_failures(
    report_payload: dict[str, Any],
) -> None:
    payload = deepcopy(report_payload)
    payload["backend_results"][0]["outcomes"]["failed"] = 1

    with pytest.raises(ValidationError, match="sum to total"):
        EvaluationReport.model_validate(payload)
