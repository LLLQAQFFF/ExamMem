"""Embedding-assisted candidate ranking at the ExamMem application boundary."""

from __future__ import annotations

from collections.abc import Sequence
import math
from typing import Annotated, Protocol
import unicodedata

from pydantic import BaseModel, ConfigDict, Field

from .domain.normalization_policy import NormalizationPolicy
from .domain.taxonomy import (
    CanonicalKnowledgePointId,
    KnowledgePointStatus,
    Taxonomy,
)

Similarity = Annotated[float, Field(ge=-1.0, le=1.0)]


class EmbeddingClientProtocol(Protocol):
    """The subset of DeepTutor's EmbeddingClient used by ExamMem."""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


class EmbeddingKnowledgePointCandidate(BaseModel):
    """One taxonomy-constrained candidate for calibration or later review."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_point_id: CanonicalKnowledgePointId
    similarity: Similarity


class TaxonomyEmbeddingCandidateRanker:
    """Rank active taxonomy leaves without deciding the final canonical ID."""

    def __init__(
        self,
        *,
        taxonomy: Taxonomy,
        policy: NormalizationPolicy,
        embedding_client: EmbeddingClientProtocol,
    ) -> None:
        active_leaves = tuple(
            node
            for node in taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
        )
        self._candidate_ids = tuple(node.id for node in active_leaves)
        self._candidate_texts = tuple(
            " | ".join((node.id, node.name_zh, *node.aliases)) for node in active_leaves
        )
        self._top_k = policy.embedding_top_k
        self._embedding_client = embedding_client

    async def rank(
        self,
        candidate_name: str,
    ) -> tuple[EmbeddingKnowledgePointCandidate, ...]:
        """Return report-only top-k candidates; callers must apply a calibrated policy."""
        normalized_name = _normalize_candidate_text(candidate_name)
        if not normalized_name or not any(character.isalnum() for character in normalized_name):
            return ()

        candidate_vectors = await self._embedding_client.embed(
            list(self._candidate_texts),
            input_type="search_document",
        )
        query_vectors = await self._embedding_client.embed(
            [normalized_name],
            input_type="search_query",
        )
        if len(candidate_vectors) != len(self._candidate_ids):
            raise ValueError(
                "embedding client returned a candidate vector count that does not "
                "match the active taxonomy leaves"
            )
        if len(query_vectors) != 1:
            raise ValueError("embedding client must return exactly one query vector")

        scored = tuple(
            EmbeddingKnowledgePointCandidate(
                knowledge_point_id=knowledge_point_id,
                similarity=_cosine_similarity(query_vectors[0], candidate_vector),
            )
            for knowledge_point_id, candidate_vector in zip(
                self._candidate_ids,
                candidate_vectors,
                strict=True,
            )
        )
        return tuple(
            sorted(
                scored,
                key=lambda candidate: (
                    -candidate.similarity,
                    candidate.knowledge_point_id,
                ),
            )[: self._top_k]
        )


def _normalize_candidate_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


def _cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    if not left or not right:
        raise ValueError("embedding vectors must not be empty")
    if len(left) != len(right):
        raise ValueError("embedding vectors must have matching dimensions")
    if not all(math.isfinite(value) for value in (*left, *right)):
        raise ValueError("embedding vectors must contain only finite values")

    left_norm_squared = math.fsum(value * value for value in left)
    right_norm_squared = math.fsum(value * value for value in right)
    if left_norm_squared == 0.0 or right_norm_squared == 0.0:
        raise ValueError("embedding vectors must be non-zero")

    score = math.fsum(
        left_value * right_value for left_value, right_value in zip(left, right, strict=True)
    ) / math.sqrt(left_norm_squared * right_norm_squared)
    return max(-1.0, min(1.0, score))


__all__ = [
    "EmbeddingClientProtocol",
    "EmbeddingKnowledgePointCandidate",
    "TaxonomyEmbeddingCandidateRanker",
]
