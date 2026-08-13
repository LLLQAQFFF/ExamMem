"""PostgreSQL persistence for reproducible L3 Student Model snapshots."""

from __future__ import annotations

from typing import Annotated, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import LearningContext, StudentModel

from .models import student_model_snapshots

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StudentModelSnapshot(BaseModel):
    """Storage envelope that preserves both independent rebuild watermarks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    snapshot_id: NonEmptyString
    model: StudentModel
    source_event_watermark: NonEmptyString
    source_memory_watermark: NonEmptyString

    @model_validator(mode="after")
    def validate_event_watermark(self) -> StudentModelSnapshot:
        if self.model.source_watermark != self.source_event_watermark:
            raise ValueError("model source_watermark must equal source_event_watermark")
        return self


class ProjectionConflict(RuntimeError):
    """Raised when a projection snapshot identity already exists."""


@runtime_checkable
class StudentModelRepository(Protocol):
    async def save_projection(self, snapshot: StudentModelSnapshot) -> None: ...

    async def get_latest(self, context: LearningContext) -> StudentModelSnapshot | None: ...

    async def clear_projection(self, context: LearningContext) -> int: ...


class PostgresStudentModelRepository:
    """Store L3 as disposable projections without changing L1 or L2."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def save_projection(self, snapshot: StudentModelSnapshot) -> None:
        model = snapshot.model
        inserted_snapshot_id = await self._connection.scalar(
            postgresql_insert(student_model_snapshots)
            .values(
                snapshot_id=snapshot.snapshot_id,
                user_id=model.context.user_id,
                exam_id=model.context.exam_id,
                subject_id=model.context.subject_id,
                model=model.model_dump(mode="json"),
                projection_version=model.projection_version,
                source_event_watermark=snapshot.source_event_watermark,
                source_memory_watermark=snapshot.source_memory_watermark,
            )
            .on_conflict_do_nothing()
            .returning(student_model_snapshots.c.snapshot_id)
        )
        if inserted_snapshot_id is None:
            raise ProjectionConflict("student model snapshot_id already exists")

    async def get_latest(self, context: LearningContext) -> StudentModelSnapshot | None:
        row = (
            (
                await self._connection.execute(
                    select(student_model_snapshots)
                    .where(*_context_predicates(context))
                    .order_by(
                        student_model_snapshots.c.projection_version.desc(),
                        student_model_snapshots.c.created_at.desc(),
                        student_model_snapshots.c.snapshot_id.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return StudentModelSnapshot.model_validate(
            {
                "snapshot_id": row["snapshot_id"],
                "model": row["model"],
                "source_event_watermark": row["source_event_watermark"],
                "source_memory_watermark": row["source_memory_watermark"],
            }
        )

    async def clear_projection(self, context: LearningContext) -> int:
        cleared_ids = (
            await self._connection.scalars(
                delete(student_model_snapshots)
                .where(*_context_predicates(context))
                .returning(student_model_snapshots.c.snapshot_id)
            )
        ).all()
        return len(cleared_ids)


def _context_predicates(context: LearningContext) -> tuple[object, ...]:
    return (
        student_model_snapshots.c.user_id == context.user_id,
        student_model_snapshots.c.exam_id == context.exam_id,
        student_model_snapshots.c.subject_id == context.subject_id,
    )


__all__ = [
    "PostgresStudentModelRepository",
    "ProjectionConflict",
    "StudentModelRepository",
    "StudentModelSnapshot",
]
