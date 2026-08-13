"""Adapter from DeepTutor question output to the Stage 07 Question contract."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from pydantic import JsonValue

from .contracts import Question


class DeepTutorQuizPair(Protocol):
    """Structural subset of DeepTutor's ``QuizPair`` used by ExamMem."""

    question_id: str
    question: str
    correct_answer: str


class DeepTutorQuestionAdapter:
    """Build a strict Question without guessing missing Stage 07 fields."""

    def adapt(
        self,
        pair: DeepTutorQuizPair,
        *,
        knowledge_point_ids: Sequence[str],
        difficulty: float,
        grading_rubric: Mapping[str, JsonValue],
    ) -> Question:
        return Question(
            question_id=pair.question_id,
            stem=pair.question,
            knowledge_point_ids=list(knowledge_point_ids),
            difficulty=difficulty,
            reference_answer=pair.correct_answer,
            grading_rubric=dict(grading_rubric),
        )


__all__ = ["DeepTutorQuestionAdapter", "DeepTutorQuizPair"]
