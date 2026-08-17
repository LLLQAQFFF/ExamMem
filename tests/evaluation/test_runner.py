from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import pytest

from evaluation.contracts.rollout import (
    ExperimentConfig,
    FairnessConfig,
    ModelSettings,
    RetrySettings,
)
from evaluation.contracts.trace import LLMCallTrace, MemoryStateTrace, TokenUsage
from evaluation.materializer import MaterializedStep
from evaluation.protocols.validation import load_cases
from evaluation.runner import run_case
from exam_mem.backends import BackendMode
from exam_mem.contracts import LearningMemory, LifecycleDecision, LifecycleOperation

pytestmark = pytest.mark.asyncio


def _config() -> ExperimentConfig:
    return ExperimentConfig(
        backend_mode=BackendMode.LIFECYCLE,
        policy_version="lifecycle_policy_v1",
        fairness=FairnessConfig(
            protocol_version="evaluation_protocol_v1",
            dataset_split="protocol_check",
            dataset_hash="a" * 64,
            seed=20260806,
            model=ModelSettings(
                provider="fake",
                model="fake",
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=100,
            ),
            retrieval_top_k=3,
            retry=RetrySettings(
                timeout_seconds=5,
                max_retries=0,
                backoff_seconds=[],
            ),
        ),
    )


class FakeSession:
    mode = BackendMode.LIFECYCLE
    policy_version = "lifecycle_policy_v1"

    def __init__(
        self,
        *,
        fail: bool = False,
        delay_seconds: float = 0.0,
        llm_calls_per_step: int = 0,
    ) -> None:
        self.fail = fail
        self.delay_seconds = delay_seconds
        self.llm_calls_per_step = llm_calls_per_step
        self.call_index = 0
        self.snapshot_payload: dict[str, Any] = {"step": 0}

    async def seed(self, case):  # noqa: ANN001, ANN201
        return self.snapshot_payload

    async def process(self, step: MaterializedStep):  # noqa: ANN201
        await asyncio.sleep(self.delay_seconds)
        if self.fail:
            raise RuntimeError("stable fake failure")
        self.snapshot_payload = {"step": self.snapshot_payload["step"] + 1}
        return (
            [
                LifecycleDecision(
                    operation=operation.operation,
                    target_memory_ids=operation.target_memory_ids,
                    reason_code="fake_vertical_slice",
                    confidence=1.0,
                    policy_version=self.policy_version,
                )
                for operation in step.gold_operations
            ],
            self.snapshot_payload,
        )

    async def retrieve(self, query):  # noqa: ANN001, ANN201
        return []

    async def recommend(self, step):  # noqa: ANN001, ANN201
        return None

    def state_trace(self, snapshot):  # noqa: ANN001, ANN201
        return MemoryStateTrace(
            active_memory_ids=[],
            archived_memory_ids=[],
            invalidated_memory_ids=[],
            contested_memory_ids=[],
            version_relations=[],
        )

    def candidate_ids(self, snapshot, candidate):  # noqa: ANN001, ANN201
        return []

    def take_llm_calls(self):  # noqa: ANN201
        calls = []
        for _ in range(self.llm_calls_per_step):
            self.call_index += 1
            calls.append(
                LLMCallTrace(
                    call_id=f"fake-call-{self.call_index}",
                    purpose="test",
                    provider="fake",
                    model="fake",
                    token_usage=TokenUsage(
                        prompt_tokens=1,
                        completion_tokens=1,
                        total_tokens=2,
                    ),
                    latency_ms=1,
                    succeeded=True,
                )
            )
        return calls


async def test_runner_builds_a_complete_vertical_slice() -> None:
    case = load_cases("protocol_check")[0]

    result = await run_case(
        run_id="vertical-slice",
        case=case,
        session=FakeSession(),
        config=_config(),
        code_sha="1de8ad56",
    )

    assert result.status.value == "completed"
    assert len(result.traces) == len(case.gold_operations)
    assert [trace.step_index for trace in result.traces] == list(range(len(result.traces)))
    assert all(trace.status.value == "completed" for trace in result.traces)


async def test_runner_preserves_a_failed_trace_instead_of_filtering_case() -> None:
    case = load_cases("protocol_check")[0]

    result = await run_case(
        run_id="failed-vertical-slice",
        case=case,
        session=FakeSession(fail=True),
        config=_config(),
        code_sha="1de8ad56",
    )

    assert result.status.value == "failed"
    assert result.errors[0].error_type == "RuntimeError"
    assert result.traces[-1].status.value == "failed"
    assert result.traces[-1].normalized_slot_key is not None
    assert "stable fake failure" in result.traces[-1].errors[0].message


async def test_runner_preserves_partial_evidence_on_timeout() -> None:
    case = load_cases("protocol_check")[0]
    config = _config()
    config.fairness.retry.timeout_seconds = 0.01

    result = await run_case(
        run_id="timeout-vertical-slice",
        case=case,
        session=FakeSession(delay_seconds=0.1),
        config=config,
        code_sha="1de8ad56",
    )

    assert result.status.value == "timeout"
    assert result.traces[-1].status.value == "timeout"
    assert result.traces[-1].errors[0].error_type == "TimeoutError"


async def test_runner_stops_after_atomic_step_when_llm_budget_is_exceeded() -> None:
    case = load_cases("dev")[0]
    config = _config()
    config.fairness.max_llm_calls_per_case = 1

    result = await run_case(
        run_id="budget-vertical-slice",
        case=case,
        session=FakeSession(llm_calls_per_step=1),
        config=config,
        code_sha="1de8ad56",
    )

    assert result.status.value == "partial"
    assert result.errors[0].error_type == "LLMCallBudgetExceeded"
    assert result.llm_call_count == 2
    assert len({trace.input_event.event_id for trace in result.traces}) == 2
