"""Versioned evaluation protocol configuration schema."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from evaluation.contracts.case import (
    PROTOCOL_SEED,
    PROTOCOL_VERSION,
    DatasetSplit,
    NonEmptyString,
    ScenarioType,
)
from evaluation.contracts.report import MetricDefinition
from exam_mem.backends import BackendMode
from exam_mem.contracts import LifecycleOperation

REQUIRED_METRIC_IDS = frozenset(
    {
        "extraction.knowledge_point_accuracy",
        "extraction.error_type_macro_f1",
        "slot.precision",
        "slot.recall",
        "slot.f1",
        "lifecycle.operation_accuracy",
        "lifecycle.operation_macro_f1",
        "pollution.false_merge_rate",
        "pollution.false_supersede_rate",
        "state.active_state_exact_match",
        "state.stale_rate",
        "state.duplicate_rate",
        "isolation.cross_scope_leakage_rate",
        "isolation.scope_test_pass_rate",
        "retrieval.weak_recall_at_k",
        "retrieval.archived_hit_at_k",
        "recommendation.knowledge_point_accuracy",
        "recommendation.difficulty_match_rate",
        "recommendation.over_review_rate",
        "engineering.llm_call_count",
        "engineering.total_tokens",
        "engineering.mean_latency_ms",
        "engineering.p95_latency_ms",
        "engineering.memory_record_growth",
        "engineering.memory_byte_growth",
    }
)


class StrictProtocolModel(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class SubjectArea(str, Enum):
    LINEAR_ALGEBRA = "linear_algebra"
    PROBABILITY_THEORY = "probability_theory"


class FairnessField(str, Enum):
    DATASET_HASH = "dataset_hash"
    EVENT_ORDER = "event_order"
    MODEL_PROVIDER = "model.provider"
    MODEL_NAME = "model.name"
    MODEL_PARAMETERS = "model.parameters"
    RETRIEVAL_TOP_K = "retrieval_top_k"
    LLM_CALL_BUDGET = "max_llm_calls_per_case"
    TIMEOUT = "retry.timeout_seconds"
    MAX_RETRIES = "retry.max_retries"
    RETRY_BACKOFF = "retry.backoff_seconds"


class DatasetSplitRule(StrictProtocolModel):
    split: DatasetSplit
    case_count: Annotated[int, Field(ge=1)]
    contributes_to_formal_score: bool
    frozen: bool


class ScenarioQuota(StrictProtocolModel):
    scenario_type: ScenarioType
    protocol_check_count: Literal[2]


class GoldPolicy(StrictProtocolModel):
    insufficient_evidence_operations: Annotated[list[LifecycleOperation], Field(min_length=1)]
    single_error_must_not_downgrade_stable_mastery: Literal[True]
    direct_conflict_requires_same_scope_and_slot: Literal[True]
    archived_excluded_from_recommendation_context: Literal[True]
    operation_reason_and_event_evidence_required: Literal[True]
    minimum_blind_review_interval_days: Annotated[int, Field(ge=1)]
    gold_revision_must_increase: Literal[True]


class CostAccounting(StrictProtocolModel):
    count_all_llm_attempts_including_retries: Literal[True]
    token_source: NonEmptyString
    latency_clock: Literal["monotonic"]
    latency_scope: Literal["end_to_end_rollout"]
    p95_method: Literal["nearest_rank"]
    pricing_snapshot_required: Literal[True]
    currency: Literal["USD"]
    memory_record_unit: Literal["logical_record"]
    memory_byte_unit: Literal["canonical_json_utf8"]


class OptimizationGate(StrictProtocolModel):
    core_metric_minimum_improvement_points: Literal[0.05]
    protected_metric_maximum_decline_points: Literal[0.02]
    maximum_cost_increase_ratio: Literal[0.20]


class ProtocolConfig(StrictProtocolModel):
    protocol_version: Literal[PROTOCOL_VERSION]
    seed: Literal[PROTOCOL_SEED]
    subject_areas: list[SubjectArea]
    backend_modes: list[BackendMode]
    dataset_splits: list[DatasetSplitRule]
    scenario_quotas: list[ScenarioQuota]
    fairness_fields: list[FairnessField]
    gold_policy: GoldPolicy
    metrics: list[MetricDefinition]
    cost_accounting: CostAccounting
    optimization_gate: OptimizationGate

    @model_validator(mode="after")
    def validate_frozen_protocol(self) -> ProtocolConfig:
        if set(self.subject_areas) != set(SubjectArea):
            raise ValueError("protocol must include both registered subject areas")
        if len(self.subject_areas) != len(set(self.subject_areas)):
            raise ValueError("subject areas must be unique")

        if set(self.backend_modes) != set(BackendMode):
            raise ValueError("protocol must include all five backend modes")
        if len(self.backend_modes) != len(set(self.backend_modes)):
            raise ValueError("backend modes must be unique")

        split_by_name = {rule.split: rule for rule in self.dataset_splits}
        if len(split_by_name) != len(self.dataset_splits):
            raise ValueError("dataset split rules must be unique")
        if set(split_by_name) != set(DatasetSplit):
            raise ValueError("protocol must define protocol_check, dev and test splits")
        expected_split_counts = {
            DatasetSplit.PROTOCOL_CHECK: 24,
            DatasetSplit.DEV: 40,
            DatasetSplit.TEST: 80,
        }
        if any(
            split_by_name[split].case_count != count
            for split, count in expected_split_counts.items()
        ):
            raise ValueError("dataset split counts must remain 24/40/80")
        if split_by_name[DatasetSplit.PROTOCOL_CHECK].contributes_to_formal_score:
            raise ValueError("protocol_check must not contribute to formal scores")
        if not split_by_name[DatasetSplit.TEST].frozen:
            raise ValueError("the test split must remain frozen")

        quota_by_scenario = {
            quota.scenario_type: quota.protocol_check_count for quota in self.scenario_quotas
        }
        if len(quota_by_scenario) != len(self.scenario_quotas):
            raise ValueError("scenario quotas must be unique")
        if set(quota_by_scenario) != set(ScenarioType):
            raise ValueError("protocol must cover all twelve scenario types")
        if sum(quota_by_scenario.values()) != 24:
            raise ValueError("protocol_check scenario quotas must sum to 24")

        if set(self.fairness_fields) != set(FairnessField):
            raise ValueError("all registered fairness fields must be frozen")
        if len(self.fairness_fields) != len(set(self.fairness_fields)):
            raise ValueError("fairness fields must be unique")

        metric_ids = [metric.metric_id for metric in self.metrics]
        if len(metric_ids) != len(set(metric_ids)):
            raise ValueError("protocol metric_id values must be unique")
        if set(metric_ids) != REQUIRED_METRIC_IDS:
            raise ValueError("protocol must define the complete registered metric set")
        return self


__all__ = [
    "CostAccounting",
    "DatasetSplitRule",
    "FairnessField",
    "GoldPolicy",
    "OptimizationGate",
    "ProtocolConfig",
    "REQUIRED_METRIC_IDS",
    "ScenarioQuota",
    "SubjectArea",
]
