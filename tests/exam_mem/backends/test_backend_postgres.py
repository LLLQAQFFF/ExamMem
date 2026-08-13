from __future__ import annotations

from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from exam_mem.backends.baseline import (
    AppendOnlyMemoryBackend,
    VectorMemoryBackend,
)
from exam_mem.backends.lifecycle import LifecycleMemoryBackend
from exam_mem.contracts import LearningEvent, MemoryScope, MemoryUpdateCandidate
from exam_mem.lifecycle import LifecycleApplier, PostCommitProjectionRefresher
from exam_mem.practice import MemoryWriter
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    PostgresBaselineFactRepository,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    PostgresLifecycleAuditRepository,
    PostgresStudentModelRepository,
    StudentModelRebuildService,
    load_database_settings,
    metadata,
)
from exam_mem.storage.models import (
    baseline_memory_facts,
    learning_events,
    learning_memories,
    lifecycle_decisions,
    memory_change_log,
    memory_provenance,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.backend_mode,
    pytest.mark.database,
    pytest.mark.repository,
]

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage07_backend_postgres_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _event(event_id: str) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
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


def _candidate(event_id: str) -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate(
        event_id=event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value={"type": "mastery", "level": "low", "score": 0.0},
        evidence={"source": "stage07_backend_postgres"},
    )


class FakeEmbeddingClient:
    async def embed(self, texts, *, input_type=None):  # noqa: ANN001, ANN201
        del input_type
        return [[1.0, *([0.0] * (LEARNING_MEMORY_EMBEDDING_DIMENSION - 1))] for _ in texts]


class FailingRelationClassifier:
    async def classify(self, candidate, candidate_snapshots):  # noqa: ANN001, ANN201
        del candidate, candidate_snapshots
        raise AssertionError("new mastery slot must not invoke relation classification")


async def _set_test_schema(connection, schema_name: str) -> None:  # noqa: ANN001
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))


@pytest.mark.parametrize("mode", ["append_only", "vector"])
async def test_baseline_backend_real_transaction_is_idempotent_and_mode_isolated(
    mode: str,
) -> None:
    event_id = f"stage07_{mode}_backend_pg_event_001"
    event = _event(event_id)
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                events = PostgresLearningEventRepository(connection)
                facts = PostgresBaselineFactRepository(connection)
                backend = (
                    AppendOnlyMemoryBackend(
                        event_repository=events,
                        fact_repository=facts,
                    )
                    if mode == "append_only"
                    else VectorMemoryBackend(
                        event_repository=events,
                        fact_repository=facts,
                        embedding_client=FakeEmbeddingClient(),
                    )
                )
                writer = MemoryWriter(backend)

                first = await writer.write(event, [_candidate(event_id)])
                replay = await writer.write(event, [_candidate(event_id)])

                assert first.decisions == ()
                assert replay.decisions == ()
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(learning_events)
                        .where(learning_events.c.event_id == event_id)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(baseline_memory_facts)
                        .where(
                            baseline_memory_facts.c.backend_mode == mode,
                            baseline_memory_facts.c.event_id == event_id,
                        )
                    )
                    == 1
                )
                other_mode = "vector" if mode == "append_only" else "append_only"
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(baseline_memory_facts)
                        .where(
                            baseline_memory_facts.c.backend_mode == other_mode,
                            baseline_memory_facts.c.event_id == event_id,
                        )
                    )
                    == 0
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_lifecycle_backend_reaches_real_l1_l2_provenance_and_audit_chain() -> None:
    event_id = "stage07_lifecycle_backend_pg_event_001"
    event = _event(event_id)
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                events = PostgresLearningEventRepository(connection)
                memories = PostgresLearningMemoryRepository(connection)
                audit = PostgresLifecycleAuditRepository(connection)
                backend = LifecycleMemoryBackend(
                    event_repository=events,
                    memory_repository=memories,
                    student_model_repository=PostgresStudentModelRepository(connection),
                    relation_classifier=FailingRelationClassifier(),
                    applier=LifecycleApplier(
                        connection,
                        memory_repository=memories,
                        audit_repository=audit,
                        event_repository=events,
                    ),
                    trace_id="stage07_lifecycle_backend_pg_trace_001",
                )

                result = await MemoryWriter(backend).write(event, [_candidate(event_id)])

                assert [decision.operation.value for decision in result.decisions] == ["ADD"]
                assert len(result.projection_requests) == 1
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(learning_events)
                        .where(learning_events.c.event_id == event_id)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(learning_memories)
                        .where(
                            learning_memories.c.user_id == SCOPE.user_id,
                            learning_memories.c.slot_key == SLOT_KEY,
                        )
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(memory_provenance)
                        .where(memory_provenance.c.event_id == event_id)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(lifecycle_decisions)
                        .where(lifecycle_decisions.c.event_id == event_id)
                    )
                    == 1
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(memory_change_log)
                        .where(
                            memory_change_log.c.trace_id == "stage07_lifecycle_backend_pg_trace_001"
                        )
                    )
                    == 2
                )
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(baseline_memory_facts)
                        .where(baseline_memory_facts.c.event_id == event_id)
                    )
                    == 0
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_lifecycle_commit_then_rebuilds_real_l3_in_a_separate_transaction() -> None:
    schema_name = f"exammem_stage07_backend_{uuid4().hex}"
    event_id = "stage07_lifecycle_backend_l3_event_001"
    event = _event(event_id)
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    write_result = None
    try:
        async with engine.begin() as write_connection:
            await write_connection.execute(CreateSchema(schema_name))
            schema_created = True
            await _set_test_schema(write_connection, schema_name)
            await write_connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    checkfirst=False,
                )
            )
            events = PostgresLearningEventRepository(write_connection)
            memories = PostgresLearningMemoryRepository(write_connection)
            audit = PostgresLifecycleAuditRepository(write_connection)
            backend = LifecycleMemoryBackend(
                event_repository=events,
                memory_repository=memories,
                student_model_repository=PostgresStudentModelRepository(write_connection),
                relation_classifier=FailingRelationClassifier(),
                applier=LifecycleApplier(
                    write_connection,
                    memory_repository=memories,
                    audit_repository=audit,
                    event_repository=events,
                ),
                trace_id="stage07_lifecycle_backend_l3_trace_001",
            )
            write_result = await MemoryWriter(backend).write(event, [_candidate(event_id)])

        assert write_result is not None
        assert len(write_result.projection_requests) == 1

        async with engine.begin() as projection_connection:
            await _set_test_schema(projection_connection, schema_name)
            event_repository = PostgresLearningEventRepository(projection_connection)
            memory_repository = PostgresLearningMemoryRepository(projection_connection)
            model_repository = PostgresStudentModelRepository(projection_connection)
            refresh = await PostCommitProjectionRefresher(
                StudentModelRebuildService(
                    event_repository=event_repository,
                    memory_repository=memory_repository,
                    student_model_repository=model_repository,
                )
            ).refresh(write_result.projection_requests[0])

            persisted = await model_repository.get_latest(event.context)
            assert persisted == refresh.snapshot
            assert persisted.source_event_watermark == event_id
            assert persisted.model.weak_points == ["math1.linear_algebra.matrix_rank"]
            assert refresh.event_count == 1
            assert refresh.memory_count == 1
    finally:
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(
                    DropSchema(schema_name, cascade=True, if_exists=True)
                )
        await engine.dispose()
