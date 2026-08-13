from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from exam_mem.backends import BackendMode
from exam_mem.contracts import LearningEvent, MemoryScope, MemoryUpdateCandidate
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    AppendStatus,
    BaselineFactRecord,
    BaselineFactRepository,
    BaselineFactSourceEventError,
    PostgresBaselineFactRepository,
    PostgresLearningEventRepository,
    load_database_settings,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"
SCOPE = MemoryScope(
    user_id="baseline_fact_postgres_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def _append_event(
    connection: AsyncConnection,
    event_id: str,
    *,
    user_id: str = SCOPE.user_id,
) -> LearningEvent:
    event = LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": {
                "user_id": user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": f"session:{event_id}",
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.linear_algebra.matrix_rank"],
            "difficulty": 0.5,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "rank conditions were confused",
            "occurred_at": NOW,
        }
    )
    result = await PostgresLearningEventRepository(connection).append(event)
    assert result.status is AppendStatus.CREATED
    return event


def _candidate(
    event_id: str,
    *,
    scope: MemoryScope = SCOPE,
    score: float = 0.3,
) -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate(
        event_id=event_id,
        scope=scope,
        slot_key=SLOT_KEY,
        proposed_value={"type": "mastery", "level": "low", "score": score},
        evidence={"source": "stage07_postgres_test"},
    )


def _basis_vector(axis: int) -> tuple[float, ...]:
    values = [0.0] * LEARNING_MEMORY_EMBEDDING_DIMENSION
    values[axis] = 1.0
    return tuple(values)


async def test_baseline_repository_is_idempotent_mode_isolated_and_scope_safe() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                first_event = await _append_event(connection, "baseline_fact_pg_event_001")
                second_event = await _append_event(connection, "baseline_fact_pg_event_002")
                other_event = await _append_event(
                    connection,
                    "baseline_fact_pg_event_other_scope",
                    user_id="baseline_fact_postgres_other_user",
                )
                repository = PostgresBaselineFactRepository(connection)
                assert isinstance(repository, BaselineFactRepository)

                append_record = BaselineFactRecord(
                    backend_mode=BackendMode.APPEND_ONLY,
                    candidate=_candidate(first_event.event_id),
                    created_at=NOW,
                )
                created = await repository.append(append_record)
                replayed = await repository.append(append_record)
                conflict = await repository.append(
                    BaselineFactRecord(
                        backend_mode=BackendMode.APPEND_ONLY,
                        candidate=_candidate(first_event.event_id, score=0.4),
                        created_at=NOW,
                    )
                )

                assert created.status is AppendStatus.CREATED
                assert replayed.status is AppendStatus.EXISTING
                assert replayed.record == append_record
                assert conflict.status is AppendStatus.CONFLICT
                assert conflict.record == append_record

                for event, embedding in (
                    (first_event, _basis_vector(0)),
                    (second_event, _basis_vector(1)),
                ):
                    result = await repository.append(
                        BaselineFactRecord(
                            backend_mode=BackendMode.VECTOR,
                            candidate=_candidate(event.event_id),
                            created_at=NOW,
                            content_embedding=embedding,
                        )
                    )
                    assert result.status is AppendStatus.CREATED

                other_scope = SCOPE.model_copy(
                    update={"user_id": "baseline_fact_postgres_other_user"}
                )
                other_result = await repository.append(
                    BaselineFactRecord(
                        backend_mode=BackendMode.VECTOR,
                        candidate=_candidate(other_event.event_id, scope=other_scope),
                        created_at=NOW,
                        content_embedding=_basis_vector(0),
                    )
                )
                assert other_result.status is AppendStatus.CREATED

                append_scope = await repository.list_scope(
                    BackendMode.APPEND_ONLY,
                    SCOPE,
                    limit=10,
                )
                vector_matches = await repository.find_similar(
                    SCOPE,
                    _basis_vector(0),
                    limit=10,
                )
                append_snapshot = await repository.snapshot(
                    BackendMode.APPEND_ONLY,
                    SCOPE,
                )

                assert [item.candidate.event_id for item in append_scope] == [first_event.event_id]
                assert [item.candidate.event_id for item in vector_matches] == [
                    first_event.event_id,
                    second_event.event_id,
                ]
                assert append_snapshot == append_scope

                with pytest.raises(BaselineFactSourceEventError, match="candidate context"):
                    await repository.append(
                        BaselineFactRecord(
                            backend_mode=BackendMode.APPEND_ONLY,
                            candidate=_candidate(other_event.event_id),
                            created_at=NOW,
                        )
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
