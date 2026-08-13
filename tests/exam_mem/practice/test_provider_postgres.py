from __future__ import annotations

from contextlib import suppress
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEventType
from deeptutor.core.stream_bus import StreamBus
from exam_mem.config import ExamMemSettings
from exam_mem.practice import ExamPracticeCapability
from exam_mem.practice.capability import PRACTICE_CONTEXT_METADATA_KEY
from exam_mem.practice.provider import (
    PRACTICE_QUESTIONS_METADATA_KEY,
    PracticeRuntimeProvider,
)
from exam_mem.storage import load_database_settings, metadata
from exam_mem.storage.models import practice_trace_spans, practice_workflow_checkpoints

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.database,
    pytest.mark.repository,
]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _practice_context() -> dict:
    return {
        "practice_session_id": "practice:provider-postgres:001",
        "scope": {
            "user_id": "practice_provider_postgres_user",
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        "step_state": "IDLE",
        "trace_id": "trace:provider-postgres:001",
    }


def _question() -> dict:
    return {
        "question_id": "question:provider-postgres:001",
        "stem": "Calculate one conditional probability.",
        "knowledge_point_ids": ["math1.probability.bayes"],
        "difficulty": 0.5,
        "reference_answer": "Apply Bayes' theorem.",
        "grading_rubric": {"required_steps": ["apply_bayes"]},
    }


async def test_real_capability_provider_commits_checkpoint_and_trace_in_isolated_schema() -> None:
    database_url = _database_url_or_skip()
    schema_name = f"practice_provider_{uuid4().hex}"
    administration_engine = create_async_engine(database_url)
    try:
        async with administration_engine.begin() as connection:
            await connection.execute(CreateSchema(schema_name))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
            # ``checkfirst=True`` can see same-named tables later in search_path
            # (normally ``public``) and incorrectly skip the isolated schema.
            await connection.run_sync(metadata.create_all, checkfirst=False)
            await connection.execute(
                text(
                    "CREATE TRIGGER tr_practice_trace_spans_append_only "
                    "BEFORE UPDATE OR DELETE ON practice_trace_spans "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    "exam_mem_reject_append_only_mutation()"
                )
            )

        def engine_factory(url: str):  # noqa: ANN202
            return create_async_engine(
                url,
                connect_args={
                    "server_settings": {
                        "search_path": f'"{schema_name}", public',
                    }
                },
            )

        capability = ExamPracticeCapability(
            runtime_factory=PracticeRuntimeProvider(
                settings=ExamMemSettings.model_validate({"memory_backend": "none"}),
                engine_factory=engine_factory,
            )
        )
        stream = StreamBus()
        await capability.run(
            UnifiedContext(
                config_overrides={
                    PRACTICE_CONTEXT_METADATA_KEY: _practice_context(),
                    PRACTICE_QUESTIONS_METADATA_KEY: [_question()],
                }
            ),
            stream,
        )
        await stream.close()
        events = [event async for event in stream.subscribe()]

        async with administration_engine.connect() as connection:
            await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
            checkpoint_count = await connection.scalar(
                select(func.count()).select_from(practice_workflow_checkpoints)
            )
            trace_count = await connection.scalar(
                select(func.count()).select_from(practice_trace_spans)
            )

        result = next(event for event in events if event.type is StreamEventType.RESULT)
        assert result.metadata["practice"]["step_state"] == "QUESTION_READY"
        assert result.metadata["practice"]["runtime"]["backend_mode"] == "none"
        assert checkpoint_count == 1
        assert trace_count == 3

        changed_capability = ExamPracticeCapability(
            runtime_factory=PracticeRuntimeProvider(
                settings=ExamMemSettings.model_validate({"memory_backend": "lifecycle"}),
                engine_factory=engine_factory,
            )
        )
        replay_stream = StreamBus()
        await changed_capability.run(
            UnifiedContext(
                config_overrides={
                    PRACTICE_CONTEXT_METADATA_KEY: _practice_context(),
                    PRACTICE_QUESTIONS_METADATA_KEY: [_question()],
                }
            ),
            replay_stream,
        )
        await replay_stream.close()
        replay_events = [event async for event in replay_stream.subscribe()]
        replay_result = next(
            event for event in replay_events if event.type is StreamEventType.RESULT
        )
        assert replay_result.metadata["practice"]["replayed"] is True
        assert replay_result.metadata["practice"]["runtime"]["backend_mode"] == "none"
    finally:
        async with administration_engine.begin() as connection:
            with suppress(Exception):
                await connection.execute(DropSchema(schema_name, cascade=True))
        await administration_engine.dispose()
