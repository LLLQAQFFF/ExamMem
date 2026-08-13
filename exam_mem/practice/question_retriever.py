"""Scope-aware deterministic question retrieval for the Stage 07 workflow."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from exam_mem.contracts import MemoryScope
from exam_mem.domain import KnowledgePointStatus, Taxonomy, load_taxonomy

from .contracts import Question


class QuestionCatalog(Protocol):
    """Question source port; implementations must apply the supplied full Scope."""

    async def list_questions(self, scope: MemoryScope) -> Sequence[Question]: ...


class QuestionRetrievalError(RuntimeError):
    """Structured no-candidate failure that never fabricates a question."""

    error_code = "question_bank_no_candidate"

    def __init__(self, available_knowledge_point_ids: Sequence[str]) -> None:
        self.available_knowledge_point_ids = tuple(sorted(set(available_knowledge_point_ids)))
        super().__init__("question bank has no candidate for the requested constraints")


class QuestionRetriever:
    """Select by target point, difficulty distance, exclusions, and stable IDs."""

    def __init__(
        self,
        catalog: QuestionCatalog,
        taxonomy_version: str = "math1_v1",
    ) -> None:
        self._catalog = catalog
        self._taxonomy = load_taxonomy(taxonomy_version)
        self._syllabus_order = _active_leaf_order(self._taxonomy)

    async def retrieve(
        self,
        *,
        scope: MemoryScope,
        target_knowledge_point_id: str,
        target_difficulty: float,
        exclude_question_ids: Sequence[str] = (),
    ) -> Question:
        if target_knowledge_point_id not in self._syllabus_order:
            raise ValueError("question target must be an active taxonomy leaf")
        if target_difficulty < 0.0 or target_difficulty > 1.0:
            raise ValueError("target difficulty must be between 0 and 1")

        questions = await self._validated_questions(scope)
        excluded = set(exclude_question_ids)
        available = [question for question in questions if question.question_id not in excluded]
        candidates = [
            question
            for question in available
            if target_knowledge_point_id in question.knowledge_point_ids
        ]
        if not candidates:
            raise QuestionRetrievalError(_available_knowledge_point_ids(available))
        return min(
            candidates,
            key=lambda question: (
                abs(question.difficulty - target_difficulty),
                question.question_id,
            ),
        )

    async def retrieve_syllabus_fallback(
        self,
        *,
        scope: MemoryScope,
        exclude_question_ids: Sequence[str] = (),
    ) -> Question:
        questions = await self._validated_questions(scope)
        excluded = set(exclude_question_ids)
        available = [question for question in questions if question.question_id not in excluded]
        if not available:
            raise QuestionRetrievalError(())
        return min(
            available,
            key=lambda question: (
                self._question_syllabus_order(question),
                question.difficulty,
                question.question_id,
            ),
        )

    def fallback_target_knowledge_point_id(self, question: Question) -> str:
        """Resolve the earliest fixed-syllabus point covered by a fallback question."""
        self._validate_question_taxonomy(question)
        return min(
            question.knowledge_point_ids,
            key=lambda knowledge_point_id: (
                self._syllabus_order[knowledge_point_id],
                knowledge_point_id,
            ),
        )

    async def _validated_questions(self, scope: MemoryScope) -> tuple[Question, ...]:
        questions = tuple(await self._catalog.list_questions(scope))
        question_ids = [question.question_id for question in questions]
        if len(question_ids) != len(set(question_ids)):
            raise ValueError("question catalog IDs must be unique within the requested scope")
        for question in questions:
            self._validate_question_taxonomy(question)
        return questions

    def _validate_question_taxonomy(self, question: Question) -> None:
        invalid_ids = sorted(
            knowledge_point_id
            for knowledge_point_id in question.knowledge_point_ids
            if knowledge_point_id not in self._syllabus_order
        )
        if invalid_ids:
            raise ValueError(
                f"question contains knowledge points outside active taxonomy leaves: {invalid_ids}"
            )

    def _question_syllabus_order(self, question: Question) -> int:
        return min(self._syllabus_order[item] for item in question.knowledge_point_ids)


def _active_leaf_order(taxonomy: Taxonomy) -> dict[str, int]:
    active_leaves = [
        node.id
        for node in taxonomy.nodes
        if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
    ]
    return {knowledge_point_id: index for index, knowledge_point_id in enumerate(active_leaves)}


def _available_knowledge_point_ids(questions: Sequence[Question]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                knowledge_point_id
                for question in questions
                for knowledge_point_id in question.knowledge_point_ids
            }
        )
    )


__all__ = ["QuestionCatalog", "QuestionRetrievalError", "QuestionRetriever"]
