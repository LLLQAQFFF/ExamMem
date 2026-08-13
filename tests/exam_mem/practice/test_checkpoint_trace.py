from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from exam_mem.contracts import MemoryScope
from exam_mem.practice import (
    AnswerSubmission,
    PracticeContext,
    PracticeSpanName,
    PracticeSpanStatus,
    PracticeState,
    PracticeTraceSpan,
    PracticeWorkflowCheckpoint,
    Question,
    checkpoint_key_for_context,
)
from exam_mem.practice.trace import PracticeTraceRecorder
from exam_mem.storage import AppendStatus, PracticeTraceAppendResult

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="checkpoint_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def test_initial_checkpoint_has_stable_session_key() -> None:
    context = PracticeContext(
        practice_session_id="practice:checkpoint:001",
        scope=SCOPE,
        trace_id="trace:checkpoint:001",
    )

    checkpoint = PracticeWorkflowCheckpoint(
        checkpoint_key=checkpoint_key_for_context(context),
        context=context,
    )

    assert checkpoint.checkpoint_key == "start"


def test_trace_span_requires_failed_error_code_and_monotonic_time() -> None:
    payload = {
        "trace_id": "trace:checkpoint:001",
        "step_id": 1,
        "name": PracticeSpanName.ANSWER_GRADED,
        "status": PracticeSpanStatus.FAILED,
        "input_summary": {"question_id": "question:001"},
        "output_summary": {},
        "versions": {},
        "started_at": NOW,
        "completed_at": NOW,
        "duration_ms": 0.0,
    }

    with pytest.raises(ValidationError, match="failed span requires error_code"):
        PracticeTraceSpan.model_validate(payload)


@pytest.mark.asyncio
async def test_trace_recorder_clamps_a_backward_wall_clock(monkeypatch) -> None:
    class Repository:
        span = None

        async def next_step_id(self, trace_id):  # noqa: ANN001, ANN201
            assert trace_id == "trace:clock:001"
            return 1

        async def append(self, span):  # noqa: ANN001, ANN201
            self.span = span
            return PracticeTraceAppendResult(status=AppendStatus.CREATED, span=span)

    repository = Repository()
    recorder = PracticeTraceRecorder(repository, trace_id="trace:clock:001")
    started_at = datetime(2026, 8, 13, 12, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(
        "exam_mem.practice.trace.datetime",
        type(
            "BackwardClock",
            (),
            {
                "now": staticmethod(
                    lambda _timezone: datetime(
                        2026, 8, 13, 12, 0, 0, tzinfo=timezone.utc
                    )
                )
            },
        ),
    )

    span = await recorder.completed(
        name=PracticeSpanName.RESPONSE_SENT,
        started=(started_at, 0.0),
        input_summary={},
        output_summary={},
    )

    assert span.started_at == started_at
    assert span.completed_at == started_at
    assert repository.span == span


def test_checkpoint_rejects_skipping_required_graded_material() -> None:
    question = Question(
        question_id="question:checkpoint:001",
        stem="Calculate one probability.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.4,
        reference_answer="Apply Bayes' theorem.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )
    submission = AnswerSubmission(
        practice_session_id="practice:checkpoint:001",
        question_id=question.question_id,
        answer="One answer.",
        submitted_at=NOW,
        idempotency_key="idempotency:checkpoint:001",
    )
    context = PracticeContext(
        practice_session_id="practice:checkpoint:001",
        scope=SCOPE,
        current_question=question,
        submitted_answer=submission,
        step_state=PracticeState.GRADED,
        trace_id="trace:checkpoint:001",
    )

    with pytest.raises(ValidationError, match="GRADED checkpoint requires grade_result"):
        PracticeWorkflowCheckpoint(
            checkpoint_key="answer:idempotency:001",
            context=context,
        )
