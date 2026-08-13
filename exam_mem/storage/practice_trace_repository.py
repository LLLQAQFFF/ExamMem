"""Append-only PostgreSQL persistence for Practice Trace spans."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from exam_mem.practice.trace import PracticeTraceSpan

from .event_repository import AppendStatus
from .models import practice_trace_spans


@dataclass(frozen=True, slots=True)
class PracticeTraceAppendResult:
    status: AppendStatus
    span: PracticeTraceSpan | None


@runtime_checkable
class PracticeTraceRepository(Protocol):
    async def next_step_id(self, trace_id: str) -> int: ...

    async def append(self, span: PracticeTraceSpan) -> PracticeTraceAppendResult: ...

    async def list_trace(self, trace_id: str) -> list[PracticeTraceSpan]: ...


class PostgresPracticeTraceRepository:
    """Persist completed/failed spans without committing the caller transaction."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def next_step_id(self, trace_id: str) -> int:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        maximum = await self._connection.scalar(
            select(func.max(practice_trace_spans.c.step_id)).where(
                practice_trace_spans.c.trace_id == trace_id
            )
        )
        return int(maximum or 0) + 1

    async def append(self, span: PracticeTraceSpan) -> PracticeTraceAppendResult:
        payload = span.model_dump(mode="json")
        statement = (
            postgresql_insert(practice_trace_spans)
            .values(
                trace_id=span.trace_id,
                step_id=span.step_id,
                span_name=span.name.value,
                status=span.status.value,
                input_summary=payload["input_summary"],
                output_summary=payload["output_summary"],
                versions=payload["versions"],
                started_at=span.started_at,
                completed_at=span.completed_at,
                duration_ms=span.duration_ms,
                retry_count=span.retry_count,
                llm_calls=span.llm_calls,
                input_tokens=span.input_tokens,
                output_tokens=span.output_tokens,
                error_code=span.error_code,
                related_record_ids=payload["related_record_ids"],
            )
            .on_conflict_do_nothing(
                index_elements=[
                    practice_trace_spans.c.trace_id,
                    practice_trace_spans.c.step_id,
                ]
            )
            .returning(practice_trace_spans.c.trace_id)
        )
        inserted = await self._connection.scalar(statement)
        if inserted is not None:
            return PracticeTraceAppendResult(status=AppendStatus.CREATED, span=span)

        existing = await self._load_one(span.trace_id, span.step_id)
        if existing is None:
            return PracticeTraceAppendResult(status=AppendStatus.CONFLICT, span=None)
        status = AppendStatus.EXISTING if existing == span else AppendStatus.CONFLICT
        return PracticeTraceAppendResult(status=status, span=existing)

    async def list_trace(self, trace_id: str) -> list[PracticeTraceSpan]:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        rows = (
            (
                await self._connection.execute(
                    select(practice_trace_spans)
                    .where(practice_trace_spans.c.trace_id == trace_id)
                    .order_by(practice_trace_spans.c.step_id)
                )
            )
            .mappings()
            .all()
        )
        return [_span_from_row(row) for row in rows]

    async def _load_one(
        self,
        trace_id: str,
        step_id: int,
    ) -> PracticeTraceSpan | None:
        row = (
            (
                await self._connection.execute(
                    select(practice_trace_spans).where(
                        practice_trace_spans.c.trace_id == trace_id,
                        practice_trace_spans.c.step_id == step_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _span_from_row(row)


class CommittedPostgresPracticeTraceRepository:
    """Commit each append-only span independently from workflow side effects."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def next_step_id(self, trace_id: str) -> int:
        async with self._engine.connect() as connection:
            return await PostgresPracticeTraceRepository(connection).next_step_id(trace_id)

    async def append(self, span: PracticeTraceSpan) -> PracticeTraceAppendResult:
        async with self._engine.begin() as connection:
            return await PostgresPracticeTraceRepository(connection).append(span)

    async def list_trace(self, trace_id: str) -> list[PracticeTraceSpan]:
        async with self._engine.connect() as connection:
            return await PostgresPracticeTraceRepository(connection).list_trace(trace_id)


def _span_from_row(row: Any) -> PracticeTraceSpan:
    return PracticeTraceSpan(
        trace_id=row["trace_id"],
        step_id=row["step_id"],
        name=row["span_name"],
        status=row["status"],
        input_summary=row["input_summary"],
        output_summary=row["output_summary"],
        versions=row["versions"],
        started_at=row["started_at"],
        completed_at=row["completed_at"],
        duration_ms=row["duration_ms"],
        retry_count=row["retry_count"],
        llm_calls=row["llm_calls"],
        input_tokens=row["input_tokens"],
        output_tokens=row["output_tokens"],
        error_code=row["error_code"],
        related_record_ids=tuple(row["related_record_ids"]),
    )


__all__ = [
    "CommittedPostgresPracticeTraceRepository",
    "PostgresPracticeTraceRepository",
    "PracticeTraceAppendResult",
    "PracticeTraceRepository",
]
