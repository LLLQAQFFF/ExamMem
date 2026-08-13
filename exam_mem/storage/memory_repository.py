"""Scope-safe PostgreSQL reads for versioned L2 Learning Memories."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import math
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import Select, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from deeptutor.plugins.host_services import validate_embedding_batch
from exam_mem.contracts import LearningMemory, LifecycleState, MemoryScope
from exam_mem.domain.candidate_query import CANDIDATE_LIFECYCLE_STATES, CandidateQuery
from exam_mem.domain.slot_key import validate_slot_key
from exam_mem.lifecycle.contracts import LifecycleCandidateSnapshot, LifecycleMemorySnapshot

from .models import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    learning_events,
    learning_memories,
    memory_provenance,
)


class RepositoryInvariantError(RuntimeError):
    """Raised when persisted rows cannot satisfy the frozen public contract."""


class MemoryVersionConflict(RuntimeError):
    """Raised when an L2 version cannot be inserted continuously and uniquely."""


class MemoryProvenanceValidationError(ValueError):
    """Raised when L2 evidence does not resolve inside the same context."""


@runtime_checkable
class LearningMemoryRepository(Protocol):
    async def find_candidates(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LearningMemory]: ...

    async def find_candidate_snapshots(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LifecycleCandidateSnapshot]: ...

    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]: ...

    async def list_contested_group_snapshots(
        self,
        scope: MemoryScope,
    ) -> list[LifecycleMemorySnapshot]: ...

    async def list_slot_snapshots(
        self,
        scope: MemoryScope,
        slot_key: str,
    ) -> list[LifecycleMemorySnapshot]: ...

    async def insert_version(
        self,
        memory: LearningMemory,
        *,
        policy_version: str,
        content_embedding: Sequence[float] | None = None,
        contested_group_id: str | None = None,
        provenance_relations: Mapping[str, str] | None = None,
    ) -> LifecycleMemorySnapshot: ...

    async def get_lifecycle_snapshot(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> LifecycleMemorySnapshot | None: ...

    async def next_version(self, scope: MemoryScope, slot_key: str) -> int: ...

    async def event_was_applied(
        self,
        scope: MemoryScope,
        slot_key: str,
        event_id: str,
    ) -> bool: ...

    async def cas_transition(
        self,
        scope: MemoryScope,
        slot_key: str,
        memory_id: str,
        *,
        expected_row_version: int,
        to_state: LifecycleState,
        valid_to: datetime | None,
        superseded_by: str | None = None,
        contested_group_id: str | None = None,
        provenance_event_id: str | None = None,
        provenance_relation: str | None = None,
    ) -> LifecycleMemorySnapshot | None: ...

    async def compare_and_archive(
        self,
        scope: MemoryScope,
        memory_id: str,
        *,
        expected_row_version: int,
        valid_to: datetime,
        superseded_by: str,
    ) -> bool: ...

    async def find_similar(
        self,
        scope: MemoryScope,
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[LearningMemory]: ...


class PostgresLearningMemoryRepository:
    """Read L2 state through mandatory four-dimensional Scope predicates."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def find_candidates(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LearningMemory]:
        snapshots = await self.find_candidate_snapshots(query, for_update=for_update)
        return [snapshot.memory for snapshot in snapshots]

    async def find_candidate_snapshots(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LifecycleCandidateSnapshot]:
        statement = select(learning_memories).where(
            *_scope_predicates(query.scope),
            learning_memories.c.slot_key == query.slot_key,
            learning_memories.c.lifecycle_state.in_(
                state.value for state in CANDIDATE_LIFECYCLE_STATES
            ),
        )
        if query.current_memory_id is not None:
            statement = statement.where(learning_memories.c.memory_id != query.current_memory_id)
        statement = statement.order_by(
            learning_memories.c.version,
            learning_memories.c.memory_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return [
            LifecycleCandidateSnapshot.model_validate(snapshot.model_dump())
            for snapshot in await self._load_lifecycle_snapshots(statement)
        ]

    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]:
        statement = (
            select(learning_memories)
            .where(*_scope_predicates(scope))
            .order_by(
                learning_memories.c.slot_key,
                learning_memories.c.version,
                learning_memories.c.memory_id,
            )
        )
        return await self._load_memories(statement)

    async def list_contested_group_snapshots(
        self,
        scope: MemoryScope,
    ) -> list[LifecycleMemorySnapshot]:
        """Return current and historical rows needed to derive open group age."""
        statement = (
            select(learning_memories)
            .where(
                *_scope_predicates(scope),
                learning_memories.c.contested_group_id.is_not(None),
            )
            .order_by(
                learning_memories.c.contested_group_id,
                learning_memories.c.slot_key,
                learning_memories.c.version,
                learning_memories.c.memory_id,
            )
        )
        return await self._load_lifecycle_snapshots(statement)

    async def list_slot_snapshots(
        self,
        scope: MemoryScope,
        slot_key: str,
    ) -> list[LifecycleMemorySnapshot]:
        """Return the complete append-only version chain for one scoped slot."""
        validated_slot_key = str(validate_slot_key(slot_key))
        if validated_slot_key.partition(":")[0] != scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        statement = (
            select(learning_memories)
            .where(
                *_scope_predicates(scope),
                learning_memories.c.slot_key == validated_slot_key,
            )
            .order_by(
                learning_memories.c.version,
                learning_memories.c.memory_id,
            )
        )
        return await self._load_lifecycle_snapshots(statement)

    async def find_similar(
        self,
        scope: MemoryScope,
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[LearningMemory]:
        if limit < 1:
            raise ValueError("limit must be greater than or equal to 1")
        validated_embedding = _validate_embedding(query_embedding)
        distance = learning_memories.c.content_embedding.cosine_distance(validated_embedding)
        statement = (
            select(learning_memories)
            .where(
                *_scope_predicates(scope),
                learning_memories.c.lifecycle_state.in_(
                    state.value for state in CANDIDATE_LIFECYCLE_STATES
                ),
                learning_memories.c.content_embedding.is_not(None),
            )
            .order_by(distance, learning_memories.c.memory_id)
            .limit(limit)
        )
        return await self._load_memories(statement)

    async def insert_version(
        self,
        memory: LearningMemory,
        *,
        policy_version: str,
        content_embedding: Sequence[float] | None = None,
        contested_group_id: str | None = None,
        provenance_relations: Mapping[str, str] | None = None,
    ) -> LifecycleMemorySnapshot:
        if not policy_version.strip():
            raise ValueError("policy_version must not be blank")
        validated_slot_key = validate_slot_key(memory.slot_key)
        if validated_slot_key.partition(":")[0] != memory.scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        if len(memory.provenance) != len(set(memory.provenance)):
            raise MemoryProvenanceValidationError("memory provenance must not contain duplicates")
        if memory.evidence_count != len(memory.provenance):
            raise MemoryProvenanceValidationError(
                "evidence_count must equal the number of provenance events"
            )
        if memory.lifecycle_state is LifecycleState.CONTESTED and not contested_group_id:
            raise ValueError("contested memory requires contested_group_id")
        relations = _resolve_provenance_relations(memory, provenance_relations)
        validated_embedding = (
            _validate_embedding(content_embedding) if content_embedding is not None else None
        )

        async with self._connection.begin_nested():
            await self._validate_provenance_context(memory)
            existing_versions = (
                await self._connection.scalars(
                    select(learning_memories.c.version)
                    .where(
                        *_scope_predicates(memory.scope),
                        learning_memories.c.slot_key == memory.slot_key,
                    )
                    .with_for_update()
                )
            ).all()
            expected_version = max(existing_versions, default=0) + 1
            if memory.version != expected_version:
                raise MemoryVersionConflict(
                    f"expected version {expected_version}, received {memory.version}"
                )

            inserted_memory_id = await self._connection.scalar(
                postgresql_insert(learning_memories)
                .values(
                    **_memory_row(
                        memory,
                        policy_version=policy_version,
                        content_embedding=validated_embedding,
                        contested_group_id=contested_group_id,
                    )
                )
                .on_conflict_do_nothing()
                .returning(learning_memories.c.memory_id)
            )
            if inserted_memory_id is None:
                raise MemoryVersionConflict("memory version conflicts with persisted L2 state")

            await self._connection.execute(
                insert(memory_provenance),
                [
                    {
                        "memory_id": memory.memory_id,
                        "event_id": event_id,
                        "relation_type": relations[event_id],
                    }
                    for event_id in memory.provenance
                ],
            )
            snapshot = await self.get_lifecycle_snapshot(memory.scope, memory.memory_id)
            if snapshot is None:
                raise RepositoryInvariantError("inserted learning memory cannot be reloaded")
            return snapshot

    async def get_lifecycle_snapshot(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> LifecycleMemorySnapshot | None:
        statement = select(learning_memories).where(
            *_scope_predicates(scope),
            learning_memories.c.memory_id == memory_id,
        )
        row = (await self._connection.execute(statement)).mappings().one_or_none()
        if row is None:
            return None
        return await self._snapshot_from_row(dict(row))

    async def next_version(self, scope: MemoryScope, slot_key: str) -> int:
        validated_slot_key = str(validate_slot_key(slot_key))
        if validated_slot_key.partition(":")[0] != scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        versions = (
            await self._connection.scalars(
                select(learning_memories.c.version)
                .where(
                    *_scope_predicates(scope),
                    learning_memories.c.slot_key == validated_slot_key,
                )
                .with_for_update()
            )
        ).all()
        return max(versions, default=0) + 1

    async def event_was_applied(
        self,
        scope: MemoryScope,
        slot_key: str,
        event_id: str,
    ) -> bool:
        validated_slot_key = str(validate_slot_key(slot_key))
        if validated_slot_key.partition(":")[0] != scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        applied_event_id = await self._connection.scalar(
            select(memory_provenance.c.event_id)
            .select_from(
                memory_provenance.join(
                    learning_memories,
                    memory_provenance.c.memory_id == learning_memories.c.memory_id,
                )
            )
            .where(
                *_scope_predicates(scope),
                learning_memories.c.slot_key == validated_slot_key,
                memory_provenance.c.event_id == event_id,
            )
            .limit(1)
        )
        return applied_event_id is not None

    async def cas_transition(
        self,
        scope: MemoryScope,
        slot_key: str,
        memory_id: str,
        *,
        expected_row_version: int,
        to_state: LifecycleState,
        valid_to: datetime | None,
        superseded_by: str | None = None,
        contested_group_id: str | None = None,
        provenance_event_id: str | None = None,
        provenance_relation: str | None = None,
    ) -> LifecycleMemorySnapshot | None:
        if expected_row_version < 1:
            raise ValueError("expected_row_version must be greater than or equal to 1")
        validated_slot_key = str(validate_slot_key(slot_key))
        if validated_slot_key.partition(":")[0] != scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        _validate_transition_shape(
            to_state=to_state,
            valid_to=valid_to,
            superseded_by=superseded_by,
            memory_id=memory_id,
            contested_group_id=contested_group_id,
            provenance_event_id=provenance_event_id,
            provenance_relation=provenance_relation,
        )

        async with self._connection.begin_nested():
            transitioned_memory_id = await self._connection.scalar(
                update(learning_memories)
                .where(
                    *_scope_predicates(scope),
                    learning_memories.c.slot_key == validated_slot_key,
                    learning_memories.c.memory_id == memory_id,
                    learning_memories.c.lifecycle_state.in_(
                        state.value for state in CANDIDATE_LIFECYCLE_STATES
                    ),
                    learning_memories.c.row_version == expected_row_version,
                )
                .values(
                    lifecycle_state=to_state.value,
                    valid_to=valid_to,
                    superseded_by=superseded_by,
                    contested_group_id=contested_group_id,
                    evidence_count=(
                        learning_memories.c.evidence_count + 1
                        if provenance_event_id is not None
                        else learning_memories.c.evidence_count
                    ),
                    row_version=learning_memories.c.row_version + 1,
                    updated_at=func.now(),
                )
                .returning(learning_memories.c.memory_id)
            )
            if transitioned_memory_id is None:
                return None

            if provenance_event_id is not None:
                await self._validate_event_context(scope, provenance_event_id)
                existing_event = await self._connection.scalar(
                    select(memory_provenance.c.event_id).where(
                        memory_provenance.c.memory_id == memory_id,
                        memory_provenance.c.event_id == provenance_event_id,
                    )
                )
                if existing_event is not None:
                    raise MemoryProvenanceValidationError(
                        "CAS provenance event is already attached to memory"
                    )
                await self._connection.execute(
                    insert(memory_provenance).values(
                        memory_id=memory_id,
                        event_id=provenance_event_id,
                        relation_type=provenance_relation,
                    )
                )

            snapshot = await self.get_lifecycle_snapshot(scope, memory_id)
            if snapshot is None:
                raise RepositoryInvariantError("transitioned learning memory cannot be reloaded")
            return snapshot

    async def compare_and_archive(
        self,
        scope: MemoryScope,
        memory_id: str,
        *,
        expected_row_version: int,
        valid_to: datetime,
        superseded_by: str,
    ) -> bool:
        snapshot = await self.get_lifecycle_snapshot(scope, memory_id)
        if (
            snapshot is None
            or snapshot.memory.lifecycle_state is not LifecycleState.ACTIVE
            or valid_to < snapshot.memory.valid_from
        ):
            return False
        transitioned = await self.cas_transition(
            scope,
            snapshot.memory.slot_key,
            memory_id,
            expected_row_version=expected_row_version,
            to_state=LifecycleState.ARCHIVED,
            valid_to=valid_to,
            superseded_by=superseded_by,
        )
        return transitioned is not None

    async def _validate_provenance_context(self, memory: LearningMemory) -> None:
        rows = (
            await self._connection.execute(
                select(
                    learning_events.c.event_id,
                    learning_events.c.user_id,
                    learning_events.c.exam_id,
                    learning_events.c.subject_id,
                ).where(learning_events.c.event_id.in_(memory.provenance))
            )
        ).mappings()
        events_by_id = {row["event_id"]: row for row in rows}
        if set(events_by_id) != set(memory.provenance):
            raise MemoryProvenanceValidationError("memory provenance event does not exist")

        expected_context = (
            memory.scope.user_id,
            memory.scope.exam_id,
            memory.scope.subject_id,
        )
        for event_id in memory.provenance:
            event = events_by_id[event_id]
            event_context = (
                event["user_id"],
                event["exam_id"],
                event["subject_id"],
            )
            if event_context != expected_context:
                raise MemoryProvenanceValidationError(
                    "memory provenance must use the same learning context"
                )

    async def _validate_event_context(self, scope: MemoryScope, event_id: str) -> None:
        row = (
            (
                await self._connection.execute(
                    select(
                        learning_events.c.user_id,
                        learning_events.c.exam_id,
                        learning_events.c.subject_id,
                    ).where(learning_events.c.event_id == event_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise MemoryProvenanceValidationError("memory provenance event does not exist")
        if (row["user_id"], row["exam_id"], row["subject_id"]) != (
            scope.user_id,
            scope.exam_id,
            scope.subject_id,
        ):
            raise MemoryProvenanceValidationError(
                "memory provenance must use the same learning context"
            )

    async def _snapshot_from_row(self, row: dict[str, Any]) -> LifecycleMemorySnapshot:
        provenance = await self._load_provenance(row["memory_id"])
        return LifecycleMemorySnapshot(
            memory=_memory_from_row(row, provenance=provenance),
            row_version=row["row_version"],
            contested_group_id=row["contested_group_id"],
            policy_version=row["policy_version"],
        )

    async def _load_provenance(self, memory_id: str) -> list[str]:
        rows = (
            await self._connection.execute(
                select(memory_provenance.c.event_id)
                .where(memory_provenance.c.memory_id == memory_id)
                .order_by(
                    memory_provenance.c.event_id,
                    memory_provenance.c.relation_type,
                )
            )
        ).all()
        provenance = list(dict.fromkeys(event_id for (event_id,) in rows))
        if not provenance:
            raise RepositoryInvariantError(f"learning memory {memory_id!r} has no provenance")
        return provenance

    async def _load_memories(self, statement: Select[Any]) -> list[LearningMemory]:
        snapshots = await self._load_lifecycle_snapshots(statement)
        return [snapshot.memory for snapshot in snapshots]

    async def _load_lifecycle_snapshots(
        self,
        statement: Select[Any],
    ) -> list[LifecycleMemorySnapshot]:
        rows = [dict(row) for row in (await self._connection.execute(statement)).mappings()]
        if not rows:
            return []

        memory_ids = [row["memory_id"] for row in rows]
        provenance_rows = (
            await self._connection.execute(
                select(
                    memory_provenance.c.memory_id,
                    memory_provenance.c.event_id,
                )
                .where(memory_provenance.c.memory_id.in_(memory_ids))
                .order_by(
                    memory_provenance.c.memory_id,
                    memory_provenance.c.event_id,
                    memory_provenance.c.relation_type,
                )
            )
        ).all()
        provenance_by_memory: dict[str, list[str]] = {memory_id: [] for memory_id in memory_ids}
        for memory_id, event_id in provenance_rows:
            if event_id not in provenance_by_memory[memory_id]:
                provenance_by_memory[memory_id].append(event_id)

        snapshots: list[LifecycleMemorySnapshot] = []
        for row in rows:
            provenance = provenance_by_memory[row["memory_id"]]
            if not provenance:
                raise RepositoryInvariantError(
                    f"learning memory {row['memory_id']!r} has no provenance"
                )
            snapshots.append(
                LifecycleMemorySnapshot(
                    memory=_memory_from_row(row, provenance=provenance),
                    row_version=row["row_version"],
                    contested_group_id=row["contested_group_id"],
                    policy_version=row["policy_version"],
                )
            )
        return snapshots


def _scope_predicates(scope: MemoryScope) -> tuple[object, ...]:
    return (
        learning_memories.c.user_id == scope.user_id,
        learning_memories.c.exam_id == scope.exam_id,
        learning_memories.c.subject_id == scope.subject_id,
        learning_memories.c.memory_namespace == scope.memory_namespace.value,
    )


def _memory_from_row(row: dict[str, Any], *, provenance: list[str]) -> LearningMemory:
    return LearningMemory.model_validate(
        {
            "memory_id": row["memory_id"],
            "scope": {
                "user_id": row["user_id"],
                "exam_id": row["exam_id"],
                "subject_id": row["subject_id"],
                "memory_namespace": row["memory_namespace"],
            },
            "slot_key": row["slot_key"],
            "value": row["value"],
            "confidence": row["confidence"],
            "evidence_count": row["evidence_count"],
            "lifecycle_state": row["lifecycle_state"],
            "version": row["version"],
            "valid_from": row["valid_from"],
            "valid_to": row["valid_to"],
            "superseded_by": row["superseded_by"],
            "provenance": provenance,
        }
    )


def _memory_row(
    memory: LearningMemory,
    *,
    policy_version: str,
    content_embedding: list[float] | None,
    contested_group_id: str | None,
) -> dict[str, Any]:
    return {
        "memory_id": memory.memory_id,
        "user_id": memory.scope.user_id,
        "exam_id": memory.scope.exam_id,
        "subject_id": memory.scope.subject_id,
        "memory_namespace": memory.scope.memory_namespace.value,
        "slot_key": memory.slot_key,
        "value": memory.value.model_dump(mode="json"),
        "confidence": memory.confidence,
        "evidence_count": memory.evidence_count,
        "lifecycle_state": memory.lifecycle_state.value,
        "version": memory.version,
        "row_version": 1,
        "valid_from": memory.valid_from,
        "valid_to": memory.valid_to,
        "superseded_by": memory.superseded_by,
        "contested_group_id": contested_group_id,
        "content_embedding": content_embedding,
        "policy_version": policy_version,
    }


_PROVENANCE_RELATIONS = {
    "created_by",
    "merged_from",
    "contradicted_by",
    "invalidated_by",
}


def _resolve_provenance_relations(
    memory: LearningMemory,
    relations: Mapping[str, str] | None,
) -> dict[str, str]:
    resolved = (
        {event_id: "created_by" for event_id in memory.provenance}
        if relations is None
        else dict(relations)
    )
    if set(resolved) != set(memory.provenance):
        raise MemoryProvenanceValidationError(
            "provenance_relations must describe every provenance event exactly once"
        )
    if any(relation not in _PROVENANCE_RELATIONS for relation in resolved.values()):
        raise MemoryProvenanceValidationError("unsupported memory provenance relation")
    return resolved


def _validate_transition_shape(
    *,
    to_state: LifecycleState,
    valid_to: datetime | None,
    superseded_by: str | None,
    memory_id: str,
    contested_group_id: str | None,
    provenance_event_id: str | None,
    provenance_relation: str | None,
) -> None:
    if valid_to is not None and valid_to.utcoffset() is None:
        raise ValueError("valid_to must include timezone information")
    if to_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}:
        if valid_to is None:
            raise ValueError("terminal lifecycle transition requires valid_to")
    elif valid_to is not None:
        raise ValueError("writable lifecycle transition must not set valid_to")

    if superseded_by is not None:
        if not superseded_by.strip():
            raise ValueError("superseded_by must not be blank")
        if superseded_by == memory_id:
            raise ValueError("superseded_by must not reference the same memory")
    if to_state is LifecycleState.CONTESTED and not contested_group_id:
        raise ValueError("contested transition requires contested_group_id")

    if (provenance_event_id is None) != (provenance_relation is None):
        raise ValueError("provenance event and relation must be supplied together")
    if provenance_relation is not None and provenance_relation not in _PROVENANCE_RELATIONS:
        raise MemoryProvenanceValidationError("unsupported memory provenance relation")


def _validate_embedding(embedding: Sequence[float]) -> list[float]:
    vector = validate_embedding_batch(
        [embedding],
        expected_count=1,
        binding="exam_mem_learning_memory",
    )[0]
    if len(vector) != LEARNING_MEMORY_EMBEDDING_DIMENSION:
        raise ValueError(
            "ExamMem embedding must have exactly "
            f"{LEARNING_MEMORY_EMBEDDING_DIMENSION} dimensions; received {len(vector)}"
        )
    if math.fsum(value * value for value in vector) == 0.0:
        raise ValueError("ExamMem embedding vector must be non-zero")
    return vector


__all__ = [
    "LearningMemoryRepository",
    "MemoryProvenanceValidationError",
    "MemoryVersionConflict",
    "PostgresLearningMemoryRepository",
    "RepositoryInvariantError",
]
