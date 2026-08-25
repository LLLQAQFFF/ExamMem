"""Exact-cosine and production PostgreSQL retrieval evaluation."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from time import perf_counter
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from deeptutor.plugins.host_services import validate_embedding_batch
from evaluation.retrieval.contracts import RetrievalDataset, RetrievalQuery
from evaluation.retrieval.metrics import (
    RetrievalMetricResult,
    RetrievalObservation,
    compute_retrieval_metrics,
)
from exam_mem.contracts import LearningMemory, LifecycleState, MemoryScope
from exam_mem.storage.memory_repository import PostgresLearningMemoryRepository


class EmbeddingClient(Protocol):
    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class SemanticRetrievalResult:
    observations: tuple[RetrievalObservation, ...]
    metrics: RetrievalMetricResult
    elapsed_ms: float


@dataclass(frozen=True, slots=True)
class HnswProfileObservation:
    query_id: str
    production_result_ids: tuple[str, ...]
    exact_result_ids: tuple[str, ...]
    control_result_ids: tuple[str, ...]
    production_exact_agreement_at_k: float
    production_hnsw_index_used: bool
    control_ann_recall_at_k: float
    control_hnsw_index_used: bool
    production_latency_ms: float
    exact_latency_ms: float
    control_latency_ms: float
    execution_plan: dict[str, Any]
    control_execution_plan: dict[str, Any]


@dataclass(frozen=True, slots=True)
class HnswProfileResult:
    observations: tuple[HnswProfileObservation, ...]
    production_mean_exact_agreement_at_k: float
    production_hnsw_plan_rate: float
    measured_production_ann_recall_at_k: float | None
    control_mean_ann_recall_at_k: float
    control_hnsw_plan_rate: float
    production_p95_latency_ms: float
    exact_p95_latency_ms: float
    control_p95_latency_ms: float
    candidate_count: int
    elapsed_ms: float


async def evaluate_semantic_retrieval(
    connection: AsyncConnection,
    dataset: RetrievalDataset,
    embedding_client: EmbeddingClient,
    *,
    embedding_batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> SemanticRetrievalResult:
    """Evaluate every frozen query without applying a score threshold or Gold reranking."""
    started = perf_counter()
    vectors = await _embed_queries(
        dataset.queries,
        embedding_client,
        batch_size=embedding_batch_size,
    )
    observations: list[RetrievalObservation] = []
    for index, (query, vector) in enumerate(zip(dataset.queries, vectors, strict=True)):
        (
            exact_ids,
            exact_distances,
            judged_distances,
            candidate_count,
            exact_ms,
        ) = await _exact_query(connection, query, vector)
        production_memories, production_distances, plan, production_ms = await _production_query(
            connection,
            query.scope,
            vector,
            query.top_k,
        )
        production_ids = [memory.memory_id for memory in production_memories]
        archived = [
            memory.memory_id
            for memory in production_memories
            if memory.lifecycle_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
        ]
        leaked = [memory.memory_id for memory in production_memories if memory.scope != query.scope]
        observations.append(
            RetrievalObservation(
                query_id=query.query_id,
                production_result_ids=production_ids,
                exact_result_ids=exact_ids,
                production_distances=production_distances,
                exact_distances=exact_distances,
                judged_memory_distances=judged_distances,
                archived_or_invalidated_result_ids=archived,
                cross_scope_result_ids=leaked,
                production_latency_ms=production_ms,
                exact_latency_ms=exact_ms,
                candidate_count=candidate_count,
                hnsw_index_used=_plan_uses_hnsw(plan),
                execution_plan=plan,
            )
        )
        if progress is not None:
            progress(index + 1, len(dataset.queries))
    return SemanticRetrievalResult(
        observations=tuple(observations),
        metrics=compute_retrieval_metrics(dataset.queries, observations),
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )


async def evaluate_hnsw_profile(
    connection: AsyncConnection,
    *,
    scope: MemoryScope,
    queries: Sequence[tuple[str, str]],
    embedding_client: EmbeddingClient,
    top_k: int = 5,
    embedding_batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> HnswProfileResult:
    """Compare production HNSW results with exact cosine on one large Scope."""
    if len(queries) < 1:
        raise ValueError("HNSW profile requires at least one query")
    started = perf_counter()
    synthetic_queries = [
        RetrievalQuery(
            query_id=query_id,
            query_family="hnsw_profile",
            text=query_text,
            scope=scope,
            top_k=top_k,
            relevant_memory_ids=["profile_only_placeholder"],
            relevance_grades={"profile_only_placeholder": 3},
            hard_negative_memory_ids=[],
            must_not_return_memory_ids=[],
        )
        for query_id, query_text in queries
    ]
    vectors = await _embed_queries(
        synthetic_queries,
        embedding_client,
        batch_size=embedding_batch_size,
    )
    observations: list[HnswProfileObservation] = []
    candidate_count: int | None = None
    for index, ((query_id, _), vector) in enumerate(zip(queries, vectors, strict=True)):
        exact_ids, _, _, observed_candidates, exact_ms = await _exact_scope_query(
            connection,
            scope,
            vector,
            top_k,
            judged_ids=(),
        )
        production_memories, _, plan, production_ms = await _production_query(
            connection,
            scope,
            vector,
            top_k,
        )
        production_ids = [memory.memory_id for memory in production_memories]
        control_ids, control_plan, control_ms = await _hnsw_control_query(
            connection,
            scope,
            vector,
            top_k,
        )
        exact_set = set(exact_ids)
        production_agreement = len(exact_set & set(production_ids)) / len(exact_set)
        control_ann_recall = len(exact_set & set(control_ids)) / len(exact_set)
        observations.append(
            HnswProfileObservation(
                query_id=query_id,
                production_result_ids=tuple(production_ids),
                exact_result_ids=tuple(exact_ids),
                control_result_ids=tuple(control_ids),
                production_exact_agreement_at_k=production_agreement,
                production_hnsw_index_used=_plan_uses_hnsw(plan),
                control_ann_recall_at_k=control_ann_recall,
                control_hnsw_index_used=_plan_uses_hnsw(control_plan),
                production_latency_ms=production_ms,
                exact_latency_ms=exact_ms,
                control_latency_ms=control_ms,
                execution_plan=plan,
                control_execution_plan=control_plan,
            )
        )
        candidate_count = observed_candidates
        if progress is not None:
            progress(index + 1, len(queries))
    production_latencies = [item.production_latency_ms for item in observations]
    exact_latencies = [item.exact_latency_ms for item in observations]
    control_latencies = [item.control_latency_ms for item in observations]
    production_plan_rate = sum(item.production_hnsw_index_used for item in observations) / len(
        observations
    )
    return HnswProfileResult(
        observations=tuple(observations),
        production_mean_exact_agreement_at_k=sum(
            item.production_exact_agreement_at_k for item in observations
        )
        / len(observations),
        production_hnsw_plan_rate=production_plan_rate,
        measured_production_ann_recall_at_k=(
            sum(item.production_exact_agreement_at_k for item in observations) / len(observations)
            if production_plan_rate == 1.0
            else None
        ),
        control_mean_ann_recall_at_k=sum(item.control_ann_recall_at_k for item in observations)
        / len(observations),
        control_hnsw_plan_rate=sum(item.control_hnsw_index_used for item in observations)
        / len(observations),
        production_p95_latency_ms=_nearest_rank(production_latencies, 0.95),
        exact_p95_latency_ms=_nearest_rank(exact_latencies, 0.95),
        control_p95_latency_ms=_nearest_rank(control_latencies, 0.95),
        candidate_count=candidate_count or 0,
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )


async def _embed_queries(
    queries: Sequence[RetrievalQuery],
    embedding_client: EmbeddingClient,
    *,
    batch_size: int,
) -> list[list[float]]:
    if batch_size < 1:
        raise ValueError("embedding_batch_size must be greater than or equal to 1")
    vectors: list[list[float]] = []
    for start in range(0, len(queries), batch_size):
        batch = queries[start : start + batch_size]
        generated = await embedding_client.embed(
            [query.text for query in batch],
            input_type="search_query",
        )
        validated = validate_embedding_batch(
            generated,
            expected_count=len(batch),
            binding="exam_mem_semantic_retrieval_v2",
            start_index=start,
        )
        if any(len(vector) != 1024 for vector in validated):
            raise ValueError("query embeddings must have exactly 1024 dimensions")
        vectors.extend(validated)
    return vectors


async def _exact_query(
    connection: AsyncConnection,
    query: RetrievalQuery,
    vector: Sequence[float],
) -> tuple[list[str], list[float], dict[str, float], int, float]:
    judged_ids = tuple(dict.fromkeys([*query.relevant_memory_ids, *query.hard_negative_memory_ids]))
    return await _exact_scope_query(
        connection,
        query.scope,
        vector,
        query.top_k,
        judged_ids=judged_ids,
    )


async def _exact_scope_query(
    connection: AsyncConnection,
    scope: MemoryScope,
    vector: Sequence[float],
    top_k: int,
    *,
    judged_ids: Sequence[str],
) -> tuple[list[str], list[float], dict[str, float], int, float]:
    await connection.rollback()
    vector_literal = _vector_literal(vector)
    async with connection.begin():
        await connection.execute(text("set local enable_indexscan = off"))
        await connection.execute(text("set local enable_indexonlyscan = off"))
        await connection.execute(text("set local enable_bitmapscan = off"))
        parameters = _scope_parameters(scope, vector_literal=vector_literal, top_k=top_k)
        candidate_count = int(await connection.scalar(text(_CANDIDATE_COUNT_SQL), parameters) or 0)
        started = perf_counter()
        rows = (await connection.execute(text(_RANKED_SQL), parameters)).all()
        elapsed_ms = (perf_counter() - started) * 1000.0
        judged: dict[str, float] = {}
        if judged_ids:
            judged_rows = (
                await connection.execute(
                    text(_JUDGED_SQL),
                    {**parameters, "judged_ids": list(judged_ids)},
                )
            ).all()
            judged = {str(memory_id): float(distance) for memory_id, distance in judged_rows}
    return (
        [str(memory_id) for memory_id, _ in rows],
        [float(distance) for _, distance in rows],
        judged,
        candidate_count,
        elapsed_ms,
    )


async def _production_query(
    connection: AsyncConnection,
    scope: MemoryScope,
    vector: Sequence[float],
    top_k: int,
) -> tuple[list[LearningMemory], list[float], dict[str, Any], float]:
    await connection.rollback()
    repository = PostgresLearningMemoryRepository(connection)
    started = perf_counter()
    memories = await repository.find_similar(scope, vector, top_k)
    elapsed_ms = (perf_counter() - started) * 1000.0
    result_ids = [memory.memory_id for memory in memories]
    parameters = _scope_parameters(
        scope,
        vector_literal=_vector_literal(vector),
        top_k=top_k,
    )
    distance_rows = (
        (
            await connection.execute(
                text(_DISTANCES_FOR_IDS_SQL),
                {**parameters, "result_ids": result_ids},
            )
        ).all()
        if result_ids
        else []
    )
    distance_by_id = {str(memory_id): float(distance) for memory_id, distance in distance_rows}
    plan_value = await connection.scalar(text(_EXPLAIN_SQL), parameters)
    plan = _normalize_plan(plan_value)
    await connection.rollback()
    return memories, [distance_by_id[memory_id] for memory_id in result_ids], plan, elapsed_ms


async def _hnsw_control_query(
    connection: AsyncConnection,
    scope: MemoryScope,
    vector: Sequence[float],
    top_k: int,
) -> tuple[list[str], dict[str, Any], float]:
    """Forced diagnostic proving the HNSW index is usable; never a production score."""
    await connection.rollback()
    parameters = _scope_parameters(
        scope,
        vector_literal=_vector_literal(vector),
        top_k=top_k,
    )
    async with connection.begin():
        await connection.execute(text("set local enable_seqscan = off"))
        await connection.execute(text("set local enable_bitmapscan = off"))
        await connection.execute(text("set local enable_sort = off"))
        started = perf_counter()
        rows = (await connection.execute(text(_HNSW_CONTROL_SQL), parameters)).all()
        elapsed_ms = (perf_counter() - started) * 1000.0
        plan_value = await connection.scalar(text(_HNSW_CONTROL_EXPLAIN_SQL), parameters)
        plan = _normalize_plan(plan_value)
    return [str(memory_id) for (memory_id,) in rows], plan, elapsed_ms


def _scope_parameters(
    scope: MemoryScope,
    *,
    vector_literal: str,
    top_k: int,
) -> dict[str, object]:
    return {
        "user_id": scope.user_id,
        "exam_id": scope.exam_id,
        "subject_id": scope.subject_id,
        "memory_namespace": scope.memory_namespace.value,
        "query_vector": vector_literal,
        "top_k": top_k,
    }


def _vector_literal(vector: Sequence[float]) -> str:
    if len(vector) != 1024 or any(not math.isfinite(value) for value in vector):
        raise ValueError("query vector must be finite and 1024-dimensional")
    return "[" + ",".join(format(float(value), ".17g") for value in vector) + "]"


def _normalize_plan(value: object) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    if isinstance(value, dict):
        return value
    raise ValueError("PostgreSQL EXPLAIN did not return a JSON plan")


def _plan_uses_hnsw(plan: object) -> bool:
    if isinstance(plan, dict):
        if plan.get("Index Name") == "ix_learning_memories_content_embedding_hnsw":
            return True
        return any(_plan_uses_hnsw(value) for value in plan.values())
    if isinstance(plan, list):
        return any(_plan_uses_hnsw(value) for value in plan)
    return False


def _nearest_rank(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    return ordered[max(1, math.ceil(percentile * len(ordered))) - 1]


_SCOPE_WHERE = """
user_id = :user_id
and exam_id = :exam_id
and subject_id = :subject_id
and memory_namespace = :memory_namespace
and lifecycle_state in ('active', 'contested')
and content_embedding is not null
"""
_CANDIDATE_COUNT_SQL = f"select count(*) from learning_memories where {_SCOPE_WHERE}"
_RANKED_SQL = f"""
select memory_id, content_embedding <=> cast(:query_vector as vector) as distance
from learning_memories
where {_SCOPE_WHERE}
order by content_embedding <=> cast(:query_vector as vector), memory_id
limit :top_k
"""
_JUDGED_SQL = f"""
select memory_id, content_embedding <=> cast(:query_vector as vector) as distance
from learning_memories
where {_SCOPE_WHERE} and memory_id = any(cast(:judged_ids as text[]))
"""
_DISTANCES_FOR_IDS_SQL = f"""
select memory_id, content_embedding <=> cast(:query_vector as vector) as distance
from learning_memories
where {_SCOPE_WHERE} and memory_id = any(cast(:result_ids as text[]))
"""
_EXPLAIN_SQL = "explain (analyze, buffers, format json) " + _RANKED_SQL
_HNSW_CONTROL_SQL = f"""
select memory_id
from learning_memories
where {_SCOPE_WHERE}
order by content_embedding <=> cast(:query_vector as vector)
limit :top_k
"""
_HNSW_CONTROL_EXPLAIN_SQL = "explain (analyze, buffers, format json) " + _HNSW_CONTROL_SQL


__all__ = [
    "HnswProfileObservation",
    "HnswProfileResult",
    "SemanticRetrievalResult",
    "evaluate_hnsw_profile",
    "evaluate_semantic_retrieval",
]
