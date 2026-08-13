from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from exam_mem.contracts import ErrorType, MemoryScope
from exam_mem.practice import (
    AnswerSubmission,
    DiagnosisResult,
    GradeResult,
    PracticeContext,
    PracticeState,
    Question,
    Recommendation,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage07_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _question() -> Question:
    return Question(
        question_id="question:bayes:001",
        stem="Given P(A) and P(B|A), calculate the requested probability.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.6,
        reference_answer="Apply Bayes' theorem and simplify.",
        grading_rubric={"required_steps": ["identify_prior", "apply_bayes"]},
    )


def _submission() -> AnswerSubmission:
    return AnswerSubmission(
        practice_session_id="practice:stage07:001",
        question_id="question:bayes:001",
        answer="P(A|B) = P(B|A)P(A) / P(B)",
        submitted_at=NOW,
        idempotency_key="answer:stage07:001",
    )


def test_tool_contracts_accept_documented_fields_and_serialize_to_json() -> None:
    question = _question()
    submission = _submission()
    grade = GradeResult(
        correct=True,
        score=1.0,
        matched_rubric_items=["identify_prior", "apply_bayes"],
        missed_rubric_items=[],
        evidence=["The answer states Bayes' theorem correctly."],
        grader_version="answer_grader_v1",
    )
    diagnosis = DiagnosisResult(
        knowledge_point_ids=["math1.probability.bayes"],
        error_type=None,
        explanation="No durable error diagnosis is needed for this answer.",
        confidence=0.9,
        analyzer_version="error_analyzer_v1",
    )
    recommendation = Recommendation(
        question_id="question:bayes:002",
        target_knowledge_point_id="math1.probability.bayes",
        target_difficulty=0.7,
        reason_codes=["mastery_follow_up"],
        source_memory_ids=[],
        policy_version="recommendation_policy_v1",
    )

    assert question.model_dump(mode="json")["difficulty"] == 0.6
    assert submission.model_dump(mode="json")["submitted_at"].endswith("Z")
    assert grade.model_dump(mode="json")["correct"] is True
    assert diagnosis.model_dump(mode="json")["error_type"] is None
    assert recommendation.model_dump(mode="json")["source_memory_ids"] == []


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        (Question, {**_question().model_dump(mode="json"), "memory_id": "forbidden"}),
        (
            AnswerSubmission,
            {**_submission().model_dump(mode="json"), "lifecycle_operation": "ADD"},
        ),
    ],
)
def test_tool_contracts_reject_undocumented_fields(
    model: type[Question] | type[AnswerSubmission],
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        model.model_validate(payload)


@pytest.mark.parametrize("field", ["difficulty", "target_difficulty", "confidence"])
def test_probability_fields_preserve_frozen_zero_to_one_boundary(field: str) -> None:
    if field == "difficulty":
        model = Question
        payload = _question().model_dump(mode="json")
    elif field == "target_difficulty":
        model = Recommendation
        payload = {
            "question_id": "question:bayes:002",
            "target_knowledge_point_id": "math1.probability.bayes",
            "target_difficulty": 0.7,
            "reason_codes": ["mastery_follow_up"],
            "source_memory_ids": [],
            "policy_version": "recommendation_policy_v1",
        }
    else:
        model = DiagnosisResult
        payload = {
            "knowledge_point_ids": ["math1.probability.bayes"],
            "error_type": "concept_confusion",
            "explanation": "The conditional direction was reversed.",
            "confidence": 0.8,
            "analyzer_version": "error_analyzer_v1",
        }
    payload[field] = 1.1

    with pytest.raises(ValidationError, match="less than or equal to 1"):
        model.model_validate(payload)


def test_diagnosis_reuses_the_frozen_error_type_vocabulary() -> None:
    diagnosis = DiagnosisResult(
        knowledge_point_ids=["math1.probability.bayes"],
        error_type="concept_confusion",
        explanation="The conditional direction was reversed.",
        confidence=0.8,
        analyzer_version="error_analyzer_v1",
    )

    assert diagnosis.error_type is ErrorType.CONCEPT_CONFUSION

    payload = diagnosis.model_dump(mode="json")
    payload["error_type"] = "new_free_form_error"
    with pytest.raises(ValidationError, match="Input should be"):
        DiagnosisResult.model_validate(payload)


def test_practice_states_match_the_documented_seven_step_workflow() -> None:
    assert [state.value for state in PracticeState] == [
        "IDLE",
        "QUESTION_READY",
        "ANSWER_RECEIVED",
        "GRADED",
        "DIAGNOSED",
        "MEMORY_UPDATED",
        "RECOMMENDED",
    ]


def test_practice_context_accepts_recoverable_completed_step_material() -> None:
    context = PracticeContext(
        practice_session_id="practice:stage07:001",
        scope=SCOPE,
        current_question=_question(),
        submitted_answer=_submission(),
        step_state=PracticeState.GRADED,
        trace_id="trace:stage07:001",
    )

    assert context.scope == SCOPE
    assert context.submitted_answer is not None
    assert context.submitted_answer.idempotency_key == "answer:stage07:001"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"step_state": PracticeState.IDLE, "current_question": _question()},
            "IDLE context must not contain",
        ),
        (
            {"step_state": PracticeState.QUESTION_READY},
            "QUESTION_READY context requires current question",
        ),
        (
            {"step_state": PracticeState.GRADED, "current_question": _question()},
            "GRADED context requires question and answer",
        ),
        (
            {
                "step_state": PracticeState.ANSWER_RECEIVED,
                "current_question": _question(),
                "submitted_answer": _submission().model_copy(
                    update={"question_id": "question:other"}
                ),
            },
            "submitted answer must match current question",
        ),
    ],
)
def test_practice_context_rejects_inconsistent_checkpoint_material(
    overrides: dict[str, object],
    message: str,
) -> None:
    payload: dict[str, object] = {
        "practice_session_id": "practice:stage07:001",
        "scope": SCOPE,
        "step_state": PracticeState.IDLE,
        "trace_id": "trace:stage07:001",
    }
    payload.update(overrides)

    with pytest.raises(ValidationError, match=message):
        PracticeContext.model_validate(payload)
