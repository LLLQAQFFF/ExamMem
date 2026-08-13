"""Storage-agnostic Learning Memory contracts.

These models belong to ExamMem rather than DeepTutor's Native Memory.  They
describe the L1 event and L2 structured-memory boundary without selecting a
database implementation.
"""

from __future__ import annotations

from enum import Enum
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

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class StrictContractModel(BaseModel):
    """Base model that rejects silent contract drift."""

    model_config = ConfigDict(extra="forbid")


class MemoryNamespace(str, Enum):
    MASTERY = "mastery"
    ERROR_PATTERN = "error_pattern"
    PLAN = "plan"
    PROFILE = "profile"
    PREFERENCE = "preference"


class LifecycleOperation(str, Enum):
    ADD = "ADD"
    NO_OP = "NO_OP"
    MERGE = "MERGE"
    SUPERSEDE = "SUPERSEDE"
    INVALIDATE = "INVALIDATE"
    CONTESTED = "CONTESTED"


class LifecycleState(str, Enum):
    ACTIVE = "active"
    ARCHIVED = "archived"
    INVALIDATED = "invalidated"
    CONTESTED = "contested"


class ErrorType(str, Enum):
    CONCEPT_CONFUSION = "concept_confusion"
    FORMULA_MISUSE = "formula_misuse"
    CONDITION_OMISSION = "condition_omission"
    CALCULATION_ERROR = "calculation_error"
    REASONING_GAP = "reasoning_gap"
    READING_ERROR = "reading_error"
    CARELESS_ERROR = "careless_error"
    UNKNOWN = "unknown"


class LearningEventType(str, Enum):
    ANSWER_ATTEMPT = "answer_attempt"
    EXPLICIT_CORRECTION = "explicit_correction"
    PLAN_TRANSITION = "plan_transition"


class CorrectionSource(str, Enum):
    USER = "user"
    TEACHER = "teacher"
    GRADER_AUDIT = "grader_audit"


class PlanTransitionSource(str, Enum):
    USER = "user"
    SYSTEM = "system"
    PRACTICE_PROGRESS = "practice_progress"


class EvidenceQualityReason(str, Enum):
    LOW_GRADER_CONFIDENCE = "low_grader_confidence"
    AMBIGUOUS_RESPONSE = "ambiguous_response"
    EXTERNAL_DISRUPTION = "external_disruption"
    USER_REPORTED_EXCEPTION = "user_reported_exception"
    INSUFFICIENT_CONTEXT = "insufficient_context"


class MasteryLevel(str, Enum):
    LOW = "low"
    IMPROVING = "improving"
    HIGH = "high"
    MASTERED = "mastered"


class PlanStatus(str, Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class LearningContext(StrictContractModel):
    """The three-dimensional scope shared by an L1 learning event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    user_id: NonEmptyString
    exam_id: NonEmptyString
    subject_id: NonEmptyString


class MemoryScope(LearningContext):
    """The full four-dimensional isolation boundary used from L2 onward."""

    memory_namespace: MemoryNamespace


class ExplicitCorrection(StrictContractModel):
    target_memory_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    source: CorrectionSource
    statement: NonEmptyString


class PlanTransition(StrictContractModel):
    target_memory_id: NonEmptyString
    to_status: PlanStatus
    source: PlanTransitionSource
    reason: NonEmptyString


class EvidenceQuality(StrictContractModel):
    confidence: Probability = 1.0
    is_temporary_exception: bool = False
    reasons: list[EvidenceQualityReason] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_quality_explanation(self) -> EvidenceQuality:
        if len(self.reasons) != len(set(self.reasons)):
            raise ValueError("evidence quality reasons must be unique")
        if (self.confidence < 1.0 or self.is_temporary_exception) and not self.reasons:
            raise ValueError("non-default evidence quality requires at least one reason")
        return self


class LearningEvent(StrictContractModel):
    """Append-only L1 evidence produced by one learning interaction."""

    event_id: NonEmptyString
    idempotency_key: NonEmptyString
    event_type: LearningEventType = LearningEventType.ANSWER_ATTEMPT
    context: LearningContext
    session_id: NonEmptyString
    question_id: NonEmptyString | None = None
    knowledge_point_ids: list[NonEmptyString] = Field(default_factory=list)
    difficulty: Probability | None = None
    answer_correct: bool | None = None
    error_type: ErrorType | None = None
    error_detail: NonEmptyString | None = None
    evidence_quality: EvidenceQuality = Field(default_factory=EvidenceQuality)
    correction: ExplicitCorrection | None = None
    plan_transition: PlanTransition | None = None
    occurred_at: AwareDatetime

    @model_validator(mode="after")
    def validate_event_payload(self) -> LearningEvent:
        if self.event_type is LearningEventType.ANSWER_ATTEMPT:
            if self.question_id is None:
                raise ValueError("answer_attempt requires question_id")
            if not self.knowledge_point_ids:
                raise ValueError("answer_attempt requires knowledge_point_ids")
            if self.difficulty is None:
                raise ValueError("answer_attempt requires difficulty")
            if self.answer_correct is None:
                raise ValueError("answer_attempt requires answer_correct")
            if self.correction is not None or self.plan_transition is not None:
                raise ValueError("answer_attempt must not contain another event payload")
        elif self.event_type is LearningEventType.EXPLICIT_CORRECTION:
            if self.correction is None:
                raise ValueError("explicit_correction requires correction")
            if self.plan_transition is not None:
                raise ValueError("explicit_correction must not contain plan_transition")
            answer_payload = (
                self.question_id,
                self.difficulty,
                self.answer_correct,
                self.error_type,
                self.error_detail,
            )
            if any(value is not None for value in answer_payload):
                raise ValueError("explicit_correction must not contain answer-attempt fields")
        else:
            if self.plan_transition is None:
                raise ValueError("plan_transition requires plan_transition payload")
            if self.correction is not None:
                raise ValueError("plan_transition must not contain correction")
            answer_payload = (
                self.question_id,
                self.difficulty,
                self.answer_correct,
                self.error_type,
                self.error_detail,
            )
            if any(value is not None for value in answer_payload):
                raise ValueError("plan_transition must not contain answer-attempt fields")
        return self


class MasteryValue(StrictContractModel):
    type: Literal[MemoryNamespace.MASTERY] = MemoryNamespace.MASTERY
    level: MasteryLevel
    score: Probability


class ErrorPatternValue(StrictContractModel):
    type: Literal[MemoryNamespace.ERROR_PATTERN] = MemoryNamespace.ERROR_PATTERN
    error_type: ErrorType
    summary: NonEmptyString
    details: list[NonEmptyString] = Field(default_factory=list)


class PlanValue(StrictContractModel):
    type: Literal[MemoryNamespace.PLAN] = MemoryNamespace.PLAN
    goal: NonEmptyString
    status: PlanStatus
    progress: Probability
    due_at: AwareDatetime | None = None


class ProfileValue(StrictContractModel):
    type: Literal[MemoryNamespace.PROFILE] = MemoryNamespace.PROFILE
    attribute: NonEmptyString
    content: NonEmptyString


class PreferenceValue(StrictContractModel):
    type: Literal[MemoryNamespace.PREFERENCE] = MemoryNamespace.PREFERENCE
    attribute: NonEmptyString
    content: NonEmptyString


MemoryValue = Annotated[
    MasteryValue | ErrorPatternValue | PlanValue | ProfileValue | PreferenceValue,
    Field(discriminator="type"),
]


class MemoryUpdateCandidate(StrictContractModel):
    """A typed proposal derived from one L1 event before policy evaluation."""

    event_id: NonEmptyString
    scope: MemoryScope
    slot_key: NonEmptyString
    proposed_value: MemoryValue
    evidence: dict[str, JsonValue]

    @model_validator(mode="after")
    def validate_namespace(self) -> MemoryUpdateCandidate:
        if self.scope.memory_namespace.value != self.proposed_value.type.value:
            raise ValueError("candidate value type must match scope.memory_namespace")
        return self


class LifecycleDecision(StrictContractModel):
    """A policy decision; persistence applies it deterministically later."""

    operation: LifecycleOperation
    target_memory_ids: list[NonEmptyString]
    reason_code: NonEmptyString
    confidence: Probability
    policy_version: NonEmptyString

    @model_validator(mode="after")
    def validate_unique_targets(self) -> LifecycleDecision:
        if len(self.target_memory_ids) != len(set(self.target_memory_ids)):
            raise ValueError("target_memory_ids must not contain duplicates")
        return self


class LearningMemory(StrictContractModel):
    """A versioned and typed L2 Learning Memory record."""

    memory_id: NonEmptyString
    scope: MemoryScope
    slot_key: NonEmptyString
    value: MemoryValue
    confidence: Probability
    evidence_count: Annotated[int, Field(ge=1)]
    lifecycle_state: LifecycleState
    version: Annotated[int, Field(ge=1)]
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None
    superseded_by: NonEmptyString | None
    provenance: Annotated[list[NonEmptyString], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_namespace_and_interval(self) -> LearningMemory:
        if self.scope.memory_namespace.value != self.value.type.value:
            raise ValueError("memory value type must match scope.memory_namespace")
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValueError("valid_to must be greater than or equal to valid_from")
        if self.lifecycle_state is LifecycleState.ACTIVE and self.valid_to is not None:
            raise ValueError("active memory must not have valid_to")
        return self


class StudentModel(StrictContractModel):
    """A reproducible L3 projection derived only from L1/L2 state."""

    context: LearningContext
    weak_points: list[NonEmptyString]
    mastered_points: list[NonEmptyString]
    stable_error_patterns: list[NonEmptyString]
    active_plans: list[NonEmptyString]
    projection_version: Annotated[int, Field(ge=1)]
    source_watermark: NonEmptyString

    @model_validator(mode="after")
    def validate_mastery_partition(self) -> StudentModel:
        overlap = set(self.weak_points) & set(self.mastered_points)
        if overlap:
            raise ValueError("weak_points and mastered_points must be disjoint")
        return self


__all__ = [
    "CorrectionSource",
    "EvidenceQuality",
    "EvidenceQualityReason",
    "ErrorPatternValue",
    "ErrorType",
    "ExplicitCorrection",
    "LearningContext",
    "LearningEvent",
    "LearningEventType",
    "LearningMemory",
    "LifecycleDecision",
    "LifecycleOperation",
    "LifecycleState",
    "MasteryLevel",
    "MasteryValue",
    "MemoryNamespace",
    "MemoryScope",
    "MemoryUpdateCandidate",
    "MemoryValue",
    "PlanStatus",
    "PlanTransition",
    "PlanTransitionSource",
    "PlanValue",
    "PreferenceValue",
    "ProfileValue",
    "StudentModel",
]
