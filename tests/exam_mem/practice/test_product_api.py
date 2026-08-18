from __future__ import annotations

from contextlib import asynccontextmanager, contextmanager
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
import pytest

from deeptutor.multi_user.context import reset_current_user, set_current_user
from deeptutor.multi_user.models import CurrentUser, UserScope
from deeptutor.plugins import SettingsContribution
from deeptutor_plugins.exam_mem.api import (
    GeneratedPracticeStartBody,
    _canonical_knowledge_point,
    _complete_attempt_if_finished,
    _generate_practice_questions,
    _practice_generation_prompt,
    _practice_response_language,
    build_router,
)
from exam_mem.config import ExamMemSettings
from exam_mem.domain import load_taxonomy
from exam_mem.storage import AppendStatus


@pytest.mark.asyncio
async def test_completed_recovery_idempotently_finishes_assessment_attempt() -> None:
    completed_sessions = []
    commit_count = 0

    class Assessments:
        async def complete_attempt(self, *, user_id, practice_session_id):
            completed_sessions.append((user_id, practice_session_id))
            return None

    class Connection:
        async def commit(self):
            nonlocal commit_count
            commit_count += 1

    class Provider:
        @asynccontextmanager
        async def open_product(self):
            yield SimpleNamespace(
                assessments=Assessments(),
                connection=Connection(),
            )

    provider = Provider()
    await _complete_attempt_if_finished(
        provider,  # type: ignore[arg-type]
        completed=False,
        user_id="learner",
        practice_session_id="practice:recovery",
    )
    await _complete_attempt_if_finished(
        provider,  # type: ignore[arg-type]
        completed=True,
        user_id="learner",
        practice_session_id="practice:recovery",
    )

    assert completed_sessions == [("learner", "practice:recovery")]
    assert commit_count == 1


@contextmanager
def _regular_user():
    user = CurrentUser(
        id="regular-user",
        username="regular",
        role="user",
        scope=UserScope(kind="user", user_id="regular-user", root=Path("/tmp/regular")),  # noqa: S108
    )
    token = set_current_user(user)
    try:
        yield
    finally:
        reset_current_user(token)


@pytest.mark.asyncio
async def test_non_admin_cannot_save_plugin_configuration(monkeypatch) -> None:
    saved = False

    def fail_if_saved(*_args, **_kwargs):  # noqa: ANN202
        nonlocal saved
        saved = True
        raise AssertionError("non-admin request reached settings persistence")

    monkeypatch.setattr("deeptutor_plugins.exam_mem.api.save_plugin_settings", fail_if_saved)
    contribution = SettingsContribution(
        namespace="exam_mem",
        defaults=ExamMemSettings().model_dump(mode="json"),
        normalize=lambda value: ExamMemSettings.model_validate(value).model_dump(mode="json"),
    )
    api = FastAPI()
    api.include_router(
        build_router(
            object(),  # type: ignore[arg-type]
            settings_contribution=contribution,
            effective_settings=ExamMemSettings(),
        ),
        prefix="/api/v1/exam-mem",
    )

    with _regular_user():
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            response = await client.put(
                "/api/v1/exam-mem/configuration",
                json=ExamMemSettings(memory_backend="none").model_dump(mode="json"),
            )

    assert response.status_code == 403
    assert saved is False


@pytest.mark.asyncio
async def test_grade_dispute_uses_the_assessment_scope() -> None:
    contexts = []

    class Checkpoints:
        async def get(self, context, practice_session_id, checkpoint_key):
            contexts.append(context)
            assert practice_session_id == "practice:dynamic:completed"
            assert checkpoint_key == "answer:4"
            return SimpleNamespace(checkpoint=SimpleNamespace(grade_result=object()))

    class Reviews:
        async def append(self, event):
            return SimpleNamespace(status=AppendStatus.CREATED, event=event)

    class Connection:
        async def commit(self):
            return None

    class Provider:
        @asynccontextmanager
        async def open_product(self):
            yield SimpleNamespace(
                checkpoints=Checkpoints(), reviews=Reviews(), connection=Connection()
            )

    api = FastAPI()
    api.include_router(
        build_router(Provider()),  # type: ignore[arg-type]
        prefix="/api/v1/exam-mem",
    )

    with _regular_user():
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            response = await client.post(
                "/api/v1/exam-mem/grade-reviews/disputes",
                json={
                    "practice_session_id": "practice:dynamic:completed",
                    "checkpoint_key": "answer:4",
                    "idempotency_key": "review:dynamic:1",
                    "reason": "The generated rubric missed valid evidence.",
                    "exam_id": "plan:test",
                    "subject_id": "math",
                },
            )

    assert response.status_code == 200, response.text
    assert len(contexts) == 1
    assert contexts[0].exam_id == "plan:test"
    assert contexts[0].subject_id == "math"
    assert response.json()["review"]["exam_id"] == "plan:test"


def test_learning_path_point_maps_only_to_the_controlled_taxonomy() -> None:
    assert (
        _canonical_knowledge_point(load_taxonomy("math1_v1"), "native-id", "贝叶斯公式")
        == "math1.probability.bayes"
    )


def test_practice_generation_uses_two_explicit_language_prompts() -> None:
    chinese = GeneratedPracticeStartBody(
        practice_session_id="practice:zh",
        trace_id="trace:zh",
        learning_path_id="path:zh",
        knowledge_point_id="point:zh",
        knowledge_point_name="学习目标",
        language="zh",
    )
    english = chinese.model_copy(
        update={
            "practice_session_id": "practice:en",
            "trace_id": "trace:en",
            "language": "en",
        }
    )

    assert "必须使用简体中文" in _practice_generation_prompt(chinese)
    assert "必须全程用中文回答" in _practice_generation_prompt(chinese)
    assert "must respond in English" in _practice_generation_prompt(english)
    assert "including all reasons" in _practice_generation_prompt(english)
    assert (
        _practice_response_language(
            {"question_catalog": [{"grading_rubric": {"response_language": "en"}}]}
        )
        == "en"
    )
    assert _practice_response_language({"question_catalog": []}) == "zh"


@pytest.mark.asyncio
async def test_native_quiz_questions_are_versioned_and_server_side() -> None:
    deleted: list[str] = []
    progress_events: list[dict[str, object]] = []

    async def record_progress(event: dict[str, object]) -> None:
        progress_events.append(event)

    class FakeHost:
        async def start_turn(self, request):
            assert request.capability == "deep_question"
            assert request.language == "zh"
            assert "必须使用简体中文" in request.content
            assert request.attachments[0]["filename"] == "lesson.pdf"
            return {"id": "generation-session"}, {"id": "generation-turn"}

        async def stream_turn(self, turn_id):
            assert turn_id == "generation-turn"
            yield {"type": "stage_start", "stage": "exploring"}
            yield {"type": "stage_start", "stage": "planning"}
            yield {"type": "stage_start", "stage": "quizzing"}
            for index in range(2):
                yield {
                    "type": "content",
                    "metadata": {
                        "call_kind": "quiz_question_emitted",
                        "qa_pair": {
                            "question_id": f"q_{index + 1}",
                            "question": f"贝叶斯练习 {index + 1}",
                            "question_type": "short_answer",
                            "correct_answer": f"答案 {index + 1}",
                            "explanation": f"解析 {index + 1}",
                            "difficulty": "medium",
                        },
                    },
                }

        async def delete_session(self, session_id):
            deleted.append(session_id)
            return True

    body = GeneratedPracticeStartBody(
        practice_session_id="practice:test:generated",
        trace_id="trace:test:generated",
        learning_path_id="path:probability",
        knowledge_point_id="native:bayes",
        knowledge_point_name="贝叶斯公式",
        num_questions=2,
        attachments=(
            {
                "filename": "lesson.pdf",
                "mime_type": "application/pdf",
                "type": "pdf",
                "base64": "cGRm",
            },
        ),
    )

    questions = await _generate_practice_questions(
        FakeHost(),  # type: ignore[arg-type]
        body=body,
        canonical_knowledge_point_id="math1.probability.bayes",
        progress=record_progress,
    )

    assert len(questions) == 2
    assert all(question.question_id.startswith("generated:") for question in questions)
    assert all(
        question.knowledge_point_ids == ["math1.probability.bayes"] for question in questions
    )
    assert questions[0].grading_rubric["source"] == {
        "kind": "deeptutor_native_quiz",
        "learning_path_id": "path:probability",
        "knowledge_point_id": "math1.probability.bayes",
        "source_artifacts": [
            {
                "filename": "lesson.pdf",
                "mime_type": "application/pdf",
                "sha256": "c35b21d6ca39aa7cc3b79a705d989f1a6e88b99ab43988d74048799e3db926a3",
            }
        ],
    }
    assert questions[0].grading_rubric["response_language"] == "zh"
    assert progress_events == [
        {"stage": "exploring", "completed_questions": 0, "total_questions": 2},
        {"stage": "planning", "completed_questions": 0, "total_questions": 2},
        {"stage": "generating", "completed_questions": 0, "total_questions": 2},
        {"stage": "generating", "completed_questions": 1, "total_questions": 2},
        {"stage": "generating", "completed_questions": 2, "total_questions": 2},
    ]
    serialized_progress = json.dumps(progress_events)
    assert "correct_answer" not in serialized_progress
    assert "reference_answer" not in serialized_progress
    assert "grading_rubric" not in serialized_progress
    assert deleted == ["generation-session"]


def test_generated_practice_rejects_future_ingestion_formats() -> None:
    with pytest.raises(ValueError, match="PDF, TXT and Markdown"):
        GeneratedPracticeStartBody(
            practice_session_id="practice:test:unsupported-source",
            trace_id="trace:test:unsupported-source",
            learning_path_id="path:one",
            knowledge_point_id="native:one",
            knowledge_point_name="贝叶斯公式",
            attachments=(
                {
                    "filename": "slides.pptx",
                    "mime_type": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "type": "file",
                    "base64": "c2xpZGVz",
                },
            ),
        )


@pytest.mark.asyncio
async def test_native_quiz_error_fails_closed_after_transient_cleanup() -> None:
    deleted: list[str] = []

    class FakeHost:
        async def start_turn(self, request):
            return {"id": "generation-session"}, {"id": "generation-turn"}

        async def stream_turn(self, turn_id):
            yield {
                "type": "error",
                "content": "controlled generation failure",
                "metadata": {"error_code": "controlled_failure"},
            }

        async def delete_session(self, session_id):
            deleted.append(session_id)
            return True

    body = GeneratedPracticeStartBody(
        practice_session_id="practice:test:generation-error",
        trace_id="trace:test:generation-error",
        learning_path_id="path:probability",
        knowledge_point_id="native:bayes",
        knowledge_point_name="贝叶斯公式",
        num_questions=2,
    )

    with pytest.raises(HTTPException) as raised:
        await _generate_practice_questions(
            FakeHost(),  # type: ignore[arg-type]
            body=body,
            canonical_knowledge_point_id="math1.probability.bayes",
        )

    assert getattr(raised.value, "detail") == {
        "error_code": "controlled_failure",
        "message": "controlled generation failure",
    }
    assert deleted == ["generation-session"]
