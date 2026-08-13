from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.contracts import LearningContext, MemoryScope
from exam_mem.practice import (
    PracticeContext,
    PracticeSpanName,
    PracticeSpanStatus,
    PracticeState,
    PracticeTraceSpan,
    PracticeWorkflowCheckpoint,
    Question,
)
from exam_mem.storage import (
    AppendStatus,
    PostgresPracticeCheckpointRepository,
    PostgresPracticeTraceRepository,
    load_database_settings,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
LEARNING_CONTEXT = LearningContext(
    user_id="practice_runtime_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)
SCOPE = MemoryScope(
    **LEARNING_CONTEXT.model_dump(),
    memory_namespace="mastery",
)


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _idle_checkpoint() -> PracticeWorkflowCheckpoint:
    return PracticeWorkflowCheckpoint(
        checkpoint_key="start",
        context=PracticeContext(
            practice_session_id="practice:runtime:001",
            scope=SCOPE,
            trace_id="trace:runtime:001",
        ),
    )


def _ready_checkpoint() -> PracticeWorkflowCheckpoint:
    question = Question(
        question_id="question:runtime:001",
        stem="Calculate one probability.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.5,
        reference_answer="Apply Bayes' theorem.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )
    return PracticeWorkflowCheckpoint(
        checkpoint_key="start",
        context=PracticeContext(
            practice_session_id="practice:runtime:001",
            scope=SCOPE,
            current_question=question,
            step_state=PracticeState.QUESTION_READY,
            trace_id="trace:runtime:001",
        ),
    )


def _span(*, output: str = "selected") -> PracticeTraceSpan:
    return PracticeTraceSpan(
        trace_id="trace:runtime:001",
        step_id=1,
        name=PracticeSpanName.QUESTION_SELECTED,
        status=PracticeSpanStatus.COMPLETED,
        input_summary={"scope": LEARNING_CONTEXT.model_dump(mode="json")},
        output_summary={"status": output},
        versions={"policy_version": "recommendation_policy_v1"},
        started_at=NOW,
        completed_at=NOW,
        duration_ms=0.0,
    )


async def test_checkpoint_repository_create_replay_and_cas_are_transactional() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresPracticeCheckpointRepository(connection)

                created = await repository.create(_idle_checkpoint())
                existing = await repository.create(_idle_checkpoint())
                advanced = await repository.advance(
                    _ready_checkpoint(),
                    expected_row_version=1,
                )
                stale = await repository.advance(
                    _ready_checkpoint(),
                    expected_row_version=1,
                )
                loaded = await repository.get(
                    LEARNING_CONTEXT,
                    "practice:runtime:001",
                    "start",
                )
                issued = await repository.find_issued_question(
                    LEARNING_CONTEXT,
                    "practice:runtime:001",
                    "question:runtime:001",
                )

                assert created.status is AppendStatus.CREATED
                assert existing.status is AppendStatus.EXISTING
                assert advanced is not None
                assert advanced.row_version == 2
                assert stale is None
                assert loaded == advanced
                assert issued == _ready_checkpoint().context.current_question
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_trace_repository_is_append_only_and_idempotent_by_step() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresPracticeTraceRepository(connection)

                assert await repository.next_step_id("trace:runtime:001") == 1
                created = await repository.append(_span())
                existing = await repository.append(_span())
                conflict = await repository.append(_span(output="different"))
                spans = await repository.list_trace("trace:runtime:001")

                assert created.status is AppendStatus.CREATED
                assert existing.status is AppendStatus.EXISTING
                assert conflict.status is AppendStatus.CONFLICT
                assert spans == [_span()]
                assert await repository.next_step_id("trace:runtime:001") == 2
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
