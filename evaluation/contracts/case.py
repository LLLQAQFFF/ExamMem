"""Versioned schema for an ExamMem evaluation case."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

from exam_mem.contracts import (
    ErrorType,
    EvidenceQuality,
    ExplicitCorrection,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    LifecycleOperation,
    MemoryScope,
    MemoryValue,
    PlanTransition,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PROTOCOL_VERSION = "evaluation_protocol_v1"
PROTOCOL_SEED = 20260806


class StrictEvaluationModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScenarioType(str, Enum):
    SEMANTIC_DUPLICATE = "semantic_duplicate"
    COMPLEMENTARY_EVIDENCE = "complementary_evidence"
    MASTERY_IMPROVEMENT = "mastery_improvement"
    MASTERY_DECLINE = "mastery_decline"
    ACCIDENTAL_ERROR = "accidental_error"
    STABLE_WEAKNESS = "stable_weakness"
    EXPLICIT_CORRECTION = "explicit_correction"
    LOW_CONFIDENCE_EXCEPTION = "low_confidence_exception"
    PLAN_TRANSITION = "plan_transition"
    MULTI_VALUE_ERROR_PATTERN = "multi_value_error_pattern"
    CROSS_SCOPE_INTERFERENCE = "cross_scope_interference"
    LONG_RANGE_CHANGE = "long_range_change"


class DatasetSplit(str, Enum):
    PROTOCOL_CHECK = "protocol_check"
    DEV = "dev"
    TEST = "test"


class VersionRelationType(str, Enum):
    SUPERSEDED_BY = "superseded_by"


class ActionType(str, Enum):
    RECOMMEND_KNOWLEDGE_POINT = "recommend_knowledge_point"
    RECOMMEND_REVIEW = "recommend_review"
    AVOID_OVER_REVIEW = "avoid_over_review"
    NO_ACTION = "no_action"


class ExtractedFields(StrictEvaluationModel):
    event_type: LearningEventType = LearningEventType.ANSWER_ATTEMPT
    knowledge_point_ids: list[NonEmptyString] = Field(default_factory=list)
    answer_correct: bool | None = None
    error_type: ErrorType | None = None
    error_detail: NonEmptyString | None = None
    evidence_quality: EvidenceQuality = Field(default_factory=EvidenceQuality)
    correction: ExplicitCorrection | None = None
    plan_transition: PlanTransition | None = None

    @model_validator(mode="after")
    def validate_extracted_payload(self) -> ExtractedFields:
        if self.event_type is LearningEventType.ANSWER_ATTEMPT:
            if not self.knowledge_point_ids:
                raise ValueError("answer_attempt extraction requires knowledge_point_ids")
            if self.answer_correct is None:
                raise ValueError("answer_attempt extraction requires answer_correct")
            if self.correction is not None or self.plan_transition is not None:
                raise ValueError("answer_attempt extraction must not contain another event payload")
        elif self.event_type is LearningEventType.EXPLICIT_CORRECTION:
            if self.correction is None:
                raise ValueError("explicit_correction extraction requires correction")
            if self.plan_transition is not None:
                raise ValueError("explicit_correction extraction must not contain plan_transition")
            answer_payload = (
                self.answer_correct,
                self.error_type,
                self.error_detail,
            )
            if any(value is not None for value in answer_payload):
                raise ValueError("explicit_correction extraction must not contain answer fields")
        else:
            if self.plan_transition is None:
                raise ValueError("plan_transition extraction requires plan_transition payload")
            if self.correction is not None:
                raise ValueError("plan_transition extraction must not contain correction")
            answer_payload = (
                self.answer_correct,
                self.error_type,
                self.error_detail,
            )
            if any(value is not None for value in answer_payload):
                raise ValueError("plan_transition extraction must not contain answer fields")
        return self


class GoldOperation(StrictEvaluationModel):
    operation_id: NonEmptyString
    step_id: NonEmptyString
    event_id: NonEmptyString
    extracted_fields: ExtractedFields
    canonical_knowledge_point_ids: Annotated[list[NonEmptyString], Field(min_length=1)]
    slot_key: NonEmptyString
    candidate_memory_ids: list[NonEmptyString]
    operation: LifecycleOperation
    target_memory_ids: list[NonEmptyString]
    result_memory_id: NonEmptyString | None
    expected_result_value: MemoryValue | None = None
    reason_code: NonEmptyString
    evidence_event_ids: Annotated[list[NonEmptyString], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_result_semantics(self) -> GoldOperation:
        producing_operations = {
            LifecycleOperation.ADD,
            LifecycleOperation.MERGE,
            LifecycleOperation.SUPERSEDE,
            LifecycleOperation.CONTESTED,
        }
        if self.operation in producing_operations and self.result_memory_id is None:
            raise ValueError(f"{self.operation.value} requires result_memory_id")
        if self.operation not in producing_operations and self.result_memory_id is not None:
            raise ValueError(f"{self.operation.value} must not produce result_memory_id")
        if self.expected_result_value is not None and self.result_memory_id is None:
            raise ValueError("expected_result_value requires result_memory_id")
        return self


class VersionRelation(StrictEvaluationModel):
    predecessor_memory_id: NonEmptyString
    successor_memory_id: NonEmptyString
    relation: VersionRelationType


class GoldState(StrictEvaluationModel):
    step_id: NonEmptyString
    active_memory_ids: list[NonEmptyString]
    archived_memory_ids: list[NonEmptyString]
    invalidated_memory_ids: list[NonEmptyString]
    contested_memory_ids: list[NonEmptyString]
    version_relations: list[VersionRelation]

    @model_validator(mode="after")
    def validate_disjoint_states(self) -> GoldState:
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


class EvaluationQuery(StrictEvaluationModel):
    query_id: NonEmptyString
    after_step_id: NonEmptyString
    scope: MemoryScope
    text: NonEmptyString
    top_k: Annotated[int, Field(ge=1)]


class GoldAction(StrictEvaluationModel):
    step_id: NonEmptyString
    action_type: ActionType
    knowledge_point_ids: list[NonEmptyString]
    reason_code: NonEmptyString


class CaseMetadata(StrictEvaluationModel):
    split: DatasetSplit
    seed: Literal[PROTOCOL_SEED]
    gold_revision: Annotated[int, Field(ge=1)]
    policy_parameters: dict[str, JsonValue] = Field(default_factory=dict)


class EvaluationCase(StrictEvaluationModel):
    protocol_version: Literal[PROTOCOL_VERSION]
    case_id: NonEmptyString
    scenario_type: ScenarioType
    initial_memory: list[LearningMemory]
    events: Annotated[list[LearningEvent], Field(min_length=1)]
    gold_operations: Annotated[list[GoldOperation], Field(min_length=1)]
    gold_states: Annotated[list[GoldState], Field(min_length=1)]
    queries: list[EvaluationQuery]
    gold_actions: Annotated[list[GoldAction], Field(min_length=1)]
    metadata: CaseMetadata

    @model_validator(mode="after")
    def validate_step_coverage(self) -> EvaluationCase:
        event_ids = [event.event_id for event in self.events]
        operation_ids = [operation.operation_id for operation in self.gold_operations]
        state_step_ids = [state.step_id for state in self.gold_states]
        action_step_ids = [action.step_id for action in self.gold_actions]

        if len(event_ids) != len(set(event_ids)):
            raise ValueError("event_id must be unique within a case")
        if len(operation_ids) != len(set(operation_ids)):
            raise ValueError("operation_id must be unique within a case")

        step_event_pairs: list[tuple[str, str]] = []
        seen_steps: set[str] = set()
        for operation in self.gold_operations:
            if not step_event_pairs or operation.step_id != step_event_pairs[-1][0]:
                if operation.step_id in seen_steps:
                    raise ValueError("operations for one step must be contiguous")
                seen_steps.add(operation.step_id)
                step_event_pairs.append((operation.step_id, operation.event_id))
            elif operation.event_id != step_event_pairs[-1][1]:
                raise ValueError("operations in one step must reference the same event")

        operation_step_ids = [step_id for step_id, _ in step_event_pairs]
        operation_event_ids = [event_id for _, event_id in step_event_pairs]
        if operation_event_ids != event_ids:
            raise ValueError("gold operation steps must cover events in event order")
        if state_step_ids != operation_step_ids:
            raise ValueError("gold states must cover operation steps in order")
        if action_step_ids != operation_step_ids:
            raise ValueError("gold actions must cover operation steps in order")

        known_steps = set(operation_step_ids)
        if any(query.after_step_id not in known_steps for query in self.queries):
            raise ValueError("query after_step_id must reference a known step")
        return self


__all__ = [
    "ActionType",
    "CaseMetadata",
    "DatasetSplit",
    "EvaluationCase",
    "EvaluationQuery",
    "ExtractedFields",
    "GoldAction",
    "GoldOperation",
    "GoldState",
    "PROTOCOL_SEED",
    "PROTOCOL_VERSION",
    "ScenarioType",
    "VersionRelation",
    "VersionRelationType",
]
