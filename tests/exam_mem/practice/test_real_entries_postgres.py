from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI, WebSocketDisconnect
from httpx import ASGITransport, AsyncClient
import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

from deeptutor.api.routers.unified_ws import unified_websocket
from deeptutor.app import DeepTutorApp, TurnRequest
from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.paths import local_admin_user
from deeptutor.plugins import PluginManager
from deeptutor.runtime.registry.capability_registry import CapabilityRegistry
from deeptutor.runtime.registry.tool_registry import ToolRegistry
from deeptutor.services.llm.config import LLMConfig
from deeptutor.services.memory import memory_path_service_override
from deeptutor.services.path_service import PathService
from deeptutor.services.session.sqlite_store import SQLiteSessionStore
from deeptutor.services.session.turn_runtime import TurnRuntimeManager
from deeptutor_plugins.exam_mem import ExamMemPlugin
from deeptutor_plugins.exam_mem.api import build_router
from exam_mem.config import ExamMemSettings
from exam_mem.practice import stage07_practice_questions, stage07_question
from exam_mem.practice.capability import (
    PRACTICE_CONTEXT_METADATA_KEY,
    PRACTICE_QUESTIONS_CONFIG_KEY,
)
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    load_database_settings,
    metadata,
)
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
PRACTICE_SESSION_ID = "practice:real-entry:001"
TRACE_ID = "trace:real-entry:001"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL E2E tests")
    return load_database_settings().sqlalchemy_url()


async def _install_schema(connection: AsyncConnection, schema_name: str) -> None:
    await connection.execute(CreateSchema(schema_name))
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))
    await connection.run_sync(metadata.create_all, checkfirst=False)
    for table_name in (
        "learning_events",
        "lifecycle_decisions",
        "memory_change_log",
        "baseline_memory_facts",
        "practice_trace_spans",
        "grade_review_events",
    ):
        await connection.execute(
            text(
                f"CREATE TRIGGER tr_{table_name}_append_only "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION exam_mem_reject_append_only_mutation()"
            )
        )


@asynccontextmanager
async def _isolated_database(
    prefix: str,
) -> AsyncIterator[tuple[AsyncEngine, str, Callable[[str], AsyncEngine]]]:
    database_url = _database_url_or_skip()
    schema_name = f"{prefix}_{uuid4().hex}"
    administration_engine = create_async_engine(database_url)
    try:
        async with administration_engine.begin() as connection:
            await _install_schema(connection, schema_name)

        def engine_factory(url: str) -> AsyncEngine:
            assert url == database_url
            return create_async_engine(
                url,
                connect_args={"server_settings": {"search_path": f'"{schema_name}", public'}},
            )

        yield administration_engine, schema_name, engine_factory
    finally:
        async with administration_engine.begin() as connection:
            with suppress(Exception):
                await connection.execute(DropSchema(schema_name, cascade=True))
        await administration_engine.dispose()


class _FixedEmbeddingClient:
    async def embed(self, texts: list[str], *, input_type: str | None = None) -> list[list[float]]:
        assert input_type in {"search_document", "search_query"}
        vector = [1.0, *([0.0] * (LEARNING_MEMORY_EMBEDDING_DIMENSION - 1))]
        return [vector.copy() for _ in texts]


async def _fixed_completion(**kwargs: object) -> str:
    response_format = kwargs["response_format"]
    assert isinstance(response_format, dict)
    json_schema = response_format["json_schema"]
    assert isinstance(json_schema, dict)
    name = json_schema["name"]
    prompt = str(kwargs.get("prompt") or "")
    probability = "bayes" in prompt.lower() or "贝叶斯" in prompt
    knowledge_point_id = (
        "math1.probability.bayes" if probability else "math1.linear_algebra.matrix_multiplication"
    )
    if name == "exam_mem_grade_result":
        return json.dumps(
            {
                "correct": False,
                "score": 0.25,
                "matched_rubric_items": [],
                "missed_rubric_items": [],
                "evidence": ["The controlled answer is intentionally incorrect."],
            }
        )
    if name == "exam_mem_knowledge_point_extraction":
        return json.dumps(
            {
                "primary": {
                    "name": "贝叶斯公式" if probability else "矩阵乘法",
                    "confidence": 0.99,
                },
                "secondary": [],
            },
            ensure_ascii=False,
        )
    if name == "exam_mem_diagnosis_result":
        return json.dumps(
            {
                "knowledge_point_ids": [knowledge_point_id],
                "error_type": "concept_confusion",
                "explanation": "The controlled answer uses the wrong rule.",
                "confidence": 0.9,
                "analyzer_version": "error_analyzer_v1",
            }
        )
    if name == "exam_mem_relation_classifier_output":
        return json.dumps(
            {
                "candidate_display_number": 1,
                "relation": "duplicate",
                "canonical_knowledge_point_id": knowledge_point_id,
                "error_type": "concept_confusion",
                "error_summary": "The controlled error repeats the same pattern.",
                "confidence": 0.9,
                "reason": "The controlled facts match.",
            }
        )
    raise AssertionError(f"unexpected completion schema: {name}")


async def _completed() -> None:
    return None


def _build_app(capabilities: CapabilityRegistry, store_path: Path) -> DeepTutorApp:
    store = SQLiteSessionStore(store_path)
    app = DeepTutorApp.__new__(DeepTutorApp)
    app.runtime = TurnRuntimeManager(store)
    app.store = store
    app.notebooks = SimpleNamespace()
    app.capabilities = capabilities
    return app


def _wire_runtime(
    monkeypatch: pytest.MonkeyPatch,
    engine_factory: Callable[[str], AsyncEngine],
    tmp_path: Path,
) -> tuple[PluginManager, ExamMemPlugin, DeepTutorApp, PathService]:
    plugin = ExamMemPlugin(
        ExamMemSettings(memory_backend="lifecycle"),
        engine_factory=engine_factory,
    )
    manager = PluginManager(factories={"exam_mem": lambda: plugin})
    capabilities = CapabilityRegistry()
    tools = ToolRegistry()
    for capability in manager.capabilities():
        capabilities.register(capability)
    for tool in manager.tools():
        tools.register(tool)

    monkeypatch.setattr(
        "deeptutor.runtime.orchestrator.get_capability_registry", lambda: capabilities
    )
    monkeypatch.setattr(
        "deeptutor.runtime.registry.capability_registry.get_capability_registry",
        lambda: capabilities,
    )
    monkeypatch.setattr("deeptutor.runtime.orchestrator.get_tool_registry", lambda: tools)
    monkeypatch.setattr("deeptutor.services.llm.complete", _fixed_completion)
    monkeypatch.setattr(
        "exam_mem.practice.provider.get_embedding_client", lambda: _FixedEmbeddingClient()
    )
    fixed_config = LLMConfig(model="fixed-entry-test", api_key="not-used")
    monkeypatch.setattr(
        "deeptutor.services.model_selection.runtime.activate_llm_selection",
        lambda _selection: (fixed_config, None),
    )
    monkeypatch.setattr(
        TurnRuntimeManager, "_maybe_generate_session_title", lambda *_a, **_k: _completed()
    )
    monkeypatch.setattr(
        TurnRuntimeManager, "_mirror_events_to_workspace", lambda *_a, **_k: _completed()
    )
    app = _build_app(capabilities, tmp_path / "entry-chat.db")
    return manager, plugin, app, PathService(workspace_root=tmp_path / "runtime")


def _questions() -> list[dict[str, object]]:
    return [question.model_dump(mode="json") for question in stage07_practice_questions()]


def _context(*, question_id: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "practice_session_id": PRACTICE_SESSION_ID,
        "scope": {
            "user_id": "untrusted-client-user",
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        "step_state": "IDLE",
        "trace_id": TRACE_ID,
    }
    if question_id is not None:
        question = stage07_question(question_id)
        assert question is not None
        payload.update(
            {
                "current_question": question.model_dump(mode="json"),
                "submitted_answer": {
                    "practice_session_id": PRACTICE_SESSION_ID,
                    "question_id": question_id,
                    "answer": "controlled incorrect answer",
                    "submitted_at": NOW.isoformat(),
                    "idempotency_key": "answer:real-entry:001",
                },
                "step_state": "ANSWER_RECEIVED",
            }
        )
    return payload


def _turn_request(*, session_id: str | None, question_id: str | None) -> TurnRequest:
    return TurnRequest(
        content="提交答案" if question_id else "开始练习",
        capability="exam_practice",
        session_id=session_id,
        language="zh",
        config={
            PRACTICE_CONTEXT_METADATA_KEY: _context(question_id=question_id),
            PRACTICE_QUESTIONS_CONFIG_KEY: _questions(),
            "_persist_user_message": False,
        },
    )


async def _sdk_turn(
    app: DeepTutorApp, request: TurnRequest
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    session, turn = await app.start_turn(request)
    events = [event async for event in app.stream_turn(str(turn["id"]))]
    assert events[-1]["type"] == "done"
    result = next(event for event in events if event.get("type") == "result")
    return session, result, events


class _MemoryWebSocket:
    def __init__(self, payload: dict[str, object]) -> None:
        self.query_params: dict[str, str] = {}
        self.cookies: dict[str, str] = {}
        self.events: list[dict[str, object]] = []
        self._payload = json.dumps(payload)
        self._received = False
        self._done = asyncio.Event()

    async def accept(self) -> None:
        return None

    async def close(self, code: int) -> None:
        raise AssertionError(f"unexpected close: {code}")

    async def receive_text(self) -> str:
        if not self._received:
            self._received = True
            return self._payload
        await asyncio.wait_for(self._done.wait(), timeout=30)
        raise WebSocketDisconnect

    async def send_text(self, raw: str) -> None:
        event = json.loads(raw)
        self.events.append(event)
        if event.get("type") == "done":
            self._done.set()


async def _websocket_turn(
    app: DeepTutorApp, request: TurnRequest, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, object], dict[str, object], list[dict[str, object]]]:
    async def authenticate(_ws: object):
        return set_current_user(local_admin_user())

    monkeypatch.setattr("deeptutor.api.routers.auth.ws_require_auth", authenticate)
    monkeypatch.setattr("deeptutor.services.session.get_turn_runtime_manager", lambda: app.runtime)
    socket = _MemoryWebSocket({"type": "start_turn", **request.to_payload()})
    await unified_websocket(socket)  # type: ignore[arg-type]
    result = next(event for event in socket.events if event.get("type") == "result")
    return {"id": result["session_id"]}, result, socket.events


class _GeneratedQuestionTurnHost:
    """Fake only native Quiz generation while keeping ExamMem on the real Host runtime."""

    def __init__(self, app: DeepTutorApp) -> None:
        self._app = app
        self.deleted_sessions: list[str] = []

    async def start_turn(self, request):  # noqa: ANN001, ANN201
        if request.capability == "deep_question":
            return {"id": "transient-generation"}, {"id": "generated-turn"}
        payload = asdict(request)
        if not request.attachments:
            payload.pop("attachments")
        return await self._app.start_turn(payload)

    async def stream_turn(self, turn_id: str):  # noqa: ANN201
        if turn_id == "generated-turn":
            yield {"type": "stage_start", "stage": "exploring"}
            yield {"type": "stage_start", "stage": "planning"}
            yield {"type": "stage_start", "stage": "quizzing"}
            for index in range(2):
                yield {
                    "type": "content",
                    "metadata": {
                        "call_kind": "quiz_question_emitted",
                        "qa_pair": {
                            "question": f"贝叶斯专项题 {index + 1}",
                            "correct_answer": f"受控答案 {index + 1}",
                            "explanation": f"受控解析 {index + 1}",
                            "difficulty": "medium",
                        },
                    },
                }
            return
        async for event in self._app.stream_turn(turn_id):
            yield event

    async def delete_session(self, session_id: str) -> bool:
        self.deleted_sessions.append(session_id)
        return True


async def _counts(engine: AsyncEngine, schema_name: str) -> tuple[int, ...]:
    tables = (
        learning_events,
        learning_memories,
        memory_provenance,
        lifecycle_decisions,
        memory_change_log,
        student_model_snapshots,
        practice_workflow_checkpoints,
        practice_trace_spans,
    )
    async with engine.connect() as connection:
        await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
        counts: list[int] = []
        for table in tables:
            count = await connection.scalar(select(func.count()).select_from(table))
            counts.append(int(count or 0))
        return tuple(counts)


async def test_generated_questions_are_checkpointed_and_attempts_share_exam_scope(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async with _isolated_database("generated_http") as (
        administration_engine,
        schema_name,
        engine_factory,
    ):
        _manager, plugin, app, path_service = _wire_runtime(monkeypatch, engine_factory, tmp_path)
        host = _GeneratedQuestionTurnHost(app)
        api = FastAPI()
        api.include_router(
            build_router(plugin._runtime_provider, turn_host=host),  # noqa: SLF001
            prefix="/api/v1/exam-mem",
        )
        user_token = set_current_user(local_admin_user())
        try:
            with memory_path_service_override(path_service):
                async with AsyncClient(
                    transport=ASGITransport(app=api), base_url="http://test"
                ) as client:
                    generated_response = await client.post(
                        "/api/v1/exam-mem/practice/generate/stream",
                        json={
                            "practice_session_id": "practice:generated:001",
                            "trace_id": "trace:generated:001",
                            "learning_path_id": "path:probability",
                            "knowledge_point_id": "native:bayes",
                            "knowledge_point_name": "贝叶斯公式",
                            "num_questions": 2,
                            "attachments": [
                                {
                                    "type": "file",
                                    "filename": "lesson.txt",
                                    "mime_type": "text/plain",
                                    "base64": "bGVzc29u",
                                }
                            ],
                        },
                    )
                    assert generated_response.status_code == 200, generated_response.text
                    generation_events = [
                        json.loads(line)
                        for line in generated_response.text.splitlines()
                        if line.strip()
                    ]
                    progress_events = [
                        event for event in generation_events if event["type"] == "progress"
                    ]
                    assert [event["stage"] for event in progress_events] == [
                        "scope",
                        "exploring",
                        "planning",
                        "generating",
                        "generating",
                        "generating",
                        "persisting",
                        "starting",
                    ]
                    assert [
                        event["completed_questions"]
                        for event in progress_events
                        if event["stage"] == "generating"
                    ] == [0, 1, 2]
                    generated = next(
                        event["result"]
                        for event in generation_events
                        if event["type"] == "complete"
                    )
                    serialized = generated_response.text
                    assert generated["practice"]["question"]["question_id"].startswith("generated:")
                    assert "reference_answer" not in serialized
                    assert "grading_rubric" not in serialized
                    answer_response = await client.post(
                        "/api/v1/exam-mem/practice/answer",
                        json={
                            "practice_session_id": "practice:generated:001",
                            "trace_id": "trace:generated:001",
                            "session_id": generated["session_id"],
                            "question_id": generated["practice"]["question"]["question_id"],
                            "answer": "controlled incorrect answer",
                            "submitted_at": NOW.isoformat(),
                            "idempotency_key": "answer:generated:001",
                        },
                    )
                    assert answer_response.status_code == 200, answer_response.text
                    review_response = await client.get(
                        "/api/v1/exam-mem/practice/sessions/practice:generated:001"
                    )
                    assert review_response.status_code == 200, review_response.text
                    generated_review = review_response.json()
                    assert generated_review["assessment"] == {
                        "attempt_id": generated["assessment"]["attempt_id"],
                        "assessment_id": generated["assessment"]["assessment_id"],
                        "assessment_version": 1,
                        "title": "贝叶斯公式 专项检测",
                        "taxonomy_version": "math1_v1",
                    }
                    generated_checkpoint = next(
                        item
                        for item in generated_review["checkpoints"]
                        if item.get("submitted_answer") is not None
                    )
                    assert generated_checkpoint["question"]["reference_answer"]
                    archive_response = await client.get(
                        "/api/v1/exam-mem/learning-archive",
                        params={
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                            "taxonomy_version": "math1_v1",
                        },
                    )
                    assert archive_response.status_code == 200, archive_response.text
                    assert any(
                        item["event"]["event_id"] == generated_checkpoint["learning_event_id"]
                        and item["source"]["assessment_id"]
                        == generated["assessment"]["assessment_id"]
                        for item in archive_response.json()["l1"]
                    )
                    second_response = await client.post(
                        "/api/v1/exam-mem/practice/start",
                        json={
                            "practice_session_id": "practice:generated:002",
                            "trace_id": "trace:generated:002",
                        },
                    )
                    assert second_response.status_code == 200, second_response.text
                    resumed_first = await client.post(
                        "/api/v1/exam-mem/practice/sessions/practice:generated:001/resume"
                    )
                    assert resumed_first.status_code == 200, resumed_first.text
                    history_response = await client.get("/api/v1/exam-mem/practice/sessions")
                    assert history_response.status_code == 200, history_response.text
                    history = history_response.json()["sessions"]
                    assert [item["attempt_number"] for item in history] == [2, 1]
                    assert {
                        item["practice_session_id"]: item["attempt_number"] for item in history
                    } == {
                        "practice:generated:001": 1,
                        "practice:generated:002": 2,
                    }
                    assert all(
                        item["practice_session_id"].startswith("practice:generated:")
                        for item in history
                    )
                    assessment_id = generated["assessment"]["assessment_id"]
                    all_assessments_response = await client.get(
                        "/api/v1/exam-mem/assessments",
                        params={"archival": "all"},
                    )
                    assert all_assessments_response.status_code == 200
                    [active_assessment] = all_assessments_response.json()["assessments"]
                    assert active_assessment["assessment_id"] == assessment_id
                    assert active_assessment["archived_at"] is None

                    archived_response = await client.post(
                        f"/api/v1/exam-mem/assessments/{assessment_id}/archive"
                    )
                    assert archived_response.status_code == 200, archived_response.text
                    assert archived_response.json()["assessment"]["archived_at"]
                    assert (await client.get("/api/v1/exam-mem/assessments")).json()[
                        "assessments"
                    ] == []
                    archived_list_response = await client.get(
                        "/api/v1/exam-mem/assessments",
                        params={"archival": "archived"},
                    )
                    [archived_assessment] = archived_list_response.json()["assessments"]
                    assert archived_assessment["attempts"][0]["status"] == "failed"

                    blocked_resume = await client.post(
                        "/api/v1/exam-mem/practice/sessions/practice:generated:001/resume"
                    )
                    assert blocked_resume.status_code == 409
                    blocked_answer = await client.post(
                        "/api/v1/exam-mem/practice/answer",
                        json={
                            "practice_session_id": "practice:generated:001",
                            "trace_id": "trace:generated:001",
                            "session_id": resumed_first.json()["session_id"],
                            "question_id": resumed_first.json()["practice"]["question"][
                                "question_id"
                            ],
                            "answer": "answer after archive",
                            "submitted_at": NOW.isoformat(),
                            "idempotency_key": "answer:generated:archived",
                        },
                    )
                    assert blocked_answer.status_code == 409
                    blocked_repeat = await client.post(
                        f"/api/v1/exam-mem/assessments/{assessment_id}/versions/1/attempts",
                        json={
                            "practice_session_id": "practice:generated:archived-repeat",
                            "trace_id": "trace:generated:archived-repeat",
                        },
                    )
                    assert blocked_repeat.status_code == 409
                    archived_review = await client.get(
                        "/api/v1/exam-mem/practice/sessions/practice:generated:001"
                    )
                    assert archived_review.status_code == 200
                    preserved_archive = await client.get(
                        "/api/v1/exam-mem/learning-archive",
                        params={
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                            "taxonomy_version": "math1_v1",
                        },
                    )
                    assert any(
                        item["event"]["event_id"] == generated_checkpoint["learning_event_id"]
                        for item in preserved_archive.json()["l1"]
                    )

                    restored_response = await client.post(
                        f"/api/v1/exam-mem/assessments/{assessment_id}/restore"
                    )
                    assert restored_response.status_code == 200, restored_response.text
                    [restored_assessment] = (
                        await client.get("/api/v1/exam-mem/assessments")
                    ).json()["assessments"]
                    assert restored_assessment["archived_at"] is None
        finally:
            reset_current_user(user_token)

        async with administration_engine.connect() as connection:
            await connection.execute(text(f'SET search_path TO "{schema_name}", public'))
            payload = await connection.scalar(
                select(practice_workflow_checkpoints.c.payload)
                .where(
                    practice_workflow_checkpoints.c.practice_session_id == "practice:generated:001"
                )
                .order_by(practice_workflow_checkpoints.c.updated_at.desc())
                .limit(1)
            )
        assert payload is not None
        catalog = payload["context"]["question_catalog"]
        assert len(catalog) == 2
        source = catalog[0]["grading_rubric"]["source"]
        assert source["kind"] == "deeptutor_native_quiz"
        assert source["source_artifacts"][0]["sha256"] == hashlib.sha256(b"lesson").hexdigest()
        assert host.deleted_sessions == ["transient-generation"]


@pytest.mark.parametrize("entry", ["sdk", "http", "websocket"])
async def test_real_entry_runs_one_plugin_workflow_and_replays_without_duplicates(
    entry: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    async with _isolated_database(f"real_{entry}") as (
        administration_engine,
        schema_name,
        engine_factory,
    ):
        manager, _plugin, app, path_service = _wire_runtime(monkeypatch, engine_factory, tmp_path)
        monkeypatch.setattr("deeptutor.app.DeepTutorApp", lambda: app)

        with memory_path_service_override(path_service):
            if entry == "http":
                api = FastAPI()
                contribution = manager.routers()[0]
                api.include_router(contribution.router, prefix=contribution.prefix)
                async with AsyncClient(
                    transport=ASGITransport(app=api), base_url="http://test"
                ) as client:
                    started_response = await client.post(
                        "/api/v1/exam-mem/practice/start",
                        json={
                            "practice_session_id": PRACTICE_SESSION_ID,
                            "trace_id": TRACE_ID,
                        },
                    )
                    assert started_response.status_code == 200, started_response.text
                    started = started_response.json()
                    question_id = started["practice"]["question"]["question_id"]
                    answer_body = {
                        "practice_session_id": PRACTICE_SESSION_ID,
                        "trace_id": TRACE_ID,
                        "session_id": started["session_id"],
                        "question_id": question_id,
                        "answer": "controlled incorrect answer",
                        "submitted_at": NOW.isoformat(),
                        "idempotency_key": "answer:real-entry:001",
                    }
                    answered_response = await client.post(
                        "/api/v1/exam-mem/practice/answer", json=answer_body
                    )
                    assert answered_response.status_code == 200, answered_response.text
                    answered = answered_response.json()
                    list_response = await client.get(
                        "/api/v1/exam-mem/memories",
                        params={
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                            "memory_namespace": "error_pattern",
                        },
                    )
                    assert list_response.status_code == 200, list_response.text
                    listed = list_response.json()
                    assert listed["count"] == 1
                    memory_id = listed["memories"][0]["memory"]["memory_id"]
                    scoped_params = {
                        "exam_id": "postgraduate_entrance_exam",
                        "subject_id": "math_1",
                        "memory_namespace": "error_pattern",
                    }
                    detail_response = await client.get(
                        f"/api/v1/exam-mem/memories/{memory_id}",
                        params=scoped_params,
                    )
                    evidence_response = await client.get(
                        f"/api/v1/exam-mem/memories/{memory_id}/evidence",
                        params=scoped_params,
                    )
                    assert detail_response.status_code == 200, detail_response.text
                    assert evidence_response.status_code == 200, evidence_response.text
                    assert detail_response.json()["snapshot"]["memory"]["memory_id"] == memory_id
                    assert evidence_response.json()["events"][0]["event_id"]
                    history_response = await client.get("/api/v1/exam-mem/practice/sessions")
                    assert history_response.status_code == 200, history_response.text
                    [history] = history_response.json()["sessions"]
                    assert history["practice_session_id"] == PRACTICE_SESSION_ID
                    assert history["attempt_number"] == 1
                    assert history["score"] == 0.25
                    assert history["correct_count"] == 0
                    assert history["current_checkpoint"]["step_state"] == "RECOMMENDED"
                    resume_response = await client.post(
                        f"/api/v1/exam-mem/practice/sessions/{PRACTICE_SESSION_ID}/resume"
                    )
                    assert resume_response.status_code == 200, resume_response.text
                    resumed = resume_response.json()
                    assert resumed["practice"]["step_state"] == "RECOMMENDED"
                    assert resumed["practice"]["question"]["question_id"] != question_id
                    assert resumed["session_id"] != started["session_id"]
                    review_response = await client.get(
                        f"/api/v1/exam-mem/practice/sessions/{PRACTICE_SESSION_ID}"
                    )
                    assert review_response.status_code == 200, review_response.text
                    review = review_response.json()
                    assert review["trace"]
                    assert review["lifecycle"]["decisions"]
                    answer_checkpoint = next(
                        item for item in review["checkpoints"] if item["grade_result"] is not None
                    )
                    assert answer_checkpoint["submitted_answer"]["answer"] == (
                        "controlled incorrect answer"
                    )
                    assert answer_checkpoint["question"]["reference_answer"]
                    assert answer_checkpoint["question"]["grading_rubric"]
                    assert answer_checkpoint["learning_event_id"]
                    summary = review["attempt_summary"]
                    assert summary["question_count"] == len(stage07_practice_questions())
                    assert summary["answered_count"] == 1
                    assert summary["correct_count"] == 0
                    assert summary["score"] == 0.25
                    assert summary["strengths"] == []
                    assert summary["weak_points"] == answer_checkpoint["mapped_knowledge_point_ids"]
                    assert summary["error_patterns"] == [
                        answer_checkpoint["diagnosis_result"]["error_type"]
                    ]
                    assert summary["next_actions"] == [
                        {
                            "knowledge_point_id": answer_checkpoint["recommendation"][
                                "target_knowledge_point_id"
                            ],
                            "reason_codes": answer_checkpoint["recommendation"]["reason_codes"],
                            "source_memory_ids": answer_checkpoint["recommendation"][
                                "source_memory_ids"
                            ],
                        }
                    ]
                    archive_response = await client.get(
                        "/api/v1/exam-mem/learning-archive",
                        params={
                            "exam_id": "postgraduate_entrance_exam",
                            "subject_id": "math_1",
                        },
                    )
                    assert archive_response.status_code == 200, archive_response.text
                    archive_event = next(
                        item
                        for item in archive_response.json()["l1"]
                        if item["event"]["event_id"] == answer_checkpoint["learning_event_id"]
                    )
                    assert archive_event["detail"]["question"]["reference_answer"]
                    assert archive_event["detail"]["submitted_answer"]["answer"] == (
                        "controlled incorrect answer"
                    )
                    assert archive_event["memories"]
                    dispute_body = {
                        "practice_session_id": PRACTICE_SESSION_ID,
                        "checkpoint_key": answer_checkpoint["checkpoint_key"],
                        "idempotency_key": "grade-review:real-entry:001",
                        "reason": "The learner disputes this controlled grade.",
                    }
                    dispute_response = await client.post(
                        "/api/v1/exam-mem/grade-reviews/disputes",
                        json=dispute_body,
                    )
                    assert dispute_response.status_code == 200, dispute_response.text
                    replay_dispute_response = await client.post(
                        "/api/v1/exam-mem/grade-reviews/disputes",
                        json=dispute_body,
                    )
                    assert replay_dispute_response.status_code == 200
                    assert replay_dispute_response.json()["status"] == "existing"
                    issues_response = await client.get("/api/v1/exam-mem/issues")
                    assert issues_response.status_code == 200, issues_response.text
                    assert any(
                        issue["type"] == "grade_disputed" and issue["status"] == "open"
                        for issue in issues_response.json()["issues"]
                    )
                    review_chain_id = dispute_response.json()["review"]["review_chain_id"]
                    disposition_response = await client.post(
                        f"/api/v1/exam-mem/grade-reviews/{review_chain_id}/dispositions",
                        json={
                            "action": "uphold",
                            "practice_session_id": PRACTICE_SESSION_ID,
                            "checkpoint_key": answer_checkpoint["checkpoint_key"],
                            "idempotency_key": "grade-review-disposition:real-entry:001",
                            "reason": "The original grade matches the rubric.",
                        },
                    )
                    assert disposition_response.status_code == 200, disposition_response.text
                    assert disposition_response.json()["status"] == "created"
                    replay_disposition_response = await client.post(
                        f"/api/v1/exam-mem/grade-reviews/{review_chain_id}/dispositions",
                        json={
                            "action": "uphold",
                            "practice_session_id": PRACTICE_SESSION_ID,
                            "checkpoint_key": answer_checkpoint["checkpoint_key"],
                            "idempotency_key": "grade-review-disposition:real-entry:001",
                            "reason": "The original grade matches the rubric.",
                        },
                    )
                    assert replay_disposition_response.status_code == 200
                    assert replay_disposition_response.json()["status"] == "existing"
                    resolved_issues_response = await client.get("/api/v1/exam-mem/issues")
                    assert any(
                        issue["type"] == "grade_disputed" and issue["status"] == "resolved"
                        for issue in resolved_issues_response.json()["issues"]
                    )
                    counts_after_answer = await _counts(administration_engine, schema_name)
                    replay_response = await client.post(
                        "/api/v1/exam-mem/practice/answer", json=answer_body
                    )
                    assert replay_response.status_code == 200, replay_response.text
                    replayed = replay_response.json()
                    serialized = started_response.text + answered_response.text
                    assert "reference_answer" not in serialized
                    assert "grading_rubric" not in serialized
            else:
                run = _sdk_turn if entry == "sdk" else None
                if run is not None:
                    start_session, started_result, _ = await run(
                        app, _turn_request(session_id=None, question_id=None)
                    )
                    started = {
                        "session_id": start_session["id"],
                        "practice": started_result["metadata"]["practice"],
                    }
                    question_id = started["practice"]["question"]["question_id"]
                    _, answered_result, _ = await run(
                        app,
                        _turn_request(session_id=str(start_session["id"]), question_id=question_id),
                    )
                    answered = {"practice": answered_result["metadata"]["practice"]}
                    counts_after_answer = await _counts(administration_engine, schema_name)
                    _, replay_result, _ = await run(
                        app,
                        _turn_request(session_id=str(start_session["id"]), question_id=question_id),
                    )
                    replayed = {"practice": replay_result["metadata"]["practice"]}
                else:
                    start_session, started_result, _ = await _websocket_turn(
                        app,
                        _turn_request(session_id=None, question_id=None),
                        monkeypatch,
                    )
                    started = {
                        "session_id": start_session["id"],
                        "practice": started_result["metadata"]["practice"],
                    }
                    question_id = started["practice"]["question"]["question_id"]
                    _, answered_result, _ = await _websocket_turn(
                        app,
                        _turn_request(session_id=str(start_session["id"]), question_id=question_id),
                        monkeypatch,
                    )
                    answered = {"practice": answered_result["metadata"]["practice"]}
                    counts_after_answer = await _counts(administration_engine, schema_name)
                    _, replay_result, _ = await _websocket_turn(
                        app,
                        _turn_request(session_id=str(start_session["id"]), question_id=question_id),
                        monkeypatch,
                    )
                    replayed = {"practice": replay_result["metadata"]["practice"]}

        assert started["practice"]["step_state"] == "QUESTION_READY"
        session_record = await app.store.get_session(str(started["session_id"]))
        assert session_record is not None
        assert session_record["preferences"]["session_surface"] == "exam_practice"
        assert await app.store.get_session(str(started["session_id"]), surface="chat") is None
        assert answered["practice"]["step_state"] == "RECOMMENDED"
        assert answered["practice"]["recommendation"]["source_memory_ids"]
        assert replayed["practice"]["replayed"] is True
        assert all(count > 0 for count in counts_after_answer)
        counts_after_replay = await _counts(administration_engine, schema_name)
        assert counts_after_replay[:-1] == counts_after_answer[:-1]
        assert counts_after_replay[-1] == counts_after_answer[-1] + 2
