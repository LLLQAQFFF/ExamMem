"""Bounded second-stage reranking for Learning Memory candidates."""

from __future__ import annotations

from collections.abc import Callable, Sequence
import json
from typing import Protocol

from exam_mem.contracts import ErrorPatternValue, LearningMemory, MasteryValue

from .contracts import RetrievalIntent, ScoredLearningMemory

RERANK_INSTRUCTION = (
    "Given a user query, retrieve relevant passages that directly answer the query"
)


class RetrievalEmbeddingClient(Protocol):
    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


class RetrievalRerankingClient(Protocol):
    async def score(
        self,
        *,
        query: str,
        documents: Sequence[str],
        instruction: str,
    ) -> list[float]: ...


class LearningMemoryReranker(Protocol):
    async def rerank(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        candidates: Sequence[ScoredLearningMemory],
        intent: RetrievalIntent,
    ) -> tuple[ScoredLearningMemory, ...]: ...


class HostLearningMemoryReranker:
    """Cross-encode a bounded candidate set through the neutral Host port."""

    def __init__(
        self,
        reranking_client: RetrievalRerankingClient,
        *,
        knowledge_point_label: Callable[[str], str],
    ) -> None:
        self._reranking_client = reranking_client
        self._knowledge_point_label = knowledge_point_label

    async def rerank(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        candidates: Sequence[ScoredLearningMemory],
        intent: RetrievalIntent,
    ) -> tuple[ScoredLearningMemory, ...]:
        del query_embedding, intent
        scores = await self._reranking_client.score(
            query=query,
            documents=[self._render_memory(candidate.memory) for candidate in candidates],
            instruction=RERANK_INSTRUCTION,
        )
        if len(scores) != len(candidates):
            raise ValueError("reranker score count must match candidate count")
        scored = tuple(
            candidate.model_copy(update={"relevance_score": score})
            for candidate, score in zip(candidates, scores, strict=True)
        )
        return tuple(
            sorted(
                scored,
                key=lambda item: (
                    -(item.relevance_score or 0.0),
                    item.distance,
                    item.memory.memory_id,
                ),
            )
        )

    def _render_memory(self, memory: LearningMemory) -> str:
        knowledge_point_id = _memory_knowledge_point_id(memory)
        label = (
            self._knowledge_point_label(knowledge_point_id)
            if knowledge_point_id is not None
            else ""
        )
        if isinstance(memory.value, MasteryValue):
            evidence = (
                f"掌握状态：{memory.value.level.value}；"
                f"掌握分数：{memory.value.score:.3f}"
            )
        elif isinstance(memory.value, ErrorPatternValue):
            evidence = f"错误表现：{memory.value.summary}"
        else:
            evidence = json.dumps(
                memory.value.model_dump(mode="json"),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        return f"知识点：{label}。{evidence}"


class DistanceLearningMemoryReranker:
    """Preserve first-stage cosine order when no Host reranker is configured."""

    async def rerank(
        self,
        *,
        query: str,
        query_embedding: Sequence[float],
        candidates: Sequence[ScoredLearningMemory],
        intent: RetrievalIntent,
    ) -> tuple[ScoredLearningMemory, ...]:
        del query, query_embedding, intent
        return tuple(
            candidate.model_copy(update={"relevance_score": 1.0 - candidate.distance / 2.0})
            for candidate in candidates
        )


def _memory_knowledge_point_id(memory: LearningMemory) -> str | None:
    parts = memory.slot_key.split(":")
    return parts[1] if len(parts) > 1 and "." in parts[1] else None


__all__ = [
    "DistanceLearningMemoryReranker",
    "HostLearningMemoryReranker",
    "LearningMemoryReranker",
    "RERANK_INSTRUCTION",
    "RetrievalEmbeddingClient",
    "RetrievalRerankingClient",
]
