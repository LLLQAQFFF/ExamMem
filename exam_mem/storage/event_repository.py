"""PostgreSQL persistence for append-only L1 Learning Events."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Protocol, runtime_checkable

from sqlalchemy import and_, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import LearningContext, LearningEvent, LearningEventType, MemoryNamespace

from .models import (
    event_correction_targets,
    event_plan_transition_targets,
    learning_events,
    learning_memories,
)

LEARNING_EVENT_SCHEMA_VERSION = 1


class AppendStatus(str, Enum):
    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class AppendResult:
    """Outcome of one idempotent L1 append attempt."""

    status: AppendStatus
    event: LearningEvent | None


class EventTargetValidationError(ValueError):
    """Raised before commit when a correction or plan target is invalid."""


class EventWatermarkError(ValueError):
    """Raised when an L1 watermark is absent from the requested context."""


class EventLookupError(ValueError):
    """Raised when requested L1 evidence is missing from one context."""


@runtime_checkable
class LearningEventRepository(Protocol):
    async def append(
        self,
        event: LearningEvent,
        *,
        trace_id: str | None = None,
    ) -> AppendResult: ...

    async def list_after(
        self,
        context: LearningContext,
        watermark: str | None,
        limit: int,
    ) -> list[LearningEvent]: ...

    async def get_by_ids(
        self,
        context: LearningContext,
        event_ids: Sequence[str],
    ) -> list[LearningEvent]: ...


class PostgresLearningEventRepository:
    """Persist L1 events on a caller-owned async transaction.

    The repository never commits.  Its savepoint keeps the event row and any
    target rows atomic while allowing a larger L1/L2 transaction to own the
    final commit.
    """

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def append(
        self,
        event: LearningEvent,
        *,
        trace_id: str | None = None,
    ) -> AppendResult:
        """Append once, return the stored event on an idempotent replay."""
        effective_trace_id = trace_id or event.event_id
        if not effective_trace_id.strip():
            raise ValueError("trace_id must not be blank")
        payload = event.model_dump(mode="json")
        statement = (
            postgresql_insert(learning_events)
            .values(
                **_event_row(
                    event,
                    payload=payload,
                    trace_id=effective_trace_id,
                )
            )
            .on_conflict_do_nothing()
            .returning(learning_events.c.event_id)
        )

        async with self._connection.begin_nested():
            inserted_event_id = await self._connection.scalar(statement)
            if inserted_event_id is not None:
                await self._append_targets(event)
                return AppendResult(status=AppendStatus.CREATED, event=event)

            existing_row = (
                await self._connection.execute(
                    select(
                        learning_events.c.raw_payload,
                        learning_events.c.trace_id,
                    ).where(
                        learning_events.c.user_id == event.context.user_id,
                        learning_events.c.idempotency_key == event.idempotency_key,
                    )
                )
            ).one_or_none()
            if existing_row is None:
                return AppendResult(status=AppendStatus.CONFLICT, event=None)

            existing_event = LearningEvent.model_validate(existing_row.raw_payload)
            status = (
                AppendStatus.EXISTING
                if existing_event == event and existing_row.trace_id == effective_trace_id
                else AppendStatus.CONFLICT
            )
            return AppendResult(status=status, event=existing_event)

    async def list_after(
        self,
        context: LearningContext,
        watermark: str | None,
        limit: int,
    ) -> list[LearningEvent]:
        """Read one deterministic page after a context-bound event watermark."""
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")

        context_predicates = _context_predicates(context)
        after_predicate = None
        if watermark is not None:
            marker = (
                await self._connection.execute(
                    select(
                        learning_events.c.created_at,
                        learning_events.c.event_id,
                    ).where(
                        *context_predicates,
                        learning_events.c.event_id == watermark,
                    )
                )
            ).one_or_none()
            if marker is None:
                raise EventWatermarkError("event watermark does not belong to this context")
            after_predicate = or_(
                learning_events.c.created_at > marker.created_at,
                and_(
                    learning_events.c.created_at == marker.created_at,
                    learning_events.c.event_id > marker.event_id,
                ),
            )

        statement = select(learning_events.c.raw_payload).where(*context_predicates)
        if after_predicate is not None:
            statement = statement.where(after_predicate)
        statement = statement.order_by(
            learning_events.c.created_at,
            learning_events.c.event_id,
        ).limit(limit)
        payloads = (await self._connection.scalars(statement)).all()
        return [LearningEvent.model_validate(payload) for payload in payloads]

    async def get_by_ids(
        self,
        context: LearningContext,
        event_ids: Sequence[str],
    ) -> list[LearningEvent]:
        ordered_ids = tuple(sorted(set(event_ids)))
        if not ordered_ids:
            return []
        rows = (
            await self._connection.execute(
                select(
                    learning_events.c.event_id,
                    learning_events.c.raw_payload,
                ).where(
                    *_context_predicates(context),
                    learning_events.c.event_id.in_(ordered_ids),
                )
            )
        ).all()
        payload_by_id = {event_id: payload for event_id, payload in rows}
        missing = [event_id for event_id in ordered_ids if event_id not in payload_by_id]
        if missing:
            raise EventLookupError(
                "learning event does not belong to the requested context: " + ", ".join(missing)
            )
        return [LearningEvent.model_validate(payload_by_id[event_id]) for event_id in ordered_ids]

    async def _append_targets(self, event: LearningEvent) -> None:
        if event.event_type is LearningEventType.EXPLICIT_CORRECTION:
            assert event.correction is not None
            target_ids = event.correction.target_memory_ids
            if len(target_ids) != len(set(target_ids)):
                raise EventTargetValidationError(
                    "explicit_correction target_memory_ids must be unique"
                )
            targets = await self._validated_targets(event, target_ids)
            await self._connection.execute(
                insert(event_correction_targets),
                [
                    {"event_id": event.event_id, "memory_id": target["memory_id"]}
                    for target in targets
                ],
            )
        elif event.event_type is LearningEventType.PLAN_TRANSITION:
            assert event.plan_transition is not None
            targets = await self._validated_targets(
                event,
                [event.plan_transition.target_memory_id],
                required_namespace=MemoryNamespace.PLAN,
            )
            await self._connection.execute(
                insert(event_plan_transition_targets).values(
                    event_id=event.event_id,
                    memory_id=targets[0]["memory_id"],
                )
            )

    async def _validated_targets(
        self,
        event: LearningEvent,
        target_ids: list[str],
        *,
        required_namespace: MemoryNamespace | None = None,
    ) -> list[dict[str, str]]:
        result = await self._connection.execute(
            select(
                learning_memories.c.memory_id,
                learning_memories.c.user_id,
                learning_memories.c.exam_id,
                learning_memories.c.subject_id,
                learning_memories.c.memory_namespace,
            ).where(learning_memories.c.memory_id.in_(target_ids))
        )
        targets = [dict(row) for row in result.mappings()]
        targets_by_id = {target["memory_id"]: target for target in targets}

        missing_ids = [target_id for target_id in target_ids if target_id not in targets_by_id]
        if missing_ids:
            raise EventTargetValidationError("event target memory does not exist")

        expected_context = (
            event.context.user_id,
            event.context.exam_id,
            event.context.subject_id,
        )
        for target_id in target_ids:
            target = targets_by_id[target_id]
            target_context = (
                target["user_id"],
                target["exam_id"],
                target["subject_id"],
            )
            if target_context != expected_context:
                raise EventTargetValidationError("event target must use the same learning context")
            if (
                required_namespace is not None
                and target["memory_namespace"] != required_namespace.value
            ):
                raise EventTargetValidationError(
                    f"event target must use the {required_namespace.value!r} namespace"
                )
        return [targets_by_id[target_id] for target_id in target_ids]


def _event_row(
    event: LearningEvent,
    *,
    payload: dict[str, object],
    trace_id: str,
) -> dict[str, object]:
    correction = event.correction
    transition = event.plan_transition
    return {
        "event_id": event.event_id,
        "idempotency_key": event.idempotency_key,
        "user_id": event.context.user_id,
        "exam_id": event.context.exam_id,
        "subject_id": event.context.subject_id,
        "event_type": event.event_type.value,
        "session_id": event.session_id,
        "question_id": event.question_id,
        "knowledge_point_ids": event.knowledge_point_ids,
        "primary_knowledge_point_id": None,
        "difficulty": event.difficulty,
        "answer_correct": event.answer_correct,
        "error_type": event.error_type.value if event.error_type is not None else None,
        "error_detail": event.error_detail,
        "evidence_quality": event.evidence_quality.model_dump(mode="json"),
        "correction_source": correction.source.value if correction is not None else None,
        "correction_statement": correction.statement if correction is not None else None,
        "plan_transition_status": transition.to_status.value if transition is not None else None,
        "plan_transition_source": transition.source.value if transition is not None else None,
        "plan_transition_reason": transition.reason if transition is not None else None,
        "raw_payload": payload,
        "occurred_at": event.occurred_at,
        "trace_id": trace_id,
        "schema_version": LEARNING_EVENT_SCHEMA_VERSION,
    }


def _context_predicates(context: LearningContext) -> tuple[object, ...]:
    return (
        learning_events.c.user_id == context.user_id,
        learning_events.c.exam_id == context.exam_id,
        learning_events.c.subject_id == context.subject_id,
    )


__all__ = [
    "AppendResult",
    "AppendStatus",
    "EventTargetValidationError",
    "EventLookupError",
    "EventWatermarkError",
    "LEARNING_EVENT_SCHEMA_VERSION",
    "LearningEventRepository",
    "PostgresLearningEventRepository",
]
