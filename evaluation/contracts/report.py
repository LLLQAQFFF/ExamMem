"""Machine-readable evaluation report contracts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from evaluation.contracts.case import (
    PROTOCOL_SEED,
    PROTOCOL_VERSION,
    DatasetSplit,
    NonEmptyString,
)
from evaluation.contracts.rollout import GitCommitSha, Sha256Digest
from evaluation.contracts.trace import TokenUsage
from exam_mem.backends import BackendMode


class StrictReportModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class MetricLayer(str, Enum):
    EXTRACTION = "extraction"
    SLOT = "slot"
    LIFECYCLE = "lifecycle"
    POLLUTION = "pollution"
    STATE = "state"
    ISOLATION = "isolation"
    RETRIEVAL = "retrieval"
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


class MetricStatus(str, Enum):
    MEASURED = "measured"
    UNDEFINED = "undefined"
    NOT_APPLICABLE = "not_applicable"


class MetricTarget(StrictReportModel):
    """A preregistered goal, never an observed result."""

    operator: TargetOperator
    threshold: float
    tolerance: Annotated[float, Field(ge=0.0)] = 0.0


class MetricDefinition(StrictReportModel):
    metric_id: NonEmptyString
    layer: MetricLayer
    display_name: NonEmptyString
    formula: NonEmptyString
    direction: MetricDirection
    target: MetricTarget | None = None


class MetricObservation(StrictReportModel):
    metric_id: NonEmptyString
    status: MetricStatus
    value: float | None
    numerator: float | None = None
    denominator: float | None = None
    sample_count: Annotated[int, Field(ge=0)]
    reason: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_status_payload(self) -> MetricObservation:
        if self.status is MetricStatus.MEASURED:
            if self.value is None:
                raise ValueError("a measured metric must contain a value")
            if self.sample_count == 0:
                raise ValueError("a measured metric must have a positive sample_count")
        else:
            if self.value is not None:
                raise ValueError("an undefined metric must not contain a value")
            if self.reason is None:
                raise ValueError("an undefined metric must explain why it has no value")
        return self


class RunOutcomeCounts(StrictReportModel):
    total: Annotated[int, Field(ge=1)]
    completed: Annotated[int, Field(ge=0)]
    partial: Annotated[int, Field(ge=0)]
    failed: Annotated[int, Field(ge=0)]
    timeout: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_total(self) -> RunOutcomeCounts:
        observed_total = self.completed + self.partial + self.failed + self.timeout
        if self.total != observed_total:
            raise ValueError("run outcome counts must sum to total")
        return self


class LatencySummary(StrictReportModel):
    mean_ms: Annotated[float, Field(ge=0.0)]
    p95_ms: Annotated[float, Field(ge=0.0)]
    max_ms: Annotated[float, Field(ge=0.0)]

    @model_validator(mode="after")
    def validate_bounds(self) -> LatencySummary:
        if self.mean_ms > self.max_ms or self.p95_ms > self.max_ms:
            raise ValueError("mean and p95 latency must not exceed max latency")
        return self


class MemoryGrowthSummary(StrictReportModel):
    records_before: Annotated[int, Field(ge=0)]
    records_after: Annotated[int, Field(ge=0)]
    record_growth: int
    byte_growth: int

    @model_validator(mode="after")
    def validate_record_growth(self) -> MemoryGrowthSummary:
        if self.record_growth != self.records_after - self.records_before:
            raise ValueError("record_growth must equal records_after - records_before")
        return self


class CostSummary(StrictReportModel):
    llm_call_count: Annotated[int, Field(ge=0)]
    tokens: TokenUsage
    estimated_cost_usd: Annotated[float, Field(ge=0.0)] | None
    estimated_cost_reason: NonEmptyString | None = None
    latency: LatencySummary
    memory_growth: MemoryGrowthSummary

    @model_validator(mode="after")
    def validate_cost_availability(self) -> CostSummary:
        if self.estimated_cost_usd is None and self.estimated_cost_reason is None:
            raise ValueError("unavailable estimated cost must explain why")
        if self.estimated_cost_usd is not None and self.estimated_cost_reason is not None:
            raise ValueError("measured estimated cost must not contain an unavailable reason")
        return self


class BackendEvaluation(StrictReportModel):
    backend_mode: BackendMode
    config_hash: Sha256Digest
    fairness_hash: Sha256Digest
    run_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    outcomes: RunOutcomeCounts
    metrics: Annotated[list[MetricObservation], Field(min_length=1)]
    cost: CostSummary

    @model_validator(mode="after")
    def validate_backend_aggregation(self) -> BackendEvaluation:
        if len(self.run_ids) != len(set(self.run_ids)):
            raise ValueError("run_ids must be unique within a backend result")
        if len(self.run_ids) != self.outcomes.total:
            raise ValueError("run_ids count must equal outcome total")
        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("metric_id must be unique within a backend result")
        return self


class EvaluationReport(StrictReportModel):
    """Comparable results for all five preregistered backend modes."""

    report_id: NonEmptyString
    protocol_version: Literal[PROTOCOL_VERSION]
    dataset_split: DatasetSplit
    dataset_hash: Sha256Digest
    fairness_hash: Sha256Digest
    seed: Literal[PROTOCOL_SEED]
    gold_revision: Annotated[int, Field(ge=1)]
    code_sha: GitCommitSha
    generated_at: AwareDatetime
    metric_definitions: Annotated[list[MetricDefinition], Field(min_length=1)]
    backend_results: Annotated[list[BackendEvaluation], Field(min_length=1)]
    warnings: list[NonEmptyString]

    @model_validator(mode="after")
    def validate_report_comparability(self) -> EvaluationReport:
        definition_ids = [definition.metric_id for definition in self.metric_definitions]
        if len(definition_ids) != len(set(definition_ids)):
            raise ValueError("metric definitions must have unique metric_id values")
        required_metrics = set(definition_ids)

        observed_modes = [result.backend_mode for result in self.backend_results]
        if len(observed_modes) != len(set(observed_modes)):
            raise ValueError("backend modes must be unique within a report")
        if set(observed_modes) != set(BackendMode):
            raise ValueError("a report must include all five backend modes")

        expected_case_count = self.backend_results[0].outcomes.total
        for result in self.backend_results:
            if result.fairness_hash != self.fairness_hash:
                raise ValueError("backend fairness_hash must match the report")
            if result.outcomes.total != expected_case_count:
                raise ValueError("all backend modes must evaluate the same case count")
            if {metric.metric_id for metric in result.metrics} != required_metrics:
                raise ValueError("every backend must report every registered metric")
        return self


__all__ = [
    "BackendEvaluation",
    "CostSummary",
    "EvaluationReport",
    "LatencySummary",
    "MemoryGrowthSummary",
    "MetricDefinition",
    "MetricDirection",
    "MetricLayer",
    "MetricObservation",
    "MetricStatus",
    "MetricTarget",
    "RunOutcomeCounts",
    "TargetOperator",
]
