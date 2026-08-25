"""Preregistered ExamMem quality gates and pure retrieval-v2 metric functions."""

from __future__ import annotations

from enum import Enum
import math
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from evaluation.retrieval.contracts import (
    RETRIEVAL_EMBEDDING_DIMENSION,
    NonEmptyString,
    RetrievalQuery,
    canonical_sha256,
)


class StrictMetricModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class MetricLayer(str, Enum):
    EXTRACTION = "extraction"
    SLOT = "slot"
    STORAGE = "storage"
    LIFECYCLE = "lifecycle"
    POLLUTION = "pollution"
    STATE = "state"
    PROJECTION = "projection"
    RETRIEVAL = "retrieval"
    ISOLATION = "isolation"
    RECOMMENDATION = "recommendation"
    ENGINEERING = "engineering"


class MetricDirection(str, Enum):
    HIGHER_IS_BETTER = "higher_is_better"
    LOWER_IS_BETTER = "lower_is_better"
    EXACT = "exact"


class TargetOperator(str, Enum):
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"


class MetricTarget(StrictMetricModel):
    operator: TargetOperator
    threshold: float


class MetricDefinition(StrictMetricModel):
    metric_id: NonEmptyString
    layer: MetricLayer
    display_name: NonEmptyString
    formula: NonEmptyString
    direction: MetricDirection
    target: MetricTarget
    minimum_sample_count: Annotated[int, Field(ge=1)]
    evaluation_condition: NonEmptyString


def _definition(
    metric_id: str,
    layer: MetricLayer,
    display_name: str,
    formula: str,
    direction: MetricDirection,
    operator: TargetOperator,
    threshold: float,
    *,
    minimum_sample_count: int,
    evaluation_condition: str,
) -> MetricDefinition:
    return MetricDefinition(
        metric_id=metric_id,
        layer=layer,
        display_name=display_name,
        formula=formula,
        direction=direction,
        target=MetricTarget(operator=operator, threshold=threshold),
        minimum_sample_count=minimum_sample_count,
        evaluation_condition=evaluation_condition,
    )


# This is a catalog, not an observed report. Threshold changes require a new
# protocol/catalog hash so that a data release cannot silently move its gates.
EXAM_MEM_METRIC_CATALOG: tuple[MetricDefinition, ...] = (
    _definition(
        "extraction.knowledge_point_accuracy",
        MetricLayer.EXTRACTION,
        "Knowledge-point extraction accuracy",
        "exact canonical knowledge-point predictions / extraction gold labels",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="raw answer text is evaluated before L1 structuring",
    ),
    _definition(
        "extraction.error_type_macro_f1",
        MetricLayer.EXTRACTION,
        "Error-type macro F1",
        "unweighted mean F1 across registered error types",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="all registered error types have positive gold support",
    ),
    _definition(
        "slot.precision",
        MetricLayer.SLOT,
        "Slot precision",
        "correct predicted slots / predicted slots",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="slot identity includes the complete four-dimensional scope",
    ),
    _definition(
        "slot.recall",
        MetricLayer.SLOT,
        "Slot recall",
        "correct predicted slots / gold slots",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="slot gold is fixed before lifecycle evaluation",
    ),
    _definition(
        "slot.f1",
        MetricLayer.SLOT,
        "Slot F1",
        "harmonic mean of slot precision and recall",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="slot precision and recall are both defined",
    ),
    _definition(
        "storage.acceptance_rate",
        MetricLayer.STORAGE,
        "Valid write acceptance",
        "accepted primary writes / valid primary write trials",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="fresh isolated PostgreSQL schema at migration head",
    ),
    _definition(
        "storage.idempotency_rate",
        MetricLayer.STORAGE,
        "Idempotent replay correctness",
        "accepted no-growth identical replays / replay trials",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="each successful primary write is replayed once",
    ),
    _definition(
        "storage.invariant_rejection_rate",
        MetricLayer.STORAGE,
        "Invariant rejection correctness",
        "rejected zero-growth invalid writes with surfaced errors / invalid write trials",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=30,
        evaluation_condition="invalid cases cover scope, provenance, version and vector invariants",
    ),
    _definition(
        "storage.vector_validity_rate",
        MetricLayer.STORAGE,
        "Stored vector validity",
        "finite non-zero 1024-dimensional vectors / embedding-enabled accepted writes",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="the configured document embedding provider returns 1024 dimensions",
    ),
    _definition(
        "storage.provenance_integrity_rate",
        MetricLayer.STORAGE,
        "L1-to-L2 provenance integrity",
        "round-tripped memories with exact ordered L1 provenance / accepted primary writes",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="L1 and L2 are written through the production transaction boundary",
    ),
    _definition(
        "lifecycle.operation_accuracy",
        MetricLayer.LIFECYCLE,
        "Lifecycle operation accuracy",
        "correct ADD/MERGE/SUPERSEDE/CONTESTED/NO_OP/INVALIDATE decisions / gold decisions",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.90,
        minimum_sample_count=200,
        evaluation_condition="versioned lifecycle trajectories with direct operation gold",
    ),
    _definition(
        "lifecycle.operation_macro_f1",
        MetricLayer.LIFECYCLE,
        "Lifecycle operation macro F1",
        "unweighted mean F1 across registered lifecycle operations",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="every registered lifecycle operation has positive gold support",
    ),
    _definition(
        "pollution.false_merge_rate",
        MetricLayer.POLLUTION,
        "False merge rate",
        "gold non-MERGE operations predicted as MERGE / gold non-MERGE operations",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        0.02,
        minimum_sample_count=200,
        evaluation_condition="semantic-neighbor and cross-scope anti-merge cases are present",
    ),
    _definition(
        "pollution.false_supersede_rate",
        MetricLayer.POLLUTION,
        "False supersede rate",
        "gold non-SUPERSEDE operations predicted as SUPERSEDE / gold non-SUPERSEDE operations",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        0.02,
        minimum_sample_count=200,
        evaluation_condition="temporary-error and low-confidence exception cases are present",
    ),
    _definition(
        "state.active_state_exact_match",
        MetricLayer.STATE,
        "Current-state exact match",
        "steps whose complete active/contested/archived/invalidated state equals gold / steps",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="comparison includes every version, not only active memory IDs",
    ),
    _definition(
        "state.stale_rate",
        MetricLayer.STATE,
        "Stale-memory rate",
        "unexpected active memories / predicted active memories",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        0.02,
        minimum_sample_count=200,
        evaluation_condition="gold state is query-independent and fixed before rollout",
    ),
    _definition(
        "state.duplicate_rate",
        MetricLayer.STATE,
        "Duplicate active-slot rate",
        "extra active memories in identical scope+slot / active memories",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        0.0,
        minimum_sample_count=200,
        evaluation_condition="all four scope dimensions participate in slot identity",
    ),
    _definition(
        "projection.rebuild_determinism_rate",
        MetricLayer.PROJECTION,
        "L3 rebuild determinism",
        "byte-identical repeated L3 rebuilds / rebuild trials",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="identical committed L1/L2 snapshot and projection version",
    ),
    _definition(
        "projection.source_watermark_integrity_rate",
        MetricLayer.PROJECTION,
        "L3 source-watermark integrity",
        "rebuilds whose watermark and derived state match committed sources / rebuilds",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=100,
        evaluation_condition="rebuild occurs after L1/L2 commit with no concurrent source mutation",
    ),
    _definition(
        "retrieval.recall_at_k",
        MetricLayer.RETRIEVAL,
        "Macro Recall@K",
        "mean(|top-K relevant IDs| / |relevant IDs|) over answerable queries",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.90,
        minimum_sample_count=200,
        evaluation_condition="at least 50 in-scope candidates and 10 hard negatives per query",
    ),
    _definition(
        "retrieval.weak_recall_at_k",
        MetricLayer.RETRIEVAL,
        "Legacy weak-point recall@K",
        "gold weak knowledge points present in top-K / gold weak knowledge points",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.90,
        minimum_sample_count=200,
        evaluation_condition="legacy continuity only; never substitutes for direct query-level relevance",
    ),
    _definition(
        "retrieval.precision_at_k",
        MetricLayer.RETRIEVAL,
        "Macro Precision@K",
        "mean(|top-K relevant IDs| / K) over answerable queries",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.15,
        minimum_sample_count=200,
        evaluation_condition=(
            "diagnostic gate for sparse gold (often one relevant item at K=5); "
            "Hit/MRR/nDCG remain the primary ranking gates"
        ),
    ),
    _definition(
        "retrieval.hit_at_k",
        MetricLayer.RETRIEVAL,
        "Hit@K",
        "answerable queries with any relevant ID in top-K / answerable queries",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="query K is pinned in the dataset rather than overridden by a report label",
    ),
    _definition(
        "retrieval.mrr",
        MetricLayer.RETRIEVAL,
        "Mean reciprocal rank",
        "mean reciprocal rank of the first relevant result over answerable queries",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="result order is preserved exactly as returned by PostgreSQL",
    ),
    _definition(
        "retrieval.ndcg_at_k",
        MetricLayer.RETRIEVAL,
        "Graded nDCG@K",
        "mean DCG@K / ideal DCG@K using query relevance grades 1..3",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="graded query-level relevance judgments are available",
    ),
    _definition(
        "retrieval.relevant_hard_negative_pairwise_accuracy",
        MetricLayer.RETRIEVAL,
        "Relevant-vs-hard-negative pairwise accuracy",
        "relevant/hard-negative judged pairs whose relevant cosine distance is lower / judged pairs",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.90,
        minimum_sample_count=2000,
        evaluation_condition="all relevant and hard-negative IDs have distances on the same query vector",
    ),
    _definition(
        "retrieval.no_answer_accuracy",
        MetricLayer.RETRIEVAL,
        "No-answer accuracy",
        "expected-no-answer queries returning zero memories / expected-no-answer queries",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=50,
        evaluation_condition=(
            "no-answer queries are separately preregistered; an always-top-K retriever is expected "
            "to fail this gate until it implements an abstention threshold"
        ),
    ),
    _definition(
        "retrieval.hard_negative_hit_rate",
        MetricLayer.RETRIEVAL,
        "Hard-negative hit rate",
        "queries returning any registered in-scope hard-negative ID / queries with hard negatives",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        0.05,
        minimum_sample_count=200,
        evaluation_condition="hard negatives are semantic neighbors rather than random unrelated records",
    ),
    _definition(
        "retrieval.archived_hit_rate",
        MetricLayer.RETRIEVAL,
        "Archived-result rate",
        "archived or invalidated returned IDs / returned IDs",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        0.0,
        minimum_sample_count=200,
        evaluation_condition="lifecycle filtering is enabled",
    ),
    _definition(
        "retrieval.archived_hit_at_k",
        MetricLayer.RETRIEVAL,
        "Legacy archived hit@K",
        "queries returning any archived memory in top-K / queries",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        0.0,
        minimum_sample_count=200,
        evaluation_condition="legacy query-level safety continuity metric",
    ),
    _definition(
        "isolation.cross_scope_leakage_rate",
        MetricLayer.ISOLATION,
        "Cross-scope leakage rate",
        "returned IDs outside exact user/exam/subject/namespace scope / returned IDs",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        0.0,
        minimum_sample_count=200,
        evaluation_condition="corpus contains collisions in every scope dimension",
    ),
    _definition(
        "isolation.scope_test_pass_rate",
        MetricLayer.ISOLATION,
        "Scope test pass rate",
        "scope-isolation cases with zero leakage / scope-isolation cases",
        MetricDirection.EXACT,
        TargetOperator.EQUAL,
        1.0,
        minimum_sample_count=50,
        evaluation_condition="each user/exam/subject/namespace dimension is varied independently",
    ),
    _definition(
        "retrieval.ann_recall_at_k",
        MetricLayer.RETRIEVAL,
        "HNSW ANN Recall@K",
        "mean(|production HNSW top-K intersect exact cosine top-K| / |exact top-K|)",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.95,
        minimum_sample_count=200,
        evaluation_condition="EXPLAIN proves HNSW use and exact scan is executed on the same snapshot",
    ),
    _definition(
        "engineering.production_p95_latency_ms",
        MetricLayer.ENGINEERING,
        "Production retrieval P95 latency",
        "nearest-rank P95 of production query latency in milliseconds",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        200.0,
        minimum_sample_count=200,
        evaluation_condition="warm-cache isolated PostgreSQL with at least 10,000 in-scope candidates",
    ),
    _definition(
        "engineering.exact_p95_latency_ms",
        MetricLayer.ENGINEERING,
        "Exact cosine reference P95 latency",
        "nearest-rank P95 of forced exact cosine latency in milliseconds",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        1000.0,
        minimum_sample_count=200,
        evaluation_condition="warm-cache isolated PostgreSQL with at least 10,000 in-scope candidates",
    ),
    _definition(
        "engineering.llm_call_count",
        MetricLayer.ENGINEERING,
        "LLM call count",
        "all attempted LLM calls including retries",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        100.0,
        minimum_sample_count=200,
        evaluation_condition="same LLM budget and retry policy across compared arms",
    ),
    _definition(
        "engineering.total_tokens",
        MetricLayer.ENGINEERING,
        "Total token use",
        "prompt tokens + completion tokens across all attempted calls",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        200000.0,
        minimum_sample_count=200,
        evaluation_condition="token accounting source is identical across compared arms",
    ),
    _definition(
        "engineering.mean_latency_ms",
        MetricLayer.ENGINEERING,
        "End-to-end mean latency",
        "mean end-to-end rollout latency in milliseconds",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        5000.0,
        minimum_sample_count=200,
        evaluation_condition="same hardware, warmup and concurrency across compared arms",
    ),
    _definition(
        "engineering.p95_latency_ms",
        MetricLayer.ENGINEERING,
        "End-to-end P95 latency",
        "nearest-rank P95 end-to-end rollout latency in milliseconds",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        10000.0,
        minimum_sample_count=200,
        evaluation_condition="same hardware, warmup and concurrency across compared arms",
    ),
    _definition(
        "engineering.memory_record_growth",
        MetricLayer.ENGINEERING,
        "Memory record growth",
        "logical memory records after rollout - records before rollout",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        2.0,
        minimum_sample_count=200,
        evaluation_condition="reported per learning event and interpreted with lifecycle correctness",
    ),
    _definition(
        "engineering.memory_byte_growth",
        MetricLayer.ENGINEERING,
        "Memory byte growth",
        "canonical JSON UTF-8 bytes after rollout - bytes before rollout",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        8192.0,
        minimum_sample_count=200,
        evaluation_condition="canonical serialization is identical across compared arms",
    ),
    _definition(
        "recommendation.knowledge_point_accuracy",
        MetricLayer.RECOMMENDATION,
        "Recommendation knowledge-point accuracy",
        "recommendations matching the complete gold knowledge-point set / opportunities",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="recommendation is scored separately from retrieval relevance",
    ),
    _definition(
        "recommendation.difficulty_match_rate",
        MetricLayer.RECOMMENDATION,
        "Recommendation difficulty match",
        "recommendations within the gold difficulty band / recommendations",
        MetricDirection.HIGHER_IS_BETTER,
        TargetOperator.GREATER_THAN_OR_EQUAL,
        0.85,
        minimum_sample_count=200,
        evaluation_condition="difficulty bands are fixed independently from generated recommendation text",
    ),
    _definition(
        "recommendation.over_review_rate",
        MetricLayer.RECOMMENDATION,
        "Over-review rate",
        "recommendations targeting stable mastered points / recommendations",
        MetricDirection.LOWER_IS_BETTER,
        TargetOperator.LESS_THAN_OR_EQUAL,
        0.05,
        minimum_sample_count=200,
        evaluation_condition="mastered state is frozen independently from the recommended action",
    ),
)


def metric_catalog_sha256() -> str:
    return canonical_sha256(
        [definition.model_dump(mode="json") for definition in EXAM_MEM_METRIC_CATALOG]
    )


class StorageTrialKind(str, Enum):
    PRIMARY_WRITE = "primary_write"
    IDEMPOTENT_REPLAY = "idempotent_replay"
    INVARIANT_REJECTION = "invariant_rejection"


class StorageWriteObservation(StrictMetricModel):
    trial_id: NonEmptyString
    trial_kind: StorageTrialKind
    write_index: Annotated[int, Field(ge=0)]
    memory_id: NonEmptyString
    provenance_event_ids: list[NonEmptyString]
    accepted: bool
    row_count_delta: int
    round_trip_equal: bool | None = None
    stored_provenance_event_ids: list[NonEmptyString] | None = None
    stored_embedding_dimensions: Annotated[int, Field(ge=0)] | None = None
    stored_embedding_all_finite: bool | None = None
    stored_embedding_nonzero: bool | None = None
    error_type: NonEmptyString | None = None
    constraint_name: NonEmptyString | None = None
    latency_ms: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def validate_trial_shape(self) -> StorageWriteObservation:
        if len(self.provenance_event_ids) != len(set(self.provenance_event_ids)):
            raise ValueError("provenance_event_ids must be unique")
        if self.stored_provenance_event_ids is not None and len(
            self.stored_provenance_event_ids
        ) != len(set(self.stored_provenance_event_ids)):
            raise ValueError("stored_provenance_event_ids must be unique")
        if self.accepted:
            if self.error_type is not None:
                raise ValueError("an accepted trial must not expose a write error")
        elif self.error_type is None:
            raise ValueError("a rejected trial must expose its error type")

        if self.trial_kind is StorageTrialKind.PRIMARY_WRITE and self.accepted:
            required = (
                self.round_trip_equal,
                self.stored_provenance_event_ids,
                self.stored_embedding_dimensions,
                self.stored_embedding_all_finite,
                self.stored_embedding_nonzero,
            )
            if self.row_count_delta != 1 or any(value is None for value in required):
                raise ValueError(
                    "an accepted primary write requires one new row and round-trip/vector evidence"
                )
        if self.trial_kind is StorageTrialKind.IDEMPOTENT_REPLAY and self.accepted:
            if self.row_count_delta != 0:
                raise ValueError("an accepted replay must not grow storage")
        return self


class RetrievalObservation(StrictMetricModel):
    query_id: NonEmptyString
    production_result_ids: list[NonEmptyString]
    exact_result_ids: list[NonEmptyString]
    production_distances: list[float] | None = None
    exact_distances: list[float] | None = None
    judged_memory_distances: dict[NonEmptyString, float] = Field(default_factory=dict)
    archived_or_invalidated_result_ids: list[NonEmptyString] = Field(default_factory=list)
    cross_scope_result_ids: list[NonEmptyString] = Field(default_factory=list)
    production_latency_ms: Annotated[float, Field(ge=0.0)]
    exact_latency_ms: Annotated[float, Field(ge=0.0)]
    candidate_count: Annotated[int, Field(ge=0)]
    hnsw_index_used: bool | None
    execution_plan: dict[str, JsonValue] | None = None

    @model_validator(mode="after")
    def validate_result_evidence(self) -> RetrievalObservation:
        if len(self.production_result_ids) != len(set(self.production_result_ids)):
            raise ValueError("production_result_ids must be unique and ranked")
        if len(self.exact_result_ids) != len(set(self.exact_result_ids)):
            raise ValueError("exact_result_ids must be unique and ranked")
        if self.production_distances is not None and len(self.production_distances) != len(
            self.production_result_ids
        ):
            raise ValueError("production distances must align with production result IDs")
        if self.exact_distances is not None and len(self.exact_distances) != len(
            self.exact_result_ids
        ):
            raise ValueError("exact distances must align with exact result IDs")
        production = set(self.production_result_ids)
        if not set(self.archived_or_invalidated_result_ids) <= production:
            raise ValueError("archived/invalidated IDs must be production results")
        if not set(self.cross_scope_result_ids) <= production:
            raise ValueError("cross-scope IDs must be production results")
        if len(self.production_result_ids) > self.candidate_count:
            raise ValueError("result count cannot exceed candidate_count")
        return self


class MetricScore(StrictMetricModel):
    metric_id: NonEmptyString
    value: float | None
    sample_count: Annotated[int, Field(ge=0)]
    reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_availability(self) -> MetricScore:
        if self.value is None:
            if self.sample_count != 0 or self.reason is None:
                raise ValueError("an unmeasured score requires zero samples and a reason")
        elif self.sample_count == 0 or self.reason is not None:
            raise ValueError("a measured score requires samples and no unavailable reason")
        return self


class LatencyDistribution(StrictMetricModel):
    mean_ms: Annotated[float, Field(ge=0.0)]
    p50_ms: Annotated[float, Field(ge=0.0)]
    p95_ms: Annotated[float, Field(ge=0.0)]
    p99_ms: Annotated[float, Field(ge=0.0)]
    max_ms: Annotated[float, Field(ge=0.0)]


class RetrievalMetricResult(StrictMetricModel):
    scores: tuple[MetricScore, ...]
    production_latency: LatencyDistribution
    exact_latency: LatencyDistribution


def _score(metric_id: str, values: list[float]) -> MetricScore:
    if not values:
        return MetricScore(
            metric_id=metric_id,
            value=None,
            sample_count=0,
            reason="no eligible observations",
        )
    return MetricScore(
        metric_id=metric_id, value=sum(values) / len(values), sample_count=len(values)
    )


def compute_storage_metrics(
    observations: list[StorageWriteObservation],
) -> tuple[MetricScore, ...]:
    """Compute storage quality independently from semantic ranking quality."""
    if not observations:
        raise ValueError("storage metrics require observations")
    primary = [item for item in observations if item.trial_kind is StorageTrialKind.PRIMARY_WRITE]
    replays = [
        item for item in observations if item.trial_kind is StorageTrialKind.IDEMPOTENT_REPLAY
    ]
    invalid = [
        item for item in observations if item.trial_kind is StorageTrialKind.INVARIANT_REJECTION
    ]

    return (
        _score("storage.acceptance_rate", [float(item.accepted) for item in primary]),
        _score(
            "storage.idempotency_rate",
            [
                float(item.accepted and item.row_count_delta == 0 and item.round_trip_equal is True)
                for item in replays
            ],
        ),
        _score(
            "storage.invariant_rejection_rate",
            [
                float(
                    not item.accepted and item.error_type is not None and item.row_count_delta == 0
                )
                for item in invalid
            ],
        ),
        _score(
            "storage.vector_validity_rate",
            [
                float(
                    item.stored_embedding_dimensions == RETRIEVAL_EMBEDDING_DIMENSION
                    and item.stored_embedding_all_finite is True
                    and item.stored_embedding_nonzero is True
                )
                for item in primary
                if item.accepted
            ],
        ),
        _score(
            "storage.provenance_integrity_rate",
            [
                float(
                    item.round_trip_equal is True
                    and item.stored_provenance_event_ids == item.provenance_event_ids
                )
                for item in primary
                if item.accepted
            ],
        ),
    )


def _nearest_rank(values: list[float], percentile: float) -> float:
    if not values:
        raise ValueError("latency distribution requires observations")
    ordered = sorted(values)
    return ordered[max(1, math.ceil(percentile * len(ordered))) - 1]


def _latency_distribution(values: list[float]) -> LatencyDistribution:
    return LatencyDistribution(
        mean_ms=sum(values) / len(values),
        p50_ms=_nearest_rank(values, 0.50),
        p95_ms=_nearest_rank(values, 0.95),
        p99_ms=_nearest_rank(values, 0.99),
        max_ms=max(values),
    )


def _ndcg(query: RetrievalQuery, result_ids: list[str]) -> float:
    gains = query.relevance_grades
    dcg = sum(
        (2 ** gains.get(memory_id, 0) - 1) / math.log2(rank + 1)
        for rank, memory_id in enumerate(result_ids[: query.top_k], start=1)
    )
    ideal = sorted(gains.values(), reverse=True)[: query.top_k]
    idcg = sum((2**grade - 1) / math.log2(rank + 1) for rank, grade in enumerate(ideal, start=1))
    return dcg / idcg


def compute_retrieval_metrics(
    queries: list[RetrievalQuery],
    observations: list[RetrievalObservation],
) -> RetrievalMetricResult:
    """Compute direct relevance, safety, ANN-agreement and latency metrics."""
    if not queries:
        raise ValueError("retrieval metrics require queries")
    query_by_id = {query.query_id: query for query in queries}
    observation_by_id = {item.query_id: item for item in observations}
    if len(query_by_id) != len(queries) or len(observation_by_id) != len(observations):
        raise ValueError("query and observation IDs must be unique")
    if set(query_by_id) != set(observation_by_id):
        raise ValueError("observations must cover the query set exactly")

    recall: list[float] = []
    precision: list[float] = []
    hit: list[float] = []
    reciprocal_rank: list[float] = []
    ndcg: list[float] = []
    no_answer: list[float] = []
    hard_negative: list[float] = []
    pairwise: list[float] = []
    ann_recall: list[float] = []
    total_results = archived_results = leaked_results = 0

    for query in queries:
        observation = observation_by_id[query.query_id]
        if len(observation.production_result_ids) > query.top_k:
            raise ValueError("production result count cannot exceed query top_k")
        if len(observation.exact_result_ids) > query.top_k:
            raise ValueError("exact result count cannot exceed query top_k")
        result_ids = observation.production_result_ids[: query.top_k]
        relevant = set(query.relevant_memory_ids)
        if query.expected_no_answer:
            no_answer.append(float(not result_ids))
        else:
            relevant_hits = relevant & set(result_ids)
            recall.append(len(relevant_hits) / len(relevant))
            precision.append(len(relevant_hits) / query.top_k)
            hit.append(float(bool(relevant_hits)))
            first_rank = next(
                (
                    rank
                    for rank, memory_id in enumerate(result_ids, start=1)
                    if memory_id in relevant
                ),
                None,
            )
            reciprocal_rank.append(0.0 if first_rank is None else 1.0 / first_rank)
            ndcg.append(_ndcg(query, result_ids))

        hard_negatives = set(query.hard_negative_memory_ids)
        hard_negative.append(float(bool(hard_negatives & set(result_ids))))
        judged_ids = relevant | hard_negatives
        missing_judgments = judged_ids - observation.judged_memory_distances.keys()
        if missing_judgments:
            raise ValueError("judged distances must cover every relevant and hard-negative ID")
        for relevant_id in relevant:
            for hard_negative_id in hard_negatives:
                pairwise.append(
                    float(
                        observation.judged_memory_distances[relevant_id]
                        < observation.judged_memory_distances[hard_negative_id]
                    )
                )
        total_results += len(result_ids)
        archived_results += len(observation.archived_or_invalidated_result_ids)
        leaked_results += len(observation.cross_scope_result_ids)

        if observation.hnsw_index_used is True and observation.exact_result_ids:
            exact_top_k = set(observation.exact_result_ids[: query.top_k])
            ann_recall.append(len(exact_top_k & set(result_ids)) / len(exact_top_k))

    production_latencies = [item.production_latency_ms for item in observations]
    exact_latencies = [item.exact_latency_ms for item in observations]
    scores = (
        _score("retrieval.recall_at_k", recall),
        _score("retrieval.precision_at_k", precision),
        _score("retrieval.hit_at_k", hit),
        _score("retrieval.mrr", reciprocal_rank),
        _score("retrieval.ndcg_at_k", ndcg),
        _score("retrieval.relevant_hard_negative_pairwise_accuracy", pairwise),
        _score("retrieval.no_answer_accuracy", no_answer),
        _score("retrieval.hard_negative_hit_rate", hard_negative),
        (
            MetricScore(
                metric_id="retrieval.archived_hit_rate",
                value=archived_results / total_results,
                sample_count=total_results,
            )
            if total_results
            else _score("retrieval.archived_hit_rate", [])
        ),
        (
            MetricScore(
                metric_id="isolation.cross_scope_leakage_rate",
                value=leaked_results / total_results,
                sample_count=total_results,
            )
            if total_results
            else _score("isolation.cross_scope_leakage_rate", [])
        ),
        _score("retrieval.ann_recall_at_k", ann_recall),
        MetricScore(
            metric_id="engineering.production_p95_latency_ms",
            value=_nearest_rank(production_latencies, 0.95),
            sample_count=len(production_latencies),
        ),
        MetricScore(
            metric_id="engineering.exact_p95_latency_ms",
            value=_nearest_rank(exact_latencies, 0.95),
            sample_count=len(exact_latencies),
        ),
    )
    return RetrievalMetricResult(
        scores=scores,
        production_latency=_latency_distribution(production_latencies),
        exact_latency=_latency_distribution(exact_latencies),
    )


__all__ = [
    "EXAM_MEM_METRIC_CATALOG",
    "LatencyDistribution",
    "MetricDefinition",
    "MetricDirection",
    "MetricLayer",
    "MetricScore",
    "MetricTarget",
    "RetrievalMetricResult",
    "RetrievalObservation",
    "StorageTrialKind",
    "StorageWriteObservation",
    "TargetOperator",
    "compute_retrieval_metrics",
    "compute_storage_metrics",
    "metric_catalog_sha256",
]
