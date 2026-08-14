from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.storage import (
    LearningObservationConflict,
    PostgresLearningObservationRepository,
    load_database_settings,
)
from exam_mem.storage.models import learning_observations

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def test_learning_observation_is_idempotent_append_only_and_actioned() -> None:
    engine = create_async_engine(_database_url_or_skip())
    suffix = uuid.uuid4().hex
    user_id = f"observation-user-{suffix}"
    values = {
        "observation_id": f"observation-{suffix}",
        "user_id": user_id,
        "exam_id": f"plan:{suffix}",
        "subject_id": "subject-1",
        "taxonomy_version": "taxonomy-v1",
        "channel": "chat",
        "source_session_id": "chat-session",
        "source_turn_ids": ["turn-1"],
        "knowledge_point_ids": ["kp-1"],
        "summary": "讨论了函数极限。",
        "rationale": "对话包含定义解释。",
        "confidence": 0.91,
        "agent_contract_version": "learning_observation_agent_v1",
        "source_fingerprint": "a" * 64,
    }
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            repository = PostgresLearningObservationRepository(connection)
            try:
                created = await repository.append(**values)
                replay = await repository.append(
                    **{
                        **values,
                        "observation_id": f"ignored-{suffix}",
                        "summary": "模型重复执行时产生了不同措辞。",
                        "confidence": 0.89,
                    }
                )
                assert created["observation_id"] == replay["observation_id"]
                assert replay["summary"] == values["summary"]
                assert replay["status"] == "pending"

                confirmed = await repository.append_action(
                    action_id=f"action-{suffix}",
                    observation_id=created["observation_id"],
                    user_id=user_id,
                    action="confirm",
                    idempotency_key=f"confirm-{suffix}",
                )
                assert confirmed["status"] == "confirmed"
                assert (
                    await repository.list(
                        user_id=user_id,
                        exam_id=values["exam_id"],
                        subject_id=values["subject_id"],
                        status="confirmed",
                    )
                )[0]["knowledge_point_ids"] == ["kp-1"]

                with pytest.raises(LearningObservationConflict):
                    await repository.append_action(
                        action_id=f"conflict-{suffix}",
                        observation_id=created["observation_id"],
                        user_id=user_id,
                        action="dismiss",
                        idempotency_key=f"confirm-{suffix}",
                    )

                savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError, match="append-only"):
                    await connection.execute(
                        update(learning_observations)
                        .where(learning_observations.c.observation_id == created["observation_id"])
                        .values(summary="forbidden")
                    )
                await savepoint.rollback()
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
