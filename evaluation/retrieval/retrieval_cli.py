"""Command-line entry point for semantic and HNSW retrieval-v2 evaluation."""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from evaluation.backend_adapters import ConfiguredHostEmbeddingClient
from evaluation.retrieval.contracts import RetrievalDataset
from evaluation.retrieval.dataset_builder import build_scale_corpus
from evaluation.retrieval.metrics import (
    EXAM_MEM_METRIC_CATALOG,
    MetricScore,
    TargetOperator,
)
from evaluation.retrieval.retrieval_runner import (
    evaluate_hnsw_profile,
    evaluate_semantic_retrieval,
)
from exam_mem.storage.models import learning_memories


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("evaluation/datasets/semantic_retrieval_v2/test.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hnsw-query-count", type=int, default=100)
    return parser


async def _run(args: argparse.Namespace) -> dict[str, object]:
    database_url = os.environ.get("EXAM_MEM_DATABASE_URL")
    if not database_url:
        raise ValueError("EXAM_MEM_DATABASE_URL is required")
    if args.hnsw_query_count < 100:
        raise ValueError("formal HNSW profile requires at least 100 queries")
    dataset = RetrievalDataset.model_validate_json(args.dataset.read_text(encoding="utf-8"))
    embedding_client = ConfiguredHostEmbeddingClient()
    engine = create_async_engine(database_url)
    try:
        async with engine.connect() as connection:
            database_name = str(await connection.scalar(text("select current_database()")))
            if not database_name.startswith("exammem_retrieval_v2_final"):
                raise ValueError("retrieval evaluation requires the final isolated database")
            memory_count = int(
                await connection.scalar(select(func.count()).select_from(learning_memories)) or 0
            )
            await connection.commit()

            def semantic_progress(done: int, total: int) -> None:
                if done == total or done % 50 == 0:
                    print(f"semantic retrieval: {done}/{total}", flush=True)

            semantic = await evaluate_semantic_retrieval(
                connection,
                dataset,
                embedding_client,
                embedding_batch_size=32,
                progress=semantic_progress,
            )

            scale = build_scale_corpus(10_000)
            profile_scope = scale[0].memory.scope
            profile_candidate_count = int(
                await connection.scalar(
                    select(func.count())
                    .select_from(learning_memories)
                    .where(
                        learning_memories.c.user_id == profile_scope.user_id,
                        learning_memories.c.exam_id == profile_scope.exam_id,
                        learning_memories.c.subject_id == profile_scope.subject_id,
                        learning_memories.c.memory_namespace
                        == profile_scope.memory_namespace.value,
                        learning_memories.c.lifecycle_state.in_(("active", "contested")),
                        learning_memories.c.content_embedding.is_not(None),
                    )
                )
                or 0
            )
            await connection.commit()
            if profile_candidate_count != 10_000:
                raise ValueError("HNSW profile requires exactly 10,000 candidates in one Scope")
            hnsw_queries = _hnsw_queries(scale, args.hnsw_query_count)

            def hnsw_progress(done: int, total: int) -> None:
                if done == total or done % 25 == 0:
                    print(f"HNSW profile: {done}/{total}", flush=True)

            hnsw = await evaluate_hnsw_profile(
                connection,
                scope=profile_scope,
                queries=hnsw_queries,
                embedding_client=embedding_client,
                top_k=5,
                embedding_batch_size=32,
                progress=hnsw_progress,
            )
            migration_head = str(
                await connection.scalar(text("select version_num from alembic_version"))
            )
    finally:
        await engine.dispose()

    return {
        "protocol_version": dataset.protocol_version,
        "dataset_sha256": dataset.canonical_hash(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "database_name": database_name,
        "migration_head": migration_head,
        "database_memory_count": memory_count,
        "embedding_provider": embedding_client.version,
        "embedding_call_count": embedding_client.call_count,
        "semantic": {
            "query_count": len(dataset.queries),
            "elapsed_ms": semantic.elapsed_ms,
            "metrics": [
                {**score.model_dump(mode="json"), **_metric_status(score)}
                for score in semantic.metrics.scores
            ],
            "production_latency": semantic.metrics.production_latency.model_dump(mode="json"),
            "exact_latency": semantic.metrics.exact_latency.model_dump(mode="json"),
            "observations": [
                observation.model_dump(mode="json") for observation in semantic.observations
            ],
        },
        "hnsw_profile": {
            "query_count": len(hnsw.observations),
            "candidate_count": hnsw.candidate_count,
            "production_mean_exact_agreement_at_k": (hnsw.production_mean_exact_agreement_at_k),
            "production_hnsw_plan_rate": hnsw.production_hnsw_plan_rate,
            "measured_production_ann_recall_at_k": (hnsw.measured_production_ann_recall_at_k),
            "control_mean_ann_recall_at_k": hnsw.control_mean_ann_recall_at_k,
            "control_hnsw_plan_rate": hnsw.control_hnsw_plan_rate,
            "production_p95_latency_ms": hnsw.production_p95_latency_ms,
            "exact_p95_latency_ms": hnsw.exact_p95_latency_ms,
            "control_p95_latency_ms": hnsw.control_p95_latency_ms,
            "control_is_forced_planner_diagnostic": True,
            "elapsed_ms": hnsw.elapsed_ms,
            "passes_production_ann_recall_gate": (
                None
                if hnsw.measured_production_ann_recall_at_k is None
                else hnsw.measured_production_ann_recall_at_k >= 0.95
            ),
            "passes_production_plan_gate": hnsw.production_hnsw_plan_rate == 1.0,
            "passes_control_ann_recall_gate": hnsw.control_mean_ann_recall_at_k >= 0.95,
            "passes_control_plan_gate": hnsw.control_hnsw_plan_rate == 1.0,
            "observations": [asdict(observation) for observation in hnsw.observations],
        },
    }


def _hnsw_queries(scale, count: int) -> list[tuple[str, str]]:
    queries: list[tuple[str, str]] = []
    for ordinal in range(count):
        record = scale[(ordinal * 97) % len(scale)]
        value = record.memory.value
        queries.append(
            (
                f"hnsw_profile_{ordinal:03d}",
                f"检索学习画像属性 {value.attribute} 对应的主题记录，忽略其他画像属性。",
            )
        )
    return queries


def _metric_status(score: MetricScore) -> dict[str, object]:
    definition = next(item for item in EXAM_MEM_METRIC_CATALOG if item.metric_id == score.metric_id)
    sufficiently_measured = score.sample_count >= definition.minimum_sample_count
    if score.value is None:
        passed: bool | None = None
    elif definition.target.operator is TargetOperator.GREATER_THAN_OR_EQUAL:
        passed = score.value >= definition.target.threshold
    elif definition.target.operator is TargetOperator.LESS_THAN_OR_EQUAL:
        passed = score.value <= definition.target.threshold
    else:
        passed = score.value == definition.target.threshold
    return {
        "target_operator": definition.target.operator.value,
        "target_threshold": definition.target.threshold,
        "minimum_sample_count": definition.minimum_sample_count,
        "sufficiently_measured": sufficiently_measured,
        "passed": passed if sufficiently_measured else None,
    }


def main() -> None:
    args = _parser().parse_args()
    report = asyncio.run(_run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"retrieval report: {args.output}", flush=True)


if __name__ == "__main__":
    main()
