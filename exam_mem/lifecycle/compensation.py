"""Audited chain-tail compensation without rewriting Learning Memory history."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from typing import TYPE_CHECKING

from exam_mem.contracts import (
    LearningEvent,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.domain.candidate_query import CandidateMatchReason, build_candidate_query
from exam_mem.storage.event_repository import AppendStatus

from .applier import LifecycleApplicationResult, LifecycleApplier
from .audit import LifecycleApplyState, LifecycleChangeAuditRecord
from .contracts import (
    LifecycleCandidateSnapshot,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    LifecyclePolicyV1Config,
)

if TYPE_CHECKING:
    from exam_mem.storage.audit_repository import LifecycleAuditRepository
    from exam_mem.storage.event_repository import LearningEventRepository
    from exam_mem.storage.memory_repository import LearningMemoryRepository


class CompensationValidationError(ValueError):
    """Raised when a source decision is absent, unsupported, or no longer the chain tail."""


class CompensationTokenError(ValueError):
    """Raised when execution was not authorized by the matching dry-run token."""


@dataclass(frozen=True, slots=True)
class CompensationPlan:
    """A deterministic recovery plan bound to the current authoritative snapshots."""

    source_decision_id: str
    restore_from_memory_id: str
    operator: str
    reason: str
    apply_token: str
    decision_id: str
    trace_id: str
    policy_input: LifecyclePolicyInput
    policy_result: LifecyclePolicyResult


class CompensationService:
    """Plan and apply one compensating version inside a caller-owned transaction."""

    def __init__(
        self,
        *,
        audit_repository: LifecycleAuditRepository,
        memory_repository: LearningMemoryRepository,
        event_repository: LearningEventRepository,
        applier: LifecycleApplier,
    ) -> None:
        self._audit_repository = audit_repository
        self._memory_repository = memory_repository
        self._event_repository = event_repository
        self._applier = applier

    async def plan(
        self,
        *,
        source_decision_id: str,
        scope: MemoryScope,
        operator: str,
        reason: str,
        compensated_at: datetime,
    ) -> CompensationPlan:
        operator = operator.strip()
        reason = reason.strip()
        if not operator:
            raise CompensationValidationError("operator must not be blank")
        if not reason:
            raise CompensationValidationError("reason must not be blank")
        if compensated_at.tzinfo is None or compensated_at.utcoffset() is None:
            raise CompensationValidationError("compensated_at must include timezone information")

        source = await self._audit_repository.get_decision(source_decision_id)
        if source is None:
            raise CompensationValidationError("source decision does not exist")
        if source.policy_result.scope != scope:
            raise CompensationValidationError("source decision does not match requested Scope")
        operation = source.policy_result.decision.operation
        if operation not in {
            LifecycleOperation.MERGE,
            LifecycleOperation.SUPERSEDE,
            LifecycleOperation.CONTESTED,
        }:
            raise CompensationValidationError(
                f"MVP compensation cannot restore {operation.value} without a prior active state"
            )

        changes = await self._audit_repository.list_changes_by_decision(source_decision_id)
        restore_from = _unique_prior_active(changes)
        current = tuple(
            await self._memory_repository.find_candidate_snapshots(
                build_candidate_query(
                    scope=scope,
                    slot_key=source.policy_result.slot_key,
                    match_reason=CandidateMatchReason.EXACT_SLOT,
                )
            )
        )
        chain = tuple(
            await self._memory_repository.list_slot_snapshots(
                scope,
                source.policy_result.slot_key,
            )
        )
        _validate_unchanged_chain_tail(changes, current=current, chain=chain)

        token = _apply_token(
            source_decision_id=source_decision_id,
            scope=scope,
            slot_key=source.policy_result.slot_key,
            restore_from=restore_from,
            current=current,
            operator=operator,
            reason=reason,
        )
        digest = token.removeprefix("sha256:")
        event_id = f"lifecycle_compensation_event:{digest}"
        decision_id = f"{source_decision_id}:compensate:{digest}"
        trace_id = f"{source.trace_id}:compensate:{digest}"
        current_ids = [snapshot.memory.memory_id for snapshot in current]
        event = LearningEvent.model_validate(
            {
                "event_id": event_id,
                "idempotency_key": event_id,
                "event_type": "explicit_correction",
                "context": {
                    "user_id": scope.user_id,
                    "exam_id": scope.exam_id,
                    "subject_id": scope.subject_id,
                },
                "session_id": f"lifecycle_compensation:{digest}",
                "knowledge_point_ids": source.policy_input.event.knowledge_point_ids,
                "correction": {
                    "target_memory_ids": current_ids,
                    "source": "grader_audit",
                    "statement": reason,
                },
                "occurred_at": compensated_at,
            }
        )
        candidate = MemoryUpdateCandidate(
            event_id=event.event_id,
            scope=scope,
            slot_key=source.policy_result.slot_key,
            proposed_value=restore_from.memory.value,
            evidence={
                "source_decision_id": source_decision_id,
                "operator": operator,
                "reason": reason,
            },
        )
        policy_input = LifecyclePolicyInput(
            event=event,
            candidate=candidate,
            candidate_snapshots=current,
            evaluated_at=compensated_at,
            config=LifecyclePolicyV1Config(maximum_cas_recomputations=0),
        )
        policy_result = LifecyclePolicyResult(
            event_id=event.event_id,
            scope=scope,
            slot_key=candidate.slot_key,
            decision=LifecycleDecision(
                operation=LifecycleOperation.SUPERSEDE,
                target_memory_ids=current_ids,
                reason_code="compensation_restore_chain_tail",
                confidence=restore_from.memory.confidence,
                policy_version=policy_input.config.policy_version,
            ),
            expected_row_versions={
                snapshot.memory.memory_id: snapshot.row_version for snapshot in current
            },
        )
        return CompensationPlan(
            source_decision_id=source_decision_id,
            restore_from_memory_id=restore_from.memory.memory_id,
            operator=operator,
            reason=reason,
            apply_token=token,
            decision_id=decision_id,
            trace_id=trace_id,
            policy_input=policy_input,
            policy_result=policy_result,
        )

    async def apply(
        self,
        plan: CompensationPlan,
        *,
        apply_token: str,
        applied_at: datetime,
    ) -> LifecycleApplicationResult:
        if apply_token != plan.apply_token:
            raise CompensationTokenError("apply token does not match the current dry-run plan")
        append_result = await self._event_repository.append(plan.policy_input.event)
        if append_result.status is not AppendStatus.CREATED:
            raise CompensationValidationError("compensation event was not newly appended")
        return await self._applier.apply(
            plan.policy_input,
            plan.policy_result,
            decision_id=plan.decision_id,
            trace_id=plan.trace_id,
            applied_at=applied_at,
        )


def _unique_prior_active(
    changes: list[LifecycleChangeAuditRecord],
) -> LifecycleMemorySnapshot:
    prior_active = {
        change.before_state.memory.memory_id: change.before_state
        for change in changes
        if change.apply_state in {LifecycleApplyState.APPLIED, LifecycleApplyState.CONTESTED}
        and change.before_state is not None
        and change.before_state.memory.lifecycle_state is LifecycleState.ACTIVE
    }
    if len(prior_active) != 1:
        raise CompensationValidationError(
            "source decision must contain exactly one restorable prior active state"
        )
    return next(iter(prior_active.values()))


def _validate_unchanged_chain_tail(
    changes: list[LifecycleChangeAuditRecord],
    *,
    current: tuple[LifecycleCandidateSnapshot, ...],
    chain: tuple[LifecycleMemorySnapshot, ...],
) -> None:
    if not current or not chain:
        raise CompensationValidationError("source decision is no longer the active chain tail")
    source_current = {
        change.after_state.memory.memory_id: change.after_state.model_dump(mode="json")
        for change in changes
        if change.apply_state in {LifecycleApplyState.APPLIED, LifecycleApplyState.CONTESTED}
        and change.after_state is not None
        and change.after_state.memory.lifecycle_state
        in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
    }
    authoritative = {
        snapshot.memory.memory_id: snapshot.model_dump(mode="json") for snapshot in current
    }
    if authoritative != source_current:
        raise CompensationValidationError("source decision has a later dependent state")
    source_tail_version = max(snapshot["memory"]["version"] for snapshot in source_current.values())
    chain_tail_version = max(snapshot.memory.version for snapshot in chain)
    if source_tail_version != chain_tail_version:
        raise CompensationValidationError("source decision is not the slot version-chain tail")


def _apply_token(
    *,
    source_decision_id: str,
    scope: MemoryScope,
    slot_key: str,
    restore_from: LifecycleMemorySnapshot,
    current: tuple[LifecycleCandidateSnapshot, ...],
    operator: str,
    reason: str,
) -> str:
    payload = json.dumps(
        {
            "source_decision_id": source_decision_id,
            "scope": scope.model_dump(mode="json"),
            "slot_key": slot_key,
            "restore_from": restore_from.model_dump(mode="json"),
            "current": [
                snapshot.model_dump(mode="json")
                for snapshot in sorted(
                    current,
                    key=lambda item: (item.memory.version, item.memory.memory_id),
                )
            ],
            "operator": operator,
            "reason": reason,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "CompensationPlan",
    "CompensationService",
    "CompensationTokenError",
    "CompensationValidationError",
]
