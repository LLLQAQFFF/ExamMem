from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, insert, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from exam_mem.contracts import LearningEvent
from exam_mem.storage import (
    AppendResult,
    AppendStatus,
    EventLookupError,
    EventTargetValidationError,
    EventWatermarkError,
    LearningEventRepository,
    PostgresLearningEventRepository,
    load_database_settings,
)
from exam_mem.storage.models import (
    event_correction_targets,
    event_plan_transition_targets,
    learning_events,
    learning_memories,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _answer_event(
    *,
    event_id: str,
    idempotency_key: str,
    question_id: str = "question_001",
    user_id: str = "repository_user",
) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": idempotency_key,
            "event_type": "answer_attempt",
            "context": {
                "user_id": user_id,
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            "session_id": "repository_session",
            "question_id": question_id,
            "knowledge_point_ids": ["linear_algebra.matrix.rank"],
            "difficulty": 0.5,
            "answer_correct": True,
            "occurred_at": "2026-08-11T08:00:00Z",
        }
    )


async def _insert_memory(
    connection: AsyncConnection,
    *,
    memory_id: str,
    user_id: str = "repository_user",
    namespace: str = "error_pattern",
    slot_key: str | None = None,
) -> None:
    await connection.execute(
        insert(learning_memories).values(
            memory_id=memory_id,
            user_id=user_id,
            exam_id="postgraduate_entrance_exam",
            subject_id="math_1",
            memory_namespace=namespace,
            slot_key=slot_key or f"{namespace}:linear_algebra.matrix.rank",
            value={"type": namespace},
            confidence=0.8,
            evidence_count=1,
            lifecycle_state="active",
            version=1,
            row_version=1,
            valid_from=datetime(2026, 8, 11, tzinfo=timezone.utc),
            valid_to=None,
            superseded_by=None,
            contested_group_id=None,
            content_embedding=None,
            policy_version="stage05_repository_test",
        )
    )


async def test_append_returns_created_existing_or_conflict_without_duplication() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresLearningEventRepository(connection)
                assert isinstance(repository, LearningEventRepository)
                event = _answer_event(
                    event_id="repository_event_001",
                    idempotency_key="repository-idempotency-001",
                )

                created = await repository.append(event)
                existing = await repository.append(event)
                conflict = await repository.append(
                    _answer_event(
                        event_id="repository_event_conflict",
                        idempotency_key=event.idempotency_key,
                        question_id="different_question",
                    )
                )

                assert created.status is AppendStatus.CREATED
                assert created.event == event
                assert existing.status is AppendStatus.EXISTING
                assert existing.event == event
                assert conflict.status is AppendStatus.CONFLICT
                assert conflict.event == event
                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(learning_events)
                        .where(learning_events.c.event_id == event.event_id)
                    )
                    == 1
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_append_persists_caller_trace_and_rejects_trace_drift_on_replay() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresLearningEventRepository(connection)
                event = _answer_event(
                    event_id="repository_trace_event_001",
                    idempotency_key="repository-trace-event-001",
                    user_id="repository_trace_user",
                )

                created = await repository.append(event, trace_id="practice_trace_001")
                existing = await repository.append(event, trace_id="practice_trace_001")
                conflict = await repository.append(event, trace_id="different_trace")
                stored_trace_id = await connection.scalar(
                    select(learning_events.c.trace_id).where(
                        learning_events.c.event_id == event.event_id
                    )
                )

                assert created.status is AppendStatus.CREATED
                assert existing.status is AppendStatus.EXISTING
                assert conflict.status is AppendStatus.CONFLICT
                assert stored_trace_id == "practice_trace_001"
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_list_after_pages_events_with_a_context_bound_watermark() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresLearningEventRepository(connection)
                events = [
                    _answer_event(
                        event_id=f"repository_list_{index:03d}",
                        idempotency_key=f"repository-list-{index:03d}",
                        user_id="repository_list_user",
                    )
                    for index in range(1, 4)
                ]
                for event in events:
                    assert (await repository.append(event)).status is AppendStatus.CREATED
                other_context_event = _answer_event(
                    event_id="repository_list_other_context",
                    idempotency_key="repository-list-other-context",
                    user_id="repository_list_other_user",
                )
                await repository.append(other_context_event)

                first_page = await repository.list_after(events[0].context, None, 2)
                second_page = await repository.list_after(
                    events[0].context,
                    first_page[-1].event_id,
                    2,
                )

                assert [event.event_id for event in first_page] == [
                    "repository_list_001",
                    "repository_list_002",
                ]
                assert [event.event_id for event in second_page] == ["repository_list_003"]
                with pytest.raises(EventWatermarkError, match="does not belong"):
                    await repository.list_after(
                        events[0].context,
                        other_context_event.event_id,
                        2,
                    )
                with pytest.raises(ValueError, match="limit"):
                    await repository.list_after(events[0].context, None, 0)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_get_by_ids_is_context_bound_and_deterministic() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresLearningEventRepository(connection)
                events = [
                    _answer_event(
                        event_id=f"repository_lookup_{index:03d}",
                        idempotency_key=f"repository-lookup-{index:03d}",
                        user_id="repository_lookup_user",
                    )
                    for index in range(1, 3)
                ]
                for event in events:
                    assert (await repository.append(event)).status is AppendStatus.CREATED

                found = await repository.get_by_ids(
                    events[0].context,
                    [events[1].event_id, events[0].event_id, events[1].event_id],
                )

                assert [event.event_id for event in found] == [
                    "repository_lookup_001",
                    "repository_lookup_002",
                ]
                assert await repository.get_by_ids(events[0].context, []) == []
                with pytest.raises(EventLookupError, match="requested context"):
                    await repository.get_by_ids(
                        events[0].context,
                        ["repository_lookup_missing"],
                    )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_correction_event_and_targets_are_inserted_atomically() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _insert_memory(connection, memory_id="correction_target_001")
                await _insert_memory(
                    connection,
                    memory_id="correction_target_002",
                    slot_key="error_pattern:linear_algebra.matrix.rank:secondary",
                )
                event = LearningEvent.model_validate(
                    {
                        "event_id": "repository_correction_001",
                        "idempotency_key": "repository-correction-001",
                        "event_type": "explicit_correction",
                        "context": {
                            "user_id": "repository_user",
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                        },
                        "session_id": "repository_session",
                        "knowledge_point_ids": ["linear_algebra.matrix.rank"],
                        "correction": {
                            "target_memory_ids": [
                                "correction_target_001",
                                "correction_target_002",
                            ],
                            "source": "teacher",
                            "statement": "the original diagnosis was incorrect",
                        },
                        "occurred_at": "2026-08-11T08:05:00Z",
                    }
                )

                result = await PostgresLearningEventRepository(connection).append(event)
                target_ids = (
                    await connection.scalars(
                        select(event_correction_targets.c.memory_id)
                        .where(event_correction_targets.c.event_id == event.event_id)
                        .order_by(event_correction_targets.c.memory_id)
                    )
                ).all()

                assert result.status is AppendStatus.CREATED
                assert target_ids == ["correction_target_001", "correction_target_002"]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_invalid_correction_target_rolls_back_the_event_savepoint() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _insert_memory(
                    connection,
                    memory_id="cross_scope_target_001",
                    user_id="another_user",
                )
                event = LearningEvent.model_validate(
                    {
                        "event_id": "repository_correction_invalid",
                        "idempotency_key": "repository-correction-invalid",
                        "event_type": "explicit_correction",
                        "context": {
                            "user_id": "repository_user",
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                        },
                        "session_id": "repository_session",
                        "knowledge_point_ids": ["linear_algebra.matrix.rank"],
                        "correction": {
                            "target_memory_ids": ["cross_scope_target_001"],
                            "source": "user",
                            "statement": "this target belongs to another user",
                        },
                        "occurred_at": "2026-08-11T08:10:00Z",
                    }
                )

                with pytest.raises(EventTargetValidationError, match="same learning context"):
                    await PostgresLearningEventRepository(connection).append(event)

                assert (
                    await connection.scalar(
                        select(func.count())
                        .select_from(learning_events)
                        .where(learning_events.c.event_id == event.event_id)
                    )
                    == 0
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_plan_transition_requires_a_same_context_plan_target() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _insert_memory(
                    connection,
                    memory_id="plan_target_001",
                    namespace="plan",
                )
                event = LearningEvent.model_validate(
                    {
                        "event_id": "repository_plan_transition_001",
                        "idempotency_key": "repository-plan-transition-001",
                        "event_type": "plan_transition",
                        "context": {
                            "user_id": "repository_user",
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                        },
                        "session_id": "repository_session",
                        "knowledge_point_ids": ["linear_algebra.matrix.rank"],
                        "plan_transition": {
                            "target_memory_id": "plan_target_001",
                            "to_status": "completed",
                            "source": "practice_progress",
                            "reason": "the deterministic goal was reached",
                        },
                        "occurred_at": "2026-08-11T08:15:00Z",
                    }
                )

                result = await PostgresLearningEventRepository(connection).append(event)
                target_id = await connection.scalar(
                    select(event_plan_transition_targets.c.memory_id).where(
                        event_plan_transition_targets.c.event_id == event.event_id
                    )
                )

                assert result.status is AppendStatus.CREATED
                assert target_id == "plan_target_001"
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def _wait_until_connection_is_lock_blocked(
    connection: AsyncConnection,
    *,
    blocked_pid: int,
) -> None:
    for _ in range(200):
        wait_event_type = await connection.scalar(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": blocked_pid},
        )
        if wait_event_type == "Lock":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the competing insert did not wait on the unique-key lock")


async def test_concurrent_idempotent_appends_create_exactly_one_event() -> None:
    schema_name = f"exammem_repository_test_{uuid4().hex}"
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    first_connection: AsyncConnection | None = None
    second_connection: AsyncConnection | None = None
    second_append: asyncio.Task[AppendResult] | None = None
    try:
        async with engine.begin() as setup_connection:
            await setup_connection.execute(CreateSchema(schema_name))
            schema_created = True
            await setup_connection.execute(
                text(
                    f'CREATE TABLE "{schema_name}".learning_events '
                    "(LIKE public.learning_events INCLUDING ALL)"
                )
            )

        first_connection = await engine.connect()
        second_connection = await engine.connect()
        first_transaction = await first_connection.begin()
        second_transaction = await second_connection.begin()
        await first_connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        await second_connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))

        event = _answer_event(
            event_id="concurrent_repository_event_001",
            idempotency_key="concurrent-repository-idempotency-001",
        )
        first_result = await PostgresLearningEventRepository(first_connection).append(event)
        second_pid = await second_connection.scalar(text("SELECT pg_backend_pid()"))
        assert second_pid is not None

        second_append = asyncio.create_task(
            PostgresLearningEventRepository(second_connection).append(event)
        )
        async with engine.connect() as observer:
            await _wait_until_connection_is_lock_blocked(observer, blocked_pid=second_pid)

        await first_transaction.commit()
        second_result = await asyncio.wait_for(second_append, timeout=5.0)
        await second_transaction.commit()

        assert first_result.status is AppendStatus.CREATED
        assert second_result.status is AppendStatus.EXISTING

        async with engine.connect() as verification_connection:
            await verification_connection.execute(
                text(f'SET search_path TO "{schema_name}", public')
            )
            assert (
                await verification_connection.scalar(
                    select(func.count()).select_from(learning_events)
                )
                == 1
            )
    finally:
        if first_connection is not None:
            if first_connection.in_transaction():
                await first_connection.rollback()
            await first_connection.close()
        if second_append is not None and not second_append.done():
            second_append.cancel()
            with suppress(asyncio.CancelledError):
                await second_append
        if second_connection is not None:
            if second_connection.in_transaction():
                await second_connection.rollback()
            await second_connection.close()
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(DropSchema(schema_name, cascade=True))
        await engine.dispose()
