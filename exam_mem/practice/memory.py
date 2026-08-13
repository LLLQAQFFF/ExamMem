"""Practice-facing Memory Reader/Writer and deterministic candidate derivation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from exam_mem.backends.protocol import MemoryBackend
from exam_mem.contracts import (
    ErrorPatternValue,
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)
from exam_mem.domain.normalizer import UNKNOWN_KNOWLEDGE_POINT_ID
from exam_mem.domain.slot_key import (
    build_error_pattern_slot_key,
    build_mastery_slot_key,
)
from exam_mem.domain.taxonomy import Taxonomy
from exam_mem.lifecycle import ProjectionRefreshRequest

from .contracts import DiagnosisResult, GradeResult


@runtime_checkable
class ProjectionRequestSource(Protocol):
    def take_projection_requests(self) -> tuple[ProjectionRefreshRequest, ...]: ...


class ProjectionRefreshExecutor(Protocol):
    async def refresh(self, request: ProjectionRefreshRequest) -> object: ...


@dataclass(frozen=True, slots=True)
class MemoryWriteResult:
    decisions: tuple[LifecycleDecision, ...]
    projection_requests: tuple[ProjectionRefreshRequest, ...]


class MemoryWriter:
    """Use one selected backend; the caller owns database commit/rollback."""

    def __init__(
        self,
        backend: MemoryBackend,
        *,
        projection_refresher: ProjectionRefreshExecutor | None = None,
    ) -> None:
        self._backend = backend
        self._projection_refresher = projection_refresher

    async def write(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> MemoryWriteResult:
        await self._backend.record_event(event)
        decisions = await self._backend.update(event, candidates)
        requests = (
            self._backend.take_projection_requests()
            if isinstance(self._backend, ProjectionRequestSource)
            else ()
        )
        return MemoryWriteResult(
            decisions=tuple(decisions),
            projection_requests=requests,
        )

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None:
        if not result.projection_requests:
            return
        if self._projection_refresher is None:
            raise RuntimeError("lifecycle projection refresh executor is not configured")
        for request in result.projection_requests:
            await self._projection_refresher.refresh(request)


class MemoryReader:
    """Read only through the selected backend and never bypass Scope."""

    def __init__(self, backend: MemoryBackend) -> None:
        self._backend = backend

    async def query_state(self, context: LearningContext) -> StudentModel | None:
        return await self._backend.query_state(context)

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]:
        return await self._backend.retrieve(scope, query, top_k)

    async def snapshot(self, context: LearningContext) -> dict:
        return await self._backend.snapshot(context)


class PracticeMemoryCandidateBuilder:
    """Derive typed candidates from graded evidence without lifecycle decisions."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy

    def build(
        self,
        *,
        event: LearningEvent,
        grade: GradeResult,
        diagnosis: DiagnosisResult,
    ) -> list[MemoryUpdateCandidate]:
        if tuple(event.knowledge_point_ids) != tuple(diagnosis.knowledge_point_ids):
            raise ValueError("event and diagnosis knowledge points must match")
        if event.answer_correct is not grade.correct:
            raise ValueError("event answer_correct must match grade result")
        if event.error_type is not diagnosis.error_type:
            raise ValueError("event error_type must match diagnosis result")

        candidates: list[MemoryUpdateCandidate] = []
        for knowledge_point_id in diagnosis.knowledge_point_ids:
            if knowledge_point_id == UNKNOWN_KNOWLEDGE_POINT_ID:
                continue
            scope = MemoryScope(
                **event.context.model_dump(),
                memory_namespace=MemoryNamespace.MASTERY,
            )
            candidates.append(
                MemoryUpdateCandidate(
                    event_id=event.event_id,
                    scope=scope,
                    slot_key=build_mastery_slot_key(
                        self._taxonomy,
                        knowledge_point_id,
                    ),
                    proposed_value=MasteryValue(
                        level=(MasteryLevel.HIGH if grade.correct else MasteryLevel.LOW),
                        score=1.0 if grade.correct else 0.0,
                    ),
                    evidence=_candidate_evidence(grade, diagnosis),
                )
            )

            if not grade.correct and diagnosis.error_type is not None:
                error_scope = scope.model_copy(
                    update={"memory_namespace": MemoryNamespace.ERROR_PATTERN}
                )
                candidates.append(
                    MemoryUpdateCandidate(
                        event_id=event.event_id,
                        scope=error_scope,
                        slot_key=build_error_pattern_slot_key(
                            self._taxonomy,
                            knowledge_point_id,
                            diagnosis.error_type,
                        ),
                        proposed_value=ErrorPatternValue(
                            error_type=diagnosis.error_type,
                            summary=diagnosis.explanation,
                            details=list(grade.evidence),
                        ),
                        evidence=_candidate_evidence(grade, diagnosis),
                    )
                )
        return candidates


def _candidate_evidence(
    grade: GradeResult,
    diagnosis: DiagnosisResult,
) -> dict:
    return {
        "grade_correct": grade.correct,
        "grade_score": grade.score,
        "grader_version": grade.grader_version,
        "matched_rubric_items": list(grade.matched_rubric_items),
        "missed_rubric_items": list(grade.missed_rubric_items),
        "diagnosis_confidence": diagnosis.confidence,
        "analyzer_version": diagnosis.analyzer_version,
    }


__all__ = [
    "MemoryReader",
    "MemoryWriteResult",
    "MemoryWriter",
    "PracticeMemoryCandidateBuilder",
    "ProjectionRefreshExecutor",
    "ProjectionRequestSource",
]
