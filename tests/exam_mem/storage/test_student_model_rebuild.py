from __future__ import annotations

from datetime import UTC, datetime
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
    StudentModel,
)
from exam_mem.storage import (
    AppendStatus,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    PostgresStudentModelRepository,
    RebuildInputError,
    StudentModelRebuildService,
    StudentModelSnapshot,
    load_database_settings,
)
from exam_mem.storage.models import learning_events, learning_memories, metadata

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CONTEXT = LearningContext(
    user_id="rebuild_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _event(event_id: str) -> LearningEvent:
    return LearningEvent(
        event_id=event_id,
        idempotency_key=event_id,
        context=_CONTEXT,
        session_id="rebuild_session",
        question_id=f"question_{event_id}",
        knowledge_point_ids=["math1.linear_algebra.matrix_rank"],
        difficulty=0.5,
        answer_correct=False,
        occurred_at=_NOW,
    )


def _memory() -> LearningMemory:
    return LearningMemory(
        memory_id="rebuild_memory_001",
        scope=MemoryScope(
            **_CONTEXT.model_dump(),
            memory_namespace=MemoryNamespace.MASTERY,
        ),
        slot_key="mastery:math1.linear_algebra.matrix_rank",
        value=MasteryValue(level=MasteryLevel.LOW, score=0.2),
        confidence=0.8,
        evidence_count=2,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=_NOW,
        valid_to=None,
        superseded_by=None,
        provenance=["rebuild_event_001", "rebuild_event_002"],
    )


class _EventRepository:
    def __init__(self) -> None:
        self.calls: list[tuple[str | None, int]] = []
        self.events = [_event("rebuild_event_001"), _event("rebuild_event_002")]

    async def append(self, event: LearningEvent) -> object:
        raise NotImplementedError

    async def list_after(
        self,
        context: LearningContext,
        watermark: str | None,
        limit: int,
    ) -> list[LearningEvent]:
        assert context == _CONTEXT
        self.calls.append((watermark, limit))
        start = 0
        if watermark is not None:
            start = next(
                index + 1 for index, event in enumerate(self.events) if event.event_id == watermark
            )
        return self.events[start : start + limit]


class _MemoryRepository:
    def __init__(self) -> None:
        self.scopes: list[MemoryScope] = []

    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]:
        self.scopes.append(scope)
        return [_memory()] if scope.memory_namespace is MemoryNamespace.MASTERY else []


class _StudentModelRepository:
    def __init__(self, latest: StudentModelSnapshot | None = None) -> None:
        self.latest = latest
        self.saved: list[StudentModelSnapshot] = []

    async def save_projection(self, snapshot: StudentModelSnapshot) -> None:
        self.saved.append(snapshot)
        self.latest = snapshot

    async def get_latest(self, context: LearningContext) -> StudentModelSnapshot | None:
        assert context == _CONTEXT
        return self.latest

    async def clear_projection(self, context: LearningContext) -> int:
        assert context == _CONTEXT
        count = int(self.latest is not None)
        self.latest = None
        return count


class _FailBeforeProjectionSaveRepository:
    def __init__(self, delegate: PostgresStudentModelRepository) -> None:
        self._delegate = delegate

    async def save_projection(self, snapshot: StudentModelSnapshot) -> None:
        raise RuntimeError("injected failure before L3 projection save")

    async def get_latest(self, context: LearningContext) -> StudentModelSnapshot | None:
        return await self._delegate.get_latest(context)

    async def clear_projection(self, context: LearningContext) -> int:
        return await self._delegate.clear_projection(context)


@pytest.mark.asyncio
async def test_rebuild_pages_l1_reads_every_namespace_and_replaces_l3() -> None:
    event_repository = _EventRepository()
    memory_repository = _MemoryRepository()
    model_repository = _StudentModelRepository()
    service = StudentModelRebuildService(
        event_repository=event_repository,
        memory_repository=memory_repository,
        student_model_repository=model_repository,
        event_page_size=1,
    )

    result = await service.rebuild(_CONTEXT)

    assert event_repository.calls == [
        (None, 1),
        ("rebuild_event_001", 1),
        ("rebuild_event_002", 1),
    ]
    assert [scope.memory_namespace for scope in memory_repository.scopes] == list(MemoryNamespace)
    assert result.event_count == 2
    assert result.memory_count == 1
    assert result.cleared_snapshot_count == 0
    assert result.snapshot.model.weak_points == ["math1.linear_algebra.matrix_rank"]
    assert result.snapshot.source_event_watermark == "rebuild_event_002"
    assert result.snapshot.source_memory_watermark.startswith("sha256:")
    assert model_repository.saved == [result.snapshot]


@pytest.mark.asyncio
async def test_fixed_inputs_produce_the_same_snapshot_and_no_model_diff() -> None:
    event_repository = _EventRepository()
    memory_repository = _MemoryRepository()
    model_repository = _StudentModelRepository()
    service = StudentModelRebuildService(
        event_repository=event_repository,
        memory_repository=memory_repository,
        student_model_repository=model_repository,
        event_page_size=10,
    )

    first = await service.rebuild(_CONTEXT)
    second = await service.rebuild(_CONTEXT)

    assert first.snapshot == second.snapshot
    assert second.previous_snapshot == first.snapshot
    assert second.changed_fields == ()
    assert second.cleared_snapshot_count == 1


@pytest.mark.asyncio
async def test_rebuild_requires_at_least_one_l1_event_before_touching_l3() -> None:
    event_repository = _EventRepository()
    event_repository.events = []
    model_repository = _StudentModelRepository(
        StudentModelSnapshot(
            snapshot_id="existing_snapshot",
            model=StudentModel(
                context=_CONTEXT,
                weak_points=[],
                mastered_points=[],
                stable_error_patterns=[],
                active_plans=[],
                projection_version=1,
                source_watermark="existing_event",
            ),
            source_event_watermark="existing_event",
            source_memory_watermark="sha256:existing",
        )
    )
    service = StudentModelRebuildService(
        event_repository=event_repository,
        memory_repository=_MemoryRepository(),
        student_model_repository=model_repository,
    )

    with pytest.raises(RebuildInputError, match="without L1"):
        await service.rebuild(_CONTEXT)

    assert model_repository.latest is not None
    assert model_repository.saved == []


async def _set_test_schema(connection: AsyncConnection, schema_name: str) -> None:
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))


async def _seed_rebuild_state(
    connection: AsyncConnection,
) -> StudentModelSnapshot:
    events = [_event("rebuild_event_001"), _event("rebuild_event_002")]
    event_repository = PostgresLearningEventRepository(connection)
    for event in events:
        assert (await event_repository.append(event)).status is AppendStatus.CREATED

    await PostgresLearningMemoryRepository(connection).insert_version(
        _memory(),
        policy_version="stage05_projection_v1",
    )
    old_snapshot = StudentModelSnapshot(
        snapshot_id="rebuild_old_snapshot",
        model=StudentModel(
            context=_CONTEXT,
            weak_points=[],
            mastered_points=[],
            stable_error_patterns=[],
            active_plans=[],
            projection_version=1,
            source_watermark="rebuild_event_001",
        ),
        source_event_watermark="rebuild_event_001",
        source_memory_watermark="sha256:old",
    )
    await PostgresStudentModelRepository(connection).save_projection(old_snapshot)
    return old_snapshot


def _postgres_rebuild_service(
    connection: AsyncConnection,
    *,
    fail_before_save: bool = False,
) -> StudentModelRebuildService:
    model_repository = PostgresStudentModelRepository(connection)
    selected_model_repository = (
        _FailBeforeProjectionSaveRepository(model_repository)
        if fail_before_save
        else model_repository
    )
    return StudentModelRebuildService(
        event_repository=PostgresLearningEventRepository(connection),
        memory_repository=PostgresLearningMemoryRepository(connection),
        student_model_repository=selected_model_repository,
        event_page_size=1,
    )


@pytest.mark.asyncio
@pytest.mark.database
@pytest.mark.repository
async def test_postgres_rebuild_failure_preserves_l1_l2_and_can_retry() -> None:
    schema_name = f"exammem_rebuild_test_{uuid4().hex}"
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    try:
        async with engine.begin() as setup_connection:
            await setup_connection.execute(CreateSchema(schema_name))
            schema_created = True
            await _set_test_schema(setup_connection, schema_name)
            await setup_connection.run_sync(
                lambda sync_connection: metadata.create_all(
                    sync_connection,
                    checkfirst=False,
                )
            )
            old_snapshot = await _seed_rebuild_state(setup_connection)

        async with engine.connect() as failing_connection:
            await failing_connection.execution_options(isolation_level="REPEATABLE READ")
            failing_transaction = await failing_connection.begin()
            await _set_test_schema(failing_connection, schema_name)
            with pytest.raises(RuntimeError, match="injected failure"):
                await _postgres_rebuild_service(
                    failing_connection,
                    fail_before_save=True,
                ).rebuild(_CONTEXT)
            await failing_transaction.rollback()

        async with engine.begin() as verification_connection:
            await _set_test_schema(verification_connection, schema_name)
            assert (
                await verification_connection.scalar(
                    select(func.count()).select_from(learning_events)
                )
                == 2
            )
            assert (
                await verification_connection.scalar(
                    select(func.count()).select_from(learning_memories)
                )
                == 1
            )
            assert (
                await PostgresStudentModelRepository(verification_connection).get_latest(_CONTEXT)
                == old_snapshot
            )

        async with engine.connect() as retry_connection:
            await retry_connection.execution_options(isolation_level="REPEATABLE READ")
            retry_transaction = await retry_connection.begin()
            await _set_test_schema(retry_connection, schema_name)
            first_rebuild = await _postgres_rebuild_service(retry_connection).rebuild(_CONTEXT)
            await retry_transaction.commit()

        assert first_rebuild.previous_snapshot == old_snapshot
        assert first_rebuild.snapshot.model.weak_points == ["math1.linear_algebra.matrix_rank"]

        async with engine.begin() as clear_connection:
            await _set_test_schema(clear_connection, schema_name)
            assert (
                await PostgresStudentModelRepository(clear_connection).clear_projection(_CONTEXT)
                == 1
            )

        async with engine.connect() as rebuild_connection:
            await rebuild_connection.execution_options(isolation_level="REPEATABLE READ")
            rebuild_transaction = await rebuild_connection.begin()
            await _set_test_schema(rebuild_connection, schema_name)
            second_rebuild = await _postgres_rebuild_service(rebuild_connection).rebuild(_CONTEXT)
            await rebuild_transaction.commit()

        assert second_rebuild.previous_snapshot is None
        assert second_rebuild.snapshot == first_rebuild.snapshot
        assert second_rebuild.snapshot.source_event_watermark == "rebuild_event_002"
        assert second_rebuild.snapshot.source_memory_watermark.startswith("sha256:")
    finally:
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(DropSchema(schema_name, cascade=True))
        await engine.dispose()
