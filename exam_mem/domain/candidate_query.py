"""Stage-four candidate-query contract without persistence implementation."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, StringConstraints, model_validator

from exam_mem.contracts import LifecycleState, MemoryNamespace, MemoryScope

from .scope import build_scope_query_parameters
from .slot_key import SlotKey

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]

CANDIDATE_LIFECYCLE_STATES = (
    LifecycleState.ACTIVE,
    LifecycleState.CONTESTED,
)


class CandidateMatchReason(str, Enum):
    EXACT_SLOT = "exact_slot"
    ALIAS_NORMALIZED = "alias_normalized"
    EMBEDDING_REVIEWED = "embedding_reviewed"


CandidateQueryField: TypeAlias = Literal[
    "user_id",
    "exam_id",
    "subject_id",
    "memory_namespace",
    "lifecycle_state",
    "slot_key",
    "memory_id",
]
CandidateQueryOperator: TypeAlias = Literal["=", "IN", "!="]
CandidateQueryValue: TypeAlias = str | tuple[str, ...]
CandidateQueryPredicate: TypeAlias = tuple[
    CandidateQueryField,
    CandidateQueryOperator,
    CandidateQueryValue,
]
CandidateQueryPredicates: TypeAlias = tuple[CandidateQueryPredicate, ...]


class CandidateQuery(BaseModel):
    """Immutable inputs shared by future candidate-query implementations."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    scope: MemoryScope
    slot_key: SlotKey
    match_reason: CandidateMatchReason
    current_memory_id: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_namespace_alignment(self) -> CandidateQuery:
        slot_namespace = MemoryNamespace(self.slot_key.partition(":")[0])
        if slot_namespace is not self.scope.memory_namespace:
            raise ValueError("slot_key namespace must match scope.memory_namespace")
        return self


def build_candidate_query(
    *,
    scope: MemoryScope,
    slot_key: SlotKey,
    match_reason: CandidateMatchReason,
    current_memory_id: str | None = None,
) -> CandidateQuery:
    return CandidateQuery(
        scope=scope,
        slot_key=slot_key,
        match_reason=match_reason,
        current_memory_id=current_memory_id,
    )


def build_candidate_query_predicates(
    query: CandidateQuery,
) -> CandidateQueryPredicates:
    """Return the mandatory filter sequence for a future Repository query."""
    predicates: CandidateQueryPredicates = tuple(
        (field, "=", value) for field, value in build_scope_query_parameters(query.scope)
    ) + (
        (
            "lifecycle_state",
            "IN",
            tuple(state.value for state in CANDIDATE_LIFECYCLE_STATES),
        ),
        ("slot_key", "=", query.slot_key),
    )
    if query.current_memory_id is not None:
        predicates += (("memory_id", "!=", query.current_memory_id),)
    return predicates


__all__ = [
    "CANDIDATE_LIFECYCLE_STATES",
    "CandidateMatchReason",
    "CandidateQuery",
    "CandidateQueryPredicate",
    "CandidateQueryPredicates",
    "build_candidate_query",
    "build_candidate_query_predicates",
]
