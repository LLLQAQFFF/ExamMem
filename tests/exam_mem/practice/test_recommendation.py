from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from exam_mem.contracts import LearningContext, LearningMemory
from exam_mem.practice import (
    Question,
    RecommendationCandidate,
    RecommendationFeatures,
    RecommendationPolicyV1,
    RecommendationPolicyV1Config,
)

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="stage07_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)


def _memory(
    *,
    memory_id: str = "memory:bayes:001",
    knowledge_point_id: str = "math1.probability.bayes",
    state: str = "active",
    user_id: str = CONTEXT.user_id,
) -> LearningMemory:
    valid_to = NOW if state in {"archived", "invalidated"} else None
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": {
                "user_id": user_id,
                "exam_id": CONTEXT.exam_id,
                "subject_id": CONTEXT.subject_id,
                "memory_namespace": "mastery",
            },
            "slot_key": f"mastery:{knowledge_point_id}",
            "value": {"type": "mastery", "level": "low", "score": 0.3},
            "confidence": 0.8,
            "evidence_count": 1,
            "lifecycle_state": state,
            "version": 1,
            "valid_from": NOW,
            "valid_to": valid_to,
            "superseded_by": None,
            "provenance": ["event:stage07:001"],
        }
    )


def _candidate(
    *,
    knowledge_point_id: str = "math1.probability.bayes",
    features: RecommendationFeatures | None = None,
    source_memories: tuple[LearningMemory, ...] = (),
    source_evidence_weight: float = 1.0,
    deduplication_weight: float = 1.0,
) -> RecommendationCandidate:
    return RecommendationCandidate(
        target_knowledge_point_id=knowledge_point_id,
        target_difficulty=0.6,
        features=features
        or RecommendationFeatures(
            weakness=0.8,
            stable_error=0.6,
            forgetting_risk=0.4,
            active_plan_priority=0.2,
            coverage_gap=1.0,
        ),
        source_memories=source_memories,
        source_evidence_weight=source_evidence_weight,
        deduplication_weight=deduplication_weight,
    )


def test_policy_v1_freezes_documented_weights_and_calculates_exact_priority() -> None:
    policy = RecommendationPolicyV1()

    ranked = policy.rank(context=CONTEXT, candidates=[_candidate()])

    assert policy.config == RecommendationPolicyV1Config()
    assert ranked[0].base_priority == pytest.approx(0.65)
    assert ranked[0].final_priority == pytest.approx(0.65)
    assert ranked[0].reason_codes == (
        "weakness",
        "stable_error",
        "forgetting_risk",
        "active_plan_priority",
        "coverage_gap",
    )
    with pytest.raises(ValidationError):
        RecommendationPolicyV1Config(weakness_weight=0.41)


def test_contested_evidence_and_recent_practice_are_explicitly_downweighted() -> None:
    contested = _memory(state="contested")
    candidate = _candidate(
        source_memories=(contested,),
        source_evidence_weight=0.5,
        deduplication_weight=0.8,
    )

    score = RecommendationPolicyV1().rank(context=CONTEXT, candidates=[candidate])[0]

    assert score.base_priority == pytest.approx(0.65)
    assert score.final_priority == pytest.approx(0.26)
    assert "contested_evidence_downweighted" in score.reason_codes
    assert "recent_practice_downweighted" in score.reason_codes


def test_contested_evidence_cannot_silently_receive_full_weight() -> None:
    with pytest.raises(ValidationError, match="must use a weight below 1"):
        _candidate(source_memories=(_memory(state="contested"),))


@pytest.mark.parametrize("state", ["archived", "invalidated"])
def test_terminal_memory_cannot_enter_recommendation_context(state: str) -> None:
    with pytest.raises(ValidationError, match="terminal memory must not enter"):
        _candidate(source_memories=(_memory(state=state),))


def test_policy_rejects_cross_context_source_memory() -> None:
    candidate = _candidate(source_memories=(_memory(user_id="another_user"),))

    with pytest.raises(ValueError, match="outside the requested context"):
        RecommendationPolicyV1().rank(context=CONTEXT, candidates=[candidate])


def test_equal_scores_use_fixed_syllabus_order_then_knowledge_point_id() -> None:
    features = RecommendationFeatures(
        weakness=1.0,
        stable_error=0.0,
        forgetting_risk=0.0,
        active_plan_priority=0.0,
        coverage_gap=0.0,
    )
    later = _candidate(
        knowledge_point_id="math1.probability.bayes",
        features=features,
    )
    earlier = _candidate(
        knowledge_point_id="math1.linear_algebra.eigenvalue",
        features=features,
    )

    ranked = RecommendationPolicyV1().rank(
        context=CONTEXT,
        candidates=[later, earlier],
    )

    assert [score.candidate.target_knowledge_point_id for score in ranked] == [
        "math1.linear_algebra.eigenvalue",
        "math1.probability.bayes",
    ]
    assert all(
        score.tie_break_rule == "priority_desc_syllabus_order_asc_knowledge_point_id_asc"
        for score in ranked
    )


def test_public_recommendation_keeps_explanation_and_memory_provenance() -> None:
    memory = _memory()
    policy = RecommendationPolicyV1()
    score = policy.rank(
        context=CONTEXT,
        candidates=[_candidate(source_memories=(memory,))],
    )[0]
    question = Question(
        question_id="question:bayes:002",
        stem="Apply Bayes' theorem.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.7,
        reference_answer="Use the posterior formula.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )

    recommendation = policy.build_recommendation(score, question)

    assert recommendation.question_id == question.question_id
    assert recommendation.target_difficulty == 0.6
    assert recommendation.source_memory_ids == [memory.memory_id]
    assert recommendation.reason_codes
    assert recommendation.policy_version == "recommendation_policy_v1"


def test_no_memory_fallback_has_explicit_reason_and_no_fake_source() -> None:
    question = Question(
        question_id="question:matrix:001",
        stem="Multiply the two matrices.",
        knowledge_point_ids=["math1.linear_algebra.matrix_multiplication"],
        difficulty=0.2,
        reference_answer="Compute row-by-column products.",
        grading_rubric={"required_steps": ["row_by_column"]},
    )

    recommendation = RecommendationPolicyV1().build_fallback_recommendation(
        question,
        target_knowledge_point_id="math1.linear_algebra.matrix_multiplication",
    )

    assert recommendation.reason_codes == ["syllabus_fallback"]
    assert recommendation.source_memory_ids == []
    assert recommendation.target_difficulty == question.difficulty
