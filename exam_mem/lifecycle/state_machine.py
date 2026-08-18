"""Deterministic dispatcher for the Stage 06 lifecycle transition table."""

from __future__ import annotations

from collections.abc import Iterable

from exam_mem.contracts import (
    ErrorPatternValue,
    ErrorType,
    LearningEvent,
    LearningEventType,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MasteryValue,
    MemoryNamespace,
    MemoryUpdateCandidate,
    PlanStatus,
    PlanTransitionSource,
    PlanValue,
)
from exam_mem.lifecycle.contracts import (
    LifecycleCandidateSnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    MemoryRelation,
)
from exam_mem.lifecycle.policy_v1 import evaluate_mastery_policy


def decide_lifecycle(policy_input: LifecyclePolicyInput) -> LifecyclePolicyResult:
    """Return one pure lifecycle decision without reading or writing storage."""
    replay_targets = _find_replay_targets(policy_input)
    if replay_targets:
        return _result(
            policy_input,
            operation=LifecycleOperation.NO_OP,
            reason_code="already_applied_replay",
            confidence=1.0,
            targets=replay_targets,
            include_expected_versions=False,
        )

    if policy_input.event.event_type is LearningEventType.EXPLICIT_CORRECTION:
        return _decide_correction(policy_input)
    if policy_input.event.event_type is LearningEventType.PLAN_TRANSITION:
        return _decide_plan(policy_input)

    no_change_reason = non_mutating_answer_reason(
        policy_input.event,
        policy_input.candidate,
        has_candidate_snapshots=bool(policy_input.candidate_snapshots),
        minimum_confidence=policy_input.config.minimum_candidate_confidence,
    )
    if no_change_reason is not None:
        return _result(
            policy_input,
            operation=LifecycleOperation.NO_OP,
            reason_code=no_change_reason,
            confidence=policy_input.event.evidence_quality.confidence,
            targets=tuple(policy_input.candidate_snapshots),
            include_expected_versions=False,
        )

    if not policy_input.candidate_snapshots:
        return _result(
            policy_input,
            operation=LifecycleOperation.ADD,
            reason_code="new_slot_without_current_memory",
            confidence=policy_input.event.evidence_quality.confidence,
        )

    namespace = policy_input.candidate.scope.memory_namespace
    if namespace is MemoryNamespace.MASTERY:
        same_direction = _decide_same_mastery_evidence(policy_input)
        if same_direction is not None:
            return same_direction
        return evaluate_mastery_policy(policy_input).result
    if namespace is MemoryNamespace.ERROR_PATTERN:
        return _decide_error_pattern(policy_input)
    if namespace is MemoryNamespace.PLAN:
        raise ValueError("existing plan changes require a plan_transition event")
    raise ValueError(f"lifecycle_policy_v1 has no update rule for {namespace.value!r}")


def non_mutating_answer_reason(
    event: LearningEvent,
    candidate: MemoryUpdateCandidate,
    *,
    has_candidate_snapshots: bool,
    minimum_confidence: float,
) -> str | None:
    """Return the frozen S05 reason when answer evidence must remain L1-only."""
    if event.event_type is not LearningEventType.ANSWER_ATTEMPT:
        return None
    if event.evidence_quality.is_temporary_exception:
        return "temporary_exception_no_change"
    if event.evidence_quality.confidence < minimum_confidence:
        return "isolated_low_confidence_no_change"
    namespace = candidate.scope.memory_namespace
    if namespace is MemoryNamespace.MASTERY and event.error_type is ErrorType.CARELESS_ERROR:
        return "careless_error_does_not_change_mastery"
    if (
        namespace is MemoryNamespace.ERROR_PATTERN
        and event.error_type is ErrorType.CARELESS_ERROR
        and not has_candidate_snapshots
    ):
        return "isolated_careless_error_no_pattern"
    return None


def _decide_same_mastery_evidence(
    policy_input: LifecyclePolicyInput,
) -> LifecyclePolicyResult | None:
    snapshots = policy_input.candidate_snapshots
    if len(snapshots) != 1:
        return None
    target = snapshots[0]
    if target.memory.lifecycle_state is not LifecycleState.ACTIVE:
        return None
    target_value = target.memory.value
    candidate_value = policy_input.candidate.proposed_value
    if not isinstance(target_value, MasteryValue) or not isinstance(
        candidate_value,
        MasteryValue,
    ):
        raise ValueError("mastery slot requires MasteryValue")
    if target_value.score != candidate_value.score:
        return None
    relation = policy_input.relation
    if relation is None or relation.target_memory_id != target.memory.memory_id:
        raise ValueError("same-direction mastery evidence requires a resolved target")
    if relation.classification.relation is not MemoryRelation.DUPLICATE:
        raise ValueError("same-direction mastery evidence requires duplicate relation")
    canonical_id = policy_input.candidate.slot_key.split(":", 1)[1]
    classified_id = relation.classification.canonical_knowledge_point_id
    if classified_id is not None and classified_id != canonical_id:
        raise ValueError("relation canonical knowledge point must match mastery slot")
    return _result(
        policy_input,
        operation=LifecycleOperation.MERGE,
        reason_code="independent_duplicate_evidence",
        confidence=relation.classification.confidence,
        targets=(target,),
        include_expected_versions=True,
    )


def _find_replay_targets(
    policy_input: LifecyclePolicyInput,
) -> tuple[LifecycleCandidateSnapshot, ...]:
    event = policy_input.event
    historical_by_id = {item.event_id: item for item in policy_input.historical_events}
    targets: list[LifecycleCandidateSnapshot] = []
    for snapshot in policy_input.candidate_snapshots:
        provenance = snapshot.memory.provenance
        same_event_applied = event.event_id in provenance
        same_idempotency_key_applied = any(
            historical_by_id[provenance_event_id].idempotency_key == event.idempotency_key
            for provenance_event_id in provenance
            if provenance_event_id in historical_by_id
        )
        if same_event_applied or same_idempotency_key_applied:
            targets.append(snapshot)
    return tuple(_ordered_snapshots(targets))


def _decide_error_pattern(policy_input: LifecyclePolicyInput) -> LifecyclePolicyResult:
    event = policy_input.event
    candidate_value = policy_input.candidate.proposed_value
    if event.event_type is not LearningEventType.ANSWER_ATTEMPT:
        raise ValueError("error-pattern evidence requires an answer_attempt event")
    if not isinstance(candidate_value, ErrorPatternValue):
        raise ValueError("error-pattern policy requires ErrorPatternValue candidate")
    if event.error_type is not candidate_value.error_type:
        raise ValueError("event error_type must match candidate error_type")

    slot_parts = policy_input.candidate.slot_key.split(":")
    if len(slot_parts) != 3 or slot_parts[2] != candidate_value.error_type.value:
        raise ValueError("error-pattern value must match the slot error_type")

    target = _resolved_target(policy_input, purpose="error-pattern update")
    target_value = target.memory.value
    if not isinstance(target_value, ErrorPatternValue):
        raise ValueError("error-pattern target must contain ErrorPatternValue")
    if target_value.error_type is not candidate_value.error_type:
        raise ValueError("different error types must use independent slots")

    classification = policy_input.relation.classification
    if (
        classification.error_type is not None
        and classification.error_type is not candidate_value.error_type
    ):
        raise ValueError("classified error_type must match the candidate slot")
    canonical_id = slot_parts[1]
    if (
        classification.canonical_knowledge_point_id is not None
        and classification.canonical_knowledge_point_id != canonical_id
    ):
        raise ValueError("classified knowledge point must match the candidate slot")

    relation_rules = {
        MemoryRelation.DUPLICATE: "independent_duplicate_evidence",
        MemoryRelation.COMPLEMENTARY: "complementary_error_detail",
    }
    try:
        reason_code = relation_rules[classification.relation]
    except KeyError as exc:
        raise ValueError(
            "error-pattern update requires duplicate or complementary relation"
        ) from exc
    return _result(
        policy_input,
        operation=LifecycleOperation.MERGE,
        reason_code=reason_code,
        confidence=classification.confidence,
        targets=(target,),
        include_expected_versions=True,
    )


def _decide_correction(policy_input: LifecyclePolicyInput) -> LifecyclePolicyResult:
    event = policy_input.event
    correction = event.correction
    if correction is None:
        raise ValueError("explicit_correction event requires correction payload")
    if len(correction.target_memory_ids) != 1:
        raise ValueError("explicit correction requires exactly one target")

    target = _snapshot_by_id(policy_input, correction.target_memory_ids[0])
    relation = policy_input.relation
    if relation is None or relation.target_memory_id != target.memory.memory_id:
        raise ValueError("explicit correction requires a relation resolved to its target")
    if relation.classification.relation is not MemoryRelation.CONTRADICTORY:
        raise ValueError("explicit correction requires contradictory relation")

    confidence = min(
        event.evidence_quality.confidence,
        relation.classification.confidence,
    )
    if (
        event.evidence_quality.is_temporary_exception
        or confidence < policy_input.config.minimum_candidate_confidence
    ):
        operation = LifecycleOperation.CONTESTED
        reason_code = "uncertain_explicit_correction"
    elif policy_input.candidate.proposed_value == target.memory.value:
        operation = LifecycleOperation.INVALIDATE
        reason_code = "correction_invalidates_false_memory"
    else:
        operation = LifecycleOperation.SUPERSEDE
        reason_code = "correction_supplies_replacement"

    return _result(
        policy_input,
        operation=operation,
        reason_code=reason_code,
        confidence=confidence,
        targets=(target,),
        include_expected_versions=True,
    )


def _decide_plan(policy_input: LifecyclePolicyInput) -> LifecyclePolicyResult:
    event = policy_input.event
    transition = event.plan_transition
    candidate_value = policy_input.candidate.proposed_value
    if transition is None:
        raise ValueError("plan_transition event requires transition payload")
    if policy_input.candidate.scope.memory_namespace is not MemoryNamespace.PLAN:
        raise ValueError("plan_transition target must use plan namespace")
    if not isinstance(candidate_value, PlanValue):
        raise ValueError("plan policy requires PlanValue candidate")
    if candidate_value.status is not transition.to_status:
        raise ValueError("candidate plan status must match transition status")

    target = _snapshot_by_id(policy_input, transition.target_memory_id)
    target_value = target.memory.value
    if not isinstance(target_value, PlanValue):
        raise ValueError("plan transition target must contain PlanValue")

    confidence = event.evidence_quality.confidence
    if (
        transition.to_status is PlanStatus.CANCELLED
        and transition.source is PlanTransitionSource.USER
        and (
            event.evidence_quality.is_temporary_exception
            or confidence < policy_input.config.minimum_candidate_confidence
        )
    ):
        return _result(
            policy_input,
            operation=LifecycleOperation.CONTESTED,
            reason_code="ambiguous_user_plan_cancellation",
            confidence=confidence,
            targets=(target,),
            include_expected_versions=True,
        )

    terminal_sources = {
        PlanStatus.COMPLETED: PlanTransitionSource.PRACTICE_PROGRESS,
        PlanStatus.CANCELLED: PlanTransitionSource.USER,
        PlanStatus.EXPIRED: PlanTransitionSource.SYSTEM,
    }
    expected_source = terminal_sources.get(transition.to_status)
    if expected_source is not None:
        if transition.source is not expected_source:
            raise ValueError("terminal plan status does not match transition source")
        return _result(
            policy_input,
            operation=LifecycleOperation.INVALIDATE,
            reason_code=f"plan_{transition.to_status.value}",
            confidence=confidence,
            targets=(target,),
            include_expected_versions=True,
        )

    if transition.to_status not in {PlanStatus.PLANNED, PlanStatus.IN_PROGRESS}:
        raise ValueError("unsupported plan transition status")
    if transition.source is PlanTransitionSource.SYSTEM:
        raise ValueError("system cannot directly create an active plan transition")

    replacement = (
        candidate_value.goal != target_value.goal or candidate_value.due_at != target_value.due_at
    )
    return _result(
        policy_input,
        operation=(LifecycleOperation.SUPERSEDE if replacement else LifecycleOperation.MERGE),
        reason_code=(
            "plan_goal_or_due_replaced" if replacement else "plan_progress_or_evidence_merged"
        ),
        confidence=confidence,
        targets=(target,),
        include_expected_versions=True,
    )


def _resolved_target(
    policy_input: LifecyclePolicyInput,
    *,
    purpose: str,
) -> LifecycleCandidateSnapshot:
    relation = policy_input.relation
    if relation is None:
        raise ValueError(f"{purpose} requires a resolved relation")
    return _snapshot_by_id(policy_input, relation.target_memory_id)


def _snapshot_by_id(
    policy_input: LifecyclePolicyInput,
    memory_id: str,
) -> LifecycleCandidateSnapshot:
    matches = [
        snapshot
        for snapshot in policy_input.candidate_snapshots
        if snapshot.memory.memory_id == memory_id
    ]
    if len(matches) != 1:
        raise ValueError(f"target memory {memory_id!r} is not an authoritative candidate")
    return matches[0]


def _ordered_snapshots(
    snapshots: Iterable[LifecycleCandidateSnapshot],
) -> list[LifecycleCandidateSnapshot]:
    return sorted(
        snapshots,
        key=lambda snapshot: (snapshot.memory.version, snapshot.memory.memory_id),
    )


def _result(
    policy_input: LifecyclePolicyInput,
    *,
    operation: LifecycleOperation,
    reason_code: str,
    confidence: float,
    targets: tuple[LifecycleCandidateSnapshot, ...] = (),
    include_expected_versions: bool = False,
) -> LifecyclePolicyResult:
    ordered_targets = _ordered_snapshots(targets)
    target_ids = [snapshot.memory.memory_id for snapshot in ordered_targets]
    expected_row_versions = (
        {snapshot.memory.memory_id: snapshot.row_version for snapshot in ordered_targets}
        if include_expected_versions
        else {}
    )
    return LifecyclePolicyResult(
        event_id=policy_input.event.event_id,
        scope=policy_input.candidate.scope,
        slot_key=policy_input.candidate.slot_key,
        decision=LifecycleDecision(
            operation=operation,
            target_memory_ids=target_ids,
            reason_code=reason_code,
            confidence=confidence,
            policy_version=policy_input.config.policy_version,
        ),
        expected_row_versions=expected_row_versions,
    )


__all__ = ["decide_lifecycle"]
