from __future__ import annotations

import os

from pydantic import ValidationError
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.contracts import StudentModel
from exam_mem.storage import (
    PostgresStudentModelRepository,
    ProjectionConflict,
    StudentModelRepository,
    StudentModelSnapshot,
    load_database_settings,
)

pytestmark = [pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _snapshot(
    *,
    snapshot_id: str,
    projection_version: int,
    user_id: str = "student_model_repository_user",
) -> StudentModelSnapshot:
    event_watermark = f"student_model_event_{projection_version:03d}"
    return StudentModelSnapshot(
        snapshot_id=snapshot_id,
        model=StudentModel(
            context={
                "user_id": user_id,
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            weak_points=["math1.linear_algebra.matrix_rank"],
            mastered_points=["math1.linear_algebra.determinant"],
            stable_error_patterns=[],
            active_plans=[],
            projection_version=projection_version,
            source_watermark=event_watermark,
        ),
        source_event_watermark=event_watermark,
        source_memory_watermark=f"student_model_memory_{projection_version:03d}",
    )


def test_snapshot_requires_one_consistent_event_watermark() -> None:
    snapshot = _snapshot(snapshot_id="student_model_snapshot_contract", projection_version=1)
    payload = snapshot.model_dump(mode="json")
    payload["source_event_watermark"] = "different_event"

    with pytest.raises(ValidationError, match="must equal"):
        StudentModelSnapshot.model_validate(payload)


@pytest.mark.asyncio
async def test_save_get_latest_and_clear_are_context_isolated() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresStudentModelRepository(connection)
                assert isinstance(repository, StudentModelRepository)
                first = _snapshot(
                    snapshot_id="student_model_snapshot_001",
                    projection_version=1,
                )
                second = _snapshot(
                    snapshot_id="student_model_snapshot_002",
                    projection_version=2,
                )
                other_context = _snapshot(
                    snapshot_id="student_model_snapshot_other_context",
                    projection_version=1,
                    user_id="student_model_repository_other_user",
                )

                await repository.save_projection(first)
                await repository.save_projection(second)
                await repository.save_projection(other_context)

                assert await repository.get_latest(first.model.context) == second
                assert await repository.get_latest(other_context.model.context) == other_context
                with pytest.raises(ProjectionConflict, match="already exists"):
                    await repository.save_projection(second)

                assert await repository.clear_projection(first.model.context) == 2
                assert await repository.get_latest(first.model.context) is None
                assert await repository.get_latest(other_context.model.context) == other_context
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
