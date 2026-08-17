"""Deterministic Stage 08 metric aggregation over frozen Gold and rollout traces."""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
import json
import math

from evaluation.contracts.case import EvaluationCase, GoldState
from evaluation.contracts.protocol import REQUIRED_METRIC_IDS
from evaluation.contracts.report import (
    BackendEvaluation,
    CostSummary,
    LatencySummary,
    MemoryGrowthSummary,
    MetricObservation,
    MetricStatus,
    RunOutcomeCounts,
)
from evaluation.contracts.rollout import RolloutResult, RolloutStatus
from evaluation.contracts.trace import TokenUsage
from exam_mem.backends import BackendMode
from exam_mem.contracts import LifecycleOperation, MemoryNamespace, MemoryScope


def _measured(
    metric_id: str,
    value: float,
    *,
    numerator: float | None = None,
    denominator: float | None = None,
    sample_count: int,
) -> MetricObservation:
    return MetricObservation(
        metric_id=metric_id,
        status=MetricStatus.MEASURED,
        value=value,
        numerator=numerator,
        denominator=denominator,
        sample_count=sample_count,
    )


def _missing(
    metric_id: str,
    reason: str,
    *,
    status: MetricStatus = MetricStatus.NOT_APPLICABLE,
) -> MetricObservation:
    return MetricObservation(
        metric_id=metric_id,
        status=status,
        value=None,
        sample_count=0,
        reason=reason,
    )


def _ratio(metric_id: str, numerator: int, denominator: int) -> MetricObservation:
    if denominator == 0:
        return _missing(
            metric_id,
            "registered denominator is zero for this backend and split",
            status=MetricStatus.UNDEFINED,
        )
    return _measured(
        metric_id,
        numerator / denominator,
        numerator=numerator,
        denominator=denominator,
        sample_count=denominator,
    )


def _nearest_rank(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("percentile requires at least one observation")
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def _trace_by_operation(result: RolloutResult) -> dict[str, object]:
    return {trace.step_id: trace for trace in result.traces}


def _id_metadata(case: EvaluationCase) -> tuple[dict[str, str], dict[str, MemoryScope]]:
    slot_by_id = {memory.memory_id: memory.slot_key for memory in case.initial_memory}
    scope_by_id = {memory.memory_id: memory.scope for memory in case.initial_memory}
    event_by_id = {event.event_id: event for event in case.events}
    for operation in case.gold_operations:
        if operation.result_memory_id is None:
            continue
        slot_by_id[operation.result_memory_id] = operation.slot_key
        namespace = MemoryNamespace(operation.slot_key.partition(":")[0])
        scope_by_id[operation.result_memory_id] = MemoryScope(
            **event_by_id[operation.event_id].context.model_dump(),
            memory_namespace=namespace,
        )
    return slot_by_id, scope_by_id


def _macro_f1(gold: Sequence[str], predicted: Sequence[str | None]) -> float:
    labels = sorted(set(gold) | {value for value in predicted if value is not None})
    scores: list[float] = []
    for label in labels:
        tp = sum(g == label and p == label for g, p in zip(gold, predicted, strict=True))
        fp = sum(g != label and p == label for g, p in zip(gold, predicted, strict=True))
        fn = sum(g == label and p != label for g, p in zip(gold, predicted, strict=True))
        denominator = 2 * tp + fp + fn
        scores.append((2 * tp / denominator) if denominator else 0.0)
    return sum(scores) / len(scores)


def _snapshot_count(snapshot: dict) -> int:
    if isinstance(snapshot.get("record_count"), int):
        return snapshot["record_count"]
    total = 0
    contexts = snapshot.get("contexts", [])
    if isinstance(contexts, list):
        for item in contexts:
            if not isinstance(item, dict):
                continue
            inner = item.get("snapshot")
            if not isinstance(inner, dict):
                continue
            for key in ("memories", "facts"):
                records = inner.get(key)
                if isinstance(records, list):
                    total += len(records)
    return total


def _snapshot_bytes(snapshot: dict) -> int:
    return len(
        json.dumps(snapshot, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )
    )


def compute_backend_metrics(
    cases: Sequence[EvaluationCase],
    results: Sequence[RolloutResult],
) -> list[MetricObservation]:
    """Return every preregistered metric with a value or an explicit N/A reason."""
    if not cases or len(cases) != len(results):
        raise ValueError("metric aggregation requires one rollout per non-empty case set")
    case_by_id = {case.case_id: case for case in cases}
    if len(case_by_id) != len(cases) or {result.case_id for result in results} != set(case_by_id):
        raise ValueError("rollout case IDs must exactly match the evaluation case set")
    modes = {result.config.backend_mode for result in results}
    if len(modes) != 1:
        raise ValueError("one metric aggregation may contain only one backend mode")
    fairness_hashes = {result.fairness_hash for result in results}
    if len(fairness_hashes) != 1:
        raise ValueError("cannot aggregate rollouts with different fairness settings")
    mode = modes.pop()
    ordered_results = {result.case_id: result for result in results}
    observations: dict[str, MetricObservation] = {}

    structured_reason = (
        "rollouts begin from validated structured LearningEvent fields; raw-text extraction "
        "is outside this layer-isolated benchmark"
    )
    for metric_id in (
        "extraction.knowledge_point_accuracy",
        "extraction.error_type_macro_f1",
    ):
        observations[metric_id] = _missing(metric_id, structured_reason)

    gold_slots: list[str] = []
    predicted_slots: list[str | None] = []
    gold_operations: list[str] = []
    predicted_operations: list[str | None] = []
    state_exact = stale = active_total = duplicate = 0
    state_steps = 0
    retrieval_total = retrieval_leaks = archived_hits = 0
    weak_gold = weak_hits = 0
    scope_cases = scope_passes = 0

    for case in cases:
        result = ordered_results[case.case_id]
        traces = _trace_by_operation(result)
        gold_state_by_step = {state.step_id: state for state in case.gold_states}
        slot_by_id, scope_by_id = _id_metadata(case)
        operations_by_step: dict[str, list] = {}
        for operation in case.gold_operations:
            operations_by_step.setdefault(operation.step_id, []).append(operation)
            trace = traces.get(operation.operation_id)
            gold_slots.append(operation.slot_key)
            predicted_slots.append(
                None if trace is None else getattr(trace, "normalized_slot_key", None)
            )
            gold_operations.append(operation.operation.value)
            decision = None if trace is None else getattr(trace, "lifecycle_decision", None)
            predicted_operations.append(None if decision is None else decision.operation.value)

        if mode is not BackendMode.NATIVE:
            for step_id, operations in operations_by_step.items():
                last_trace = traces.get(operations[-1].operation_id)
                if last_trace is None or last_trace.state_after is None:
                    continue
                expected = gold_state_by_step[step_id]
                predicted_active = set(last_trace.state_after.active_memory_ids)
                expected_active = set(expected.active_memory_ids)
                state_exact += predicted_active == expected_active
                state_steps += 1
                stale += len(predicted_active - expected_active)
                active_total += len(predicted_active)
                counts = Counter(
                    slot_by_id.get(memory_id, f"unknown:{memory_id}")
                    for memory_id in predicted_active
                )
                duplicate += sum(count - 1 for count in counts.values() if count > 1)

        for query in case.queries:
            operation = operations_by_step[query.after_step_id][0]
            trace = traces.get(operation.operation_id)
            ids = [] if trace is None else trace.retrieval_ids
            expected_state: GoldState = gold_state_by_step[query.after_step_id]
            retrieval_total += len(ids)
            archived_hits += sum(
                memory_id in expected_state.archived_memory_ids for memory_id in ids
            )
            retrieval_leaks += sum(scope_by_id.get(memory_id) != query.scope for memory_id in ids)
            action = next(
                action for action in case.gold_actions if action.step_id == query.after_step_id
            )
            retrieved_kps = {
                slot_by_id[memory_id].split(":")[1]
                for memory_id in ids
                if memory_id in slot_by_id and ":" in slot_by_id[memory_id]
            }
            weak_gold += len(action.knowledge_point_ids)
            weak_hits += len(set(action.knowledge_point_ids) & retrieved_kps)
            if case.scenario_type.value == "cross_scope_interference" and ids:
                scope_cases += 1
                scope_passes += all(scope_by_id.get(memory_id) == query.scope for memory_id in ids)

    tp = sum(gold == predicted for gold, predicted in zip(gold_slots, predicted_slots, strict=True))
    fp = sum(
        predicted is not None and predicted != gold
        for gold, predicted in zip(gold_slots, predicted_slots, strict=True)
    )
    fn = len(gold_slots) - tp
    observations["slot.precision"] = _ratio("slot.precision", tp, tp + fp)
    observations["slot.recall"] = _ratio("slot.recall", tp, tp + fn)
    precision = observations["slot.precision"].value
    recall = observations["slot.recall"].value
    if precision is None or recall is None or precision + recall == 0:
        observations["slot.f1"] = _missing(
            "slot.f1",
            "precision/recall are undefined or both zero",
            status=MetricStatus.UNDEFINED,
        )
    else:
        observations["slot.f1"] = _measured(
            "slot.f1",
            2 * precision * recall / (precision + recall),
            sample_count=len(gold_slots),
        )

    if mode is BackendMode.LIFECYCLE:
        correct = sum(
            gold == predicted
            for gold, predicted in zip(gold_operations, predicted_operations, strict=True)
        )
        observations["lifecycle.operation_accuracy"] = _ratio(
            "lifecycle.operation_accuracy", correct, len(gold_operations)
        )
        observations["lifecycle.operation_macro_f1"] = _measured(
            "lifecycle.operation_macro_f1",
            _macro_f1(gold_operations, predicted_operations),
            sample_count=len(gold_operations),
        )
        for operation_name, metric_id in (
            (LifecycleOperation.MERGE.value, "pollution.false_merge_rate"),
            (LifecycleOperation.SUPERSEDE.value, "pollution.false_supersede_rate"),
        ):
            predicted_count = predicted_operations.count(operation_name)
            incorrect = sum(
                predicted == operation_name and gold != operation_name
                for gold, predicted in zip(gold_operations, predicted_operations, strict=True)
            )
            observations[metric_id] = _ratio(metric_id, incorrect, predicted_count)
    else:
        reason = "backend does not expose ExamMem typed LifecycleDecision operations"
        for metric_id in (
            "lifecycle.operation_accuracy",
            "lifecycle.operation_macro_f1",
            "pollution.false_merge_rate",
            "pollution.false_supersede_rate",
        ):
            observations[metric_id] = _missing(metric_id, reason)

    if mode is BackendMode.NATIVE:
        reason = (
            "DeepTutor Native Memory exposes Markdown L2/L3, not ExamMem typed lifecycle states"
        )
        for metric_id in (
            "state.active_state_exact_match",
            "state.stale_rate",
            "state.duplicate_rate",
        ):
            observations[metric_id] = _missing(metric_id, reason)
    else:
        observations["state.active_state_exact_match"] = _ratio(
            "state.active_state_exact_match", state_exact, state_steps
        )
        observations["state.stale_rate"] = _ratio("state.stale_rate", stale, active_total)
        observations["state.duplicate_rate"] = _ratio(
            "state.duplicate_rate", duplicate, active_total
        )

    observations["isolation.cross_scope_leakage_rate"] = _ratio(
        "isolation.cross_scope_leakage_rate", retrieval_leaks, retrieval_total
    )
    observations["isolation.scope_test_pass_rate"] = _ratio(
        "isolation.scope_test_pass_rate", scope_passes, scope_cases
    )
    observations["retrieval.weak_recall_at_k"] = _ratio(
        "retrieval.weak_recall_at_k", weak_hits, weak_gold
    )
    observations["retrieval.archived_hit_at_k"] = _ratio(
        "retrieval.archived_hit_at_k", archived_hits, retrieval_total
    )

    recommendation_reason = (
        "the frozen rollout currently evaluates memory backends only and does not invoke the "
        "question-bank recommendation policy"
    )
    for metric_id in (
        "recommendation.knowledge_point_accuracy",
        "recommendation.difficulty_match_rate",
        "recommendation.over_review_rate",
    ):
        observations[metric_id] = _missing(metric_id, recommendation_reason)

    latencies = [result.latency_ms for result in results]
    call_count = sum(result.llm_call_count for result in results)
    token_count = sum(result.tokens.total_tokens for result in results)
    observations["engineering.llm_call_count"] = _measured(
        "engineering.llm_call_count", float(call_count), sample_count=len(results)
    )
    if call_count and token_count == 0:
        observations["engineering.total_tokens"] = _missing(
            "engineering.total_tokens",
            "configured Host LLM interface did not expose provider token usage metadata",
            status=MetricStatus.UNDEFINED,
        )
    else:
        observations["engineering.total_tokens"] = _measured(
            "engineering.total_tokens", float(token_count), sample_count=len(results)
        )
    observations["engineering.mean_latency_ms"] = _measured(
        "engineering.mean_latency_ms",
        sum(latencies) / len(latencies),
        sample_count=len(latencies),
    )
    observations["engineering.p95_latency_ms"] = _measured(
        "engineering.p95_latency_ms",
        _nearest_rank(latencies, 0.95),
        sample_count=len(latencies),
    )
    records_before = sum(_snapshot_count(result.initial_snapshot) for result in results)
    records_after = sum(_snapshot_count(result.final_snapshot) for result in results)
    bytes_before = sum(_snapshot_bytes(result.initial_snapshot) for result in results)
    bytes_after = sum(_snapshot_bytes(result.final_snapshot) for result in results)
    observations["engineering.memory_record_growth"] = _measured(
        "engineering.memory_record_growth",
        float(records_after - records_before),
        sample_count=len(results),
    )
    observations["engineering.memory_byte_growth"] = _measured(
        "engineering.memory_byte_growth",
        float(bytes_after - bytes_before),
        sample_count=len(results),
    )

    if set(observations) != REQUIRED_METRIC_IDS:
        missing = sorted(REQUIRED_METRIC_IDS - set(observations))
        extra = sorted(set(observations) - REQUIRED_METRIC_IDS)
        raise AssertionError(f"metric registry mismatch missing={missing} extra={extra}")
    return [observations[metric_id] for metric_id in sorted(observations)]


def build_backend_evaluation(
    cases: Sequence[EvaluationCase],
    results: Sequence[RolloutResult],
) -> BackendEvaluation:
    """Build one audited backend result after enforcing aggregation fairness."""
    metrics = compute_backend_metrics(cases, results)
    config_hashes = {result.config_hash for result in results}
    fairness_hashes = {result.fairness_hash for result in results}
    modes = {result.config.backend_mode for result in results}
    if len(config_hashes) != 1 or len(fairness_hashes) != 1 or len(modes) != 1:
        raise ValueError("backend results must share one config, fairness hash, and mode")
    latencies = [result.latency_ms for result in results]
    llm_calls = sum(result.llm_call_count for result in results)
    prompt_tokens = sum(result.tokens.prompt_tokens for result in results)
    completion_tokens = sum(result.tokens.completion_tokens for result in results)
    records_before = sum(_snapshot_count(result.initial_snapshot) for result in results)
    records_after = sum(_snapshot_count(result.final_snapshot) for result in results)
    bytes_before = sum(_snapshot_bytes(result.initial_snapshot) for result in results)
    bytes_after = sum(_snapshot_bytes(result.final_snapshot) for result in results)
    counts = Counter(result.status for result in results)
    return BackendEvaluation(
        backend_mode=modes.pop(),
        config_hash=config_hashes.pop(),
        fairness_hash=fairness_hashes.pop(),
        run_ids=[result.run_id for result in results],
        outcomes=RunOutcomeCounts(
            total=len(results),
            completed=counts[RolloutStatus.COMPLETED],
            partial=counts[RolloutStatus.PARTIAL],
            failed=counts[RolloutStatus.FAILED],
            timeout=counts[RolloutStatus.TIMEOUT],
        ),
        metrics=metrics,
        cost=CostSummary(
            llm_call_count=llm_calls,
            tokens=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
            ),
            estimated_cost_usd=0.0 if llm_calls == 0 else None,
            estimated_cost_reason=(
                None
                if llm_calls == 0
                else "provider token usage and a frozen pricing snapshot are unavailable"
            ),
            latency=LatencySummary(
                mean_ms=sum(latencies) / len(latencies),
                p95_ms=_nearest_rank(latencies, 0.95),
                max_ms=max(latencies),
            ),
            memory_growth=MemoryGrowthSummary(
                records_before=records_before,
                records_after=records_after,
                record_growth=records_after - records_before,
                byte_growth=bytes_after - bytes_before,
            ),
        ),
    )


__all__ = ["build_backend_evaluation", "compute_backend_metrics"]
