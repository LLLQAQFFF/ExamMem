"""CAS persistence for resumable Stage 07 practice checkpoints."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from exam_mem.contracts import LearningContext
from exam_mem.practice.checkpoint import PracticeRuntimeSnapshot, PracticeWorkflowCheckpoint
from exam_mem.practice.contracts import GradeArtifactIdentity, Question

from .event_repository import AppendStatus
from .models import practice_workflow_checkpoints


@dataclass(frozen=True, slots=True)
class PracticeCheckpointRecord:
    checkpoint: PracticeWorkflowCheckpoint
    row_version: int
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class PracticeCheckpointAppendResult:
    status: AppendStatus
    record: PracticeCheckpointRecord | None


class PracticeCheckpointIdentityError(ValueError):
    """Raised when an update attempts to move a checkpoint across identity."""


@runtime_checkable
class PracticeCheckpointRepository(Protocol):
    async def create(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
    ) -> PracticeCheckpointAppendResult: ...

    async def get(
        self,
        context: LearningContext,
        practice_session_id: str,
        checkpoint_key: str,
    ) -> PracticeCheckpointRecord | None: ...

    async def advance(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
        *,
        expected_row_version: int,
    ) -> PracticeCheckpointRecord | None: ...

    async def find_issued_question(
        self,
        context: LearningContext,
        practice_session_id: str,
        question_id: str,
    ) -> Question | None: ...

    async def find_grade_artifact(
        self,
        context: LearningContext,
        identity: GradeArtifactIdentity,
    ) -> PracticeCheckpointRecord | None: ...

    async def get_runtime_snapshot(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeRuntimeSnapshot | None: ...

    async def get_latest(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeCheckpointRecord | None: ...


class PostgresPracticeCheckpointRepository:
    """Read/write checkpoints inside a caller-owned transaction."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def create(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
    ) -> PracticeCheckpointAppendResult:
        row = _checkpoint_row(checkpoint)
        statement = (
            postgresql_insert(practice_workflow_checkpoints)
            .values(**row, row_version=1)
            .on_conflict_do_nothing(
                index_elements=[
                    practice_workflow_checkpoints.c.practice_session_id,
                    practice_workflow_checkpoints.c.checkpoint_key,
                ]
            )
            .returning(practice_workflow_checkpoints)
        )
        async with self._connection.begin_nested():
            inserted = (await self._connection.execute(statement)).mappings().one_or_none()
            if inserted is not None:
                return PracticeCheckpointAppendResult(
                    status=AppendStatus.CREATED,
                    record=_record_from_row(inserted),
                )

            existing = await self.get(
                _learning_context(checkpoint),
                checkpoint.context.practice_session_id,
                checkpoint.checkpoint_key,
            )
            if existing is None:
                return PracticeCheckpointAppendResult(
                    status=AppendStatus.CONFLICT,
                    record=None,
                )
            status = (
                AppendStatus.EXISTING
                if existing.checkpoint == checkpoint
                else AppendStatus.CONFLICT
            )
            return PracticeCheckpointAppendResult(status=status, record=existing)

    async def get(
        self,
        context: LearningContext,
        practice_session_id: str,
        checkpoint_key: str,
    ) -> PracticeCheckpointRecord | None:
        if not practice_session_id.strip() or not checkpoint_key.strip():
            raise ValueError("practice_session_id and checkpoint_key must not be blank")
        row = (
            (
                await self._connection.execute(
                    select(practice_workflow_checkpoints).where(
                        practice_workflow_checkpoints.c.practice_session_id == practice_session_id,
                        practice_workflow_checkpoints.c.checkpoint_key == checkpoint_key,
                        *_context_predicates(context),
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _record_from_row(row)

    async def advance(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
        *,
        expected_row_version: int,
    ) -> PracticeCheckpointRecord | None:
        if expected_row_version < 1:
            raise ValueError("expected_row_version must be greater than or equal to 1")
        context = _learning_context(checkpoint)
        existing = await self.get(
            context,
            checkpoint.context.practice_session_id,
            checkpoint.checkpoint_key,
        )
        if existing is None:
            raise PracticeCheckpointIdentityError("checkpoint does not exist in this context")
        _validate_same_identity(existing.checkpoint, checkpoint)

        statement = (
            update(practice_workflow_checkpoints)
            .where(
                practice_workflow_checkpoints.c.practice_session_id
                == checkpoint.context.practice_session_id,
                practice_workflow_checkpoints.c.checkpoint_key == checkpoint.checkpoint_key,
                practice_workflow_checkpoints.c.row_version == expected_row_version,
                *_context_predicates(context),
            )
            .values(
                step_state=checkpoint.context.step_state.value,
                payload=checkpoint.model_dump(mode="json"),
                row_version=practice_workflow_checkpoints.c.row_version + 1,
                updated_at=func.now(),
            )
            .returning(practice_workflow_checkpoints)
        )
        row = (await self._connection.execute(statement)).mappings().one_or_none()
        return None if row is None else _record_from_row(row)

    async def find_issued_question(
        self,
        context: LearningContext,
        practice_session_id: str,
        question_id: str,
    ) -> Question | None:
        if not practice_session_id.strip() or not question_id.strip():
            raise ValueError("practice_session_id and question_id must not be blank")
        payloads = (
            await self._connection.scalars(
                select(practice_workflow_checkpoints.c.payload)
                .where(
                    practice_workflow_checkpoints.c.practice_session_id == practice_session_id,
                    *_context_predicates(context),
                )
                .order_by(
                    practice_workflow_checkpoints.c.updated_at.desc(),
                    practice_workflow_checkpoints.c.checkpoint_key.desc(),
                )
            )
        ).all()
        for payload in payloads:
            checkpoint = PracticeWorkflowCheckpoint.model_validate(payload)
            for question in (
                checkpoint.recommended_question,
                checkpoint.context.current_question,
            ):
                if question is not None and question.question_id == question_id:
                    return question
        return None

    async def find_grade_artifact(
        self,
        context: LearningContext,
        identity: GradeArtifactIdentity,
    ) -> PracticeCheckpointRecord | None:
        rows = (
            (
                await self._connection.execute(
                    select(practice_workflow_checkpoints)
                    .where(
                        practice_workflow_checkpoints.c.step_state.in_(
                            ("GRADED", "DIAGNOSED", "MEMORY_UPDATED", "RECOMMENDED")
                        ),
                        *_context_predicates(context),
                    )
                    .order_by(
                        practice_workflow_checkpoints.c.updated_at.desc(),
                        practice_workflow_checkpoints.c.checkpoint_key.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            record = _record_from_row(row)
            if record.checkpoint.grade_artifact_identity == identity:
                return record
        return None

    async def get_runtime_snapshot(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeRuntimeSnapshot | None:
        if not practice_session_id.strip():
            raise ValueError("practice_session_id must not be blank")
        payloads = (
            await self._connection.scalars(
                select(practice_workflow_checkpoints.c.payload)
                .where(
                    practice_workflow_checkpoints.c.practice_session_id == practice_session_id,
                    *_context_predicates(context),
                )
                .order_by(
                    practice_workflow_checkpoints.c.updated_at.asc(),
                    practice_workflow_checkpoints.c.checkpoint_key.asc(),
                )
            )
        ).all()
        for payload in payloads:
            snapshot = PracticeWorkflowCheckpoint.model_validate(payload).runtime_snapshot
            if snapshot is not None:
                return snapshot
        return None

    async def get_latest(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeCheckpointRecord | None:
        if not practice_session_id.strip():
            raise ValueError("practice_session_id must not be blank")
        row = (
            (
                await self._connection.execute(
                    select(practice_workflow_checkpoints)
                    .where(
                        practice_workflow_checkpoints.c.practice_session_id == practice_session_id,
                        *_context_predicates(context),
                    )
                    .order_by(
                        practice_workflow_checkpoints.c.updated_at.desc(),
                        practice_workflow_checkpoints.c.checkpoint_key.desc(),
                    )
                    .limit(1)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _record_from_row(row)


class CommittedPostgresPracticeCheckpointRepository:
    """Persist every checkpoint operation in its own short transaction."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def create(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
    ) -> PracticeCheckpointAppendResult:
        async with self._engine.begin() as connection:
            return await PostgresPracticeCheckpointRepository(connection).create(checkpoint)

    async def get(
        self,
        context: LearningContext,
        practice_session_id: str,
        checkpoint_key: str,
    ) -> PracticeCheckpointRecord | None:
        async with self._engine.connect() as connection:
            return await PostgresPracticeCheckpointRepository(connection).get(
                context,
                practice_session_id,
                checkpoint_key,
            )

    async def advance(
        self,
        checkpoint: PracticeWorkflowCheckpoint,
        *,
        expected_row_version: int,
    ) -> PracticeCheckpointRecord | None:
        async with self._engine.begin() as connection:
            return await PostgresPracticeCheckpointRepository(connection).advance(
                checkpoint,
                expected_row_version=expected_row_version,
            )

    async def find_issued_question(
        self,
        context: LearningContext,
        practice_session_id: str,
        question_id: str,
    ) -> Question | None:
        async with self._engine.connect() as connection:
            return await PostgresPracticeCheckpointRepository(connection).find_issued_question(
                context,
                practice_session_id,
                question_id,
            )

    async def find_grade_artifact(
        self,
        context: LearningContext,
        identity: GradeArtifactIdentity,
    ) -> PracticeCheckpointRecord | None:
        async with self._engine.connect() as connection:
            return await PostgresPracticeCheckpointRepository(connection).find_grade_artifact(
                context,
                identity,
            )

    async def get_runtime_snapshot(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeRuntimeSnapshot | None:
        async with self._engine.connect() as connection:
            return await PostgresPracticeCheckpointRepository(connection).get_runtime_snapshot(
                context,
                practice_session_id,
            )

    async def get_latest(
        self,
        context: LearningContext,
        practice_session_id: str,
    ) -> PracticeCheckpointRecord | None:
        async with self._engine.connect() as connection:
            return await PostgresPracticeCheckpointRepository(connection).get_latest(
                context,
                practice_session_id,
            )


def _checkpoint_row(checkpoint: PracticeWorkflowCheckpoint) -> dict[str, Any]:
    context = checkpoint.context
    scope = context.scope
    return {
        "practice_session_id": context.practice_session_id,
        "checkpoint_key": checkpoint.checkpoint_key,
        "user_id": scope.user_id,
        "exam_id": scope.exam_id,
        "subject_id": scope.subject_id,
        "trace_id": context.trace_id,
        "step_state": context.step_state.value,
        "payload": checkpoint.model_dump(mode="json"),
    }


def _record_from_row(row: Any) -> PracticeCheckpointRecord:
    return PracticeCheckpointRecord(
        checkpoint=PracticeWorkflowCheckpoint.model_validate(row["payload"]),
        row_version=row["row_version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _learning_context(checkpoint: PracticeWorkflowCheckpoint) -> LearningContext:
    scope = checkpoint.context.scope
    return LearningContext(
        user_id=scope.user_id,
        exam_id=scope.exam_id,
        subject_id=scope.subject_id,
    )


def _context_predicates(context: LearningContext) -> tuple[Any, ...]:
    return (
        practice_workflow_checkpoints.c.user_id == context.user_id,
        practice_workflow_checkpoints.c.exam_id == context.exam_id,
        practice_workflow_checkpoints.c.subject_id == context.subject_id,
    )


def _validate_same_identity(
    existing: PracticeWorkflowCheckpoint,
    proposed: PracticeWorkflowCheckpoint,
) -> None:
    existing_context = existing.context
    proposed_context = proposed.context
    immutable_existing = (
        existing.checkpoint_key,
        existing_context.practice_session_id,
        existing_context.trace_id,
        existing_context.scope,
        existing.runtime_snapshot,
    )
    immutable_proposed = (
        proposed.checkpoint_key,
        proposed_context.practice_session_id,
        proposed_context.trace_id,
        proposed_context.scope,
        proposed.runtime_snapshot,
    )
    if immutable_existing != immutable_proposed:
        raise PracticeCheckpointIdentityError("checkpoint identity is immutable")


__all__ = [
    "CommittedPostgresPracticeCheckpointRepository",
    "PostgresPracticeCheckpointRepository",
    "PracticeCheckpointAppendResult",
    "PracticeCheckpointIdentityError",
    "PracticeCheckpointRecord",
    "PracticeCheckpointRepository",
]
