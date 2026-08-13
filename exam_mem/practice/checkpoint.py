"""Strict persistent checkpoint contract for the seven-state practice flow."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from exam_mem.backends import BackendMode
from exam_mem.config import backend_side_effects
from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LifecycleDecision,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import ProjectionRefreshRequest

from .contracts import (
    DiagnosisResult,
    GradeArtifactIdentity,
    GradeResult,
    PracticeContext,
    PracticeState,
    Question,
    Recommendation,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PracticeRuntimeSnapshot(BaseModel):
    """Immutable effective configuration pinned when an exam instance starts."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    config_revision: NonEmptyString
    backend_mode: BackendMode
    side_effects: tuple[NonEmptyString, ...]

    @model_validator(mode="after")
    def validate_side_effects(self) -> PracticeRuntimeSnapshot:
        if self.side_effects != backend_side_effects(self.backend_mode):
            raise ValueError("runtime side effects must be derived from backend_mode")
        return self


class PracticeWorkflowCheckpoint(BaseModel):
    """Last durable output of each completed workflow step.

    The payload contains validated structured results, never raw LLM output.
    ``step_state`` remains on the last fully completed state when a later step
    fails, so replay resumes without repeating grading or Memory writes.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    checkpoint_key: NonEmptyString
    context: PracticeContext
    runtime_snapshot: PracticeRuntimeSnapshot | None = None
    grade_result: GradeResult | None = None
    grade_artifact_identity: GradeArtifactIdentity | None = None
    grade_reused_from_checkpoint: NonEmptyString | None = None
    mapped_knowledge_point_ids: tuple[NonEmptyString, ...] = ()
    diagnosis_result: DiagnosisResult | None = None
    learning_event: LearningEvent | None = None
    memory_candidates: tuple[MemoryUpdateCandidate, ...] = ()
    lifecycle_decisions: tuple[LifecycleDecision, ...] = ()
    projection_requests: tuple[ProjectionRefreshRequest, ...] = ()
    projection_refreshed: bool = False
    memory_write_completed: bool = False
    recommendation: Recommendation | None = None
    recommended_question: Question | None = None

    @model_validator(mode="after")
    def validate_progress_material(self) -> PracticeWorkflowCheckpoint:
        state = self.context.step_state
        if (
            state
            in {
                PracticeState.GRADED,
                PracticeState.DIAGNOSED,
                PracticeState.MEMORY_UPDATED,
                PracticeState.RECOMMENDED,
            }
            and self.grade_result is None
        ):
            raise ValueError(f"{state.value} checkpoint requires grade_result")
        if self.grade_reused_from_checkpoint is not None and self.grade_result is None:
            raise ValueError("grade reuse source requires grade_result")

        if state in {
            PracticeState.DIAGNOSED,
            PracticeState.MEMORY_UPDATED,
            PracticeState.RECOMMENDED,
        }:
            if not self.mapped_knowledge_point_ids:
                raise ValueError(f"{state.value} checkpoint requires mapped knowledge points")
            if self.diagnosis_result is None:
                raise ValueError(f"{state.value} checkpoint requires diagnosis_result")
            if self.learning_event is None:
                raise ValueError(f"{state.value} checkpoint requires learning_event")
        if state in {PracticeState.MEMORY_UPDATED, PracticeState.RECOMMENDED}:
            if not self.memory_write_completed:
                raise ValueError(f"{state.value} checkpoint requires completed Memory write")

        if state is PracticeState.RECOMMENDED:
            if self.recommendation is None or self.recommended_question is None:
                raise ValueError("RECOMMENDED checkpoint requires recommendation and question")

        if self.projection_refreshed and self.projection_requests:
            raise ValueError("refreshed checkpoint must not retain projection requests")
        self._validate_cross_links()
        return self

    def _validate_cross_links(self) -> None:
        context = self.context
        event = self.learning_event
        if event is not None:
            if context.submitted_answer is None:
                raise ValueError("checkpoint learning_event requires submitted answer")
            if event.idempotency_key != context.submitted_answer.idempotency_key:
                raise ValueError("learning_event must match submission idempotency_key")
            if event.context != LearningContext.model_validate(
                context.scope.model_dump(exclude={"memory_namespace"})
            ):
                raise ValueError("learning_event context must match practice scope")
        if self.diagnosis_result is not None and self.mapped_knowledge_point_ids:
            if set(self.diagnosis_result.knowledge_point_ids) - set(
                self.mapped_knowledge_point_ids
            ):
                raise ValueError("diagnosis knowledge points must come from mapped IDs")
        if event is not None:
            if any(candidate.event_id != event.event_id for candidate in self.memory_candidates):
                raise ValueError("memory candidate event_id must match learning_event")


def checkpoint_key_for_context(context: PracticeContext) -> str:
    """Use the frozen answer idempotency key, or one initial-session key."""
    if context.submitted_answer is None:
        return "start"
    return f"answer:{context.submitted_answer.idempotency_key}"


__all__ = [
    "PracticeRuntimeSnapshot",
    "PracticeWorkflowCheckpoint",
    "checkpoint_key_for_context",
]
