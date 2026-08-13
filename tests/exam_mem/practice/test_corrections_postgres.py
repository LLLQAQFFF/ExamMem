from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from exam_mem.config import ExamMemSettings
from exam_mem.contracts import (
    ErrorPatternValue,
    ErrorType,
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
)
from exam_mem.practice.corrections import ExplicitCorrectionRequest
from exam_mem.practice.provider import PracticeRuntimeProvider
from exam_mem.storage import (
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    load_database_settings,
    metadata,
)
from exam_mem.storage.models import (
    event_correction_targets,
    learning_events,
    learning_memories,
    lifecycle_decisions,
    memory_change_log,
    memory_provenance,
    practice_trace_spans,
    student_model_snapshots,
)

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.database,
    pytest.mark.repository,
]

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="correction_postgres_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)
SCOPE = MemoryScope(**CONTEXT.model_dump(), memory_namespace="error_pattern")
TARGET_MEMORY_ID = "correction_postgres_memory_v1"
TRACE_ID = "trace:correction-postgres:001"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _seed_event() -> LearningEvent:
    return LearningEvent(
        event_id="correction_postgres_seed_event",
        idempotency_key="idem:correction-postgres:seed",
        context=CONTEXT,
        session_id="practice:correction-postgres:001",
        question_id="question:correction-postgres:seed",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.5,
        answer_correct=False,
        error_type=ErrorType.FORMULA_MISUSE,
        error_detail="the numerator was reversed",
        occurred_at=NOW - timedelta(days=1),
    )


def _seed_memory() -> LearningMemory:
    event = _seed_event()
    return LearningMemory(
        memory_id=TARGET_MEMORY_ID,
        scope=SCOPE,
        slot_key="error_pattern:math1.probability.bayes:formula_misuse",
        value=ErrorPatternValue(
            error_type=ErrorType.FORMULA_MISUSE,
            summary="The learner always reverses the Bayes numerator.",
            details=["one graded answer"],
        ),
        confidence=0.9,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=event.occurred_at,
        valid_to=None,
        superseded_by=None,
        provenance=[event.event_id],
    )


def _request() -> ExplicitCorrectionRequest:
    return ExplicitCorrectionRequest(
        context=CONTEXT,
        memory_namespace="error_pattern",
        target_memory_id=TARGET_MEMORY_ID,
        session_id="practice:correction-postgres:001",
        idempotency_key="idem:correction-postgres:invalidate",
        statement="That diagnosis was false; the answer used an equivalent form.",
        occurred_at=NOW,
        trace_id=TRACE_ID,
        confirmed=True,
    )


async def _install_isolated_schema(connection: AsyncConnection, schema_name: str) -> None:
    await connection.execute(CreateSchema(schema_name))
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
    await connection.run_sync(metadata.create_all, checkfirst=False)
    for table_name in (
        "learning_events",
        "lifecycle_decisions",
        "memory_change_log",
        "baseline_memory_facts",
        "practice_trace_spans",
    ):
        await connection.execute(
            text(
                f"CREATE TRIGGER tr_{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()"
            )
        )


async def _seed_target(connection: AsyncConnection) -> None:
    event = _seed_event()
    appended = await PostgresLearningEventRepository(connection).append(
        event,
        trace_id="trace:correction-postgres:seed",
    )
    assert appended.event == event
    await PostgresLearningMemoryRepository(connection).insert_version(
        _seed_memory(),
        policy_version="lifecycle_policy_v1",
    )


async def _business_counts(connection: AsyncConnection) -> tuple[int, ...]:
    counts: list[int] = []
    for table in (
        learning_events,
        learning_memories,
        event_correction_targets,
        lifecycle_decisions,
        memory_change_log,
        memory_provenance,
        student_model_snapshots,
    ):
        count = await connection.scalar(select(func.count()).select_from(table))
        counts.append(int(count or 0))
    return tuple(counts)


async def test_correction_commits_full_chain_replays_and_hides_cross_scope_target() -> None:
    database_url = _database_url_or_skip()
    schema_name = f"correction_{uuid4().hex}"
    administration_engine = create_async_engine(database_url)
    try:
        async with administration_engine.begin() as connection:
            await _install_isolated_schema(connection, schema_name)
            await _seed_target(connection)

        def engine_factory(url: str):  # noqa: ANN202
            return create_async_engine(
                url,
                connect_args={
                    "server_settings": {
                        "search_path": f'"{schema_name}", public',
                    }
                },
            )

        provider = PracticeRuntimeProvider(
            settings=ExamMemSettings.model_validate({"memory_backend": "lifecycle"}),
            engine_factory=engine_factory,
        )
        async with provider.open_learning_memories(trace_id=TRACE_ID) as runtime:
            first = await runtime.corrections.apply(_request())
            async with runtime.engine.connect() as connection:
                counts_after_first = await _business_counts(connection)
            replay = await runtime.corrections.apply(_request())
            cross_scope = await runtime.queries.get_detail(
                context=CONTEXT.model_copy(update={"user_id": "other_user"}),
                memory_namespace="error_pattern",
                memory_id=TARGET_MEMORY_ID,
            )

        async with administration_engine.connect() as connection:
            await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
            counts_after_replay = await _business_counts(connection)
            target_state = await connection.scalar(
                select(learning_memories.c.lifecycle_state).where(
                    learning_memories.c.memory_id == TARGET_MEMORY_ID
                )
            )
            target_provenance = (
                await connection.scalars(
                    select(memory_provenance.c.event_id).where(
                        memory_provenance.c.memory_id == TARGET_MEMORY_ID
                    )
                )
            ).all()
            latest_model = await connection.scalar(
                select(student_model_snapshots.c.model).order_by(
                    student_model_snapshots.c.projection_version.desc()
                )
            )
            trace_names = (
                await connection.scalars(
                    select(practice_trace_spans.c.span_name)
                    .where(practice_trace_spans.c.trace_id == TRACE_ID)
                    .order_by(practice_trace_spans.c.step_id)
                )
            ).all()

        assert first.memory_result.decisions[0].operation is LifecycleOperation.INVALIDATE
        assert replay.memory_result.decisions[0].operation is LifecycleOperation.NO_OP
        assert counts_after_replay == counts_after_first
        assert cross_scope is None
        assert target_state == LifecycleState.INVALIDATED.value
        assert first.event.event_id in target_provenance
        assert latest_model["stable_error_patterns"] == []
        assert TARGET_MEMORY_ID not in first.recommendation_source_memory_ids
        assert trace_names == [
            "correction_target_resolved",
            "correction_event_appended",
            "correction_lifecycle_applied",
            "student_model_projected",
            "recommendation_refreshed",
            "correction_target_resolved",
            "correction_event_appended",
            "correction_lifecycle_applied",
            "recommendation_refreshed",
        ]
    finally:
        async with administration_engine.begin() as connection:
            with suppress(Exception):
                await connection.execute(DropSchema(schema_name, cascade=True))
        await administration_engine.dispose()


@pytest.mark.parametrize(
    ("suffix", "request_updates", "operation", "expected_states"),
    [
        (
            "supersede",
            {
                "idempotency_key": "idem:correction-postgres:supersede",
                "trace_id": "trace:correction-postgres:supersede",
                "replacement_value": ErrorPatternValue(
                    error_type=ErrorType.FORMULA_MISUSE,
                    summary="The learner sometimes omits the Bayes denominator.",
                    details=["confirmed correction"],
                ),
            },
            LifecycleOperation.SUPERSEDE,
            (LifecycleState.ARCHIVED, LifecycleState.ACTIVE),
        ),
        (
            "contested",
            {
                "idempotency_key": "idem:correction-postgres:contested",
                "trace_id": "trace:correction-postgres:contested",
                "uncertain": True,
            },
            LifecycleOperation.CONTESTED,
            (LifecycleState.ACTIVE, LifecycleState.CONTESTED),
        ),
    ],
)
async def test_replacement_and_uncertain_corrections_persist_frozen_policy_branches(
    suffix: str,
    request_updates: dict,
    operation: LifecycleOperation,
    expected_states: tuple[LifecycleState, ...],
) -> None:
    database_url = _database_url_or_skip()
    schema_name = f"correction_{suffix}_{uuid4().hex}"
    administration_engine = create_async_engine(database_url)
    try:
        async with administration_engine.begin() as connection:
            await _install_isolated_schema(connection, schema_name)
            await _seed_target(connection)

        def engine_factory(url: str):  # noqa: ANN202
            return create_async_engine(
                url,
                connect_args={
                    "server_settings": {
                        "search_path": f'"{schema_name}", public',
                    }
                },
            )

        provider = PracticeRuntimeProvider(
            settings=ExamMemSettings.model_validate({"memory_backend": "lifecycle"}),
            engine_factory=engine_factory,
        )
        request = _request().model_copy(update=request_updates)
        async with provider.open_learning_memories(trace_id=request.trace_id) as runtime:
            result = await runtime.corrections.apply(request)

        async with administration_engine.connect() as connection:
            await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
            states = (
                await connection.scalars(
                    select(learning_memories.c.lifecycle_state).order_by(
                        learning_memories.c.version
                    )
                )
            ).all()
            correction_event_count = await connection.scalar(
                select(func.count())
                .select_from(learning_events)
                .where(learning_events.c.event_type == "explicit_correction")
            )
            decision_count = await connection.scalar(
                select(func.count()).select_from(lifecycle_decisions)
            )

        assert result.memory_result.decisions[0].operation is operation
        assert states == [state.value for state in expected_states]
        assert correction_event_count == 1
        assert decision_count == 1
    finally:
        async with administration_engine.begin() as connection:
            with suppress(Exception):
                await connection.execute(DropSchema(schema_name, cascade=True))
        await administration_engine.dispose()
