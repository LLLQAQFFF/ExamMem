from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from typing import Any

import pytest
from sqlalchemy import event as sqlalchemy_event
from sqlalchemy import insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from exam_mem.contracts import LearningEvent, LearningMemory, LifecycleState, MemoryNamespace
from exam_mem.domain import build_memory_scope
from exam_mem.domain.candidate_query import CandidateMatchReason, build_candidate_query
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    AppendStatus,
    LearningMemoryRepository,
    MemoryProvenanceValidationError,
    MemoryVersionConflict,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    RepositoryInvariantError,
    load_database_settings,
)
from exam_mem.storage.models import learning_memories, memory_provenance

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]

NOW = datetime(2026, 8, 11, tzinfo=timezone.utc)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def _append_source_event(
    connection: AsyncConnection,
    *,
    event_id: str,
    user_id: str = "memory_repository_user",
) -> None:
    event = LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"{event_id}-idempotency",
            "event_type": "answer_attempt",
            "context": {
                "user_id": user_id,
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            "session_id": "memory_repository_session",
            "question_id": "memory_repository_question",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "difficulty": 0.5,
            "answer_correct": True,
            "occurred_at": NOW,
        }
    )
    assert (
        await PostgresLearningEventRepository(connection).append(event)
    ).status is AppendStatus.CREATED


async def _insert_memory(
    connection: AsyncConnection,
    *,
    memory_id: str,
    user_id: str = "memory_repository_user",
    namespace: str = "mastery",
    lifecycle_state: str = "active",
    version: int = 1,
    with_provenance: bool = True,
    event_id: str = "memory_repository_event_001",
    contested_group_id: str | None = None,
) -> None:
    valid_to = NOW + timedelta(days=1) if lifecycle_state in {"archived", "invalidated"} else None
    value = (
        {"type": "mastery", "level": "low", "score": 0.3}
        if namespace == "mastery"
        else {"type": "preference", "attribute": "format", "content": "concise"}
    )
    await connection.execute(
        insert(learning_memories).values(
            memory_id=memory_id,
            user_id=user_id,
            exam_id="postgraduate_entrance_exam",
            subject_id="math_1",
            memory_namespace=namespace,
            slot_key=SLOT_KEY if namespace == "mastery" else "preference:format",
            value=value,
            confidence=0.8,
            evidence_count=1,
            lifecycle_state=lifecycle_state,
            version=version,
            row_version=1,
            valid_from=NOW,
            valid_to=valid_to,
            superseded_by=None,
            contested_group_id=contested_group_id,
            content_embedding=None,
            policy_version="stage05_repository_test",
        )
    )
    if with_provenance:
        await connection.execute(
            insert(memory_provenance).values(
                memory_id=memory_id,
                event_id=event_id,
                relation_type="created_by",
            )
        )


def _memory_version(
    *,
    memory_id: str,
    version: int,
    provenance: list[str],
    lifecycle_state: str = "active",
    slot_key: str = SLOT_KEY,
    user_id: str = "memory_repository_user",
) -> LearningMemory:
    valid_to = (
        NOW + timedelta(minutes=1) if lifecycle_state in {"archived", "invalidated"} else None
    )
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": {
                "user_id": user_id,
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
                "memory_namespace": "mastery",
            },
            "slot_key": slot_key,
            "value": {"type": "mastery", "level": "low", "score": 0.3},
            "confidence": 0.8,
            "evidence_count": len(provenance),
            "lifecycle_state": lifecycle_state,
            "version": version,
            "valid_from": NOW,
            "valid_to": valid_to,
            "superseded_by": None,
            "provenance": provenance,
        }
    )


def _basis_vector(axis: int) -> list[float]:
    vector = [0.0] * LEARNING_MEMORY_EMBEDDING_DIMENSION
    vector[axis] = 1.0
    return vector


async def test_candidate_and_snapshot_reads_enforce_full_scope_and_state() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _append_source_event(connection, event_id="memory_repository_event_001")
                await _insert_memory(
                    connection,
                    memory_id="memory_candidate_active",
                    lifecycle_state="active",
                    version=1,
                    contested_group_id="memory_repository_open_group",
                )
                await _insert_memory(
                    connection,
                    memory_id="memory_candidate_contested",
                    lifecycle_state="contested",
                    version=2,
                    contested_group_id="memory_repository_open_group",
                )
                await _insert_memory(
                    connection,
                    memory_id="memory_candidate_archived",
                    lifecycle_state="archived",
                    version=3,
                    contested_group_id="memory_repository_open_group",
                )
                await _insert_memory(
                    connection,
                    memory_id="memory_other_user",
                    user_id="memory_repository_other_user",
                )
                await _insert_memory(
                    connection,
                    memory_id="memory_other_namespace",
                    namespace="preference",
                )

                scope = build_memory_scope(
                    user_id="memory_repository_user",
                    exam_id="postgraduate_entrance_exam",
                    subject_id="math_1",
                    memory_namespace=MemoryNamespace.MASTERY,
                )
                query = build_candidate_query(
                    scope=scope,
                    slot_key=SLOT_KEY,
                    match_reason=CandidateMatchReason.EXACT_SLOT,
                )
                repository = PostgresLearningMemoryRepository(connection)
                assert isinstance(repository, LearningMemoryRepository)

                candidates = await repository.find_candidates(query)
                candidate_snapshots = await repository.find_candidate_snapshots(query)
                snapshot = await repository.snapshot(scope)
                contested_group_snapshots = await repository.list_contested_group_snapshots(scope)
                slot_snapshots = await repository.list_slot_snapshots(scope, SLOT_KEY)
                excluding_current = await repository.find_candidates(
                    query.model_copy(update={"current_memory_id": "memory_candidate_active"})
                )

                assert [memory.memory_id for memory in candidates] == [
                    "memory_candidate_active",
                    "memory_candidate_contested",
                ]
                assert [candidate.memory.memory_id for candidate in candidate_snapshots] == [
                    "memory_candidate_active",
                    "memory_candidate_contested",
                ]
                assert [candidate.row_version for candidate in candidate_snapshots] == [1, 1]
                assert await repository.event_was_applied(
                    scope,
                    SLOT_KEY,
                    "memory_repository_event_001",
                )
                assert not await repository.event_was_applied(
                    scope,
                    SLOT_KEY,
                    "memory_repository_unseen_event",
                )
                assert [memory.memory_id for memory in snapshot] == [
                    "memory_candidate_active",
                    "memory_candidate_contested",
                    "memory_candidate_archived",
                ]
                assert [item.memory.memory_id for item in contested_group_snapshots] == [
                    "memory_candidate_active",
                    "memory_candidate_contested",
                    "memory_candidate_archived",
                ]
                assert all(
                    item.contested_group_id == "memory_repository_open_group"
                    for item in contested_group_snapshots
                )
                assert [item.memory.memory_id for item in slot_snapshots] == [
                    "memory_candidate_active",
                    "memory_candidate_contested",
                    "memory_candidate_archived",
                ]
                assert [memory.memory_id for memory in excluding_current] == [
                    "memory_candidate_contested"
                ]
                assert all(
                    memory.provenance == ["memory_repository_event_001"] for memory in snapshot
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_snapshot_rejects_a_memory_without_provenance() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _insert_memory(
                    connection,
                    memory_id="memory_without_provenance",
                    user_id="memory_repository_invalid_user",
                    with_provenance=False,
                )
                scope = build_memory_scope(
                    user_id="memory_repository_invalid_user",
                    exam_id="postgraduate_entrance_exam",
                    subject_id="math_1",
                    memory_namespace=MemoryNamespace.MASTERY,
                )

                with pytest.raises(RepositoryInvariantError, match="has no provenance"):
                    await PostgresLearningMemoryRepository(connection).snapshot(scope)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_insert_version_and_cas_archive_form_an_atomic_replacement() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_id = "memory_repository_version_event_001"
                await _append_source_event(connection, event_id=event_id)
                repository = PostgresLearningMemoryRepository(connection)
                first = _memory_version(
                    memory_id="memory_repository_version_001",
                    version=1,
                    provenance=[event_id],
                )
                second = _memory_version(
                    memory_id="memory_repository_version_002",
                    version=2,
                    provenance=[event_id],
                )

                await repository.insert_version(first, policy_version="stage05_repository_v1")
                archived = await repository.compare_and_archive(
                    first.scope,
                    first.memory_id,
                    expected_row_version=1,
                    valid_to=NOW + timedelta(minutes=1),
                    superseded_by=second.memory_id,
                )
                await repository.insert_version(second, policy_version="stage05_repository_v1")
                await connection.execute(
                    text("SET CONSTRAINTS learning_memories_superseded_by_fkey IMMEDIATE")
                )
                stale_cas = await repository.compare_and_archive(
                    first.scope,
                    first.memory_id,
                    expected_row_version=1,
                    valid_to=NOW + timedelta(minutes=2),
                    superseded_by="unused_successor",
                )

                snapshot = await repository.snapshot(first.scope)
                first_row = (
                    await connection.execute(
                        select(
                            learning_memories.c.row_version,
                            learning_memories.c.superseded_by,
                        ).where(learning_memories.c.memory_id == first.memory_id)
                    )
                ).one()

                assert archived is True
                assert stale_cas is False
                assert [memory.memory_id for memory in snapshot] == [
                    first.memory_id,
                    second.memory_id,
                ]
                assert snapshot[0].lifecycle_state.value == "archived"
                assert snapshot[0].superseded_by == second.memory_id
                assert snapshot[1].lifecycle_state.value == "active"
                assert first_row.row_version == 2
                assert first_row.superseded_by == second.memory_id
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_lifecycle_snapshot_and_cas_preserve_scope_group_and_provenance() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                source_event = "memory_repository_cas_event_001"
                contradiction_event = "memory_repository_cas_event_002"
                await _append_source_event(connection, event_id=source_event)
                await _append_source_event(connection, event_id=contradiction_event)
                repository = PostgresLearningMemoryRepository(connection)
                first = _memory_version(
                    memory_id="memory_repository_cas_v1",
                    version=1,
                    provenance=[source_event],
                )
                inserted = await repository.insert_version(
                    first,
                    policy_version="lifecycle_policy_v1",
                )

                transitioned = await repository.cas_transition(
                    first.scope,
                    first.slot_key,
                    first.memory_id,
                    expected_row_version=inserted.row_version,
                    to_state=LifecycleState.ACTIVE,
                    valid_to=None,
                    contested_group_id="memory_repository_contested_group",
                    provenance_event_id=contradiction_event,
                    provenance_relation="contradicted_by",
                )
                cross_scope = await repository.get_lifecycle_snapshot(
                    first.scope.model_copy(update={"user_id": "another_user"}),
                    first.memory_id,
                )
                relation = await connection.scalar(
                    select(memory_provenance.c.relation_type).where(
                        memory_provenance.c.memory_id == first.memory_id,
                        memory_provenance.c.event_id == contradiction_event,
                    )
                )

                assert transitioned is not None
                assert transitioned.row_version == 2
                assert transitioned.contested_group_id == "memory_repository_contested_group"
                assert transitioned.memory.evidence_count == 2
                assert transitioned.memory.provenance == [
                    source_event,
                    contradiction_event,
                ]
                assert relation == "contradicted_by"
                assert cross_scope is None
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_insert_version_rejects_gaps_and_inconsistent_provenance() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_id = "memory_repository_version_event_002"
                await _append_source_event(connection, event_id=event_id)
                repository = PostgresLearningMemoryRepository(connection)
                await repository.insert_version(
                    _memory_version(
                        memory_id="memory_repository_gap_base",
                        version=1,
                        provenance=[event_id],
                    ),
                    policy_version="stage05_repository_v1",
                )

                with pytest.raises(MemoryVersionConflict, match="expected version 2"):
                    await repository.insert_version(
                        _memory_version(
                            memory_id="memory_repository_gap_v3",
                            version=3,
                            provenance=[event_id],
                            lifecycle_state="contested",
                        ),
                        policy_version="stage05_repository_v1",
                        contested_group_id="memory_repository_gap_group",
                    )

                inconsistent = _memory_version(
                    memory_id="memory_repository_bad_evidence",
                    version=2,
                    provenance=[event_id],
                    lifecycle_state="contested",
                ).model_copy(update={"evidence_count": 2})
                with pytest.raises(
                    MemoryProvenanceValidationError,
                    match="evidence_count",
                ):
                    await repository.insert_version(
                        inconsistent,
                        policy_version="stage05_repository_v1",
                        contested_group_id="memory_repository_bad_evidence_group",
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_insert_version_rolls_back_l2_when_provenance_write_fails() -> None:
    engine = create_async_engine(_database_url_or_skip())

    def fail_before_provenance(
        _connection: Any,
        _cursor: Any,
        statement: str,
        _parameters: Any,
        _context: Any,
        _executemany: bool,
    ) -> None:
        if statement.lstrip().startswith("INSERT INTO memory_provenance"):
            raise RuntimeError("injected failure before provenance write")

    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_id = "memory_repository_failure_event_001"
                memory_id = "memory_repository_failure_memory_001"
                await _append_source_event(connection, event_id=event_id)
                memory = _memory_version(
                    memory_id=memory_id,
                    version=1,
                    provenance=[event_id],
                )
                repository = PostgresLearningMemoryRepository(connection)
                sqlalchemy_event.listen(
                    engine.sync_engine,
                    "before_cursor_execute",
                    fail_before_provenance,
                )
                try:
                    with pytest.raises(RuntimeError, match="injected failure"):
                        await repository.insert_version(
                            memory,
                            policy_version="stage05_repository_v1",
                        )
                finally:
                    sqlalchemy_event.remove(
                        engine.sync_engine,
                        "before_cursor_execute",
                        fail_before_provenance,
                    )

                assert (
                    await connection.scalar(
                        select(learning_memories.c.memory_id).where(
                            learning_memories.c.memory_id == memory_id
                        )
                    )
                    is None
                )
                assert (
                    await connection.scalar(
                        select(memory_provenance.c.memory_id).where(
                            memory_provenance.c.memory_id == memory_id
                        )
                    )
                    is None
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_vector_search_writes_vectors_and_filters_scope_before_distance() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_id = "memory_repository_vector_event_001"
                other_event_id = "memory_repository_vector_event_other_scope"
                await _append_source_event(connection, event_id=event_id)
                await _append_source_event(
                    connection,
                    event_id=other_event_id,
                    user_id="memory_repository_vector_other_user",
                )
                repository = PostgresLearningMemoryRepository(connection)
                rank = _memory_version(
                    memory_id="memory_vector_rank",
                    version=1,
                    provenance=[event_id],
                )
                determinant = _memory_version(
                    memory_id="memory_vector_determinant",
                    version=1,
                    provenance=[event_id],
                    slot_key="mastery:math1.linear_algebra.determinant",
                )
                archived = _memory_version(
                    memory_id="memory_vector_archived",
                    version=1,
                    provenance=[event_id],
                    lifecycle_state="archived",
                    slot_key="mastery:math1.linear_algebra.inverse_matrix",
                )
                other_scope = _memory_version(
                    memory_id="memory_vector_other_scope",
                    version=1,
                    provenance=[other_event_id],
                    user_id="memory_repository_vector_other_user",
                )

                await repository.insert_version(
                    rank,
                    policy_version="stage05_vector_v1",
                    content_embedding=_basis_vector(0),
                )
                await repository.insert_version(
                    determinant,
                    policy_version="stage05_vector_v1",
                    content_embedding=_basis_vector(1),
                )
                await repository.insert_version(
                    archived,
                    policy_version="stage05_vector_v1",
                    content_embedding=_basis_vector(0),
                )
                await repository.insert_version(
                    other_scope,
                    policy_version="stage05_vector_v1",
                    content_embedding=_basis_vector(0),
                )

                similar = await repository.find_similar(
                    rank.scope,
                    _basis_vector(0),
                    10,
                )
                top_one = await repository.find_similar(
                    rank.scope,
                    _basis_vector(0),
                    1,
                )

                assert [memory.memory_id for memory in similar] == [
                    rank.memory_id,
                    determinant.memory_id,
                ]
                assert [memory.memory_id for memory in top_one] == [rank.memory_id]
                with pytest.raises(ValueError, match="1024 dimensions"):
                    await repository.find_similar(rank.scope, [1.0], 10)
                with pytest.raises(ValueError, match="non-zero"):
                    await repository.find_similar(
                        rank.scope,
                        [0.0] * LEARNING_MEMORY_EMBEDDING_DIMENSION,
                        10,
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
