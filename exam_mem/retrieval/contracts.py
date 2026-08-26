"""Storage-neutral contracts for scored Learning Memory retrieval."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from exam_mem.contracts import ErrorType, LearningMemory, MasteryLevel

CosineDistance = Annotated[float, Field(ge=0.0, le=2.0)]
RelevanceScore = Annotated[float, Field(ge=0.0, le=1.0)]


class ScoredLearningMemory(BaseModel):
    """One scoped Memory accompanied by auditable retrieval scores."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    memory: LearningMemory
    distance: CosineDistance
    relevance_score: RelevanceScore | None = None

    @property
    def similarity(self) -> float:
        return 1.0 - self.distance


class RetrievalDecision(str, Enum):
    ANSWERED = "answered"
    NO_MATCH = "no_match"
    INSUFFICIENT_REFERENCE = "insufficient_reference"
    OUT_OF_SCOPE_TARGET = "out_of_scope_target"
    BELOW_CONFIDENCE = "below_confidence"


class RetrievalIntent(BaseModel):
    """Deterministic constraints extracted before semantic candidate ranking."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    knowledge_point_ids: tuple[str, ...] = ()
    error_type: ErrorType | None = None
    mastery_level: MasteryLevel | None = None
    explicit_target: bool = False
    unknown_explicit_target: bool = False
    reference_sufficient: bool = True
    return_all_contested_branches: bool = False


class MemoryRetrievalResult(BaseModel):
    """Auditable variable-length output of one retrieval decision."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    items: tuple[ScoredLearningMemory, ...] = ()
    decision: RetrievalDecision
    intent: RetrievalIntent


__all__ = [
    "CosineDistance",
    "MemoryRetrievalResult",
    "RelevanceScore",
    "RetrievalDecision",
    "RetrievalIntent",
    "ScoredLearningMemory",
]
