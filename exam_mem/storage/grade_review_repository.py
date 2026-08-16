"""Append-only Grade Review persistence and scoped queries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import LearningContext
from exam_mem.practice.review import GradeReviewEvent

from .event_repository import AppendStatus
from .models import grade_review_events


@dataclass(frozen=True, slots=True)
class GradeReviewAppendResult:
    status: AppendStatus
    event: GradeReviewEvent | None


class PostgresGradeReviewRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def append(self, event: GradeReviewEvent) -> GradeReviewAppendResult:
        statement = (
            postgresql_insert(grade_review_events)
            .values(**_event_row(event))
            .on_conflict_do_nothing()
            .returning(grade_review_events.c.review_event_id)
        )
        inserted = await self._connection.scalar(statement)
        if inserted is not None:
            return GradeReviewAppendResult(AppendStatus.CREATED, event)
        existing = await self._by_idempotency(event.user_id, event.idempotency_key)
        if existing is None:
            return GradeReviewAppendResult(AppendStatus.CONFLICT, None)
        status = (
            AppendStatus.EXISTING
            if existing.model_dump(exclude={"created_at"})
            == event.model_dump(exclude={"created_at"})
            else AppendStatus.CONFLICT
        )
        return GradeReviewAppendResult(status, existing)

    async def list_scope(self, context: LearningContext) -> list[GradeReviewEvent]:
        rows = (
            (
                await self._connection.execute(
                    select(grade_review_events)
                    .where(
                        grade_review_events.c.user_id == context.user_id,
                        grade_review_events.c.exam_id == context.exam_id,
                        grade_review_events.c.subject_id == context.subject_id,
                    )
                    .order_by(
                        grade_review_events.c.created_at,
                        grade_review_events.c.review_event_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        return [_event_from_row(row) for row in rows]

    async def list_chain(
        self, context: LearningContext, review_chain_id: str
    ) -> list[GradeReviewEvent]:
        return [
            event
            for event in await self.list_scope(context)
            if event.review_chain_id == review_chain_id
        ]

    async def _by_idempotency(self, user_id: str, idempotency_key: str) -> GradeReviewEvent | None:
        row = (
            (
                await self._connection.execute(
                    select(grade_review_events).where(
                        grade_review_events.c.user_id == user_id,
                        grade_review_events.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _event_from_row(row)


def _event_row(event: GradeReviewEvent) -> dict[str, Any]:
    return {
        "review_event_id": event.review_event_id,
        "review_chain_id": event.review_chain_id,
        "idempotency_key": event.idempotency_key,
        "action": event.action.value,
        "user_id": event.user_id,
        "exam_id": event.exam_id,
        "subject_id": event.subject_id,
        "practice_session_id": event.practice_session_id,
        "checkpoint_key": event.checkpoint_key,
        "payload": {
            "reason": event.reason,
            "replacement_grade": (
                None
                if event.replacement_grade is None
                else event.replacement_grade.model_dump(mode="json")
            ),
        },
        "created_at": event.created_at,
    }


def _event_from_row(row: Any) -> GradeReviewEvent:
    payload = row["payload"]
    return GradeReviewEvent(
        review_event_id=row["review_event_id"],
        review_chain_id=row["review_chain_id"],
        idempotency_key=row["idempotency_key"],
        action=row["action"],
        user_id=row["user_id"],
        exam_id=row["exam_id"],
        subject_id=row["subject_id"],
        practice_session_id=row["practice_session_id"],
        checkpoint_key=row["checkpoint_key"],
        reason=payload["reason"],
        replacement_grade=payload.get("replacement_grade"),
        created_at=row["created_at"],
    )


__all__ = ["GradeReviewAppendResult", "PostgresGradeReviewRepository"]
