"""Deterministic learning profile and review queue derived from Learning Memory."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningContext,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    PlanStatus,
    PlanValue,
    StudentModel,
)
from exam_mem.domain import KnowledgePointStatus, Taxonomy

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]

LEARNING_PROFILE_POLICY_VERSION = "learning_profile_policy_v1"


class StrictProfileModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LearningProfileSummary(StrictProfileModel):
    knowledge_point_count: Annotated[int, Field(ge=0)]
    assessed_count: Annotated[int, Field(ge=0)]
    mastered_count: Annotated[int, Field(ge=0)]
    weak_count: Annotated[int, Field(ge=0)]
    due_count: Annotated[int, Field(ge=0)]
    total_attempts: Annotated[int, Field(ge=0)]
    accuracy: Probability | None
    recent_accuracy: Probability | None
    coverage_rate: Probability
    mastery_rate: Probability
    trend: Literal["improving", "stable", "declining", "insufficient_evidence"]


class KnowledgePointProfile(StrictProfileModel):
    knowledge_point_id: NonEmptyString
    name: NonEmptyString
    module_id: NonEmptyString
    module_name: NonEmptyString
    status: Literal["unassessed", "developing", "weak", "mastered", "contested"]
    mastery_level: MasteryLevel | None
    mastery_score: Probability | None
    confidence: Probability | None
    attempts: Annotated[int, Field(ge=0)]
    correct_attempts: Annotated[int, Field(ge=0)]
    accuracy: Probability | None
    latest_correct: bool | None
    last_practiced_at: AwareDatetime | None
    error_types: tuple[NonEmptyString, ...]
    source_memory_ids: tuple[NonEmptyString, ...]


class ReviewQueueItem(StrictProfileModel):
    knowledge_point_id: NonEmptyString
    name: NonEmptyString
    module_name: NonEmptyString
    status: Literal["due", "upcoming", "unassessed"]
    due_at: AwareDatetime
    interval_days: Annotated[int, Field(ge=0)]
    priority: Probability
    suggested_difficulty: Probability
    reason_codes: tuple[NonEmptyString, ...]
    source_memory_ids: tuple[NonEmptyString, ...]


class LearningProfile(StrictProfileModel):
    context: LearningContext
    taxonomy_version: NonEmptyString
    evaluated_at: AwareDatetime
    policy_version: Literal["learning_profile_policy_v1"] = LEARNING_PROFILE_POLICY_VERSION
    projection_version: Annotated[int, Field(ge=1)] | None
    source_watermark: NonEmptyString | None
    summary: LearningProfileSummary
    knowledge_points: tuple[KnowledgePointProfile, ...]
    review_queue: tuple[ReviewQueueItem, ...]


def build_learning_profile(
    *,
    context: LearningContext,
    taxonomy: Taxonomy,
    events: Sequence[LearningEvent],
    memories: Sequence[LearningMemory],
    model: StudentModel | None,
    evaluated_at: datetime,
) -> LearningProfile:
    """Project an actionable profile without creating a second source of truth."""
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    _validate_inputs(context=context, events=events, memories=memories, model=model)

    leaves = tuple(
        node
        for node in taxonomy.nodes
        if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
    )
    leaf_ids = {node.id for node in leaves}
    formal_events = tuple(
        sorted(
            (
                event
                for event in events
                if event.event_type is LearningEventType.ANSWER_ATTEMPT
                and set(event.knowledge_point_ids) & leaf_ids
            ),
            key=lambda event: (event.occurred_at, event.event_id),
        )
    )
    current_memories = tuple(
        memory
        for memory in memories
        if memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
    )

    profiles: list[KnowledgePointProfile] = []
    review_items: list[ReviewQueueItem] = []
    for node in leaves:
        point_events = tuple(
            event for event in formal_events if node.id in event.knowledge_point_ids
        )
        point_event_ids = {event.event_id for event in point_events}
        point_memories = tuple(
            memory
            for memory in current_memories
            if _memory_targets(memory, node.id, point_event_ids)
        )
        mastery = _current_mastery(point_memories)
        error_memories = tuple(
            memory
            for memory in point_memories
            if memory.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN
            and memory.lifecycle_state is LifecycleState.ACTIVE
        )
        contested = any(
            memory.lifecycle_state is LifecycleState.CONTESTED for memory in point_memories
        )
        weak = (
            mastery.value.level is MasteryLevel.LOW
            if mastery is not None
            else bool(model and node.id in model.weak_points)
        )
        mastered = (
            mastery.value.level in {MasteryLevel.HIGH, MasteryLevel.MASTERED}
            if mastery is not None
            else bool(model and node.id in model.mastered_points)
        )
        status: Literal["unassessed", "developing", "weak", "mastered", "contested"]
        if contested:
            status = "contested"
        elif weak:
            status = "weak"
        elif mastered:
            status = "mastered"
        elif point_events:
            status = "developing"
        else:
            status = "unassessed"

        correct_attempts = sum(event.answer_correct is True for event in point_events)
        last_event = point_events[-1] if point_events else None
        source_memory_ids = tuple(sorted(memory.memory_id for memory in point_memories))
        parent = taxonomy.get(node.parent_id or "")
        profile = KnowledgePointProfile(
            knowledge_point_id=node.id,
            name=node.name_zh,
            module_id=node.parent_id or node.id,
            module_name=parent.name_zh if parent is not None else node.name_zh,
            status=status,
            mastery_level=None if mastery is None else mastery.value.level,
            mastery_score=None if mastery is None else mastery.value.score,
            confidence=None if mastery is None else mastery.confidence,
            attempts=len(point_events),
            correct_attempts=correct_attempts,
            accuracy=(None if not point_events else correct_attempts / len(point_events)),
            latest_correct=None if last_event is None else last_event.answer_correct,
            last_practiced_at=None if last_event is None else last_event.occurred_at,
            error_types=tuple(
                sorted(
                    {
                        memory.value.error_type.value
                        for memory in error_memories
                        if isinstance(memory.value, ErrorPatternValue)
                    }
                )
            ),
            source_memory_ids=source_memory_ids,
        )
        interval_days = _review_interval(profile, has_stable_error=bool(error_memories))
        due_at = (
            evaluated_at
            if profile.last_practiced_at is None
            else profile.last_practiced_at + timedelta(days=interval_days)
        )
        reason_codes = _review_reasons(
            profile,
            has_stable_error=bool(error_memories),
            has_active_plan=_has_active_plan(point_memories, point_events),
            due_at=due_at,
            evaluated_at=evaluated_at,
        )
        review_items.append(
            ReviewQueueItem(
                knowledge_point_id=node.id,
                name=node.name_zh,
                module_name=profile.module_name,
                status=(
                    "unassessed"
                    if profile.status == "unassessed"
                    else "due"
                    if due_at <= evaluated_at
                    else "upcoming"
                ),
                due_at=due_at,
                interval_days=interval_days,
                priority=_review_priority(reason_codes),
                suggested_difficulty=(
                    0.35
                    if profile.status == "weak"
                    else 0.7
                    if profile.status == "mastered"
                    else 0.5
                ),
                reason_codes=reason_codes,
                source_memory_ids=source_memory_ids,
            )
        )
        profiles.append(profile)

    syllabus_order = {node.id: index for index, node in enumerate(leaves)}
    review_items.sort(
        key=lambda item: (
            item.status == "upcoming",
            item.due_at,
            -item.priority,
            syllabus_order[item.knowledge_point_id],
            item.knowledge_point_id,
        )
    )
    total_correct = sum(event.answer_correct is True for event in formal_events)
    assessed_count = sum(profile.attempts > 0 for profile in profiles)
    mastered_count = sum(profile.status == "mastered" for profile in profiles)
    weak_count = sum(profile.status in {"weak", "contested"} for profile in profiles)
    due_count = sum(item.status == "due" for item in review_items)
    recent = formal_events[-10:]
    point_count = len(profiles)
    return LearningProfile(
        context=context,
        taxonomy_version=taxonomy.taxonomy_version,
        evaluated_at=evaluated_at,
        projection_version=None if model is None else model.projection_version,
        source_watermark=None if model is None else model.source_watermark,
        summary=LearningProfileSummary(
            knowledge_point_count=point_count,
            assessed_count=assessed_count,
            mastered_count=mastered_count,
            weak_count=weak_count,
            due_count=due_count,
            total_attempts=len(formal_events),
            accuracy=(None if not formal_events else total_correct / len(formal_events)),
            recent_accuracy=(
                None
                if not recent
                else sum(event.answer_correct is True for event in recent) / len(recent)
            ),
            coverage_rate=0.0 if point_count == 0 else assessed_count / point_count,
            mastery_rate=0.0 if point_count == 0 else mastered_count / point_count,
            trend=_trend(formal_events),
        ),
        knowledge_points=tuple(profiles),
        review_queue=tuple(review_items),
    )


def _validate_inputs(
    *,
    context: LearningContext,
    events: Sequence[LearningEvent],
    memories: Sequence[LearningMemory],
    model: StudentModel | None,
) -> None:
    for event in events:
        if event.context != context:
            raise ValueError("learning profile event is outside the requested context")
    for memory in memories:
        memory_context = LearningContext(
            user_id=memory.scope.user_id,
            exam_id=memory.scope.exam_id,
            subject_id=memory.scope.subject_id,
        )
        if memory_context != context:
            raise ValueError("learning profile memory is outside the requested context")
    if model is not None and model.context != context:
        raise ValueError("learning profile model is outside the requested context")


def _memory_targets(
    memory: LearningMemory, knowledge_point_id: str, point_event_ids: set[str]
) -> bool:
    if memory.scope.memory_namespace is MemoryNamespace.MASTERY:
        return memory.slot_key == f"mastery:{knowledge_point_id}"
    if memory.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN:
        return memory.slot_key.startswith(f"error_pattern:{knowledge_point_id}:")
    if memory.scope.memory_namespace is MemoryNamespace.PLAN:
        return bool(set(memory.provenance) & point_event_ids)
    return False


def _current_mastery(memories: Sequence[LearningMemory]) -> LearningMemory | None:
    active = [
        memory
        for memory in memories
        if memory.scope.memory_namespace is MemoryNamespace.MASTERY
        and memory.lifecycle_state is LifecycleState.ACTIVE
        and isinstance(memory.value, MasteryValue)
    ]
    if not active:
        return None
    return max(active, key=lambda memory: (memory.version, memory.memory_id))


def _has_active_plan(memories: Sequence[LearningMemory], events: Sequence[LearningEvent]) -> bool:
    event_ids = {event.event_id for event in events}
    return any(
        memory.scope.memory_namespace is MemoryNamespace.PLAN
        and memory.lifecycle_state is LifecycleState.ACTIVE
        and isinstance(memory.value, PlanValue)
        and memory.value.status in {PlanStatus.PLANNED, PlanStatus.IN_PROGRESS}
        and bool(set(memory.provenance) & event_ids)
        for memory in memories
    )


def _review_interval(profile: KnowledgePointProfile, *, has_stable_error: bool) -> int:
    if profile.status in {"unassessed", "contested"}:
        return 0
    if profile.latest_correct is False or has_stable_error:
        return 1
    return {
        MasteryLevel.LOW: 1,
        MasteryLevel.IMPROVING: 3,
        MasteryLevel.HIGH: 7,
        MasteryLevel.MASTERED: 14,
        None: 3,
    }[profile.mastery_level]


def _review_reasons(
    profile: KnowledgePointProfile,
    *,
    has_stable_error: bool,
    has_active_plan: bool,
    due_at: datetime,
    evaluated_at: datetime,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if profile.status in {"weak", "contested"}:
        reasons.append("weakness")
    if profile.status == "contested":
        reasons.append("contested_evidence")
    if has_stable_error:
        reasons.append("stable_error")
    if profile.status == "unassessed":
        reasons.append("coverage_gap")
    if has_active_plan:
        reasons.append("active_plan_priority")
    if profile.last_practiced_at is not None and due_at <= evaluated_at:
        reasons.append("forgetting_risk")
    if not reasons:
        reasons.append("scheduled_review")
    return tuple(reasons)


def _review_priority(reason_codes: Sequence[str]) -> float:
    weights = {
        "weakness": 0.40,
        "stable_error": 0.25,
        "forgetting_risk": 0.15,
        "active_plan_priority": 0.10,
        "coverage_gap": 0.10,
        "contested_evidence": 0.10,
        "scheduled_review": 0.05,
    }
    return min(1.0, sum(weights[reason] for reason in set(reason_codes)))


def _trend(events: Sequence[LearningEvent]) -> str:
    ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))[-10:]
    if len(ordered) < 4:
        return "insufficient_evidence"
    midpoint = len(ordered) // 2
    earlier = ordered[:midpoint]
    later = ordered[midpoint:]
    earlier_accuracy = sum(event.answer_correct is True for event in earlier) / len(earlier)
    later_accuracy = sum(event.answer_correct is True for event in later) / len(later)
    delta = later_accuracy - earlier_accuracy
    if delta >= 0.15:
        return "improving"
    if delta <= -0.15:
        return "declining"
    return "stable"


__all__ = [
    "LEARNING_PROFILE_POLICY_VERSION",
    "KnowledgePointProfile",
    "LearningProfile",
    "LearningProfileSummary",
    "ReviewQueueItem",
    "build_learning_profile",
]
