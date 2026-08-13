from __future__ import annotations

from dataclasses import dataclass

import pytest

from deeptutor.core.context import UnifiedContext
from exam_mem.backends import BackendMode
from exam_mem.config import ExamMemSettings
from exam_mem.contracts import MemoryScope
from exam_mem.practice import PracticeContext, Question
import exam_mem.practice.provider as provider_module
from exam_mem.practice.provider import (
    PRACTICE_QUESTIONS_METADATA_KEY,
    BoundQuestionCatalog,
    PracticeRuntimeConfigurationError,
    PracticeRuntimeProvider,
    RuntimeRecommendationTool,
    TransactionalPracticeMemoryWriter,
)
from exam_mem.practice.question_retriever import QuestionRetriever
from exam_mem.practice.tools import QuestionRetrieverTool

pytestmark = pytest.mark.asyncio

SCOPE = MemoryScope(
    user_id="practice_provider_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _question() -> Question:
    return Question(
        question_id="question:provider:001",
        stem="Calculate one probability.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.5,
        reference_answer="Apply Bayes' theorem.",
        grading_rubric={"required_steps": ["apply_bayes"]},
    )


def _practice_context() -> PracticeContext:
    return PracticeContext(
        practice_session_id="practice:provider:001",
        scope=SCOPE,
        trace_id="trace:provider:001",
    )


class NoConnectionEngine:
    def __init__(self) -> None:
        self.disposed = False

    async def dispose(self) -> None:
        self.disposed = True


@dataclass
class FakeDatabaseSettings:
    def sqlalchemy_url(self) -> str:
        return "postgresql+asyncpg://redacted:redacted@127.0.0.1/exammem"


async def test_none_writer_has_no_database_or_memory_side_effects() -> None:
    writer = TransactionalPracticeMemoryWriter(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        trace_id="trace:provider:001",
    )

    result = await writer.write(None, [])  # type: ignore[arg-type]

    assert result.decisions == ()
    assert result.projection_requests == ()


async def test_non_lifecycle_recommendation_uses_neutral_policy_without_database() -> None:
    retriever = QuestionRetrieverTool(QuestionRetriever(BoundQuestionCatalog(SCOPE, [_question()])))
    tool = RuntimeRecommendationTool(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        retriever=retriever,
    )

    recommendation, question = await tool.recommend(_practice_context())

    assert question == _question()
    assert recommendation.target_knowledge_point_id == "math1.probability.bayes"
    assert recommendation.reason_codes == ["coverage_gap"]
    assert recommendation.source_memory_ids == []


async def test_runtime_provider_requires_structured_question_catalog_before_database_use(
) -> None:
    with pytest.raises(PracticeRuntimeConfigurationError, match="structured questions"):
        async with PracticeRuntimeProvider(
            settings=ExamMemSettings.model_validate({"memory_backend": "none"})
        ).open(
            UnifiedContext(),
            _practice_context(),
        ):
            pass


async def test_runtime_provider_builds_turn_bound_workflow_and_disposes_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = NoConnectionEngine()
    monkeypatch.setattr(provider_module, "load_database_settings", FakeDatabaseSettings)
    unified = UnifiedContext(
        config_overrides={PRACTICE_QUESTIONS_METADATA_KEY: [_question().model_dump(mode="json")]},
        metadata={PRACTICE_QUESTIONS_METADATA_KEY: [{"invalid": "must-not-win"}]},
    )

    provider = PracticeRuntimeProvider(
        settings=ExamMemSettings.model_validate({"memory_backend": "none"}),
        engine_factory=lambda url: engine,  # type: ignore[arg-type,return-value]
    )
    async with provider.open(unified, _practice_context()) as runtime:
        assert runtime.workflow is not None
        assert runtime.engine is engine

    assert engine.disposed is True
