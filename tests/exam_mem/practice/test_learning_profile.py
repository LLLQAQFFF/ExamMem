from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exam_mem.contracts import LearningContext, LearningEvent, LearningMemory, StudentModel
from exam_mem.domain import Taxonomy
from exam_mem.practice.learning_profile import build_learning_profile
from exam_mem.practice.learning_profile_service import LearningProfileQueryService

NOW = datetime(2026, 8, 18, 9, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(user_id="profile_user", exam_id="plan:math", subject_id="math")
TAXONOMY = Taxonomy.model_validate(
    {
        "taxonomy_version": "pmath_s001_v1",
        "nodes": [
            {"id": "pmath", "name_zh": "数学"},
            {"id": "pmath.algebra", "name_zh": "代数", "parent_id": "pmath"},
            {
                "id": "pmath.algebra.linear",
                "name_zh": "线性方程",
                "parent_id": "pmath.algebra",
            },
            {
                "id": "pmath.algebra.matrix",
                "name_zh": "矩阵",
                "parent_id": "pmath.algebra",
            },
        ],
    }
)


def _event(event_id: str, point: str, *, correct: bool, days_ago: int) -> LearningEvent:
    return LearningEvent(
        event_id=event_id,
        idempotency_key=event_id,
        context=CONTEXT,
        session_id=f"session:{event_id}",
        question_id=f"question:{event_id}",
        knowledge_point_ids=[point],
        difficulty=0.5,
        answer_correct=correct,
        error_type=None if correct else "concept_confusion",
        error_detail=None if correct else "概念边界混淆",
        occurred_at=NOW - timedelta(days=days_ago),
    )


def _memory(
    memory_id: str,
    point: str,
    *,
    namespace: str = "mastery",
    state: str = "active",
    level: str = "low",
) -> LearningMemory:
    value = (
        {"type": "mastery", "level": level, "score": 0.3 if level == "low" else 0.9}
        if namespace == "mastery"
        else {
            "type": "error_pattern",
            "error_type": "concept_confusion",
            "summary": "概念边界混淆",
            "details": [],
        }
    )
    valid_from = NOW - timedelta(days=10)
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": {**CONTEXT.model_dump(), "memory_namespace": namespace},
            "slot_key": (
                f"mastery:{point}"
                if namespace == "mastery"
                else f"error_pattern:{point}:concept_confusion"
            ),
            "value": value,
            "confidence": 0.9,
            "evidence_count": 1,
            "lifecycle_state": state,
            "version": 1,
            "valid_from": valid_from,
            "valid_to": valid_from if state in {"archived", "invalidated"} else None,
            "superseded_by": None,
            "provenance": ["event:linear:1"],
        }
    )


def _model() -> StudentModel:
    return StudentModel(
        context=CONTEXT,
        weak_points=["pmath.algebra.linear"],
        mastered_points=[],
        stable_error_patterns=["error_pattern:pmath.algebra.linear:concept_confusion"],
        active_plans=[],
        projection_version=3,
        source_watermark="event:linear:2",
    )


def test_profile_is_actionable_explainable_and_scope_safe() -> None:
    events = [
        _event("event:linear:1", "pmath.algebra.linear", correct=False, days_ago=9),
        _event("event:linear:2", "pmath.algebra.linear", correct=False, days_ago=3),
    ]
    memories = [
        _memory("memory:mastery", "pmath.algebra.linear"),
        _memory("memory:error", "pmath.algebra.linear", namespace="error_pattern"),
    ]

    profile = build_learning_profile(
        context=CONTEXT,
        taxonomy=TAXONOMY,
        events=events,
        memories=memories,
        model=_model(),
        evaluated_at=NOW,
    )

    linear = profile.knowledge_points[0]
    assert linear.name == "线性方程"
    assert linear.status == "weak"
    assert linear.attempts == 2
    assert linear.accuracy == 0.0
    assert linear.error_types == ("concept_confusion",)
    review = profile.review_queue[0]
    assert review.knowledge_point_id == linear.knowledge_point_id
    assert review.status == "due"
    assert review.interval_days == 1
    assert review.reason_codes == ("weakness", "stable_error", "forgetting_risk")
    assert review.source_memory_ids == ("memory:error", "memory:mastery")
    assert profile.summary.weak_count == 1
    assert profile.summary.due_count == 1
    assert profile.projection_version == 3


def test_unassessed_point_is_visible_without_fabricated_memory_evidence() -> None:
    profile = build_learning_profile(
        context=CONTEXT,
        taxonomy=TAXONOMY,
        events=[],
        memories=[],
        model=None,
        evaluated_at=NOW,
    )

    assert [item.status for item in profile.knowledge_points] == ["unassessed", "unassessed"]
    assert profile.review_queue[0].reason_codes == ("coverage_gap",)
    assert profile.review_queue[0].source_memory_ids == ()
    assert profile.summary.coverage_rate == 0.0
    assert profile.summary.trend == "insufficient_evidence"


def test_terminal_memory_does_not_drive_profile_or_review() -> None:
    archived = _memory(
        "memory:archived",
        "pmath.algebra.linear",
        state="archived",
        level="mastered",
    )

    profile = build_learning_profile(
        context=CONTEXT,
        taxonomy=TAXONOMY,
        events=[],
        memories=[archived],
        model=None,
        evaluated_at=NOW,
    )

    assert profile.knowledge_points[0].status == "unassessed"
    assert profile.knowledge_points[0].source_memory_ids == ()


def test_contested_error_evidence_is_visible_but_not_labeled_stable() -> None:
    contested = _memory(
        "memory:contested",
        "pmath.algebra.linear",
        namespace="error_pattern",
        state="contested",
    )

    profile = build_learning_profile(
        context=CONTEXT,
        taxonomy=TAXONOMY,
        events=[],
        memories=[contested],
        model=None,
        evaluated_at=NOW,
    )

    point = profile.knowledge_points[0]
    review = next(
        item for item in profile.review_queue if item.knowledge_point_id == point.knowledge_point_id
    )
    assert point.status == "contested"
    assert point.error_types == ()
    assert "contested_evidence" in review.reason_codes
    assert "stable_error" not in review.reason_codes


def test_mastered_point_receives_longer_review_interval_and_trend_is_reproducible() -> None:
    events = [
        _event(
            f"event:linear:{index}",
            "pmath.algebra.linear",
            correct=index >= 3,
            days_ago=8 - index,
        )
        for index in range(1, 7)
    ]
    model = _model().model_copy(
        update={"weak_points": [], "mastered_points": ["pmath.algebra.linear"]}
    )
    profile = build_learning_profile(
        context=CONTEXT,
        taxonomy=TAXONOMY,
        events=events,
        memories=[_memory("memory:mastered", "pmath.algebra.linear", level="mastered")],
        model=model,
        evaluated_at=NOW,
    )

    linear = next(
        item for item in profile.review_queue if item.knowledge_point_id == "pmath.algebra.linear"
    )
    assert linear.interval_days == 14
    assert linear.status == "upcoming"
    assert profile.summary.trend == "improving"


def test_profile_rejects_cross_scope_inputs() -> None:
    event = _event("event:linear:1", "pmath.algebra.linear", correct=False, days_ago=1)
    foreign = event.model_copy(
        update={"context": CONTEXT.model_copy(update={"user_id": "other_user"})}
    )

    with pytest.raises(ValueError, match="outside the requested context"):
        build_learning_profile(
            context=CONTEXT,
            taxonomy=TAXONOMY,
            events=[foreign],
            memories=[],
            model=None,
            evaluated_at=NOW,
        )


@pytest.mark.asyncio
async def test_query_service_pages_events_and_reads_each_formal_namespace() -> None:
    events = [
        _event("event:linear:1", "pmath.algebra.linear", correct=False, days_ago=2),
        _event("event:linear:2", "pmath.algebra.linear", correct=True, days_ago=1),
    ]

    class Events:
        calls: list[str | None] = []

        async def list_after(self, context, watermark, limit):  # noqa: ANN001, ANN201
            assert context == CONTEXT
            assert limit == 1
            self.calls.append(watermark)
            if watermark is None:
                return events[:1]
            if watermark == events[0].event_id:
                return events[1:]
            return []

    class Memories:
        namespaces = []

        async def snapshot(self, scope):  # noqa: ANN001, ANN201
            self.namespaces.append(scope.memory_namespace)
            return []

    class Models:
        async def get_latest(self, context):  # noqa: ANN001, ANN201
            assert context == CONTEXT
            return None

    event_repository = Events()
    memory_repository = Memories()
    service = LearningProfileQueryService(
        event_repository=event_repository,
        memory_repository=memory_repository,
        model_repository=Models(),
        event_page_size=1,
    )

    profile = await service.get(context=CONTEXT, taxonomy=TAXONOMY, evaluated_at=NOW)

    assert event_repository.calls == [None, "event:linear:1", "event:linear:2"]
    assert [namespace.value for namespace in memory_repository.namespaces] == [
        "mastery",
        "error_pattern",
        "plan",
    ]
    assert profile.summary.total_attempts == 2
