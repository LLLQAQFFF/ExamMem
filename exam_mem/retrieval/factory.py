"""Composition root for the default ExamMem retrieval policy."""

from __future__ import annotations

from exam_mem.domain.taxonomy import Taxonomy

from .intent import TaxonomyRetrievalIntentResolver
from .reranker import (
    DistanceLearningMemoryReranker,
    HostLearningMemoryReranker,
    RetrievalEmbeddingClient,
    RetrievalRerankingClient,
)
from .service import LearningMemoryRetrievalService, RetrievalPolicy, ScoredMemoryRepository


def build_learning_memory_retrieval_service(
    *,
    memory_repository: ScoredMemoryRepository,
    embedding_client: RetrievalEmbeddingClient | None,
    taxonomy: Taxonomy,
    reranking_client: RetrievalRerankingClient | None = None,
    policy: RetrievalPolicy | None = None,
) -> LearningMemoryRetrievalService:
    resolver = TaxonomyRetrievalIntentResolver(taxonomy)
    return LearningMemoryRetrievalService(
        memory_repository=memory_repository,
        embedding_client=embedding_client,
        intent_resolver=resolver,
        reranker=(
            HostLearningMemoryReranker(
                reranking_client,
                knowledge_point_label=resolver.label_for,
            )
            if reranking_client is not None
            else DistanceLearningMemoryReranker()
        ),
        policy=policy,
    )


__all__ = ["build_learning_memory_retrieval_service"]
