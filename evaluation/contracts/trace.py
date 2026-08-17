"""Observed, step-level trace contract for evaluation rollouts."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from evaluation.contracts.case import (
    PROTOCOL_VERSION,
    ActionType,
    ExtractedFields,
    NonEmptyString,
    VersionRelation,
)
from exam_mem.backends import BackendMode
from exam_mem.contracts import (
    LearningEvent,
    LifecycleDecision,
)


class StrictTraceModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TraceStatus(str, Enum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMEOUT = "timeout"


class TraceStage(str, Enum):
    RECORD_EVENT = "record_event"
    EXTRACT = "extract"
    NORMALIZE = "normalize"
    RETRIEVE_CANDIDATES = "retrieve_candidates"
    DECIDE = "decide"
    APPLY = "apply"
    PROJECT = "project"
    RETRIEVE = "retrieve"
    RECOMMEND = "recommend"
    SNAPSHOT = "snapshot"


class TraceError(StrictTraceModel):
    stage: TraceStage
    error_type: NonEmptyString
    message: NonEmptyString
    retryable: bool
    attempt: Annotated[int, Field(ge=1)]


class TokenUsage(StrictTraceModel):
    prompt_tokens: Annotated[int, Field(ge=0)]
    completion_tokens: Annotated[int, Field(ge=0)]
    total_tokens: Annotated[int, Field(ge=0)]

    @model_validator(mode="after")
    def validate_total(self) -> TokenUsage:
        if self.total_tokens != self.prompt_tokens + self.completion_tokens:
            raise ValueError("total_tokens must equal prompt_tokens + completion_tokens")
        return self


class LLMCallTrace(StrictTraceModel):
    call_id: NonEmptyString
    purpose: NonEmptyString
    provider: NonEmptyString
    model: NonEmptyString
    token_usage: TokenUsage
    latency_ms: Annotated[float, Field(ge=0.0)]
    succeeded: bool
    error: TraceError | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> LLMCallTrace:
        if self.succeeded and self.error is not None:
            raise ValueError("a successful LLM call must not contain an error")
        if not self.succeeded and self.error is None:
            raise ValueError("a failed LLM call must contain an error")
        return self


class MemoryStateTrace(StrictTraceModel):
    active_memory_ids: list[NonEmptyString]
    archived_memory_ids: list[NonEmptyString]
    invalidated_memory_ids: list[NonEmptyString]
    contested_memory_ids: list[NonEmptyString]
    version_relations: list[VersionRelation]

    @model_validator(mode="after")
    def validate_disjoint_states(self) -> MemoryStateTrace:
        groups = (
            self.active_memory_ids,
            self.archived_memory_ids,
            self.invalidated_memory_ids,
            self.contested_memory_ids,
        )
        all_ids = [memory_id for group in groups for memory_id in group]
        if len(all_ids) != len(set(all_ids)):
            raise ValueError("a memory_id must appear in exactly one lifecycle state")
        return self


class RecommendationTrace(StrictTraceModel):
    action_type: ActionType
    knowledge_point_ids: list[NonEmptyString]
    difficulty: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    reason_code: NonEmptyString | None = None


class RolloutTrace(StrictTraceModel):
    """One JSONL-safe observation for one event step in one case rollout."""

    run_id: NonEmptyString
    case_id: NonEmptyString
    trace_id: NonEmptyString
    step_id: NonEmptyString
    step_index: Annotated[int, Field(ge=0)]
    backend_mode: BackendMode
    protocol_version: Literal[PROTOCOL_VERSION]
    policy_version: NonEmptyString
    started_at: AwareDatetime
    completed_at: AwareDatetime
    input_event: LearningEvent
    extracted_fields: ExtractedFields | None
    normalized_slot_key: NonEmptyString | None
    candidate_ids: list[NonEmptyString]
    lifecycle_decision: LifecycleDecision | None
    state_before: MemoryStateTrace | None
    state_after: MemoryStateTrace | None
    retrieval_ids: list[NonEmptyString]
    recommendation: RecommendationTrace | None
    llm_calls: list[LLMCallTrace]
    tokens: TokenUsage
    latency_ms: Annotated[float, Field(ge=0.0)]
    status: TraceStatus
    errors: list[TraceError]

    @model_validator(mode="after")
    def validate_observation_consistency(self) -> RolloutTrace:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")

        if self.status is TraceStatus.COMPLETED:
            if self.errors:
                raise ValueError("a completed trace must not contain errors")
            if self.state_before is None or self.state_after is None:
                raise ValueError("a completed trace must contain before/after state")
        elif not self.errors:
            raise ValueError("a non-completed trace must contain at least one error")

        prompt_tokens = sum(call.token_usage.prompt_tokens for call in self.llm_calls)
        completion_tokens = sum(call.token_usage.completion_tokens for call in self.llm_calls)
        if (
            self.tokens.prompt_tokens != prompt_tokens
            or self.tokens.completion_tokens != completion_tokens
        ):
            raise ValueError("trace token totals must equal the sum of LLM call usage")
        return self


__all__ = [
    "LLMCallTrace",
    "MemoryStateTrace",
    "RecommendationTrace",
    "RolloutTrace",
    "TokenUsage",
    "TraceError",
    "TraceStage",
    "TraceStatus",
]
