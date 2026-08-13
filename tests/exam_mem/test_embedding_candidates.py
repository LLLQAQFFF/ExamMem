from __future__ import annotations

import pytest

from exam_mem.domain import load_normalization_policy, load_taxonomy
from exam_mem.normalization import TaxonomyEmbeddingCandidateRanker


class _FakeEmbeddingClient:
    def __init__(self) -> None:
        self.calls: list[tuple[list[str], str | None]] = []

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]:
        self.calls.append((texts, input_type))
        if input_type == "search_query":
            return [[1.0, 0.0] for _ in texts]
        return [self._candidate_vector(text) for text in texts]

    @staticmethod
    def _candidate_vector(text: str) -> list[float]:
        if "条件概率" in text:
            return [1.0, 0.0]
        if "贝叶斯" in text:
            return [0.8, 0.2]
        return [0.0, 1.0]


def _build_ranker(
    embedding_client: _FakeEmbeddingClient,
) -> TaxonomyEmbeddingCandidateRanker:
    return TaxonomyEmbeddingCandidateRanker(
        taxonomy=load_taxonomy("math1_v1"),
        policy=load_normalization_policy("slot_normalizer_v1"),
        embedding_client=embedding_client,
    )


@pytest.mark.taxonomy
@pytest.mark.asyncio
async def test_embedding_ranker_returns_only_active_leaf_top_k() -> None:
    embedding_client = _FakeEmbeddingClient()
    taxonomy = load_taxonomy("math1_v1")
    ranker = _build_ranker(embedding_client)

    candidates = await ranker.rank("条件概率的计算方式")

    assert len(candidates) == 5
    assert candidates[0].knowledge_point_id == ("math1.probability.conditional_probability")
    assert candidates[0].similarity == 1.0
    assert candidates[1].knowledge_point_id == "math1.probability.bayes"
    assert all(
        (node := taxonomy.get(candidate.knowledge_point_id)) is not None
        and not taxonomy.children_of(node.id)
        for candidate in candidates
    )


@pytest.mark.taxonomy
@pytest.mark.asyncio
async def test_embedding_ranker_uses_document_and_query_roles() -> None:
    embedding_client = _FakeEmbeddingClient()
    ranker = _build_ranker(embedding_client)

    await ranker.rank("ＣＤＦ")

    assert [input_type for _, input_type in embedding_client.calls] == [
        "search_document",
        "search_query",
    ]
    document_texts = embedding_client.calls[0][0]
    assert any("math1.probability.distribution_function" in text for text in document_texts)
    assert any("CDF" in text for text in document_texts)
    assert embedding_client.calls[1][0] == ["cdf"]


@pytest.mark.taxonomy
@pytest.mark.asyncio
async def test_embedding_ranker_has_deterministic_tie_order() -> None:
    embedding_client = _FakeEmbeddingClient()
    ranker = _build_ranker(embedding_client)

    first = await ranker.rank("未知但可编码的候选")
    second = await ranker.rank("未知但可编码的候选")

    assert first == second
    candidate_ids = [candidate.knowledge_point_id for candidate in first]
    assert candidate_ids[:2] == [
        "math1.probability.conditional_probability",
        "math1.probability.bayes",
    ]
    assert candidate_ids[2:] == sorted(candidate_ids[2:])


@pytest.mark.taxonomy
@pytest.mark.asyncio
async def test_embedding_ranker_skips_blank_or_punctuation_only_input() -> None:
    embedding_client = _FakeEmbeddingClient()
    ranker = _build_ranker(embedding_client)

    assert await ranker.rank("  ，！？  ") == ()
    assert embedding_client.calls == []


@pytest.mark.taxonomy
@pytest.mark.asyncio
async def test_embedding_ranker_rejects_invalid_vectors() -> None:
    class _ZeroVectorEmbeddingClient(_FakeEmbeddingClient):
        @staticmethod
        def _candidate_vector(text: str) -> list[float]:
            return [0.0, 0.0]

    ranker = _build_ranker(_ZeroVectorEmbeddingClient())

    with pytest.raises(ValueError, match="must be non-zero"):
        await ranker.rank("条件概率")
