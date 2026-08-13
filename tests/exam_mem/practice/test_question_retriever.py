from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import pytest

from exam_mem.contracts import MemoryScope
from exam_mem.practice import (
    Question,
    QuestionRetrievalError,
    QuestionRetriever,
)

SCOPE = MemoryScope(
    user_id="stage07_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _question(
    question_id: str,
    knowledge_point_id: str,
    difficulty: float,
) -> Question:
    return Question(
        question_id=question_id,
        stem=f"Question for {knowledge_point_id}",
        knowledge_point_ids=[knowledge_point_id],
        difficulty=difficulty,
        reference_answer="Reference answer.",
        grading_rubric={"required_steps": ["required_step"]},
    )


@dataclass
class RecordingQuestionCatalog:
    questions: Sequence[Question]
    requested_scopes: list[MemoryScope] = field(default_factory=list)

    async def list_questions(self, scope: MemoryScope) -> Sequence[Question]:
        self.requested_scopes.append(scope)
        return self.questions


@pytest.mark.asyncio
async def test_retriever_passes_full_scope_and_selects_nearest_difficulty() -> None:
    farther = _question("question:bayes:far", "math1.probability.bayes", 0.2)
    nearer = _question("question:bayes:near", "math1.probability.bayes", 0.7)
    catalog = RecordingQuestionCatalog([farther, nearer])
    retriever = QuestionRetriever(catalog)

    selected = await retriever.retrieve(
        scope=SCOPE,
        target_knowledge_point_id="math1.probability.bayes",
        target_difficulty=0.6,
    )

    assert selected == nearer
    assert catalog.requested_scopes == [SCOPE]


@pytest.mark.asyncio
async def test_retriever_excludes_questions_and_uses_question_id_tie_break() -> None:
    first = _question("question:bayes:a", "math1.probability.bayes", 0.5)
    second = _question("question:bayes:b", "math1.probability.bayes", 0.5)
    retriever = QuestionRetriever(RecordingQuestionCatalog([second, first]))

    tie_broken = await retriever.retrieve(
        scope=SCOPE,
        target_knowledge_point_id="math1.probability.bayes",
        target_difficulty=0.5,
    )
    after_exclusion = await retriever.retrieve(
        scope=SCOPE,
        target_knowledge_point_id="math1.probability.bayes",
        target_difficulty=0.5,
        exclude_question_ids=[first.question_id],
    )

    assert tie_broken == first
    assert after_exclusion == second


@pytest.mark.asyncio
async def test_no_candidate_returns_error_code_and_available_points() -> None:
    retriever = QuestionRetriever(
        RecordingQuestionCatalog(
            [
                _question(
                    "question:eigenvalue:001",
                    "math1.linear_algebra.eigenvalue",
                    0.4,
                )
            ]
        )
    )

    with pytest.raises(QuestionRetrievalError) as captured:
        await retriever.retrieve(
            scope=SCOPE,
            target_knowledge_point_id="math1.probability.bayes",
            target_difficulty=0.5,
        )

    assert captured.value.error_code == "question_bank_no_candidate"
    assert captured.value.available_knowledge_point_ids == ("math1.linear_algebra.eigenvalue",)


@pytest.mark.asyncio
async def test_fallback_uses_fixed_syllabus_then_difficulty_then_question_id() -> None:
    later = _question("question:bayes:001", "math1.probability.bayes", 0.1)
    earlier_hard = _question(
        "question:matrix:hard",
        "math1.linear_algebra.matrix_multiplication",
        0.8,
    )
    earlier_easy = _question(
        "question:matrix:easy",
        "math1.linear_algebra.matrix_multiplication",
        0.3,
    )
    retriever = QuestionRetriever(RecordingQuestionCatalog([later, earlier_hard, earlier_easy]))

    selected = await retriever.retrieve_syllabus_fallback(scope=SCOPE)

    assert selected == earlier_easy
    assert (
        retriever.fallback_target_knowledge_point_id(selected)
        == "math1.linear_algebra.matrix_multiplication"
    )


@pytest.mark.asyncio
async def test_catalog_rejects_duplicate_question_ids_within_scope() -> None:
    first = _question("question:duplicate", "math1.probability.bayes", 0.5)
    duplicate = _question("question:duplicate", "math1.probability.bayes", 0.7)
    retriever = QuestionRetriever(RecordingQuestionCatalog([first, duplicate]))

    with pytest.raises(ValueError, match="question catalog IDs must be unique"):
        await retriever.retrieve_syllabus_fallback(scope=SCOPE)


@pytest.mark.asyncio
async def test_catalog_rejects_non_leaf_or_unknown_knowledge_points() -> None:
    retriever = QuestionRetriever(
        RecordingQuestionCatalog([_question("question:invalid", "math1.probability", 0.5)])
    )

    with pytest.raises(ValueError, match="outside active taxonomy leaves"):
        await retriever.retrieve_syllabus_fallback(scope=SCOPE)
