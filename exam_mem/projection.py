"""Deterministic L3 Student Model projection from Learning L1 and L2."""

from __future__ import annotations

from collections.abc import Sequence

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    StudentModel,
)
from exam_mem.domain.slot_key import validate_slot_key


class ProjectionInputError(ValueError):
    """Raised when rebuild inputs do not form one traceable context snapshot."""


def project_student_model(
    *,
    context: LearningContext,
    events: Sequence[LearningEvent],
    memories: Sequence[LearningMemory],
    projection_version: int,
    source_event_watermark: str,
) -> StudentModel:
    """Build one order-independent L3 projection from a fixed L1/L2 snapshot.

    Stage 05 deliberately projects only meanings already represented by the
    frozen StudentModel contract.  Contested and terminal L2 records remain
    auditable but are not presented as stable current state.
    """
    event_ids = _validate_events(
        context=context,
        events=events,
        source_event_watermark=source_event_watermark,
    )

    weak_points: set[str] = set()
    mastered_points: set[str] = set()
    stable_error_patterns: set[str] = set()
    active_plans: set[str] = set()

    for memory in memories:
        _validate_memory(context=context, memory=memory, event_ids=event_ids)
        if memory.lifecycle_state is not LifecycleState.ACTIVE:
            continue

        slot_key = str(validate_slot_key(memory.slot_key))
        namespace = memory.scope.memory_namespace
        if namespace is MemoryNamespace.MASTERY:
            if not isinstance(memory.value, MasteryValue):
                raise ProjectionInputError("mastery memory must contain MasteryValue")
            knowledge_point_id = slot_key.split(":", maxsplit=1)[1]
            if memory.value.level is MasteryLevel.LOW:
                weak_points.add(knowledge_point_id)
            elif memory.value.level in {MasteryLevel.HIGH, MasteryLevel.MASTERED}:
                mastered_points.add(knowledge_point_id)
        elif namespace is MemoryNamespace.ERROR_PATTERN:
            stable_error_patterns.add(slot_key)
        elif namespace is MemoryNamespace.PLAN:
            active_plans.add(slot_key)

    overlap = weak_points & mastered_points
    if overlap:
        points = ", ".join(sorted(overlap))
        raise ProjectionInputError(f"conflicting active mastery memories: {points}")

    return StudentModel(
        context=context,
        weak_points=sorted(weak_points),
        mastered_points=sorted(mastered_points),
        stable_error_patterns=sorted(stable_error_patterns),
        active_plans=sorted(active_plans),
        projection_version=projection_version,
        source_watermark=source_event_watermark,
    )


def _validate_events(
    *,
    context: LearningContext,
    events: Sequence[LearningEvent],
    source_event_watermark: str,
) -> set[str]:
    event_ids: set[str] = set()
    for event in events:
        if event.context != context:
            raise ProjectionInputError("L1 event is outside the rebuild context")
        if event.event_id in event_ids:
            raise ProjectionInputError(f"duplicate L1 event_id: {event.event_id}")
        event_ids.add(event.event_id)

    if source_event_watermark not in event_ids:
        raise ProjectionInputError("source event watermark is absent from L1 input")
    return event_ids


def _validate_memory(
    *,
    context: LearningContext,
    memory: LearningMemory,
    event_ids: set[str],
) -> None:
    memory_context = LearningContext(
        user_id=memory.scope.user_id,
        exam_id=memory.scope.exam_id,
        subject_id=memory.scope.subject_id,
    )
    if memory_context != context:
        raise ProjectionInputError("L2 memory is outside the rebuild context")

    if len(memory.provenance) != len(set(memory.provenance)):
        raise ProjectionInputError("L2 provenance must not contain duplicates")
    if memory.evidence_count != len(memory.provenance):
        raise ProjectionInputError("L2 evidence_count must equal provenance count")

    missing_provenance = sorted(set(memory.provenance) - event_ids)
    if missing_provenance:
        missing = ", ".join(missing_provenance)
        raise ProjectionInputError(f"L2 provenance is absent from L1 input: {missing}")


__all__ = ["ProjectionInputError", "project_student_model"]
