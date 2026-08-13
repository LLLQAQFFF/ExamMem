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

from exam_mem.contracts import LifecycleState, MemoryScope
from exam_mem.storage import PostgresLearningMemoryRepository, load_database_settings
from exam_mem.storage.models import learning_events, learning_memories, memory_provenance

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.database,
    pytest.mark.repository,
    pytest.mark.cas,
    pytest.mark.concurrency,
]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_concurrency_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def _wait_until_lock_blocked(
    observer: AsyncConnection,
    *,
    blocked_pid: int,
) -> None:
    for _ in range(200):
        wait_event_type = await observer.scalar(
            text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
            {"pid": blocked_pid},
        )
        if wait_event_type == "Lock":
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the competing lifecycle CAS did not wait on the row lock")


async def _create_isolated_tables(connection: AsyncConnection, schema_name: str) -> None:
    for table_name in ("learning_events", "learning_memories", "memory_provenance"):
        await connection.execute(
            text(
                f'CREATE TABLE "{schema_name}"."{table_name}" '
                f"(LIKE public.{table_name} INCLUDING ALL)"
            )
        )
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
    await connection.execute(
        insert(learning_events).values(
            event_id="stage06_concurrency_event_001",
            idempotency_key="idem:stage06_concurrency_event_001",
            user_id=SCOPE.user_id,
            exam_id=SCOPE.exam_id,
            subject_id=SCOPE.subject_id,
            event_type="answer_attempt",
            session_id="stage06_concurrency_session",
            question_id="stage06_concurrency_question",
            knowledge_point_ids=["math1.probability.bayes"],
            primary_knowledge_point_id="math1.probability.bayes",
            difficulty=0.7,
            answer_correct=False,
            error_type="concept_confusion",
            error_detail="reversed conditional direction",
            evidence_quality={
                "confidence": 1.0,
                "is_temporary_exception": False,
                "reasons": [],
            },
            correction_source=None,
            correction_statement=None,
            plan_transition_status=None,
            plan_transition_source=None,
            plan_transition_reason=None,
            raw_payload={"source": "stage06_concurrency_test"},
            occurred_at=NOW,
            trace_id="stage06_concurrency_trace",
            schema_version=1,
        )
    )
    await connection.execute(
        insert(learning_memories).values(
            memory_id="stage06_concurrency_memory_v1",
            user_id=SCOPE.user_id,
            exam_id=SCOPE.exam_id,
            subject_id=SCOPE.subject_id,
            memory_namespace=SCOPE.memory_namespace.value,
            slot_key=SLOT_KEY,
            value={
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses conditional probability",
                "details": ["reversed conditional direction"],
            },
            confidence=0.8,
            evidence_count=1,
            lifecycle_state="active",
            version=1,
            row_version=1,
            valid_from=NOW,
            valid_to=None,
            superseded_by=None,
            contested_group_id=None,
            content_embedding=None,
            policy_version="lifecycle_policy_v1",
        )
    )
    await connection.execute(
        insert(memory_provenance).values(
            memory_id="stage06_concurrency_memory_v1",
            event_id="stage06_concurrency_event_001",
            relation_type="created_by",
        )
    )


async def test_two_writers_with_same_row_version_allow_only_one_cas() -> None:
    schema_name = f"exammem_lifecycle_cas_{uuid4().hex}"
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    first_connection: AsyncConnection | None = None
    second_connection: AsyncConnection | None = None
    second_cas: asyncio.Task[object] | None = None
    try:
        async with engine.begin() as setup_connection:
            await setup_connection.execute(CreateSchema(schema_name))
            schema_created = True
            await _create_isolated_tables(setup_connection, schema_name)

        first_connection = await engine.connect()
        second_connection = await engine.connect()
        first_transaction = await first_connection.begin()
        second_transaction = await second_connection.begin()
        await first_connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
        await second_connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))

        first_repository = PostgresLearningMemoryRepository(first_connection)
        second_repository = PostgresLearningMemoryRepository(second_connection)
        first_result = await first_repository.cas_transition(
            SCOPE,
            SLOT_KEY,
            "stage06_concurrency_memory_v1",
            expected_row_version=1,
            to_state=LifecycleState.INVALIDATED,
            valid_to=NOW,
        )
        assert first_result is not None

        second_pid = await second_connection.scalar(text("SELECT pg_backend_pid()"))
        assert second_pid is not None
        second_cas = asyncio.create_task(
            second_repository.cas_transition(
                SCOPE,
                SLOT_KEY,
                "stage06_concurrency_memory_v1",
                expected_row_version=1,
                to_state=LifecycleState.ARCHIVED,
                valid_to=NOW,
                superseded_by="stage06_concurrency_unused_successor",
            )
        )
        async with engine.connect() as observer:
            await _wait_until_lock_blocked(observer, blocked_pid=second_pid)

        await first_transaction.commit()
        second_result = await asyncio.wait_for(second_cas, timeout=5.0)
        await second_transaction.commit()

        assert second_result is None
        async with engine.connect() as verification_connection:
            await verification_connection.execute(
                text(f'SET search_path TO "{schema_name}", public')
            )
            row = (
                await verification_connection.execute(
                    select(
                        learning_memories.c.lifecycle_state,
                        learning_memories.c.row_version,
                    ).where(learning_memories.c.memory_id == "stage06_concurrency_memory_v1")
                )
            ).one()
            assert row.lifecycle_state == "invalidated"
            assert row.row_version == 2
            assert (
                await verification_connection.scalar(
                    select(func.count()).select_from(learning_memories)
                )
                == 1
            )
    finally:
        if first_connection is not None:
            if first_connection.in_transaction():
                await first_connection.rollback()
            await first_connection.close()
        if second_cas is not None and not second_cas.done():
            second_cas.cancel()
            with suppress(asyncio.CancelledError):
                await second_cas
        if second_connection is not None:
            if second_connection.in_transaction():
                await second_connection.rollback()
            await second_connection.close()
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(DropSchema(schema_name, cascade=True))
        await engine.dispose()
