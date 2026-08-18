"""Strict, storage-agnostic contracts for the Stage 07 practice workflow."""

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

from exam_mem.contracts import ErrorType, MemoryScope

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
KnowledgePointIds = Annotated[list[NonEmptyString], Field(min_length=1)]
ReasonCodes = Annotated[list[NonEmptyString], Field(min_length=1)]


class StrictPracticeModel(BaseModel):
    """Reject silent contract drift at practice Capability and Tool boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class PracticeState(str, Enum):
    """The seven persisted steps defined by the Stage 07 workflow."""

    IDLE = "IDLE"
    QUESTION_READY = "QUESTION_READY"
    ANSWER_RECEIVED = "ANSWER_RECEIVED"
    GRADED = "GRADED"
    DIAGNOSED = "DIAGNOSED"
    MEMORY_UPDATED = "MEMORY_UPDATED"
    RECOMMENDED = "RECOMMENDED"


class Question(StrictPracticeModel):
    """A question selected by Question Retriever without Memory side effects."""

    question_id: NonEmptyString
    stem: NonEmptyString
    knowledge_point_ids: KnowledgePointIds
    difficulty: Probability
    reference_answer: NonEmptyString
    grading_rubric: dict[str, JsonValue]

    @property
    def response_language(self) -> Literal["zh", "en"]:
        """Return the server-pinned language for learner-facing LLM output."""

        return "en" if self.grading_rubric.get("response_language") == "en" else "zh"


class AnswerSubmission(StrictPracticeModel):
    """One idempotent answer submission for a practice question."""

    practice_session_id: NonEmptyString
    question_id: NonEmptyString
    answer: NonEmptyString
    submitted_at: AwareDatetime
    idempotency_key: NonEmptyString


class GradeResult(StrictPracticeModel):
    """Structured grading evidence without long-term mastery inference."""

    correct: bool
    score: float
    matched_rubric_items: list[NonEmptyString]
    missed_rubric_items: list[NonEmptyString]
    evidence: list[NonEmptyString]
    grader_version: NonEmptyString

    @model_validator(mode="after")
    def validate_score_scale(self) -> GradeResult:
        # answer_grader_v1 did not constrain the scale. Keep those persisted
        # artifacts readable for audit, but require every new contract to use
        # the canonical probability scale.
        if self.grader_version != "answer_grader_v1" and not 0.0 <= self.score <= 1.0:
            raise ValueError("grade score must be between 0.0 and 1.0")
        return self


class GradeArtifactIdentity(StrictPracticeModel):
    """Strict identity for reusing grading computation across exam instances."""

    question_version: NonEmptyString
    normalized_answer_hash: NonEmptyString
    rubric_version: NonEmptyString
    grader_contract_version: NonEmptyString
    config_revision: NonEmptyString


class DiagnosisResult(StrictPracticeModel):
    """Structured diagnosis that cannot create free-form error types."""

    knowledge_point_ids: KnowledgePointIds
    error_type: ErrorType | None
    explanation: NonEmptyString
    confidence: Probability
    analyzer_version: NonEmptyString


class Recommendation(StrictPracticeModel):
    """A deterministic next-question recommendation with explicit provenance."""

    question_id: NonEmptyString
    target_knowledge_point_id: NonEmptyString
    target_difficulty: Probability
    reason_codes: ReasonCodes
    source_memory_ids: list[NonEmptyString]
    policy_version: NonEmptyString


class PracticeContext(StrictPracticeModel):
    """The exact Stage 07 Capability context persisted between workflow steps."""

    practice_session_id: NonEmptyString
    scope: MemoryScope
    current_question: Question | None = None
    submitted_answer: AnswerSubmission | None = None
    step_state: PracticeState = PracticeState.IDLE
    trace_id: NonEmptyString
    taxonomy_version: NonEmptyString = "math1_v1"
    question_catalog: tuple[Question, ...] = ()
    answered_question_ids: tuple[NonEmptyString, ...] = ()
    catalog_completed: bool = False

    @model_validator(mode="after")
    def validate_step_material(self) -> PracticeContext:
        catalog_ids = [question.question_id for question in self.question_catalog]
        if len(catalog_ids) != len(set(catalog_ids)):
            raise ValueError("question catalog IDs must be unique")
        if len(self.answered_question_ids) != len(set(self.answered_question_ids)):
            raise ValueError("answered question IDs must be unique")
        if catalog_ids and set(self.answered_question_ids) - set(catalog_ids):
            raise ValueError("answered question IDs must come from the pinned catalog")
        if self.current_question is not None and self.question_catalog:
            catalog_question = next(
                (
                    question
                    for question in self.question_catalog
                    if question.question_id == self.current_question.question_id
                ),
                None,
            )
            if catalog_question != self.current_question:
                raise ValueError("current question must match its catalog snapshot")
        if self.submitted_answer is not None:
            if self.current_question is None:
                raise ValueError("submitted answer requires current question")
            if self.submitted_answer.practice_session_id != self.practice_session_id:
                raise ValueError("submitted answer must match practice session")
            if self.submitted_answer.question_id != self.current_question.question_id:
                raise ValueError("submitted answer must match current question")

        if self.step_state is PracticeState.IDLE:
            if self.current_question is not None or self.submitted_answer is not None:
                raise ValueError("IDLE context must not contain a question or answer")
        elif self.step_state is PracticeState.QUESTION_READY:
            if self.current_question is None:
                raise ValueError("QUESTION_READY context requires current question")
            if self.submitted_answer is not None:
                raise ValueError("QUESTION_READY context must not contain an answer")
        elif self.current_question is None or self.submitted_answer is None:
            raise ValueError(f"{self.step_state.value} context requires question and answer")

        if self.catalog_completed:
            if not catalog_ids or set(catalog_ids) != set(self.answered_question_ids):
                raise ValueError(
                    "catalog_completed requires every pinned catalog question to be answered"
                )
            if self.step_state is not PracticeState.MEMORY_UPDATED:
                raise ValueError("catalog_completed is a terminal MEMORY_UPDATED checkpoint")

        return self
