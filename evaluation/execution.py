"""Reproducible Stage 08 execution and immutable artifact materialization."""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deeptutor.services.config import resolve_llm_runtime_config
from evaluation.backend_adapters import (
    NativeEvaluationSession,
    NoMemoryEvaluationSession,
    PostgresEvaluationSession,
)
from evaluation.contracts.case import PROTOCOL_SEED, DatasetSplit, EvaluationCase
from evaluation.contracts.report import EvaluationReport
from evaluation.contracts.rollout import (
    ExperimentConfig,
    FairnessConfig,
    ModelSettings,
    RetrySettings,
    RolloutResult,
)
from evaluation.data_builder import DATASET_VERSION
from evaluation.evaluators.report import build_backend_evaluation, compute_backend_metrics
from evaluation.protocols.validation import DATASET_ROOT, load_cases, load_protocol
from evaluation.runner import EvaluationBackendSession, run_case
from exam_mem.backends import BackendMode


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n"


def _dataset_hash(split: DatasetSplit, cases: Sequence[EvaluationCase]) -> str:
    if split in {DatasetSplit.DEV, DatasetSplit.TEST}:
        manifest = json.loads(
            (DATASET_ROOT / f"{DATASET_VERSION}.manifest.json").read_text(encoding="utf-8")
        )
        return next(
            item["aggregate_sha256"] for item in manifest["splits"] if item["split"] == split.value
        )
    digest = hashlib.sha256()
    for case in cases:
        digest.update(case.model_dump_json().encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest()


def _code_sha() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _config(
    *,
    mode: BackendMode,
    split: DatasetSplit,
    dataset_hash: str,
    provider: str,
    model: str,
    timeout_seconds: float,
    top_k: int,
) -> ExperimentConfig:
    policy_versions = {
        BackendMode.NONE: "none_v1",
        BackendMode.NATIVE: "deeptutor_native_memory_v1",
        BackendMode.APPEND_ONLY: "append_only_v1",
        BackendMode.VECTOR: "vector_v1",
        BackendMode.LIFECYCLE: "lifecycle_policy_v1",
    }
    options: dict[str, Any] = {}
    if mode is BackendMode.VECTOR:
        options["embedding_model"] = "feature_hash_embedding_v1"
    if mode is BackendMode.NATIVE:
        options["typed_lifecycle_available"] = False
    return ExperimentConfig(
        backend_mode=mode,
        policy_version=policy_versions[mode],
        backend_options=options,
        fairness=FairnessConfig(
            protocol_version="evaluation_protocol_v1",
            dataset_split=split,
            dataset_hash=dataset_hash,
            seed=PROTOCOL_SEED,
            model=ModelSettings(
                provider=provider,
                model=model,
                temperature=0.0,
                top_p=1.0,
                max_output_tokens=1500,
                additional_parameters={"language": "zh"},
            ),
            retrieval_top_k=top_k,
            retry=RetrySettings(
                timeout_seconds=timeout_seconds,
                max_retries=0,
                backoff_seconds=[],
            ),
        ),
    )


def _session(
    *,
    mode: BackendMode,
    engine: AsyncEngine | None,
    native_root: Path,
    run_id: str,
    case: EvaluationCase,
) -> EvaluationBackendSession:
    if mode is BackendMode.NONE:
        return NoMemoryEvaluationSession()
    if mode is BackendMode.NATIVE:
        return NativeEvaluationSession(
            root=native_root,
            run_id=run_id,
            case=case,
        )
    if engine is None:
        raise ValueError(f"{mode.value} requires an isolated PostgreSQL database URL")
    return PostgresEvaluationSession(engine=engine, mode=mode, run_id=run_id, case=case)


async def _run_mode(
    *,
    experiment_id: str,
    output: Path,
    mode: BackendMode,
    cases: Sequence[EvaluationCase],
    config: ExperimentConfig,
    engine: AsyncEngine | None,
    code_sha: str,
    concurrency: int,
    resume: bool,
) -> list[RolloutResult]:
    partial_dir = output / "partial" / mode.value
    partial_dir.mkdir(parents=True, exist_ok=True)
    semaphore = asyncio.Semaphore(concurrency)
    by_user: dict[str, list[EvaluationCase]] = defaultdict(list)
    for case in cases:
        user_ids = {event.context.user_id for event in case.events}
        if len(user_ids) != 1:
            raise ValueError(f"case {case.case_id} must contain exactly one rollout user")
        by_user[user_ids.pop()].append(case)

    async def run_user(user_cases: list[EvaluationCase]) -> list[RolloutResult]:
        collected: list[RolloutResult] = []
        async with semaphore:
            for case in user_cases:
                path = partial_dir / f"{case.case_id}.json"
                if resume and path.is_file():
                    cached = RolloutResult.model_validate_json(path.read_text(encoding="utf-8"))
                    if cached.config_hash != config.canonical_hash() or cached.code_sha != code_sha:
                        raise ValueError(f"resume artifact config/code mismatch: {path}")
                    collected.append(cached)
                    continue
                run_id = f"{experiment_id}:{mode.value}:{case.case_id}"
                result = await run_case(
                    run_id=run_id,
                    case=case,
                    session=_session(
                        mode=mode,
                        engine=engine,
                        native_root=output / "native",
                        run_id=run_id,
                        case=case,
                    ),
                    config=config,
                    code_sha=code_sha,
                )
                path.write_bytes(_json_bytes(result.model_dump(mode="json")))
                collected.append(result)
        return collected

    groups = await asyncio.gather(*(run_user(group) for group in by_user.values()))
    by_case = {result.case_id: result for group in groups for result in group}
    return [by_case[case.case_id] for case in cases]


def _first_bad_case(case: EvaluationCase, result: RolloutResult) -> dict[str, Any] | None:
    if result.errors:
        error = result.errors[0]
        return {
            "case_id": case.case_id,
            "backend_mode": result.config.backend_mode.value,
            "first_error_layer": error.stage.value,
            "error_type": error.error_type,
            "message": error.message,
        }
    gold = {operation.operation_id: operation for operation in case.gold_operations}
    for trace in result.traces:
        operation = gold.get(trace.step_id)
        if operation is None or result.config.backend_mode is not BackendMode.LIFECYCLE:
            continue
        predicted = None if trace.lifecycle_decision is None else trace.lifecycle_decision.operation
        if predicted is not operation.operation:
            return {
                "case_id": case.case_id,
                "backend_mode": result.config.backend_mode.value,
                "first_error_layer": "lifecycle",
                "step_id": trace.step_id,
                "gold": operation.operation.value,
                "predicted": None if predicted is None else predicted.value,
            }
    return None


def _write_jsonl(path: Path, values: Sequence[Any]) -> None:
    payload = b"".join(
        json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        + b"\n"
        for value in values
    )
    path.write_bytes(payload)


def _markdown(report: EvaluationReport, scenario_metrics: dict[str, Any]) -> str:
    lines = [
        f"# ExamMem Stage 08 评测报告：{report.report_id}",
        "",
        f"- 数据划分：`{report.dataset_split.value}`",
        f"- 数据哈希：`{report.dataset_hash}`",
        f"- 公平配置哈希：`{report.fairness_hash}`",
        f"- 代码提交：`{report.code_sha}`",
        "",
        "## 总体结果",
        "",
        "| Backend | 完成/总数 | Lifecycle Acc | State Exact | Retrieval Recall@K | LLM calls | 平均延迟(ms) |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for backend in report.backend_results:
        metrics = {metric.metric_id: metric for metric in backend.metrics}

        def display(metric_id: str) -> str:
            metric = metrics[metric_id]
            return "N/A" if metric.value is None else f"{metric.value:.4f}"

        lines.append(
            f"| {backend.backend_mode.value} | {backend.outcomes.completed}/{backend.outcomes.total} "
            f"| {display('lifecycle.operation_accuracy')} "
            f"| {display('state.active_state_exact_match')} "
            f"| {display('retrieval.weak_recall_at_k')} "
            f"| {backend.cost.llm_call_count} | {backend.cost.latency.mean_ms:.2f} |"
        )
    lines.extend(
        [
            "",
            "## 场景分解",
            "",
            "完整机器可读分解见 `scenario_metrics.json`。",
            "",
            "## 局限",
            "",
            "- 输入从结构化 LearningEvent 开始，因此原始文本抽取指标为 N/A。",
            "- Native Memory 不暴露 ExamMem typed lifecycle，相关指标为 N/A。",
            "- 当前 rollout 未调用题库推荐策略，三个 recommendation 指标为 N/A。",
            "- Vector 使用冻结的本地 1024 维 feature-hash，仅是可复现基线，不代表生产 embedding。",
            "- Host LLM 未返回 token usage 时，token 与美元成本为 N/A，不做静默估算。",
            "",
            f"场景分组数：{len(scenario_metrics)}。",
        ]
    )
    return "\n".join(lines) + "\n"


async def execute_evaluation(
    *,
    experiment_id: str,
    split: DatasetSplit,
    modes: Sequence[BackendMode],
    output_root: Path,
    database_url: str | None,
    concurrency: int = 1,
    timeout_seconds: float = 300.0,
    top_k: int = 5,
    resume: bool = False,
) -> dict[str, Any]:
    """Run selected arms; finalize a report only after all five exist."""
    if split is DatasetSplit.TEST:
        raise ValueError("frozen test may only be schema/hash verified in Stage 08")
    if concurrency < 1:
        raise ValueError("concurrency must be at least one")
    cases = load_cases(split)
    dataset_hash = _dataset_hash(split, cases)
    code_sha = _code_sha()
    resolved = resolve_llm_runtime_config()
    output = output_root / experiment_id
    if output.exists() and not resume:
        raise ValueError(f"immutable run directory already exists: {output}")
    output.mkdir(parents=True, exist_ok=True)
    engine = create_async_engine(database_url) if database_url else None
    all_results: dict[BackendMode, list[RolloutResult]] = {}
    try:
        for mode in modes:
            config = _config(
                mode=mode,
                split=split,
                dataset_hash=dataset_hash,
                provider=resolved.provider_name,
                model=resolved.model,
                timeout_seconds=timeout_seconds,
                top_k=top_k,
            )
            all_results[mode] = await _run_mode(
                experiment_id=experiment_id,
                output=output,
                mode=mode,
                cases=cases,
                config=config,
                engine=engine,
                code_sha=code_sha,
                concurrency=concurrency,
                resume=resume,
            )
    finally:
        if engine is not None:
            await engine.dispose()

    manifest = {
        "experiment_id": experiment_id,
        "protocol_version": "evaluation_protocol_v1",
        "split": split.value,
        "dataset_hash": dataset_hash,
        "code_sha": code_sha,
        "seed": PROTOCOL_SEED,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "backend_modes": [mode.value for mode in modes],
        "complete_five_arm_report": set(all_results) == set(BackendMode),
    }
    (output / "manifest.json").write_bytes(_json_bytes(manifest))
    configs = [results[0].config.model_dump(mode="json") for results in all_results.values()]
    (output / "config.json").write_bytes(_json_bytes(configs))
    _write_jsonl(output / "cases.jsonl", [case.model_dump(mode="json") for case in cases])
    flat_results = [result for mode in modes for result in all_results[mode]]
    _write_jsonl(
        output / "traces.jsonl",
        [trace.model_dump(mode="json") for result in flat_results for trace in result.traces],
    )
    snapshots = output / "snapshots"
    snapshots.mkdir(exist_ok=True)
    for result in flat_results:
        (snapshots / f"{result.config.backend_mode.value}__{result.case_id}.json").write_bytes(
            _json_bytes({"initial": result.initial_snapshot, "final": result.final_snapshot})
        )
    bad_cases = [
        bad
        for mode in modes
        for case, result in zip(cases, all_results[mode], strict=True)
        if (bad := _first_bad_case(case, result)) is not None
    ]
    _write_jsonl(output / "bad_cases.jsonl", bad_cases)

    metrics = {
        mode.value: [
            metric.model_dump(mode="json")
            for metric in compute_backend_metrics(cases, all_results[mode])
        ]
        for mode in modes
    }
    (output / "metrics.json").write_bytes(_json_bytes(metrics))
    csv_lines = ["backend_mode,metric_id,status,value,numerator,denominator,sample_count,reason"]
    for mode, observations in metrics.items():
        for metric in observations:
            row = [
                mode,
                metric["metric_id"],
                metric["status"],
                "" if metric["value"] is None else str(metric["value"]),
                "" if metric["numerator"] is None else str(metric["numerator"]),
                "" if metric["denominator"] is None else str(metric["denominator"]),
                str(metric["sample_count"]),
                json.dumps(metric["reason"] or "", ensure_ascii=False),
            ]
            csv_lines.append(",".join(row))
    (output / "metrics.csv").write_text("\n".join(csv_lines) + "\n", encoding="utf-8")

    scenario_metrics: dict[str, Any] = {}
    for scenario in sorted({case.scenario_type.value for case in cases}):
        indices = [
            index for index, case in enumerate(cases) if case.scenario_type.value == scenario
        ]
        scenario_cases = [cases[index] for index in indices]
        scenario_metrics[scenario] = {
            mode.value: [
                metric.model_dump(mode="json")
                for metric in compute_backend_metrics(
                    scenario_cases, [all_results[mode][index] for index in indices]
                )
            ]
            for mode in modes
        }
    (output / "scenario_metrics.json").write_bytes(_json_bytes(scenario_metrics))

    if set(all_results) == set(BackendMode):
        protocol = load_protocol("evaluation_protocol_v1")
        backend_results = [
            build_backend_evaluation(cases, all_results[mode]) for mode in protocol.backend_modes
        ]
        fairness_hashes = {result.fairness_hash for result in backend_results}
        if len(fairness_hashes) != 1:
            raise ValueError("five-arm report fairness hashes differ")
        report = EvaluationReport(
            report_id=experiment_id,
            protocol_version="evaluation_protocol_v1",
            dataset_split=split,
            dataset_hash=dataset_hash,
            fairness_hash=fairness_hashes.pop(),
            seed=PROTOCOL_SEED,
            gold_revision=next(iter({case.metadata.gold_revision for case in cases})),
            code_sha=code_sha,
            generated_at=datetime.now(timezone.utc),
            metric_definitions=protocol.metrics,
            backend_results=backend_results,
            warnings=[
                "Layer-isolated rollout uses Gold-normalized slots after extraction.",
                "Native consolidator internal temperature is owned by DeepTutor Native Memory.",
                "Recommendation policy is not invoked by this memory-backend rollout.",
            ],
        )
        (output / "report.json").write_bytes(_json_bytes(report.model_dump(mode="json")))
        (output / "report.md").write_text(_markdown(report, scenario_metrics), encoding="utf-8")
    else:
        (output / "report.md").write_text(
            "# Partial Stage 08 run\n\nNo comparative report until all five backend arms complete.\n",
            encoding="utf-8",
        )
    return manifest


__all__ = ["execute_evaluation"]
