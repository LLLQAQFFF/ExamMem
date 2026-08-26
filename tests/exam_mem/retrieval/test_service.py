from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.contracts import LearningMemory, MemoryScope
from exam_mem.domain import load_taxonomy
from exam_mem.retrieval import (
    LearningMemoryRetrievalService,
    RetrievalDecision,
    RetrievalPolicy,
    ScoredLearningMemory,
    TaxonomyRetrievalIntentResolver,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="retrieval_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)


def _memory(memory_id: str, *, error_type: str, knowledge_point_id: str) -> LearningMemory:
    return LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": f"error_pattern:{knowledge_point_id}:{error_type}",
            "value": {
                "type": "error_pattern",
                "error_type": error_type,
                "summary": f"stored {error_type} evidence",
                "details": [],
            },
            "confidence": 0.9,
            "evidence_count": 1,
            "lifecycle_state": "active",
            "version": 1,
            "valid_from": NOW,
            "valid_to": None,
            "superseded_by": None,
            "provenance": [f"{memory_id}_event"],
        }
    )


class _Repository:
    def __init__(self, candidates: list[ScoredLearningMemory]) -> None:
        self.candidates = candidates
        self.similar_calls = 0

    async def find_candidates(self, query):  # noqa: ANN001, ANN201
        del query
        return [item.memory for item in self.candidates]

    async def find_similar_scored(self, scope, query_embedding, limit):  # noqa: ANN001, ANN201
        del scope, query_embedding
        self.similar_calls += 1
        return self.candidates[:limit]


class _Embedding:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    async def embed(self, texts, *, input_type=None):  # noqa: ANN001, ANN201
        self.calls.append((texts, input_type))
        return [[1.0, 0.0] for _ in texts]


class _Reranker:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    async def rerank(  # noqa: ANN201
        self,
        *,
        query,
        query_embedding,
        candidates,
        intent,
    ):
        del query, query_embedding, intent
        return tuple(
            sorted(
                (
                    candidate.model_copy(
                        update={"relevance_score": self.scores[candidate.memory.memory_id]}
                    )
                    for candidate in candidates
                ),
                key=lambda item: -(item.relevance_score or 0.0),
            )
        )


def _service(
    repository: _Repository,
    *,
    scores: dict[str, float],
    threshold: float = 0.0,
) -> tuple[LearningMemoryRetrievalService, _Embedding]:
    embedding = _Embedding()
    return (
        LearningMemoryRetrievalService(
            memory_repository=repository,
            embedding_client=embedding,
            intent_resolver=TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1")),
            reranker=_Reranker(scores),
            policy=RetrievalPolicy(candidate_limit=50, minimum_relevance_score=threshold),
        ),
        embedding,
    )


async def test_unknown_explicit_target_rejects_before_embedding_or_database_search() -> None:
    repository = _Repository([])
    service, embedding = _service(repository, scores={})

    result = await service.retrieve(
        SCOPE,
        "我有没有关于拉普拉斯变换收敛域的错误记忆？",
        5,
    )

    assert result.decision is RetrievalDecision.OUT_OF_SCOPE_TARGET
    assert result.items == ()
    assert repository.similar_calls == 0
    assert embedding.calls == []


async def test_explicit_error_type_is_filtered_without_semantic_fallback() -> None:
    concept = _memory(
        "inverse_concept",
        error_type="concept_confusion",
        knowledge_point_id="math1.linear_algebra.inverse_matrix",
    )
    repository = _Repository([ScoredLearningMemory(memory=concept, distance=0.1)])
    service, _ = _service(repository, scores={concept.memory_id: 0.9})

    result = await service.retrieve(
        SCOPE,
        "只查逆矩阵因为看错题干造成的 reading error；不要拿概念错误代替。",
        5,
    )

    assert result.decision is RetrievalDecision.NO_MATCH
    assert result.items == ()
    assert repository.similar_calls == 1


async def test_confidence_gate_returns_variable_length_results() -> None:
    first = _memory(
        "rank_concept",
        error_type="concept_confusion",
        knowledge_point_id="math1.linear_algebra.matrix_rank",
    )
    second = _memory(
        "rank_formula",
        error_type="formula_misuse",
        knowledge_point_id="math1.linear_algebra.matrix_rank",
    )
    repository = _Repository(
        [
            ScoredLearningMemory(memory=first, distance=0.1),
            ScoredLearningMemory(memory=second, distance=0.2),
        ]
    )
    service, _ = _service(
        repository,
        scores={first.memory_id: 0.85, second.memory_id: 0.60},
        threshold=0.70,
    )

    result = await service.retrieve(SCOPE, "查找矩阵秩的历史错误记录", 5)

    assert result.decision is RetrievalDecision.ANSWERED
    assert [item.memory.memory_id for item in result.items] == [first.memory_id]


async def test_all_candidates_below_gate_produce_auditable_rejection() -> None:
    memory = _memory(
        "rank_concept",
        error_type="concept_confusion",
        knowledge_point_id="math1.linear_algebra.matrix_rank",
    )
    repository = _Repository([ScoredLearningMemory(memory=memory, distance=0.4)])
    service, _ = _service(repository, scores={memory.memory_id: 0.55}, threshold=0.70)

    result = await service.retrieve(SCOPE, "查找矩阵秩的历史错误记录", 5)

    assert result.decision is RetrievalDecision.BELOW_CONFIDENCE
    assert result.items == ()
