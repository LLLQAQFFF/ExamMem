"""Transactional application of validated Stage 06 lifecycle decisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, NoReturn

from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MasteryValue,
    MemoryNamespace,
    MemoryValue,
)
from exam_mem.domain.candidate_query import CandidateMatchReason, build_candidate_query
from exam_mem.lifecycle.audit import (
    AuditAppendStatus,
    LifecycleApplyState,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
)
from exam_mem.lifecycle.contracts import (
    LifecycleCandidateSnapshot,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    ResolvedRelationClassification,
)
from exam_mem.lifecycle.state_machine import decide_lifecycle

if TYPE_CHECKING:
    from exam_mem.storage.audit_repository import LifecycleAuditRepository
    from exam_mem.storage.event_repository import LearningEventRepository
    from exam_mem.storage.memory_repository import LearningMemoryRepository


class LifecycleApplicationConflict(RuntimeError):
    """Raised when an audit identity or validated decision is inconsistent."""


@dataclass(frozen=True)
class LifecycleApplicationResult:
    """The persisted Decision plus terminal Change observations."""

    decision: LifecycleDecisionAuditRecord
    changes: tuple[LifecycleChangeAuditRecord, ...]

    @property
    def apply_state(self) -> LifecycleApplyState:
        states = {change.apply_state for change in self.changes}
        if len(states) != 1:
            raise LifecycleApplicationConflict("terminal changes disagree on apply_state")
        return next(iter(states))


@dataclass(frozen=True)
class _StaleTransition(Exception):
    snapshot: LifecycleCandidateSnapshot
    expected_row_version: int


@dataclass(frozen=True)
class _RecomputeBlocked(Exception):
    error_code: str


class LifecycleApplier:
    """Apply one validated Decision inside a caller-owned transaction."""

    def __init__(
        self,
        connection: AsyncConnection,
        *,
        memory_repository: LearningMemoryRepository,
        audit_repository: LifecycleAuditRepository,
        event_repository: LearningEventRepository,
    ) -> None:
        self._connection = connection
        self._memory_repository = memory_repository
        self._audit_repository = audit_repository
        self._event_repository = event_repository

    async def apply(
        self,
        policy_input: LifecyclePolicyInput,
        policy_result: LifecyclePolicyResult,
        *,
        decision_id: str,
        trace_id: str,
        applied_at: datetime,
    ) -> LifecycleApplicationResult:
        """Apply once and deterministically recompute a stale Decision at most twice."""
        if applied_at.tzinfo is None or applied_at.utcoffset() is None:
            raise ValueError("applied_at must include timezone information")
        current_input = policy_input
        current_result = policy_result
        current_decision_id = decision_id
        maximum_recomputations = policy_input.config.maximum_cas_recomputations

        for recomputation_count in range(maximum_recomputations + 1):
            application = await self._apply_once(
                current_input,
                current_result,
                decision_id=current_decision_id,
                trace_id=trace_id,
                applied_at=applied_at,
            )
            if application.apply_state is not LifecycleApplyState.STALE:
                return application
            if recomputation_count == maximum_recomputations:
                return await self._mark_failed(
                    application.decision,
                    error_code="cas_recompute_exhausted",
                    recorded_at=applied_at,
                )

            next_decision_id = f"{decision_id}:recompute:{recomputation_count + 1}"
            existing_next = await self._audit_repository.get_decision(next_decision_id)
            if existing_next is not None:
                if existing_next.trace_id != trace_id:
                    raise LifecycleApplicationConflict(
                        "recomputed decision trace_id conflicts with retry chain"
                    )
                current_input = existing_next.policy_input
                current_result = existing_next.policy_result
            else:
                try:
                    current_input, current_result = await self._recompute(
                        current_input,
                        applied_at=applied_at,
                    )
                except _RecomputeBlocked as blocked:
                    return await self._mark_failed(
                        application.decision,
                        error_code=blocked.error_code,
                        recorded_at=applied_at,
                    )
                except ValueError:
                    return await self._mark_failed(
                        application.decision,
                        error_code="cas_recompute_input_invalid",
                        recorded_at=applied_at,
                    )
            current_decision_id = next_decision_id

        raise AssertionError("bounded recomputation loop must return")

    async def _apply_once(
        self,
        policy_input: LifecyclePolicyInput,
        policy_result: LifecyclePolicyResult,
        *,
        decision_id: str,
        trace_id: str,
        applied_at: datetime,
    ) -> LifecycleApplicationResult:
        """Persist one Decision, mutation attempt, and terminal observation atomically."""
        _validate_operation_shape(policy_input, policy_result)
        decision_record = LifecycleDecisionAuditRecord(
            decision_id=decision_id,
            trace_id=trace_id,
            policy_input=policy_input,
            policy_result=policy_result,
            created_at=applied_at,
        )

        async with self._connection.begin_nested():
            decision_status = await self._audit_repository.append_decision(decision_record)
            if decision_status is AuditAppendStatus.CONFLICT:
                raise LifecycleApplicationConflict("decision_id conflicts with existing audit")
            if decision_status is AuditAppendStatus.EXISTING:
                return await self._existing_result(decision_record)

            planned = _change_record(
                decision=decision_record,
                suffix="planned",
                apply_state=LifecycleApplyState.PLANNED,
                recorded_at=applied_at,
            )
            _require_created(await self._audit_repository.append_change(planned), "planned change")

            try:
                async with self._connection.begin_nested():
                    terminal_changes = await self._apply_operation(
                        decision_record,
                        applied_at=applied_at,
                    )
                    for change in terminal_changes:
                        _require_created(
                            await self._audit_repository.append_change(change),
                            "terminal change",
                        )
            except _StaleTransition as stale:
                actual = await self._memory_repository.get_lifecycle_snapshot(
                    policy_result.scope,
                    stale.snapshot.memory.memory_id,
                )
                if actual is None:
                    raise LifecycleApplicationConflict(
                        "stale target disappeared from append-only L2"
                    ) from stale
                terminal_changes = (
                    _change_record(
                        decision=decision_record,
                        suffix=f"stale:{stale.snapshot.memory.memory_id}",
                        apply_state=LifecycleApplyState.STALE,
                        recorded_at=applied_at,
                        memory_id=stale.snapshot.memory.memory_id,
                        before_state=stale.snapshot,
                        after_state=actual,
                        expected_row_version=stale.expected_row_version,
                        actual_row_version=actual.row_version,
                    ),
                )
                _require_created(
                    await self._audit_repository.append_change(terminal_changes[0]),
                    "stale change",
                )

            return LifecycleApplicationResult(
                decision=decision_record,
                changes=terminal_changes,
            )

    async def _recompute(
        self,
        policy_input: LifecyclePolicyInput,
        *,
        applied_at: datetime,
    ) -> tuple[LifecyclePolicyInput, LifecyclePolicyResult]:
        query = build_candidate_query(
            scope=policy_input.candidate.scope,
            slot_key=policy_input.candidate.slot_key,
            match_reason=CandidateMatchReason.EXACT_SLOT,
        )
        snapshots = tuple(await self._memory_repository.find_candidate_snapshots(query))
        event_was_applied = await self._memory_repository.event_was_applied(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
            policy_input.event.event_id,
        )
        if event_was_applied:
            rebased_input = _rebase_policy_input(
                policy_input,
                snapshots=snapshots,
                relation=None,
                evaluated_at=applied_at,
            )
            replay_targets = tuple(
                snapshot
                for snapshot in snapshots
                if policy_input.event.event_id in snapshot.memory.provenance
            )
            return rebased_input, _idempotent_replay_result(
                rebased_input,
                targets=replay_targets,
            )

        relation = policy_input.relation
        snapshot_ids = {snapshot.memory.memory_id for snapshot in snapshots}
        if relation is not None and relation.target_memory_id not in snapshot_ids:
            raise _RecomputeBlocked("relation_reclassification_required")
        historical_events = policy_input.historical_events
        if policy_input.candidate.scope.memory_namespace is MemoryNamespace.MASTERY:
            required_event_ids = {
                event_id
                for snapshot in snapshots
                for event_id in snapshot.memory.provenance
                if event_id != policy_input.event.event_id
            }
            historical_by_id = {event.event_id: event for event in policy_input.historical_events}
            missing_event_ids = sorted(required_event_ids - set(historical_by_id))
            fetched = await self._event_repository.get_by_ids(
                policy_input.event.context,
                missing_event_ids,
            )
            historical_by_id.update({event.event_id: event for event in fetched})
            historical_events = tuple(
                historical_by_id[event_id] for event_id in sorted(historical_by_id)
            )
        rebased_input = _rebase_policy_input(
            policy_input,
            snapshots=snapshots,
            relation=relation,
            evaluated_at=applied_at,
            historical_events=historical_events,
        )
        return rebased_input, decide_lifecycle(rebased_input)

    async def _mark_failed(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        error_code: str,
        recorded_at: datetime,
    ) -> LifecycleApplicationResult:
        failed = _change_record(
            decision=decision,
            suffix=f"failed:{error_code}",
            apply_state=LifecycleApplyState.FAILED,
            recorded_at=recorded_at,
            error_code=error_code,
        )
        async with self._connection.begin_nested():
            status = await self._audit_repository.append_change(failed)
            if status is AuditAppendStatus.CONFLICT:
                raise LifecycleApplicationConflict("failed change audit identity conflicts")
        return LifecycleApplicationResult(decision=decision, changes=(failed,))

    async def _existing_result(
        self,
        decision: LifecycleDecisionAuditRecord,
    ) -> LifecycleApplicationResult:
        changes = await self._audit_repository.list_changes_by_decision(decision.decision_id)
        terminal = tuple(
            change for change in changes if change.apply_state is not LifecycleApplyState.PLANNED
        )
        if not terminal:
            raise LifecycleApplicationConflict("existing decision has no terminal change")
        failed = tuple(
            change for change in terminal if change.apply_state is LifecycleApplyState.FAILED
        )
        if failed:
            return LifecycleApplicationResult(decision=decision, changes=failed)
        successful_states = {
            LifecycleApplyState.APPLIED,
            LifecycleApplyState.CONTESTED,
            LifecycleApplyState.IDEMPOTENT,
        }
        successful = tuple(change for change in terminal if change.apply_state in successful_states)
        return LifecycleApplicationResult(
            decision=decision,
            changes=successful or terminal,
        )

    async def _apply_operation(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> tuple[LifecycleChangeAuditRecord, ...]:
        operation = decision.policy_result.decision.operation
        if operation is LifecycleOperation.ADD:
            return (await self._apply_add(decision, applied_at=applied_at),)
        if operation is LifecycleOperation.NO_OP:
            return self._apply_no_op(decision, applied_at=applied_at)
        if operation in {LifecycleOperation.MERGE, LifecycleOperation.SUPERSEDE}:
            return await self._apply_replacement(decision, applied_at=applied_at)
        if operation is LifecycleOperation.INVALIDATE:
            return (await self._apply_invalidate(decision, applied_at=applied_at),)
        if operation is LifecycleOperation.CONTESTED:
            return await self._apply_contested(decision, applied_at=applied_at)
        raise LifecycleApplicationConflict(f"unsupported operation {operation.value!r}")

    async def _apply_add(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> LifecycleChangeAuditRecord:
        policy_input = decision.policy_input
        version = await self._memory_repository.next_version(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
        )
        memory = _new_memory(
            memory_id=_new_memory_id(decision.decision_id, version),
            policy_input=policy_input,
            confidence=decision.policy_result.decision.confidence,
            version=version,
            lifecycle_state=LifecycleState.ACTIVE,
            applied_at=applied_at,
            provenance=(policy_input.event.event_id,),
        )
        after = await self._memory_repository.insert_version(
            memory,
            policy_version=decision.policy_result.decision.policy_version,
        )
        return _change_record(
            decision=decision,
            suffix=f"applied:{memory.memory_id}",
            apply_state=LifecycleApplyState.APPLIED,
            recorded_at=applied_at,
            memory_id=memory.memory_id,
            after_state=after,
            actual_row_version=after.row_version,
        )

    def _apply_no_op(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> tuple[LifecycleChangeAuditRecord, ...]:
        targets = _target_snapshots(decision)
        is_replay = decision.policy_result.decision.reason_code == "already_applied_replay"
        apply_state = LifecycleApplyState.IDEMPOTENT if is_replay else LifecycleApplyState.APPLIED
        if not targets:
            apply_state = LifecycleApplyState.IDEMPOTENT
            return (
                _change_record(
                    decision=decision,
                    suffix=apply_state.value.lower(),
                    apply_state=apply_state,
                    recorded_at=applied_at,
                ),
            )
        return tuple(
            _change_record(
                decision=decision,
                suffix=f"{apply_state.value.lower()}:{target.memory.memory_id}",
                apply_state=apply_state,
                recorded_at=applied_at,
                memory_id=target.memory.memory_id,
                before_state=target,
                after_state=target,
                actual_row_version=target.row_version,
            )
            for target in targets
        )

    async def _apply_replacement(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> tuple[LifecycleChangeAuditRecord, ...]:
        policy_input = decision.policy_input
        operation = decision.policy_result.decision.operation
        targets = _target_snapshots(decision)
        version = await self._memory_repository.next_version(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
        )
        memory_id = _new_memory_id(decision.decision_id, version)
        new_state, group_id = _replacement_state(operation, targets)
        provenance = _combined_provenance(targets, policy_input.event.event_id)
        value = _replacement_value(policy_input, operation, targets)
        memory = _new_memory(
            memory_id=memory_id,
            policy_input=policy_input,
            confidence=decision.policy_result.decision.confidence,
            version=version,
            lifecycle_state=new_state,
            applied_at=applied_at,
            provenance=provenance,
            value=value,
        )

        changes: list[LifecycleChangeAuditRecord] = []
        for target in targets:
            expected = decision.policy_result.expected_row_versions[target.memory.memory_id]
            after = await self._memory_repository.cas_transition(
                policy_input.candidate.scope,
                policy_input.candidate.slot_key,
                target.memory.memory_id,
                expected_row_version=expected,
                to_state=LifecycleState.ARCHIVED,
                valid_to=applied_at,
                superseded_by=memory_id,
                contested_group_id=target.contested_group_id,
            )
            if after is None:
                self._raise_stale(target, expected)
            changes.append(
                _change_record(
                    decision=decision,
                    suffix=f"applied:{target.memory.memory_id}",
                    apply_state=LifecycleApplyState.APPLIED,
                    recorded_at=applied_at,
                    memory_id=target.memory.memory_id,
                    before_state=target,
                    after_state=after,
                    expected_row_version=expected,
                    actual_row_version=after.row_version,
                )
            )

        after = await self._memory_repository.insert_version(
            memory,
            policy_version=decision.policy_result.decision.policy_version,
            contested_group_id=group_id,
            provenance_relations={
                event_id: (
                    "created_by" if event_id == policy_input.event.event_id else "merged_from"
                )
                for event_id in provenance
            },
        )
        changes.append(
            _change_record(
                decision=decision,
                suffix=f"applied:{memory_id}",
                apply_state=LifecycleApplyState.APPLIED,
                recorded_at=applied_at,
                memory_id=memory_id,
                after_state=after,
                actual_row_version=after.row_version,
            )
        )
        return tuple(changes)

    async def _apply_invalidate(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> LifecycleChangeAuditRecord:
        policy_input = decision.policy_input
        target = _target_snapshots(decision)[0]
        expected = decision.policy_result.expected_row_versions[target.memory.memory_id]
        after = await self._memory_repository.cas_transition(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
            target.memory.memory_id,
            expected_row_version=expected,
            to_state=LifecycleState.INVALIDATED,
            valid_to=applied_at,
            contested_group_id=target.contested_group_id,
            provenance_event_id=policy_input.event.event_id,
            provenance_relation="invalidated_by",
        )
        if after is None:
            self._raise_stale(target, expected)
        return _change_record(
            decision=decision,
            suffix=f"applied:{target.memory.memory_id}",
            apply_state=LifecycleApplyState.APPLIED,
            recorded_at=applied_at,
            memory_id=target.memory.memory_id,
            before_state=target,
            after_state=after,
            expected_row_version=expected,
            actual_row_version=after.row_version,
        )

    async def _apply_contested(
        self,
        decision: LifecycleDecisionAuditRecord,
        *,
        applied_at: datetime,
    ) -> tuple[LifecycleChangeAuditRecord, ...]:
        policy_input = decision.policy_input
        target = _target_snapshots(decision)[0]
        expected = decision.policy_result.expected_row_versions[target.memory.memory_id]
        group_id = target.contested_group_id or f"{decision.decision_id}:contested"
        current_after = await self._memory_repository.cas_transition(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
            target.memory.memory_id,
            expected_row_version=expected,
            to_state=LifecycleState.ACTIVE,
            valid_to=None,
            contested_group_id=group_id,
            provenance_event_id=policy_input.event.event_id,
            provenance_relation="contradicted_by",
        )
        if current_after is None:
            self._raise_stale(target, expected)

        version = await self._memory_repository.next_version(
            policy_input.candidate.scope,
            policy_input.candidate.slot_key,
        )
        branch = _new_memory(
            memory_id=_new_memory_id(decision.decision_id, version),
            policy_input=policy_input,
            confidence=decision.policy_result.decision.confidence,
            version=version,
            lifecycle_state=LifecycleState.CONTESTED,
            applied_at=applied_at,
            provenance=(policy_input.event.event_id,),
        )
        branch_after = await self._memory_repository.insert_version(
            branch,
            policy_version=decision.policy_result.decision.policy_version,
            contested_group_id=group_id,
            provenance_relations={policy_input.event.event_id: "contradicted_by"},
        )
        return (
            _change_record(
                decision=decision,
                suffix=f"contested:{target.memory.memory_id}",
                apply_state=LifecycleApplyState.CONTESTED,
                recorded_at=applied_at,
                memory_id=target.memory.memory_id,
                before_state=target,
                after_state=current_after,
                expected_row_version=expected,
                actual_row_version=current_after.row_version,
            ),
            _change_record(
                decision=decision,
                suffix=f"contested:{branch.memory_id}",
                apply_state=LifecycleApplyState.CONTESTED,
                recorded_at=applied_at,
                memory_id=branch.memory_id,
                after_state=branch_after,
                actual_row_version=branch_after.row_version,
            ),
        )

    @staticmethod
    def _raise_stale(
        target: LifecycleCandidateSnapshot,
        expected: int,
    ) -> NoReturn:
        raise _StaleTransition(target, expected)


def _validate_operation_shape(
    policy_input: LifecyclePolicyInput,
    policy_result: LifecyclePolicyResult,
) -> None:
    operation = policy_result.decision.operation
    target_ids = tuple(policy_result.decision.target_memory_ids)
    expected_ids = set(policy_result.expected_row_versions)
    mutating_target_operations = {
        LifecycleOperation.MERGE,
        LifecycleOperation.SUPERSEDE,
        LifecycleOperation.INVALIDATE,
        LifecycleOperation.CONTESTED,
    }
    if operation is LifecycleOperation.ADD:
        if target_ids or policy_input.candidate_snapshots:
            raise LifecycleApplicationConflict("ADD requires an empty candidate pool")
    elif operation in mutating_target_operations:
        if not target_ids or expected_ids != set(target_ids):
            raise LifecycleApplicationConflict(
                "mutating target operation requires one CAS version per target"
            )
    elif operation is LifecycleOperation.NO_OP:
        if expected_ids:
            raise LifecycleApplicationConflict("NO_OP must not carry CAS versions")

    if (
        operation in {LifecycleOperation.INVALIDATE, LifecycleOperation.CONTESTED}
        and len(target_ids) != 1
    ):
        raise LifecycleApplicationConflict(f"{operation.value} requires exactly one target")

    target_snapshots = _target_snapshots_from_input(policy_input, target_ids)
    if operation is LifecycleOperation.CONTESTED and (
        target_snapshots[0].memory.lifecycle_state is not LifecycleState.ACTIVE
    ):
        raise LifecycleApplicationConflict("CONTESTED requires the current active target")
    if operation in {LifecycleOperation.MERGE, LifecycleOperation.SUPERSEDE}:
        produces_active = not (
            operation is LifecycleOperation.MERGE
            and len(target_snapshots) == 1
            and target_snapshots[0].memory.lifecycle_state is LifecycleState.CONTESTED
        )
        active_ids = {
            snapshot.memory.memory_id
            for snapshot in policy_input.candidate_snapshots
            if snapshot.memory.lifecycle_state is LifecycleState.ACTIVE
        }
        if produces_active and not active_ids.issubset(target_ids):
            raise LifecycleApplicationConflict(
                "active replacement must transition every current active candidate"
            )

    if operation in mutating_target_operations:
        current_event_id = policy_input.event.event_id
        if any(current_event_id in snapshot.memory.provenance for snapshot in target_snapshots):
            raise LifecycleApplicationConflict(
                "mutating decision cannot reapply an existing provenance event"
            )


def _target_snapshots(
    decision: LifecycleDecisionAuditRecord,
) -> tuple[LifecycleCandidateSnapshot, ...]:
    return _target_snapshots_from_input(
        decision.policy_input,
        tuple(decision.policy_result.decision.target_memory_ids),
    )


def _target_snapshots_from_input(
    policy_input: LifecyclePolicyInput,
    target_ids: tuple[str, ...],
) -> tuple[LifecycleCandidateSnapshot, ...]:
    by_id = {snapshot.memory.memory_id: snapshot for snapshot in policy_input.candidate_snapshots}
    try:
        return tuple(by_id[memory_id] for memory_id in target_ids)
    except KeyError as exc:
        raise LifecycleApplicationConflict("decision target is not authoritative") from exc


def _replacement_state(
    operation: LifecycleOperation,
    targets: tuple[LifecycleCandidateSnapshot, ...],
) -> tuple[LifecycleState, str | None]:
    if operation is LifecycleOperation.MERGE and len(targets) == 1:
        target = targets[0]
        if target.contested_group_id is not None:
            return target.memory.lifecycle_state, target.contested_group_id
    return LifecycleState.ACTIVE, None


def _replacement_value(
    policy_input: LifecyclePolicyInput,
    operation: LifecycleOperation,
    targets: tuple[LifecycleCandidateSnapshot, ...],
) -> MemoryValue:
    namespace = policy_input.candidate.scope.memory_namespace
    candidate_value = policy_input.candidate.proposed_value
    if namespace is MemoryNamespace.ERROR_PATTERN and operation is LifecycleOperation.MERGE:
        current_value = targets[0].memory.value
        if not isinstance(current_value, ErrorPatternValue) or not isinstance(
            candidate_value, ErrorPatternValue
        ):
            raise LifecycleApplicationConflict("error-pattern merge requires typed values")
        details = list(dict.fromkeys([*current_value.details, *candidate_value.details]))
        return current_value.model_copy(update={"details": details})

    if namespace is MemoryNamespace.MASTERY:
        if operation is LifecycleOperation.MERGE:
            active = next(
                (
                    target
                    for target in targets
                    if target.memory.lifecycle_state is LifecycleState.ACTIVE
                ),
                targets[0],
            )
            return active.memory.value
        contested = next(
            (
                target
                for target in targets
                if target.memory.lifecycle_state is LifecycleState.CONTESTED
            ),
            None,
        )
        if contested is not None:
            if not isinstance(contested.memory.value, MasteryValue):
                raise LifecycleApplicationConflict("mastery target requires MasteryValue")
            return contested.memory.value
    return candidate_value


def _combined_provenance(
    targets: tuple[LifecycleCandidateSnapshot, ...],
    current_event_id: str,
) -> tuple[str, ...]:
    event_ids = [event_id for target in targets for event_id in target.memory.provenance]
    event_ids.append(current_event_id)
    return tuple(dict.fromkeys(event_ids))


def _new_memory(
    *,
    memory_id: str,
    policy_input: LifecyclePolicyInput,
    confidence: float,
    version: int,
    lifecycle_state: LifecycleState,
    applied_at: datetime,
    provenance: tuple[str, ...],
    value: MemoryValue | None = None,
) -> LearningMemory:
    return LearningMemory(
        memory_id=memory_id,
        scope=policy_input.candidate.scope,
        slot_key=policy_input.candidate.slot_key,
        value=value if value is not None else policy_input.candidate.proposed_value,
        confidence=confidence,
        evidence_count=len(provenance),
        lifecycle_state=lifecycle_state,
        version=version,
        valid_from=applied_at,
        valid_to=None,
        superseded_by=None,
        provenance=list(provenance),
    )


def _new_memory_id(decision_id: str, version: int) -> str:
    return f"{decision_id}:memory:v{version}"


def _rebase_policy_input(
    policy_input: LifecyclePolicyInput,
    *,
    snapshots: tuple[LifecycleCandidateSnapshot, ...],
    relation: ResolvedRelationClassification | None,
    evaluated_at: datetime,
    historical_events: tuple[LearningEvent, ...] | None = None,
) -> LifecyclePolicyInput:
    return LifecyclePolicyInput.model_validate(
        {
            **policy_input.model_dump(mode="python"),
            "candidate_snapshots": snapshots,
            "relation": relation,
            "evaluated_at": evaluated_at,
            "historical_events": (
                historical_events
                if historical_events is not None
                else policy_input.historical_events
            ),
        }
    )


def _idempotent_replay_result(
    policy_input: LifecyclePolicyInput,
    *,
    targets: tuple[LifecycleCandidateSnapshot, ...],
) -> LifecyclePolicyResult:
    ordered = sorted(
        targets,
        key=lambda snapshot: (snapshot.memory.version, snapshot.memory.memory_id),
    )
    return LifecyclePolicyResult(
        event_id=policy_input.event.event_id,
        scope=policy_input.candidate.scope,
        slot_key=policy_input.candidate.slot_key,
        decision=LifecycleDecision(
            operation=LifecycleOperation.NO_OP,
            target_memory_ids=[snapshot.memory.memory_id for snapshot in ordered],
            reason_code="already_applied_replay",
            confidence=1.0,
            policy_version=policy_input.config.policy_version,
        ),
        expected_row_versions={},
    )


def _change_record(
    *,
    decision: LifecycleDecisionAuditRecord,
    suffix: str,
    apply_state: LifecycleApplyState,
    recorded_at: datetime,
    memory_id: str | None = None,
    before_state: LifecycleMemorySnapshot | None = None,
    after_state: LifecycleMemorySnapshot | None = None,
    expected_row_version: int | None = None,
    actual_row_version: int | None = None,
    error_code: str | None = None,
) -> LifecycleChangeAuditRecord:
    return LifecycleChangeAuditRecord(
        change_id=f"{decision.decision_id}:{suffix}",
        decision_id=decision.decision_id,
        trace_id=decision.trace_id,
        apply_state=apply_state,
        memory_id=memory_id,
        before_state=before_state,
        after_state=after_state,
        expected_row_version=expected_row_version,
        actual_row_version=actual_row_version,
        error_code=error_code,
        recorded_at=recorded_at,
    )


def _require_created(status: AuditAppendStatus, kind: str) -> None:
    if status is not AuditAppendStatus.CREATED:
        raise LifecycleApplicationConflict(f"{kind} audit identity is not new")


__all__ = [
    "LifecycleApplicationConflict",
    "LifecycleApplicationResult",
    "LifecycleApplier",
]
