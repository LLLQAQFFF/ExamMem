from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.contracts import LearningMemory, MemoryScope
from exam_mem.domain import load_taxonomy
from exam_mem.retrieval import (
    RERANK_INSTRUCTION,
    DistanceLearningMemoryReranker,
    HostLearningMemoryReranker,
    RetrievalIntent,
    ScoredLearningMemory,
    TaxonomyRetrievalIntentResolver,
)

pytestmark = pytest.mark.asyncio

SCOPE = MemoryScope(
    user_id="reranker_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)


def _candidate(memory_id: str, summary: str, distance: float) -> ScoredLearningMemory:
    memory = LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": "error_pattern:math1.linear_algebra.inverse_matrix:concept_confusion",
            "value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": summary,
                "details": [],
            },
            "confidence": 0.9,
            "evidence_count": 1,
            "lifecycle_state": "active",
            "version": 1,
            "valid_from": datetime(2026, 8, 25, tzinfo=timezone.utc),
            "valid_to": None,
            "superseded_by": None,
            "provenance": [f"{memory_id}_event"],
        }
    )
    return ScoredLearningMemory(memory=memory, distance=distance)


class _Client:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[dict[str, object]] = []

    async def score(self, **kwargs):  # noqa: ANN003, ANN202
        self.calls.append(kwargs)
        return self.scores


async def test_host_reranker_projects_memories_and_sorts_by_cross_score() -> None:
    first = _candidate("first", "把矩阵每个元素分别取倒数", 0.1)
    second = _candidate("second", "遗漏逆矩阵存在的前提", 0.2)
    client = _Client([0.25, 0.9])
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))
    reranker = HostLearningMemoryReranker(client, knowledge_point_label=resolver.label_for)

    result = await reranker.rerank(
        query="我忘了检查矩阵是否可逆",
        query_embedding=[1.0, 0.0],
        candidates=[first, second],
        intent=RetrievalIntent(),
    )

    assert [item.memory.memory_id for item in result] == ["second", "first"]
    assert [item.relevance_score for item in result] == [0.9, 0.25]
    assert client.calls[0]["instruction"] == RERANK_INSTRUCTION
    assert "把矩阵每个元素分别取倒数" in client.calls[0]["documents"][0]
    assert "逆矩阵" in client.calls[0]["documents"][0]


async def test_distance_reranker_preserves_repository_order() -> None:
    first = _candidate("first", "first", 0.1)
    second = _candidate("second", "second", 0.2)

    result = await DistanceLearningMemoryReranker().rerank(
        query="query",
        query_embedding=[1.0, 0.0],
        candidates=[first, second],
        intent=RetrievalIntent(),
    )

    assert [item.memory.memory_id for item in result] == ["first", "second"]
    assert [item.relevance_score for item in result] == [0.95, 0.9]
