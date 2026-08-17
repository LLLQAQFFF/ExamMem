"""Backend-neutral Stage 08 rollout orchestration."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import datetime, timezone
from time import monotonic
from typing import Protocol

from pydantic import JsonValue

from evaluation.contracts.case import EvaluationCase, EvaluationQuery
from evaluation.contracts.rollout import ExperimentConfig, RolloutResult, RolloutStatus
from evaluation.contracts.trace import (
    LLMCallTrace,
    MemoryStateTrace,
    RecommendationTrace,
    RolloutTrace,
    TokenUsage,
    TraceError,
    TraceStage,
    TraceStatus,
)
from evaluation.materializer import MaterializedStep, materialize_case
from exam_mem.backends import BackendMode
from exam_mem.contracts import (
    LearningMemory,
    LifecycleDecision,
    MemoryUpdateCandidate,
)


class EvaluationBackendSession(Protocol):
    """One clean case scope supplied by a concrete baseline adapter."""

    mode: BackendMode
    policy_version: str

    async def seed(self, case: EvaluationCase) -> dict[str, JsonValue]: ...

    async def process(
        self,
        step: MaterializedStep,
    ) -> tuple[list[LifecycleDecision], dict[str, JsonValue]]: ...

    async def retrieve(self, query: EvaluationQuery) -> list[LearningMemory]: ...

    async def recommend(
        self,
        step: MaterializedStep,
    ) -> RecommendationTrace | None: ...

    def state_trace(self, snapshot: dict[str, JsonValue]) -> MemoryStateTrace: ...

    def candidate_ids(
        self,
        snapshot: dict[str, JsonValue],
        candidate: MemoryUpdateCandidate,
    ) -> list[str]: ...

    def take_llm_calls(self) -> list[LLMCallTrace]: ...


def _zero_tokens() -> TokenUsage:
    return TokenUsage(prompt_tokens=0, completion_tokens=0, total_tokens=0)


def _sum_tokens(calls: Sequence[LLMCallTrace]) -> TokenUsage:
    prompt = sum(call.token_usage.prompt_tokens for call in calls)
    completion = sum(call.token_usage.completion_tokens for call in calls)
    return TokenUsage(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )


def _queries_after(case: EvaluationCase, step_id: str) -> tuple[EvaluationQuery, ...]:
    return tuple(query for query in case.queries if query.after_step_id == step_id)


async def _run_case(
    *,
    run_id: str,
    case: EvaluationCase,
    session: EvaluationBackendSession,
    config: ExperimentConfig,
    code_sha: str,
) -> RolloutResult:
    started_at = datetime.now(timezone.utc)
    rollout_started = monotonic()
    traces: list[RolloutTrace] = []
    errors: list[TraceError] = []
    initial_snapshot: dict[str, JsonValue] = {}
    final_snapshot: dict[str, JsonValue] = {}
    trace_index = 0
    current_step: MaterializedStep | None = None
    current_candidate_ids: list[list[str]] = []
    current_stage = TraceStage.RECORD_EVENT
    rollout_status = RolloutStatus.COMPLETED
    observed_llm_calls = 0
    try:
        async with asyncio.timeout(config.fairness.retry.timeout_seconds):
            initial_snapshot = await session.seed(case)
            final_snapshot = initial_snapshot
            for current_step in materialize_case(case):
                before_snapshot = final_snapshot
                before_state = session.state_trace(before_snapshot)
                current_candidate_ids = [
                    session.candidate_ids(before_snapshot, candidate)
                    for candidate in current_step.candidates
                ]
                step_started_at = datetime.now(timezone.utc)
                step_started = monotonic()
                current_stage = TraceStage.APPLY
                decisions, final_snapshot = await session.process(current_step)
                calls = session.take_llm_calls()
                observed_llm_calls += len(calls)
                after_state = session.state_trace(final_snapshot)

                retrieval_ids: list[str] = []
                current_stage = TraceStage.RETRIEVE
                for query in _queries_after(case, current_step.step_id):
                    retrieval_ids.extend(
                        memory.memory_id for memory in await session.retrieve(query)
                    )
                current_stage = TraceStage.RECOMMEND
                recommendation = await session.recommend(current_step)
                step_completed_at = datetime.now(timezone.utc)
                step_latency_ms = (monotonic() - step_started) * 1000

                for candidate_index, (candidate, operation) in enumerate(
                    zip(
                        current_step.candidates,
                        current_step.gold_operations,
                        strict=True,
                    )
                ):
                    decision = (
                        decisions[candidate_index] if candidate_index < len(decisions) else None
                    )
                    trace_calls = calls if candidate_index == 0 else []
                    traces.append(
                        RolloutTrace(
                            run_id=run_id,
                            case_id=case.case_id,
                            trace_id=f"{run_id}:{case.case_id}:trace:{trace_index:03d}",
                            step_id=operation.operation_id,
                            step_index=trace_index,
                            backend_mode=session.mode,
                            protocol_version=case.protocol_version,
                            policy_version=session.policy_version,
                            started_at=step_started_at,
                            completed_at=step_completed_at,
                            input_event=current_step.event,
                            extracted_fields=operation.extracted_fields,
                            normalized_slot_key=candidate.slot_key,
                            candidate_ids=current_candidate_ids[candidate_index],
                            lifecycle_decision=decision,
                            state_before=before_state,
                            state_after=after_state,
                            retrieval_ids=(retrieval_ids if candidate_index == 0 else []),
                            recommendation=(recommendation if candidate_index == 0 else None),
                            llm_calls=trace_calls,
                            tokens=_sum_tokens(trace_calls),
                            latency_ms=step_latency_ms,
                            status=TraceStatus.COMPLETED,
                            errors=[],
                        )
                    )
                    trace_index += 1
                if observed_llm_calls > config.fairness.max_llm_calls_per_case:
                    errors.append(
                        TraceError(
                            stage=TraceStage.DECIDE,
                            error_type="LLMCallBudgetExceeded",
                            message=(
                                "per-case LLM call budget exceeded: "
                                f"{observed_llm_calls} > "
                                f"{config.fairness.max_llm_calls_per_case}"
                            ),
                            retryable=False,
                            attempt=1,
                        )
                    )
                    rollout_status = RolloutStatus.PARTIAL
                    break
                current_stage = TraceStage.APPLY
    except Exception as exc:  # noqa: BLE001 - failures are evaluation evidence
        timed_out = isinstance(exc, TimeoutError)
        rollout_status = RolloutStatus.TIMEOUT if timed_out else RolloutStatus.FAILED
        error = TraceError(
            stage=current_stage,
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
            retryable=False,
            attempt=1,
        )
        errors.append(error)
        failed_step = current_step or materialize_case(case)[0]
        failed_operation = failed_step.gold_operations[0]
        now = datetime.now(timezone.utc)
        state = session.state_trace(final_snapshot)
        calls = session.take_llm_calls()
        traces.append(
            RolloutTrace(
                run_id=run_id,
                case_id=case.case_id,
                trace_id=f"{run_id}:{case.case_id}:trace:{trace_index:03d}",
                step_id=failed_operation.operation_id,
                step_index=trace_index,
                backend_mode=session.mode,
                protocol_version=case.protocol_version,
                policy_version=session.policy_version,
                started_at=now,
                completed_at=now,
                input_event=failed_step.event,
                extracted_fields=failed_operation.extracted_fields,
                normalized_slot_key=failed_step.candidates[0].slot_key,
                candidate_ids=(current_candidate_ids[0] if current_candidate_ids else []),
                lifecycle_decision=None,
                state_before=state,
                state_after=state,
                retrieval_ids=[],
                recommendation=None,
                llm_calls=calls,
                tokens=_sum_tokens(calls),
                latency_ms=0.0,
                status=TraceStatus.TIMEOUT if timed_out else TraceStatus.FAILED,
                errors=[error],
            )
        )

    completed_at = datetime.now(timezone.utc)
    all_calls = [call for trace in traces for call in trace.llm_calls]
    return RolloutResult(
        run_id=run_id,
        case_id=case.case_id,
        config=config,
        config_hash=config.canonical_hash(),
        fairness_hash=config.fairness.canonical_hash(),
        code_sha=code_sha,
        started_at=started_at,
        completed_at=completed_at,
        initial_snapshot=initial_snapshot,
        final_snapshot=final_snapshot,
        traces=traces,
        tokens=_sum_tokens(all_calls) if all_calls else _zero_tokens(),
        llm_call_count=len(all_calls),
        latency_ms=(monotonic() - rollout_started) * 1000,
        status=rollout_status,
        errors=errors,
    )


async def run_case(
    *,
    run_id: str,
    case: EvaluationCase,
    session: EvaluationBackendSession,
    config: ExperimentConfig,
    code_sha: str,
) -> RolloutResult:
    """Run one case under its frozen hard timeout and preserve partial evidence."""
    if session.mode is not config.backend_mode:
        raise ValueError("backend session mode must match experiment config")
    return await _run_case(
        run_id=run_id,
        case=case,
        session=session,
        config=config,
        code_sha=code_sha,
    )


__all__ = ["EvaluationBackendSession", "run_case"]
