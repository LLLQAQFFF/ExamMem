from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from exam_mem.contracts import LearningEvent, MemoryScope
from exam_mem.practice import AnswerSubmission, GradeResult, Question
from exam_mem.practice.provider import BoundQuestionCatalog
from exam_mem.practice.question_retriever import QuestionRetriever
from exam_mem.practice.tools import (
    EXAM_MEM_PRACTICE_TOOL_NAMES,
    EXAM_MEM_PRACTICE_TOOL_TYPES,
    AnswerGraderTool,
    MemoryWriterTool,
    QuestionRetrieverTool,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="practice_tool_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _question() -> Question:
    return Question(
        question_id="question:tool:001",
        stem="Calculate one probability.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.5,
        reference_answer="Apply Bayes' theorem.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )


def _submission() -> AnswerSubmission:
    return AnswerSubmission(
        practice_session_id="practice:tool:001",
        question_id=_question().question_id,
        answer="One answer.",
        submitted_at=NOW,
        idempotency_key="answer:tool:001",
    )


def _event() -> LearningEvent:
    return LearningEvent(
        event_id="event:tool:001",
        idempotency_key="answer:tool:001",
        context=SCOPE.model_dump(exclude={"memory_namespace"}),
        session_id="practice:tool:001",
        question_id=_question().question_id,
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.5,
        answer_correct=True,
        occurred_at=NOW,
    )


class FakeGrader:
    async def grade(self, question, submission):  # noqa: ANN001, ANN201
        del question, submission
        return GradeResult(
            correct=True,
            score=1.0,
            matched_rubric_items=["apply_bayes"],
            missed_rubric_items=[],
            evidence=["Bayes' theorem was applied."],
            grader_version="fake_grader_v1",
        )


async def test_all_seven_tools_have_strict_root_json_schemas() -> None:
    assert tuple(tool_type().name for tool_type in EXAM_MEM_PRACTICE_TOOL_TYPES) == (
        EXAM_MEM_PRACTICE_TOOL_NAMES
    )
    for tool_type in EXAM_MEM_PRACTICE_TOOL_TYPES:
        schema = tool_type().get_definition().to_openai_schema()["function"]["parameters"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


async def test_bound_answer_grader_returns_structured_tool_result() -> None:
    result = await AnswerGraderTool(FakeGrader()).execute(
        question=_question().model_dump(mode="json"),
        submission=_submission().model_dump(mode="json"),
    )

    assert result.success is True
    assert json.loads(result.content)["grader_version"] == "fake_grader_v1"


async def test_tool_validation_failure_is_structured_without_traceback() -> None:
    result = await AnswerGraderTool(FakeGrader()).execute(question={})

    assert result.success is False
    assert result.metadata["error_code"] == "practice_tool_failed"
    assert "Traceback" not in result.content


async def test_registry_style_unbound_memory_writer_cannot_write() -> None:
    result = await MemoryWriterTool().execute(
        event=_event().model_dump(mode="json"),
        candidates=[],
    )

    assert result.success is False
    assert result.metadata["error_code"] == "practice_tool_not_bound"


async def test_bound_question_retriever_enforces_scope_and_selects_question() -> None:
    tool = QuestionRetrieverTool(QuestionRetriever(BoundQuestionCatalog(SCOPE, [_question()])))

    result = await tool.execute(
        scope=SCOPE.model_dump(mode="json"),
        target_knowledge_point_id="math1.probability.bayes",
        target_difficulty=0.5,
    )

    assert result.success is True
    assert json.loads(result.content)["question_id"] == _question().question_id
