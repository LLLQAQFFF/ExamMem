"""Case-level rollout and reproducible experiment configuration contracts."""

from __future__ import annotations

from enum import Enum
import hashlib
import json
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from evaluation.contracts.case import (
    PROTOCOL_SEED,
    PROTOCOL_VERSION,
    DatasetSplit,
    NonEmptyString,
)
from evaluation.contracts.trace import RolloutTrace, TokenUsage, TraceError, TraceStatus
from exam_mem.backends import BackendMode

Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
GitCommitSha = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{7,40}$")]


class StrictRolloutModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _canonical_hash(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class ModelSettings(StrictRolloutModel):
    provider: NonEmptyString
    model: NonEmptyString
    temperature: Annotated[float, Field(ge=0.0, le=2.0)]
    top_p: Annotated[float, Field(gt=0.0, le=1.0)]
    max_output_tokens: Annotated[int, Field(ge=1)]
    additional_parameters: dict[str, JsonValue] = Field(default_factory=dict)


class RetrySettings(StrictRolloutModel):
    timeout_seconds: Annotated[float, Field(gt=0.0)]
    max_retries: Annotated[int, Field(ge=0)]
    backoff_seconds: list[Annotated[float, Field(ge=0.0)]]

    @model_validator(mode="after")
    def validate_backoff_count(self) -> RetrySettings:
        if len(self.backoff_seconds) != self.max_retries:
            raise ValueError("backoff_seconds must contain one delay per retry")
        return self


class FairnessConfig(StrictRolloutModel):
    """Inputs that must remain identical when comparing backend modes."""

    protocol_version: Literal[PROTOCOL_VERSION]
    dataset_split: DatasetSplit
    dataset_hash: Sha256Digest
    seed: Literal[PROTOCOL_SEED]
    model: ModelSettings
    retrieval_top_k: Annotated[int, Field(ge=1)]
    max_llm_calls_per_case: Annotated[int, Field(ge=1)] = 100
    retry: RetrySettings

    def canonical_hash(self) -> str:
        return _canonical_hash(self.model_dump(mode="json"))

    def differences(self, other: FairnessConfig) -> list[str]:
        left = self.model_dump(mode="json")
        right = other.model_dump(mode="json")
        return sorted(key for key in left.keys() | right.keys() if left.get(key) != right.get(key))

    def assert_fair_with(self, other: FairnessConfig) -> None:
        differences = self.differences(other)
        if differences:
            raise ValueError("baseline fairness settings differ: " + ", ".join(differences))


class ExperimentConfig(StrictRolloutModel):
    """A full experiment arm; backend_options must never contain secrets."""

    backend_mode: BackendMode
    policy_version: NonEmptyString
    backend_options: dict[str, JsonValue] = Field(default_factory=dict)
    fairness: FairnessConfig

    def canonical_hash(self) -> str:
        return _canonical_hash(self.model_dump(mode="json"))


class RolloutStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"


class RolloutResult(StrictRolloutModel):
    """A reproducible result for replaying one case with one backend mode."""

    run_id: NonEmptyString
    case_id: NonEmptyString
    config: ExperimentConfig
    config_hash: Sha256Digest
    fairness_hash: Sha256Digest
    code_sha: GitCommitSha
    started_at: AwareDatetime
    completed_at: AwareDatetime
    initial_snapshot: dict[str, JsonValue]
    final_snapshot: dict[str, JsonValue]
    traces: Annotated[list[RolloutTrace], Field(min_length=1)]
    tokens: TokenUsage
    llm_call_count: Annotated[int, Field(ge=0)]
    latency_ms: Annotated[float, Field(ge=0.0)]
    status: RolloutStatus
    errors: list[TraceError]

    @model_validator(mode="after")
    def validate_rollout_consistency(self) -> RolloutResult:
        if self.config_hash != self.config.canonical_hash():
            raise ValueError("config_hash does not match the canonical config")
        if self.fairness_hash != self.config.fairness.canonical_hash():
            raise ValueError("fairness_hash does not match the fairness config")
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")

        trace_ids = [trace.trace_id for trace in self.traces]
        if len(trace_ids) != len(set(trace_ids)):
            raise ValueError("trace_id must be unique within a rollout")
        if [trace.step_index for trace in self.traces] != list(range(len(self.traces))):
            raise ValueError("trace step_index values must be contiguous and ordered")

        for trace in self.traces:
            if trace.run_id != self.run_id or trace.case_id != self.case_id:
                raise ValueError("trace run_id/case_id must match its rollout")
            if trace.backend_mode is not self.config.backend_mode:
                raise ValueError("trace backend_mode must match the rollout config")
            if trace.protocol_version != self.config.fairness.protocol_version:
                raise ValueError("trace protocol_version must match the rollout config")

        prompt_tokens = sum(trace.tokens.prompt_tokens for trace in self.traces)
        completion_tokens = sum(trace.tokens.completion_tokens for trace in self.traces)
        if (
            self.tokens.prompt_tokens != prompt_tokens
            or self.tokens.completion_tokens != completion_tokens
        ):
            raise ValueError("rollout token totals must equal the sum of trace usage")
        if self.llm_call_count != sum(len(trace.llm_calls) for trace in self.traces):
            raise ValueError("llm_call_count must equal the number of traced LLM calls")

        if self.status is RolloutStatus.COMPLETED:
            if self.errors:
                raise ValueError("a completed rollout must not contain errors")
            if any(trace.status is not TraceStatus.COMPLETED for trace in self.traces):
                raise ValueError("a completed rollout requires every trace to complete")
        elif not self.errors and all(
            trace.status is TraceStatus.COMPLETED for trace in self.traces
        ):
            raise ValueError("a non-completed rollout must expose an error")
        return self


__all__ = [
    "ExperimentConfig",
    "FairnessConfig",
    "ModelSettings",
    "RetrySettings",
    "RolloutResult",
    "RolloutStatus",
]
