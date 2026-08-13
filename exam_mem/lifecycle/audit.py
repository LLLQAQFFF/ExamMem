"""Strict append-only audit contracts for Stage 06 lifecycle execution."""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from exam_mem.lifecycle.contracts import (
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
RowVersion = Annotated[int, Field(ge=1)]


class LifecycleApplyState(str, Enum):
    """Append-only observations of a planned lifecycle application."""

    PLANNED = "PLANNED"
    APPLIED = "APPLIED"
    IDEMPOTENT = "IDEMPOTENT"
    CONTESTED = "CONTESTED"
    STALE = "STALE"
    FAILED = "FAILED"


class AuditAppendStatus(str, Enum):
    """Idempotent append outcome for one audit primary key."""

    CREATED = "created"
    EXISTING = "existing"
    CONFLICT = "conflict"


class _StrictAuditModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class LifecycleDecisionAuditRecord(_StrictAuditModel):
    """A reproducible record of what deterministic policy planned to do."""

    decision_id: NonEmptyString
    trace_id: NonEmptyString
    policy_input: LifecyclePolicyInput
    policy_result: LifecyclePolicyResult
    created_at: AwareDatetime

    @model_validator(mode="after")
    def validate_decision_identity(self) -> LifecycleDecisionAuditRecord:
        if self.policy_result.event_id != self.policy_input.event.event_id:
            raise ValueError("decision audit event identity must match")
        if self.policy_result.scope != self.policy_input.candidate.scope:
            raise ValueError("decision audit scope must match")
        if self.policy_result.slot_key != self.policy_input.candidate.slot_key:
            raise ValueError("decision audit slot_key must match")
        if self.policy_result.decision.policy_version != self.policy_input.config.policy_version:
            raise ValueError("decision audit policy_version must match")

        snapshots_by_id = {
            snapshot.memory.memory_id: snapshot
            for snapshot in self.policy_input.candidate_snapshots
        }
        unknown_targets = set(self.policy_result.decision.target_memory_ids) - set(snapshots_by_id)
        if unknown_targets:
            raise ValueError("decision audit targets must come from candidate snapshots")
        for memory_id, expected_version in self.policy_result.expected_row_versions.items():
            if snapshots_by_id[memory_id].row_version != expected_version:
                raise ValueError("decision audit expected row version must match snapshot")
        return self


class LifecycleChangeAuditRecord(_StrictAuditModel):
    """One actual append-only application observation for a decision."""

    change_id: NonEmptyString
    decision_id: NonEmptyString
    trace_id: NonEmptyString
    apply_state: LifecycleApplyState
    memory_id: NonEmptyString | None = None
    before_state: LifecycleMemorySnapshot | None = None
    after_state: LifecycleMemorySnapshot | None = None
    expected_row_version: RowVersion | None = None
    actual_row_version: RowVersion | None = None
    error_code: NonEmptyString | None = None
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def validate_apply_observation(self) -> LifecycleChangeAuditRecord:
        for state in (self.before_state, self.after_state):
            if state is not None:
                if self.memory_id is None:
                    raise ValueError("state snapshots require memory_id")
                if state.memory.memory_id != self.memory_id:
                    raise ValueError("state snapshot memory_id must match change memory_id")

        if self.apply_state in {
            LifecycleApplyState.APPLIED,
            LifecycleApplyState.CONTESTED,
        }:
            if self.memory_id is None or self.after_state is None:
                raise ValueError("successful apply state requires memory_id and after_state")

        if self.apply_state is LifecycleApplyState.IDEMPOTENT:
            if (self.before_state is None) != (self.after_state is None):
                raise ValueError("idempotent snapshots must be both present or both absent")
            if self.before_state is not None and self.before_state != self.after_state:
                raise ValueError("idempotent before_state and after_state must match")

        if self.apply_state is LifecycleApplyState.STALE:
            if self.expected_row_version is None or self.actual_row_version is None:
                raise ValueError("stale change requires expected and actual row versions")

        if self.apply_state is LifecycleApplyState.FAILED:
            if self.error_code is None:
                raise ValueError("failed change requires error_code")
        elif self.error_code is not None:
            raise ValueError("error_code is allowed only for failed changes")
        return self


class LifecycleAuditTrail(_StrictAuditModel):
    """Trace-scoped decisions and independently recorded apply outcomes."""

    trace_id: NonEmptyString
    decisions: tuple[LifecycleDecisionAuditRecord, ...]
    changes: tuple[LifecycleChangeAuditRecord, ...]

    @model_validator(mode="after")
    def validate_trace_membership(self) -> LifecycleAuditTrail:
        if any(decision.trace_id != self.trace_id for decision in self.decisions):
            raise ValueError("audit trail decision trace_id must match")
        if any(change.trace_id != self.trace_id for change in self.changes):
            raise ValueError("audit trail change trace_id must match")
        decision_ids = {decision.decision_id for decision in self.decisions}
        if any(change.decision_id not in decision_ids for change in self.changes):
            raise ValueError("audit trail change must reference a returned decision")
        return self


__all__ = [
    "AuditAppendStatus",
    "LifecycleApplyState",
    "LifecycleAuditTrail",
    "LifecycleChangeAuditRecord",
    "LifecycleDecisionAuditRecord",
]
