"""Conservative rule-based knowledge-point normalization."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Annotated, Literal, TypeAlias
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .taxonomy import CanonicalKnowledgePointId, KnowledgePointStatus, Taxonomy

UNKNOWN_KNOWLEDGE_POINT_ID = "unknown"

Confidence = Annotated[float, Field(ge=0.0, le=1.0)]
KnowledgePointIdOrUnknown: TypeAlias = CanonicalKnowledgePointId | Literal["unknown"]
KnowledgePointCandidate: TypeAlias = tuple[str, float]

_CONTROLLED_RULES = {
    "先验后验混淆": "math1.probability.bayes",
}


class NormalizedKnowledgePoint(BaseModel):
    """One normalized candidate with the extractor's original confidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_point_id: KnowledgePointIdOrUnknown
    confidence: Confidence


class KnowledgePointNormalizationResult(BaseModel):
    """Stable primary/secondary output for a multi-knowledge-point question."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    primary_knowledge_point_id: KnowledgePointIdOrUnknown
    primary_confidence: Confidence
    secondary_knowledge_point_ids: tuple[KnowledgePointIdOrUnknown, ...] = ()
    secondary_confidences: tuple[Confidence, ...] = ()

    @model_validator(mode="after")
    def validate_parallel_secondary_values(self) -> KnowledgePointNormalizationResult:
        if len(self.secondary_knowledge_point_ids) != len(self.secondary_confidences):
            raise ValueError("secondary IDs and confidences must have equal lengths")
        if len(self.secondary_knowledge_point_ids) != len(set(self.secondary_knowledge_point_ids)):
            raise ValueError("secondary knowledge point IDs must be unique")
        if self.primary_knowledge_point_id in self.secondary_knowledge_point_ids:
            raise ValueError("primary knowledge point must not be repeated as secondary")
        return self


class RuleBasedKnowledgePointNormalizer:
    """Apply stage-four deterministic rules without embedding or LLM fallback."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        active_leaves = tuple(
            node
            for node in taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
        )
        self._active_leaf_ids = frozenset(node.id for node in active_leaves)
        self._labels = {
            _normalized_label(label): node.id
            for node in active_leaves
            for label in (node.name_zh, *node.aliases)
        }
        self._controlled_rules = {
            _normalized_label(source): target
            for source, target in _CONTROLLED_RULES.items()
            if target in self._active_leaf_ids
        }

    def normalize(
        self,
        candidate_name: str,
        confidence: float,
    ) -> NormalizedKnowledgePoint:
        """Normalize one extracted name, returning unknown when rules are insufficient."""
        normalized_name = _normalized_label(candidate_name)
        knowledge_point_id = self._match(normalized_name)
        return NormalizedKnowledgePoint(
            knowledge_point_id=knowledge_point_id,
            confidence=confidence,
        )

    def normalize_many(
        self,
        *,
        primary: KnowledgePointCandidate,
        secondary: Iterable[KnowledgePointCandidate] = (),
    ) -> KnowledgePointNormalizationResult:
        """Normalize and deterministically de-duplicate one primary and its secondaries."""
        normalized_primary = self.normalize(*primary)
        secondary_by_id: dict[KnowledgePointIdOrUnknown, float] = {}

        for candidate in secondary:
            normalized = self.normalize(*candidate)
            if normalized.knowledge_point_id == normalized_primary.knowledge_point_id:
                continue
            previous_confidence = secondary_by_id.get(normalized.knowledge_point_id)
            if previous_confidence is None or normalized.confidence > previous_confidence:
                secondary_by_id[normalized.knowledge_point_id] = normalized.confidence

        ordered_secondary = tuple(sorted(secondary_by_id.items()))
        return KnowledgePointNormalizationResult(
            primary_knowledge_point_id=normalized_primary.knowledge_point_id,
            primary_confidence=normalized_primary.confidence,
            secondary_knowledge_point_ids=tuple(item[0] for item in ordered_secondary),
            secondary_confidences=tuple(item[1] for item in ordered_secondary),
        )

    def _match(self, normalized_name: str) -> KnowledgePointIdOrUnknown:
        if normalized_name in self._active_leaf_ids:
            return normalized_name

        alias_match = self._labels.get(normalized_name)
        if alias_match is not None:
            return alias_match

        return self._controlled_rules.get(
            normalized_name,
            UNKNOWN_KNOWLEDGE_POINT_ID,
        )


def _normalized_label(value: str) -> str:
    normalized = " ".join(unicodedata.normalize("NFKC", value).split()).casefold()
    while normalized and unicodedata.category(normalized[0]).startswith("P"):
        normalized = normalized[1:].lstrip()
    while normalized and unicodedata.category(normalized[-1]).startswith("P"):
        normalized = normalized[:-1].rstrip()
    return normalized


__all__ = [
    "KnowledgePointCandidate",
    "KnowledgePointNormalizationResult",
    "NormalizedKnowledgePoint",
    "RuleBasedKnowledgePointNormalizer",
    "UNKNOWN_KNOWLEDGE_POINT_ID",
]
