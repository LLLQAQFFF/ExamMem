from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest

from deeptutor.core.context import UnifiedContext
from exam_mem.backends import BackendMode
from exam_mem.config import ExamMemSettings
from exam_mem.contracts import MemoryScope
from exam_mem.domain import Taxonomy
from exam_mem.practice import (
    AnswerSubmission,
    PracticeContext,
    PracticeState,
    Question,
    RecommendationAction,
    RecommendationCandidate,
    RecommendationFeatures,
    RecommendationSelection,
)
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


def _dynamic_taxonomy() -> Taxonomy:
    return Taxonomy.model_validate(
        {
            "taxonomy_version": "ptest_s001_v1",
            "nodes": [
                {"id": "ptest", "name_zh": "Imported subject"},
                {
                    "id": "ptest.module",
                    "name_zh": "Imported module",
                    "parent_id": "ptest",
                },
                {
                    "id": "ptest.module.point",
                    "name_zh": "Imported knowledge point",
                    "parent_id": "ptest.module",
                },
            ],
        }
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


async def test_non_lifecycle_initial_question_uses_catalog_without_database() -> None:
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


async def test_non_lifecycle_recommendation_uses_resolved_dynamic_taxonomy() -> None:
    taxonomy = _dynamic_taxonomy()
    scope = SCOPE.model_copy(update={"exam_id": "ptest", "subject_id": "ptest"})
    question = _question().model_copy(
        update={
            "question_id": "question:dynamic:001",
            "knowledge_point_ids": ["ptest.module.point"],
        }
    )
    context = _practice_context().model_copy(
        update={"scope": scope, "taxonomy_version": taxonomy.taxonomy_version}
    )
    retriever = QuestionRetrieverTool(
        QuestionRetriever(BoundQuestionCatalog(scope, [question]), taxonomy=taxonomy)
    )
    tool = RuntimeRecommendationTool(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        retriever=retriever,
        taxonomy=taxonomy,
    )

    recommendation, selected = await tool.recommend(context)

    assert selected == question
    assert recommendation.target_knowledge_point_id == "ptest.module.point"


async def test_post_answer_without_review_evidence_returns_no_recommendation() -> None:
    question = _question()
    context = _practice_context().model_copy(
        update={
            "current_question": question,
            "submitted_answer": AnswerSubmission(
                practice_session_id="practice:provider:001",
                question_id=question.question_id,
                answer="A correct answer.",
                submitted_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
                idempotency_key="answer:provider:001",
            ),
            "step_state": PracticeState.MEMORY_UPDATED,
        }
    )
    retriever = QuestionRetrieverTool(QuestionRetriever(BoundQuestionCatalog(SCOPE, [question])))
    tool = RuntimeRecommendationTool(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        retriever=retriever,
    )

    recommendation, selected = await tool.recommend(context)

    assert selected is None
    assert recommendation.action_type is RecommendationAction.NO_RECOMMENDATION
    assert recommendation.reason_codes == ["insufficient_evidence"]


async def test_llm_selection_is_audited_after_rule_candidate_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeSelector:
        async def select(self, scores):  # noqa: ANN001, ANN201
            assert len(scores) == 1
            return RecommendationSelection(
                target_knowledge_point_id="math1.probability.bayes",
                confidence=0.91,
            )

    retriever = QuestionRetrieverTool(QuestionRetriever(BoundQuestionCatalog(SCOPE, [_question()])))
    tool = RuntimeRecommendationTool(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        retriever=retriever,
        llm_selector=FakeSelector(),  # type: ignore[arg-type]
    )

    async def candidates(_context):  # noqa: ANN001
        return (
            RecommendationCandidate(
                target_knowledge_point_id="math1.probability.bayes",
                target_difficulty=0.5,
                features=RecommendationFeatures(
                    weakness=1.0,
                    stable_error=0.0,
                    forgetting_risk=0.0,
                    active_plan_priority=0.0,
                    coverage_gap=0.0,
                ),
            ),
        )

    monkeypatch.setattr(tool, "_candidates", candidates)

    recommendation, selected = await tool.recommend(_practice_context())

    assert selected == _question()
    assert recommendation.selection_strategy == "llm"
    assert recommendation.selection_confidence == 0.91
    assert recommendation.selection_candidate_ids == ["math1.probability.bayes"]
    assert recommendation.selector_version == "llm_selector_v1"


async def test_invalid_llm_selection_uses_audited_rule_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RejectingSelector:
        async def select(self, scores):  # noqa: ANN001, ANN201
            assert len(scores) == 1
            return None

    retriever = QuestionRetrieverTool(QuestionRetriever(BoundQuestionCatalog(SCOPE, [_question()])))
    tool = RuntimeRecommendationTool(
        NoConnectionEngine(),  # type: ignore[arg-type]
        mode=BackendMode.NONE,
        retriever=retriever,
        llm_selector=RejectingSelector(),  # type: ignore[arg-type]
    )

    async def candidates(_context):  # noqa: ANN001
        return (
            RecommendationCandidate(
                target_knowledge_point_id="math1.probability.bayes",
                target_difficulty=0.5,
                features=RecommendationFeatures(
                    weakness=1.0,
                    stable_error=0.0,
                    forgetting_risk=0.0,
                    active_plan_priority=0.0,
                    coverage_gap=0.0,
                ),
            ),
        )

    monkeypatch.setattr(tool, "_candidates", candidates)

    recommendation, selected = await tool.recommend(_practice_context())

    assert selected == _question()
    assert recommendation.selection_strategy == "rule_fallback"
    assert recommendation.selection_confidence is None
    assert recommendation.selection_candidate_ids == ["math1.probability.bayes"]


async def test_runtime_provider_requires_structured_question_catalog_before_database_use() -> None:
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

    class FakeCheckpointRepository:
        def __init__(self, _engine) -> None:  # noqa: ANN001
            pass

        async def get_runtime_snapshot(self, _context, _practice_session_id):  # noqa: ANN001
            return None

    monkeypatch.setattr(
        provider_module,
        "CommittedPostgresPracticeCheckpointRepository",
        FakeCheckpointRepository,
    )
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
