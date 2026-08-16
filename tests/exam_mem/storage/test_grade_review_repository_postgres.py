from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.contracts import LearningContext
from exam_mem.practice import GradeReviewAction, GradeReviewEvent
from exam_mem.storage import AppendStatus, PostgresGradeReviewRepository, load_database_settings
from exam_mem.storage.models import grade_review_events

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="grade_review_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _event(*, reason: str = "The rubric was applied incorrectly.") -> GradeReviewEvent:
    return GradeReviewEvent(
        review_event_id="grade_review_event:test:001",
        review_chain_id="grade_review:test:001",
        idempotency_key="grade_review_idem:test:001",
        action=GradeReviewAction.DISPUTE,
        user_id=CONTEXT.user_id,
        exam_id=CONTEXT.exam_id,
        subject_id=CONTEXT.subject_id,
        practice_session_id="practice:review:001",
        checkpoint_key="answer:review:001",
        reason=reason,
        created_at=NOW,
    )


async def test_grade_review_is_scoped_idempotent_and_append_only() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            repository = PostgresGradeReviewRepository(connection)
            created = await repository.append(_event())
            existing = await repository.append(_event())
            conflict = await repository.append(_event(reason="Different content."))
            own = await repository.list_scope(CONTEXT)
            other = await repository.list_scope(
                CONTEXT.model_copy(update={"user_id": "another_user"})
            )

            assert created.status is AppendStatus.CREATED
            assert existing.status is AppendStatus.EXISTING
            assert conflict.status is AppendStatus.CONFLICT
            assert own == [_event()]
            assert other == []

            with pytest.raises(Exception, match="append-only"):
                await connection.execute(grade_review_events.update().values(action="uphold"))
            await transaction.rollback()
    finally:
        await engine.dispose()


async def test_grade_review_transaction_rollback_leaves_no_rows() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            await PostgresGradeReviewRepository(connection).append(_event())
            await transaction.rollback()
        async with engine.connect() as connection:
            count = await connection.scalar(
                select(func.count())
                .select_from(grade_review_events)
                .where(grade_review_events.c.review_event_id == _event().review_event_id)
            )
            trigger = await connection.scalar(
                text(
                    "SELECT tgname FROM pg_trigger "
                    "WHERE tgname = 'tr_grade_review_events_append_only'"
                )
            )
        assert count == 0
        assert trigger == "tr_grade_review_events_append_only"
    finally:
        await engine.dispose()
