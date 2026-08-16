from __future__ import annotations

from contextlib import suppress
from datetime import datetime, timezone
import json
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.plugins import PluginManager
from deeptutor_plugins.exam_mem import ExamMemPlugin
from exam_mem.config import ExamMemSettings
from exam_mem.practice.capability import (
    PRACTICE_CONTEXT_METADATA_KEY,
    PRACTICE_QUESTIONS_CONFIG_KEY,
)
from exam_mem.storage import load_database_settings, metadata
from exam_mem.storage.models import (
    learning_events,
    learning_memories,
    lifecycle_decisions,
    memory_change_log,
    memory_provenance,
    practice_trace_spans,
    practice_workflow_checkpoints,
    student_model_snapshots,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.e2e]

NOW = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL E2E tests")
    return load_database_settings().sqlalchemy_url()


def _questions() -> list[dict[str, object]]:
    rubric = {"required_steps": [{"id": "apply_bayes", "description": "Apply Bayes"}]}
    return [
        {
            "question_id": "question:plugin-closure:001",
            "stem": "Calculate P(A|B) using Bayes' theorem.",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.5,
            "reference_answer": "Apply Bayes' theorem.",
            "grading_rubric": rubric,
        },
        {
            "question_id": "question:plugin-closure:002",
            "stem": "Calculate another posterior probability.",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.4,
            "reference_answer": "Apply Bayes' theorem.",
            "grading_rubric": rubric,
        },
    ]


def _practice_context(*, with_answer: bool) -> dict[str, object]:
    payload: dict[str, object] = {
        "practice_session_id": "practice:plugin-closure:001",
        "scope": {
            "user_id": "untrusted-user",
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        "step_state": "IDLE",
        "trace_id": "trace:plugin-closure:001",
    }
    if with_answer:
        payload.update(
            {
                "current_question": _questions()[0],
                "submitted_answer": {
                    "practice_session_id": "practice:plugin-closure:001",
                    "question_id": "question:plugin-closure:001",
                    "answer": "I reversed the conditional probability.",
                    "submitted_at": NOW.isoformat(),
                    "idempotency_key": "answer:plugin-closure:001",
                },
                "step_state": "ANSWER_RECEIVED",
            }
        )
    return payload


async def _fixed_completion(**kwargs: object) -> str:
    response_format = kwargs["response_format"]
    assert isinstance(response_format, dict)
    schema = response_format["json_schema"]
    assert isinstance(schema, dict)
    name = schema["name"]
    payloads = {
        "exam_mem_grade_result": {
            "correct": False,
            "score": 0.2,
            "matched_rubric_items": [],
            "missed_rubric_items": ["apply_bayes"],
            "evidence": ["The conditional direction was reversed."],
        },
        "exam_mem_knowledge_point_extraction": {
            "primary": {"name": "贝叶斯公式", "confidence": 0.99},
            "secondary": [],
        },
        "exam_mem_diagnosis_result": {
            "knowledge_point_ids": ["math1.probability.bayes"],
            "error_type": "concept_confusion",
            "explanation": "The prior and posterior were reversed.",
            "confidence": 0.9,
            "analyzer_version": "error_analyzer_v1",
        },
    }
    return json.dumps(payloads[name], ensure_ascii=False)


async def _run(capability, context: dict[str, object]):
    bus = StreamBus()
    await capability.run(
        UnifiedContext(
            config_overrides={
                PRACTICE_CONTEXT_METADATA_KEY: context,
                PRACTICE_QUESTIONS_CONFIG_KEY: _questions(),
            }
        ),
        bus,
    )
    await bus.close()
    events = [event async for event in bus.subscribe()]
    return next(event for event in events if event.type is StreamEventType.RESULT)


async def test_plugin_registry_runs_recoverable_postgresql_closure(monkeypatch) -> None:
    database_url = _database_url_or_skip()
    schema_name = f"practice_plugin_{uuid4().hex}"
    administration_engine = create_async_engine(database_url)
    try:
        async with administration_engine.begin() as connection:
            await connection.execute(CreateSchema(schema_name))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
            await connection.run_sync(metadata.create_all, checkfirst=False)
            await connection.execute(
                text(
                    "CREATE TRIGGER tr_practice_trace_spans_append_only "
                    "BEFORE UPDATE OR DELETE ON practice_trace_spans "
                    "FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()"
                )
            )

        def engine_factory(url: str):
            assert url == database_url
            return create_async_engine(
                url,
                connect_args={"server_settings": {"search_path": f'"{schema_name}", public'}},
            )

        plugin = ExamMemPlugin(
            ExamMemSettings(memory_backend="lifecycle"),
            engine_factory=engine_factory,
        )
        manager = PluginManager(factories={"exam_mem": lambda: plugin})
        capability = manager.capabilities()[0]
        monkeypatch.setattr("deeptutor.services.llm.complete", _fixed_completion)

        issued = await _run(capability, _practice_context(with_answer=False))
        completed = await _run(capability, _practice_context(with_answer=True))
        replay = await _run(capability, _practice_context(with_answer=True))

        assert issued.metadata["practice"]["step_state"] == "QUESTION_READY"
        assert completed.metadata["practice"]["step_state"] == "RECOMMENDED"
        assert replay.metadata["practice"]["replayed"] is True
        assert completed.metadata["practice"]["question"]["question_id"] == (
            "question:plugin-closure:002"
        )

        async with administration_engine.connect() as connection:
            await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
            counts = {
                "events": await connection.scalar(
                    select(func.count()).select_from(learning_events)
                ),
                "memories": await connection.scalar(
                    select(func.count()).select_from(learning_memories)
                ),
                "provenance": await connection.scalar(
                    select(func.count()).select_from(memory_provenance)
                ),
                "decisions": await connection.scalar(
                    select(func.count()).select_from(lifecycle_decisions)
                ),
                "changes": await connection.scalar(
                    select(func.count()).select_from(memory_change_log)
                ),
                "models": await connection.scalar(
                    select(func.count()).select_from(student_model_snapshots)
                ),
                "checkpoints": await connection.scalar(
                    select(func.count()).select_from(practice_workflow_checkpoints)
                ),
                "trace": await connection.scalar(
                    select(func.count()).select_from(practice_trace_spans)
                ),
            }
            change_states = dict(
                (
                    await connection.execute(
                        select(
                            memory_change_log.c.apply_state,
                            func.count(),
                        ).group_by(memory_change_log.c.apply_state)
                    )
                ).all()
            )

        assert counts["events"] == 1
        assert counts["memories"] == 2
        assert counts["provenance"] == 2
        assert counts["decisions"] == 2
        assert counts["changes"] == 4
        assert change_states == {"PLANNED": 2, "APPLIED": 2}
        assert counts["models"] == 1
        assert counts["checkpoints"] == 2
        assert counts["trace"] > 10
    finally:
        async with administration_engine.begin() as connection:
            with suppress(Exception):
                await connection.execute(DropSchema(schema_name, cascade=True))
        await administration_engine.dispose()
