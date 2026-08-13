"""Caller-transaction persistence for isolated Stage 07 baseline facts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from deeptutor.plugins.host_services import validate_embedding_batch
from exam_mem.backends.protocol import BackendMode
from exam_mem.contracts import LearningContext, MemoryScope, MemoryUpdateCandidate
from exam_mem.domain.slot_key import validate_slot_key

from .event_repository import AppendStatus
from .models import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    baseline_memory_facts,
    learning_events,
)

_BASELINE_MODES = frozenset({BackendMode.APPEND_ONLY, BackendMode.VECTOR})


class BaselineFactRecord(BaseModel):
    """One typed fact owned only by an append-only or vector baseline."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    backend_mode: BackendMode
    candidate: MemoryUpdateCandidate
    created_at: AwareDatetime
    content_embedding: tuple[float, ...] | None = None

    @field_validator("content_embedding")
    @classmethod
    def validate_content_embedding(
        cls,
        value: tuple[float, ...] | None,
    ) -> tuple[float, ...] | None:
        if value is None:
            return None
        return tuple(_validate_embedding(value))

    @model_validator(mode="after")
    def validate_mode_shape(self) -> BaselineFactRecord:
        if self.backend_mode not in _BASELINE_MODES:
            raise ValueError("baseline facts require append_only or vector backend mode")
        if self.backend_mode is BackendMode.APPEND_ONLY and self.content_embedding is not None:
            raise ValueError("append_only baseline facts must not contain an embedding")
        if self.backend_mode is BackendMode.VECTOR and self.content_embedding is None:
            raise ValueError("vector baseline facts require an embedding")

        slot_key = str(validate_slot_key(self.candidate.slot_key))
        if slot_key.partition(":")[0] != self.candidate.scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match candidate scope")
        return self


@dataclass(frozen=True, slots=True)
class BaselineFactAppendResult:
    """Outcome of one idempotent baseline-fact append attempt."""

    status: AppendStatus
    record: BaselineFactRecord | None


class BaselineFactSourceEventError(ValueError):
    """Raised when the candidate's L1 source is absent from its context."""


@runtime_checkable
class BaselineFactRepository(Protocol):
    async def append(self, record: BaselineFactRecord) -> BaselineFactAppendResult: ...

    async def list_scope(
        self,
        backend_mode: BackendMode,
        scope: MemoryScope,
        limit: int,
    ) -> list[BaselineFactRecord]: ...

    async def find_similar(
        self,
        scope: MemoryScope,
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[BaselineFactRecord]: ...

    async def snapshot(
        self,
        backend_mode: BackendMode,
        context: LearningContext,
    ) -> list[BaselineFactRecord]: ...


class PostgresBaselineFactRepository:
    """Persist baseline facts without committing the caller-owned transaction."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def append(self, record: BaselineFactRecord) -> BaselineFactAppendResult:
        validated_record = BaselineFactRecord.model_validate(record)
        await self._require_source_event(validated_record.candidate)
        row = _fact_row(validated_record)
        statement = (
            postgresql_insert(baseline_memory_facts)
            .values(**row)
            .on_conflict_do_nothing(
                index_elements=[
                    baseline_memory_facts.c.backend_mode,
                    baseline_memory_facts.c.event_id,
                    baseline_memory_facts.c.slot_key,
                ]
            )
            .returning(baseline_memory_facts.c.event_id)
        )

        async with self._connection.begin_nested():
            inserted_event_id = await self._connection.scalar(statement)
            if inserted_event_id is not None:
                return BaselineFactAppendResult(
                    status=AppendStatus.CREATED,
                    record=validated_record,
                )

            existing = await self._load_one(
                validated_record.backend_mode,
                validated_record.candidate.event_id,
                validated_record.candidate.slot_key,
            )
            if existing is None:
                return BaselineFactAppendResult(
                    status=AppendStatus.CONFLICT,
                    record=None,
                )
            status = (
                AppendStatus.EXISTING if existing == validated_record else AppendStatus.CONFLICT
            )
            return BaselineFactAppendResult(status=status, record=existing)

    async def list_scope(
        self,
        backend_mode: BackendMode,
        scope: MemoryScope,
        limit: int,
    ) -> list[BaselineFactRecord]:
        mode = _validate_mode(backend_mode)
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")
        statement = (
            select(baseline_memory_facts)
            .where(
                baseline_memory_facts.c.backend_mode == mode.value,
                *_scope_predicates(scope),
            )
            .order_by(
                baseline_memory_facts.c.created_at,
                baseline_memory_facts.c.event_id,
                baseline_memory_facts.c.slot_key,
            )
            .limit(limit)
        )
        return await self._load_many(statement)

    async def find_similar(
        self,
        scope: MemoryScope,
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[BaselineFactRecord]:
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")
        embedding = _validate_embedding(query_embedding)
        distance = baseline_memory_facts.c.content_embedding.cosine_distance(embedding)
        statement = (
            select(baseline_memory_facts)
            .where(
                baseline_memory_facts.c.backend_mode == BackendMode.VECTOR.value,
                *_scope_predicates(scope),
                baseline_memory_facts.c.content_embedding.is_not(None),
            )
            .order_by(
                distance,
                baseline_memory_facts.c.event_id,
                baseline_memory_facts.c.slot_key,
            )
            .limit(limit)
        )
        return await self._load_many(statement)

    async def snapshot(
        self,
        backend_mode: BackendMode,
        context: LearningContext,
    ) -> list[BaselineFactRecord]:
        mode = _validate_mode(backend_mode)
        statement = (
            select(baseline_memory_facts)
            .where(
                baseline_memory_facts.c.backend_mode == mode.value,
                *_context_predicates(context),
            )
            .order_by(
                baseline_memory_facts.c.memory_namespace,
                baseline_memory_facts.c.slot_key,
                baseline_memory_facts.c.created_at,
                baseline_memory_facts.c.event_id,
            )
        )
        return await self._load_many(statement)

    async def _require_source_event(self, candidate: MemoryUpdateCandidate) -> None:
        event_id = await self._connection.scalar(
            select(learning_events.c.event_id).where(
                learning_events.c.event_id == candidate.event_id,
                learning_events.c.user_id == candidate.scope.user_id,
                learning_events.c.exam_id == candidate.scope.exam_id,
                learning_events.c.subject_id == candidate.scope.subject_id,
            )
        )
        if event_id is None:
            raise BaselineFactSourceEventError(
                "baseline fact source event does not belong to the candidate context"
            )

    async def _load_one(
        self,
        backend_mode: BackendMode,
        event_id: str,
        slot_key: str,
    ) -> BaselineFactRecord | None:
        row = (
            (
                await self._connection.execute(
                    select(baseline_memory_facts).where(
                        baseline_memory_facts.c.backend_mode == backend_mode.value,
                        baseline_memory_facts.c.event_id == event_id,
                        baseline_memory_facts.c.slot_key == slot_key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _record_from_row(row)

    async def _load_many(self, statement: Any) -> list[BaselineFactRecord]:
        rows = (await self._connection.execute(statement)).mappings().all()
        return [_record_from_row(row) for row in rows]


def _validate_mode(backend_mode: BackendMode) -> BackendMode:
    try:
        mode = BackendMode(backend_mode)
    except ValueError as exc:
        raise ValueError(f"unsupported backend mode: {backend_mode!r}") from exc
    if mode not in _BASELINE_MODES:
        raise ValueError("baseline facts require append_only or vector backend mode")
    return mode


def _validate_embedding(embedding: Sequence[float]) -> list[float]:
    vector = validate_embedding_batch(
        [embedding],
        expected_count=1,
        binding="exam_mem_baseline_fact",
    )[0]
    if len(vector) != LEARNING_MEMORY_EMBEDDING_DIMENSION:
        raise ValueError(
            "ExamMem embedding must have exactly "
            f"{LEARNING_MEMORY_EMBEDDING_DIMENSION} dimensions; received {len(vector)}"
        )
    if math.fsum(value * value for value in vector) == 0.0:
        raise ValueError("ExamMem embedding vector must be non-zero")
    return vector


def _fact_row(record: BaselineFactRecord) -> dict[str, Any]:
    candidate = record.candidate
    scope = candidate.scope
    candidate_payload = candidate.model_dump(mode="json")
    return {
        "backend_mode": record.backend_mode.value,
        "event_id": candidate.event_id,
        "user_id": scope.user_id,
        "exam_id": scope.exam_id,
        "subject_id": scope.subject_id,
        "memory_namespace": scope.memory_namespace.value,
        "slot_key": candidate.slot_key,
        "value": candidate_payload["proposed_value"],
        "evidence": candidate_payload["evidence"],
        "created_at": record.created_at,
        "content_embedding": (
            None if record.content_embedding is None else list(record.content_embedding)
        ),
    }


def _record_from_row(row: Mapping[str, Any]) -> BaselineFactRecord:
    embedding = row["content_embedding"]
    return BaselineFactRecord(
        backend_mode=row["backend_mode"],
        candidate=MemoryUpdateCandidate(
            event_id=row["event_id"],
            scope=MemoryScope(
                user_id=row["user_id"],
                exam_id=row["exam_id"],
                subject_id=row["subject_id"],
                memory_namespace=row["memory_namespace"],
            ),
            slot_key=row["slot_key"],
            proposed_value=row["value"],
            evidence=row["evidence"],
        ),
        content_embedding=(
            None if embedding is None else tuple(float(value) for value in embedding)
        ),
        created_at=row["created_at"],
    )


def _context_predicates(context: LearningContext) -> tuple[Any, ...]:
    return (
        baseline_memory_facts.c.user_id == context.user_id,
        baseline_memory_facts.c.exam_id == context.exam_id,
        baseline_memory_facts.c.subject_id == context.subject_id,
    )


def _scope_predicates(scope: MemoryScope) -> tuple[Any, ...]:
    return (
        *_context_predicates(scope),
        baseline_memory_facts.c.memory_namespace == scope.memory_namespace.value,
    )


__all__ = [
    "BaselineFactAppendResult",
    "BaselineFactRecord",
    "BaselineFactRepository",
    "BaselineFactSourceEventError",
    "PostgresBaselineFactRepository",
]
