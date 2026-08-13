from __future__ import annotations

from datetime import UTC, datetime

import pytest

from exam_mem.contracts import (
    ErrorPatternValue,
    ErrorType,
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
    PlanStatus,
    PlanValue,
    PreferenceValue,
)
from exam_mem.projection import ProjectionInputError, project_student_model

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CONTEXT = LearningContext(
    user_id="projection_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)


def _event(*, event_id: str = "projection_event_001") -> LearningEvent:
    return LearningEvent(
        event_id=event_id,
        idempotency_key=event_id,
        context=_CONTEXT,
        session_id="projection_session",
        question_id="projection_question",
        knowledge_point_ids=["math1.linear_algebra.matrix_rank"],
        difficulty=0.5,
        answer_correct=False,
        occurred_at=_NOW,
    )


def _memory(
    *,
    memory_id: str,
    namespace: MemoryNamespace,
    slot_key: str,
    value: object,
    state: LifecycleState = LifecycleState.ACTIVE,
    provenance: list[str] | None = None,
) -> LearningMemory:
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": MemoryScope(**_CONTEXT.model_dump(), memory_namespace=namespace),
            "slot_key": slot_key,
            "value": value,
            "confidence": 0.8,
            "evidence_count": len(provenance or ["projection_event_001"]),
            "lifecycle_state": state,
            "version": 1,
            "valid_from": _NOW,
            "valid_to": None if state is LifecycleState.ACTIVE else _NOW,
            "superseded_by": None,
            "provenance": provenance or ["projection_event_001"],
        }
    )


def _projection_memories() -> list[LearningMemory]:
    return [
        _memory(
            memory_id="memory_low",
            namespace=MemoryNamespace.MASTERY,
            slot_key="mastery:math1.linear_algebra.matrix_rank",
            value=MasteryValue(level=MasteryLevel.LOW, score=0.2),
        ),
        _memory(
            memory_id="memory_high",
            namespace=MemoryNamespace.MASTERY,
            slot_key="mastery:math1.linear_algebra.determinant",
            value=MasteryValue(level=MasteryLevel.HIGH, score=0.8),
        ),
        _memory(
            memory_id="memory_improving",
            namespace=MemoryNamespace.MASTERY,
            slot_key="mastery:math1.linear_algebra.eigenvalue",
            value=MasteryValue(level=MasteryLevel.IMPROVING, score=0.6),
        ),
        _memory(
            memory_id="memory_error",
            namespace=MemoryNamespace.ERROR_PATTERN,
            slot_key=("error_pattern:math1.linear_algebra.matrix_rank:condition_omission"),
            value=ErrorPatternValue(
                error_type=ErrorType.CONDITION_OMISSION,
                summary="Forgets the non-zero minor condition.",
            ),
        ),
        _memory(
            memory_id="memory_plan",
            namespace=MemoryNamespace.PLAN,
            slot_key="plan:postgraduate_entrance_exam:math_1",
            value=PlanValue(
                goal="Review matrix rank",
                status=PlanStatus.IN_PROGRESS,
                progress=0.25,
            ),
        ),
        _memory(
            memory_id="memory_preference",
            namespace=MemoryNamespace.PREFERENCE,
            slot_key="preference:format",
            value=PreferenceValue(attribute="format", content="concise"),
        ),
        _memory(
            memory_id="memory_archived",
            namespace=MemoryNamespace.MASTERY,
            slot_key="mastery:math1.linear_algebra.inverse_matrix",
            value=MasteryValue(level=MasteryLevel.LOW, score=0.1),
            state=LifecycleState.ARCHIVED,
        ),
        _memory(
            memory_id="memory_contested",
            namespace=MemoryNamespace.MASTERY,
            slot_key="mastery:math1.linear_algebra.vector_space",
            value=MasteryValue(level=MasteryLevel.LOW, score=0.1),
            state=LifecycleState.CONTESTED,
        ),
    ]


def test_projection_is_stable_for_fixed_inputs_in_any_order() -> None:
    events = [_event()]
    memories = _projection_memories()

    first = project_student_model(
        context=_CONTEXT,
        events=events,
        memories=memories,
        projection_version=3,
        source_event_watermark="projection_event_001",
    )
    second = project_student_model(
        context=_CONTEXT,
        events=list(reversed(events)),
        memories=list(reversed(memories)),
        projection_version=3,
        source_event_watermark="projection_event_001",
    )

    assert first == second
    assert first.weak_points == ["math1.linear_algebra.matrix_rank"]
    assert first.mastered_points == ["math1.linear_algebra.determinant"]
    assert first.stable_error_patterns == [
        "error_pattern:math1.linear_algebra.matrix_rank:condition_omission"
    ]
    assert first.active_plans == ["plan:postgraduate_entrance_exam:math_1"]


@pytest.mark.parametrize("invalid_layer", ["event", "memory", "provenance"])
def test_projection_rejects_cross_context_or_untraceable_inputs(
    invalid_layer: str,
) -> None:
    events = [_event()]
    memories = _projection_memories()
    if invalid_layer == "event":
        event_payload = events[0].model_dump()
        event_payload["context"]["user_id"] = "other_user"
        events = [LearningEvent.model_validate(event_payload)]
    elif invalid_layer == "memory":
        memory_payload = memories[0].model_dump()
        memory_payload["scope"]["user_id"] = "other_user"
        memories = [LearningMemory.model_validate(memory_payload)]
    else:
        memories = [
            _memory(
                memory_id="memory_missing_provenance",
                namespace=MemoryNamespace.MASTERY,
                slot_key="mastery:math1.linear_algebra.matrix_rank",
                value=MasteryValue(level=MasteryLevel.LOW, score=0.2),
                provenance=["missing_event"],
            )
        ]

    with pytest.raises(ProjectionInputError):
        project_student_model(
            context=_CONTEXT,
            events=events,
            memories=memories,
            projection_version=1,
            source_event_watermark="projection_event_001",
        )


def test_projection_requires_watermark_to_exist_in_l1_input() -> None:
    with pytest.raises(ProjectionInputError, match="watermark"):
        project_student_model(
            context=_CONTEXT,
            events=[_event()],
            memories=[],
            projection_version=1,
            source_event_watermark="missing_event",
        )
