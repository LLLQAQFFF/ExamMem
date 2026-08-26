"""Policy-complete retrieval over scoped Learning Memory candidates."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningMemory,
    LifecycleState,
    MasteryValue,
    MemoryScope,
)
from exam_mem.domain.candidate_query import CandidateMatchReason, build_candidate_query
from exam_mem.domain.slot_key import validate_slot_key

from .contracts import (
    MemoryRetrievalResult,
    RetrievalDecision,
    RetrievalIntent,
    ScoredLearningMemory,
)
from .intent import TaxonomyRetrievalIntentResolver
from .reranker import LearningMemoryReranker, RetrievalEmbeddingClient


class ScoredMemoryRepository(Protocol):
    async def find_candidates(self, query) -> list[LearningMemory]: ...  # noqa: ANN001

    async def find_similar_scored(
        self,
        scope: MemoryScope,
        query_embedding: Sequence[float],
        limit: int,
    ) -> list[ScoredLearningMemory]: ...


@dataclass(frozen=True, slots=True)
class RetrievalPolicy:
    candidate_limit: int = 5
    minimum_relevance_score: float = 0.003

    def __post_init__(self) -> None:
        if self.candidate_limit < 1:
            raise ValueError("candidate_limit must be greater than or equal to 1")
        if not 0.0 <= self.minimum_relevance_score <= 1.0:
            raise ValueError("minimum_relevance_score must be between 0 and 1")


class LearningMemoryRetrievalService:
    """Resolve intent, retrieve a bounded set, rerank, and return 0..K results."""

    def __init__(
        self,
        *,
        memory_repository: ScoredMemoryRepository,
        embedding_client: RetrievalEmbeddingClient | None,
        intent_resolver: TaxonomyRetrievalIntentResolver,
        reranker: LearningMemoryReranker | None,
        policy: RetrievalPolicy | None = None,
    ) -> None:
        self._memory_repository = memory_repository
        self._embedding_client = embedding_client
        self._intent_resolver = intent_resolver
        self._reranker = reranker
        self._policy = policy or RetrievalPolicy()

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
        *,
        query_embedding: Sequence[float] | None = None,
    ) -> MemoryRetrievalResult:
        if top_k < 1:
            raise ValueError("top_k must be greater than or equal to 1")
        if not query.strip():
            raise ValueError("retrieval query must not be blank")

        try:
            slot_key = str(validate_slot_key(query))
        except ValueError:
            slot_key = None
        if slot_key is not None:
            if slot_key.partition(":")[0] != scope.memory_namespace.value:
                raise ValueError("retrieval slot_key namespace must match scope")
            memories = await self._memory_repository.find_candidates(
                build_candidate_query(
                    scope=scope,
                    slot_key=slot_key,
                    match_reason=CandidateMatchReason.EXACT_SLOT,
                )
            )
            intent = RetrievalIntent(
                knowledge_point_ids=(_slot_knowledge_point_id(slot_key),),
                explicit_target=True,
            )
            return MemoryRetrievalResult(
                items=tuple(
                    ScoredLearningMemory(memory=memory, distance=0.0, relevance_score=1.0)
                    for memory in memories[:top_k]
                ),
                decision=(RetrievalDecision.ANSWERED if memories else RetrievalDecision.NO_MATCH),
                intent=intent,
            )

        intent = self._intent_resolver.resolve(scope, query)
        if not intent.reference_sufficient:
            return MemoryRetrievalResult(
                decision=RetrievalDecision.INSUFFICIENT_REFERENCE,
                intent=intent,
            )
        if intent.unknown_explicit_target:
            return MemoryRetrievalResult(
                decision=RetrievalDecision.OUT_OF_SCOPE_TARGET,
                intent=intent,
            )

        if self._embedding_client is None or self._reranker is None:
            raise ValueError("semantic lifecycle retrieval requires an embedding client")

        vector = list(query_embedding) if query_embedding is not None else await self._embed(query)
        candidates = await self._memory_repository.find_similar_scored(
            scope,
            vector,
            max(top_k, self._policy.candidate_limit),
        )
        constrained = tuple(
            candidate for candidate in candidates if _matches_intent(candidate.memory, intent)
        )
        if not constrained:
            return MemoryRetrievalResult(
                decision=RetrievalDecision.NO_MATCH,
                intent=intent,
            )

        reranked = await self._reranker.rerank(
            query=query,
            query_embedding=vector,
            candidates=constrained,
            intent=intent,
        )
        accepted = tuple(
            candidate
            for candidate in reranked
            if (candidate.relevance_score or 0.0) >= self._policy.minimum_relevance_score
        )[:top_k]
        return MemoryRetrievalResult(
            items=accepted,
            decision=(
                RetrievalDecision.ANSWERED
                if accepted
                else RetrievalDecision.BELOW_CONFIDENCE
            ),
            intent=intent,
        )

    async def _embed(self, query: str) -> list[float]:
        embedding_client = self._embedding_client
        if embedding_client is None:
            raise ValueError("semantic lifecycle retrieval requires an embedding client")
        vectors = await embedding_client.embed([query], input_type="search_query")
        if len(vectors) != 1:
            raise ValueError("embedding client must return exactly one query vector")
        return vectors[0]


def _matches_intent(memory: LearningMemory, intent: RetrievalIntent) -> bool:
    if intent.knowledge_point_ids:
        knowledge_point_id = _slot_knowledge_point_id(memory.slot_key)
        if knowledge_point_id not in intent.knowledge_point_ids:
            return False
    if intent.error_type is not None:
        if not isinstance(memory.value, ErrorPatternValue):
            return False
        if memory.value.error_type is not intent.error_type:
            return False
    if intent.mastery_level is not None and not intent.return_all_contested_branches:
        if not isinstance(memory.value, MasteryValue):
            return False
        if memory.value.level is not intent.mastery_level:
            return False
    if intent.return_all_contested_branches:
        return memory.lifecycle_state is LifecycleState.CONTESTED
    return True


def _slot_knowledge_point_id(slot_key: str) -> str:
    parts = slot_key.split(":")
    return parts[1] if len(parts) > 1 else slot_key


__all__ = [
    "LearningMemoryRetrievalService",
    "RetrievalPolicy",
    "ScoredMemoryRepository",
]
