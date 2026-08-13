"""Strict, storage-agnostic contracts for the Stage 06 lifecycle boundary."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from exam_mem.contracts import (
    ErrorType,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.domain.slot_key import validate_slot_key

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
PositiveInteger = Annotated[int, Field(ge=1)]
RowVersion = Annotated[int, Field(ge=1)]


class StrictLifecycleModel(BaseModel):
    """Reject silent drift at the LLM, policy, and applier boundaries."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class MemoryRelation(str, Enum):
    """The only semantic relations the classifier may emit."""

    DUPLICATE = "duplicate"
    COMPLEMENTARY = "complementary"
    CONTRADICTORY = "contradictory"
    UNRELATED = "unrelated"


class RelationClassifierOutput(StrictLifecycleModel):
    """Strict JSON result visible at the untrusted LLM boundary.

    The model sees and returns only a one-based display number. Database IDs,
    row versions, and contested-group identifiers are deliberately absent.
    """

    candidate_display_number: PositiveInteger
    relation: MemoryRelation
    canonical_knowledge_point_id: NonEmptyString | None = None
    error_type: ErrorType | None = None
    error_summary: NonEmptyString | None = None
    confidence: Probability
    reason: NonEmptyString


class LifecycleMemorySnapshot(StrictLifecycleModel):
    """Complete storage metadata required for CAS and before/after audit."""

    memory: LearningMemory
    row_version: RowVersion
    contested_group_id: NonEmptyString | None = None
    policy_version: NonEmptyString

    @model_validator(mode="after")
    def validate_contested_group(self) -> LifecycleMemorySnapshot:
        if (
            self.memory.lifecycle_state is LifecycleState.CONTESTED
            and self.contested_group_id is None
        ):
            raise ValueError("contested memory requires contested_group_id")
        return self


class LifecycleCandidateSnapshot(LifecycleMemorySnapshot):
    """A writable active/contested snapshot supplied to deterministic policy."""

    @model_validator(mode="after")
    def validate_candidate_state(self) -> LifecycleCandidateSnapshot:
        if self.memory.lifecycle_state not in {
            LifecycleState.ACTIVE,
            LifecycleState.CONTESTED,
        }:
            raise ValueError("lifecycle candidate must be active or contested")
        return self


class ResolvedRelationClassification(StrictLifecycleModel):
    """A validated classifier result mapped back to one authoritative row."""

    target_memory_id: NonEmptyString
    classification: RelationClassifierOutput


class CandidateDisplayRangeError(ValueError):
    """Raised when an LLM display number cannot resolve to this candidate pool."""


def resolve_relation_output(
    classification: RelationClassifierOutput,
    candidate_snapshots: list[LifecycleCandidateSnapshot] | tuple[LifecycleCandidateSnapshot, ...],
) -> ResolvedRelationClassification:
    """Resolve a one-based display number using deterministic candidate order."""
    ordered = sorted(
        candidate_snapshots,
        key=lambda snapshot: (
            snapshot.memory.version,
            snapshot.memory.memory_id,
        ),
    )
    memory_ids = [snapshot.memory.memory_id for snapshot in ordered]
    if len(memory_ids) != len(set(memory_ids)):
        raise ValueError("candidate memory IDs must be unique")

    candidate_count = len(ordered)
    display_number = classification.candidate_display_number
    if display_number > candidate_count:
        if candidate_count == 0:
            raise CandidateDisplayRangeError("candidate display number has an empty candidate pool")
        raise CandidateDisplayRangeError(
            f"candidate display number {display_number} is outside candidate range 1..{candidate_count}"
        )

    return ResolvedRelationClassification(
        target_memory_id=ordered[display_number - 1].memory.memory_id,
        classification=classification,
    )


class LifecyclePolicyV1Config(StrictLifecycleModel):
    """Only the numeric defaults explicitly defined by the Stage 06 spec."""

    policy_version: Literal["lifecycle_policy_v1"] = "lifecycle_policy_v1"
    minimum_directional_event_count: PositiveInteger = 3
    minimum_session_count: PositiveInteger = 2
    minimum_candidate_confidence: Probability = 0.70
    minimum_support_margin: Probability = 0.15
    maximum_cas_recomputations: Annotated[int, Field(ge=0, le=2)] = 2
    manual_review_after_days: PositiveInteger = 30


class LifecyclePolicyInput(StrictLifecycleModel):
    """Complete, unweighted evidence supplied to the deterministic policy.

    Historical events remain raw so callers cannot inject a pre-decided
    support score. The versioned policy will calculate weights in a later
    Stage 06 step after that formula is explicitly frozen.
    """

    event: LearningEvent
    candidate: MemoryUpdateCandidate
    candidate_snapshots: tuple[LifecycleCandidateSnapshot, ...] = ()
    relation: ResolvedRelationClassification | None = None
    historical_events: tuple[LearningEvent, ...] = ()
    evaluated_at: AwareDatetime
    config: LifecyclePolicyV1Config = Field(default_factory=LifecyclePolicyV1Config)

    @model_validator(mode="after")
    def validate_policy_boundary(self) -> LifecyclePolicyInput:
        if self.candidate.event_id != self.event.event_id:
            raise ValueError("candidate event_id must match current event")

        event_scope = (
            self.event.context.user_id,
            self.event.context.exam_id,
            self.event.context.subject_id,
        )
        candidate_scope = (
            self.candidate.scope.user_id,
            self.candidate.scope.exam_id,
            self.candidate.scope.subject_id,
        )
        if event_scope != candidate_scope:
            raise ValueError("candidate scope must match current event context")

        slot_key = str(validate_slot_key(self.candidate.slot_key))
        if slot_key.partition(":")[0] != self.candidate.scope.memory_namespace.value:
            raise ValueError("candidate slot_key namespace must match candidate scope")

        snapshot_ids: list[str] = []
        for snapshot in self.candidate_snapshots:
            memory = snapshot.memory
            snapshot_ids.append(memory.memory_id)
            if memory.scope != self.candidate.scope:
                raise ValueError("candidate snapshot must match candidate scope")
            if memory.slot_key != slot_key:
                raise ValueError("candidate snapshot must match slot_key")
        if len(snapshot_ids) != len(set(snapshot_ids)):
            raise ValueError("candidate snapshot memory IDs must be unique")

        if self.relation is not None and self.relation.target_memory_id not in snapshot_ids:
            raise ValueError("relation target must be in candidate snapshots")

        historical_ids: set[str] = set()
        for historical_event in self.historical_events:
            if historical_event.event_id == self.event.event_id:
                raise ValueError("historical events must not contain the current event")
            if historical_event.event_id in historical_ids:
                raise ValueError("historical events must have unique event IDs")
            if historical_event.context != self.event.context:
                raise ValueError("historical events must match current event context")
            historical_ids.add(historical_event.event_id)
        return self


class LifecyclePolicyResult(StrictLifecycleModel):
    """A deterministic decision plus the exact CAS versions it observed."""

    event_id: NonEmptyString
    scope: MemoryScope
    slot_key: NonEmptyString
    decision: LifecycleDecision
    expected_row_versions: dict[NonEmptyString, RowVersion] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_policy_result(self) -> LifecyclePolicyResult:
        slot_key = str(validate_slot_key(self.slot_key))
        if slot_key.partition(":")[0] != self.scope.memory_namespace.value:
            raise ValueError("result slot_key namespace must match result scope")

        unknown_targets = set(self.expected_row_versions) - set(self.decision.target_memory_ids)
        if unknown_targets:
            raise ValueError("expected_row_versions contains unknown target")
        return self


__all__ = [
    "CandidateDisplayRangeError",
    "LifecycleCandidateSnapshot",
    "LifecycleMemorySnapshot",
    "LifecyclePolicyInput",
    "LifecyclePolicyResult",
    "LifecyclePolicyV1Config",
    "MemoryRelation",
    "RelationClassifierOutput",
    "ResolvedRelationClassification",
    "resolve_relation_output",
]
