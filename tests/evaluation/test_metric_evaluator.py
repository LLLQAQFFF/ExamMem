from __future__ import annotations

import pytest

from evaluation.backend_adapters import NoMemoryEvaluationSession
from evaluation.contracts.protocol import REQUIRED_METRIC_IDS
from evaluation.contracts.rollout import (
    ExperimentConfig,
    FairnessConfig,
    ModelSettings,
    RetrySettings,
)
from evaluation.evaluators.report import compute_backend_metrics
from evaluation.protocols.validation import load_cases
from evaluation.runner import run_case
from exam_mem.backends import BackendMode

pytestmark = pytest.mark.asyncio


async def test_metric_evaluator_emits_all_metrics_and_explicit_na() -> None:
    case = load_cases("protocol_check")[0]
    config = ExperimentConfig(
        backend_mode=BackendMode.NONE,
        policy_version="none_v1",
        fairness=FairnessConfig(
            protocol_version="evaluation_protocol_v1",
            dataset_split="protocol_check",
            dataset_hash="a" * 64,
            seed=20260806,
            model=ModelSettings(
                provider="offline",
                model="none",
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=1,
            ),
            retrieval_top_k=3,
            retry=RetrySettings(
                timeout_seconds=2,
                max_retries=0,
                backoff_seconds=[],
            ),
        ),
    )
    result = await run_case(
        run_id="metric-none",
        case=case,
        session=NoMemoryEvaluationSession(),
        config=config,
        code_sha="1de8ad56",
    )

    metrics = compute_backend_metrics([case], [result])
    by_id = {metric.metric_id: metric for metric in metrics}

    assert set(by_id) == REQUIRED_METRIC_IDS
    assert by_id["slot.f1"].value == 1.0
    assert by_id["extraction.knowledge_point_accuracy"].status.value == "not_applicable"
    assert by_id["lifecycle.operation_accuracy"].status.value == "not_applicable"
    assert by_id["isolation.cross_scope_leakage_rate"].status.value == "undefined"
    assert by_id["recommendation.knowledge_point_accuracy"].status.value == "measured"
    assert by_id["recommendation.difficulty_match_rate"].status.value == "not_applicable"
    assert by_id["engineering.llm_call_count"].value == 0.0


async def test_metric_evaluator_rejects_mixed_fairness_hashes() -> None:
    first_case, second_case = load_cases("protocol_check")[:2]
    config = ExperimentConfig(
        backend_mode=BackendMode.NONE,
        policy_version="none_v1",
        fairness=FairnessConfig(
            protocol_version="evaluation_protocol_v1",
            dataset_split="protocol_check",
            dataset_hash="a" * 64,
            seed=20260806,
            model=ModelSettings(
                provider="offline",
                model="none",
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=1,
            ),
            retrieval_top_k=3,
            retry=RetrySettings(
                timeout_seconds=2,
                max_retries=0,
                backoff_seconds=[],
            ),
        ),
    )
    first = await run_case(
        run_id="metric-none-a",
        case=first_case,
        session=NoMemoryEvaluationSession(),
        config=config,
        code_sha="1de8ad56",
    )
    second_config = config.model_copy(
        update={"fairness": config.fairness.model_copy(update={"dataset_hash": "b" * 64})}
    )
    second = await run_case(
        run_id="metric-none-b",
        case=second_case,
        session=NoMemoryEvaluationSession(),
        config=second_config,
        code_sha="1de8ad56",
    )

    with pytest.raises(ValueError, match="different fairness"):
        compute_backend_metrics([first_case, second_case], [first, second])


async def test_failed_rollout_remains_in_state_and_recommendation_denominators() -> None:
    case = load_cases("protocol_check")[0]
    config = ExperimentConfig(
        backend_mode=BackendMode.NONE,
        policy_version="none_v1",
        fairness=FairnessConfig(
            protocol_version="evaluation_protocol_v1",
            dataset_split="protocol_check",
            dataset_hash="a" * 64,
            seed=20260806,
            model=ModelSettings(
                provider="offline",
                model="none",
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=1,
            ),
            retrieval_top_k=3,
            retry=RetrySettings(
                timeout_seconds=2,
                max_retries=0,
                backoff_seconds=[],
            ),
        ),
    )
    completed = await run_case(
        run_id="metric-none-failed",
        case=case,
        session=NoMemoryEvaluationSession(),
        config=config,
        code_sha="1de8ad56",
    )
    failed = completed.model_copy(update={"traces": []})

    by_id = {
        metric.metric_id: metric for metric in compute_backend_metrics([case], [failed])
    }

    assert by_id["state.active_state_exact_match"].denominator == len(case.gold_states)
    assert by_id["state.active_state_exact_match"].numerator == 0
    assert by_id["recommendation.knowledge_point_accuracy"].denominator == len(
        case.gold_actions
    )
    assert by_id["recommendation.knowledge_point_accuracy"].numerator == 0
