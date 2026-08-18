"""Pure evidence scoring for the versioned Stage 06 lifecycle policy."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from math import fsum, isfinite
from typing import Annotated, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field, FiniteFloat

from exam_mem.contracts import (
    ErrorType,
    LearningEvent,
    LearningEventType,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
)
from exam_mem.lifecycle.contracts import (
    LifecycleCandidateSnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
    LifecyclePolicyV1Config,
    MemoryRelation,
)

_EVIDENCE_HALF_LIFE_DAYS = 30.0
_SECONDS_PER_DAY = 86_400.0

NonNegativeFiniteFloat = Annotated[FiniteFloat, Field(ge=0.0)]
Probability = Annotated[FiniteFloat, Field(ge=0.0, le=1.0)]
NonNegativeInteger = Annotated[int, Field(ge=0)]


class _FrozenPolicyModel(BaseModel):
    """Immutable JSON-safe output produced only by deterministic code."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class EvidenceDirection(str, Enum):
    """Which side of a mastery dispute one event supports."""

    CURRENT = "current"
    CANDIDATE = "candidate"


class ScoredMasteryEvidence(_FrozenPolicyModel):
    """Auditable components of one mastery evidence calculation."""

    event_id: str
    session_id: str
    direction: EvidenceDirection
    is_qualifying: bool
    exclusion_reason: Literal["temporary_exception"] | None = None
    evidence_confidence: Probability
    difficulty_factor: NonNegativeFiniteFloat
    error_factor: NonNegativeFiniteFloat
    time_decay: Probability
    base_weight: NonNegativeFiniteFloat
    event_mass: NonNegativeFiniteFloat


class DirectionalSupport(_FrozenPolicyModel):
    """Aggregate evidence for one side of a mastery dispute."""

    direction: EvidenceDirection
    event_count: NonNegativeInteger
    session_count: NonNegativeInteger
    mass: NonNegativeFiniteFloat
    confidence: Probability | None
    support: Probability | None


class MasterySupportSummary(_FrozenPolicyModel):
    """Order-independent current/candidate support summary."""

    current: DirectionalSupport
    candidate: DirectionalSupport
    total_mass: NonNegativeFiniteFloat
    support_margin: Annotated[FiniteFloat, Field(ge=-1.0, le=1.0)] | None


class DirectionalStabilityGate(_FrozenPolicyModel):
    """Auditable result of applying all four stable-winner thresholds."""

    policy_version: str
    direction: EvidenceDirection
    event_count: NonNegativeInteger
    minimum_event_count: NonNegativeInteger
    event_count_met: bool
    session_count: NonNegativeInteger
    minimum_session_count: NonNegativeInteger
    session_count_met: bool
    confidence: Probability | None
    minimum_confidence: Probability
    confidence_met: bool
    directional_margin: Annotated[FiniteFloat, Field(ge=-1.0, le=1.0)] | None
    minimum_support_margin: Probability
    margin_met: bool
    is_stable_winner: bool


class MasteryPolicyEvaluation(_FrozenPolicyModel):
    """Mastery decision plus the exact deterministic calculation it used."""

    result: LifecyclePolicyResult
    scored_events: tuple[ScoredMasteryEvidence, ...]
    support_summary: MasterySupportSummary
    current_gate: DirectionalStabilityGate
    candidate_gate: DirectionalStabilityGate


def resolve_mastery_evidence_direction(
    event: LearningEvent,
    *,
    current: MasteryValue,
    candidate: MasteryValue,
) -> EvidenceDirection | None:
    """Map an answer fact to the lower or higher authoritative mastery value."""
    if event.event_type is not LearningEventType.ANSWER_ATTEMPT:
        raise ValueError("mastery evidence direction accepts only answer_attempt events")
    if event.answer_correct is None:
        raise ValueError("answer_attempt mastery evidence requires answer_correct")
    if current.score == candidate.score:
        return None

    candidate_is_higher = candidate.score > current.score
    supports_higher = event.answer_correct
    if candidate_is_higher == supports_higher:
        return EvidenceDirection.CANDIDATE
    return EvidenceDirection.CURRENT


def score_mastery_event(
    event: LearningEvent,
    *,
    direction: EvidenceDirection,
    evaluated_at: datetime,
) -> ScoredMasteryEvidence:
    """Score one answer attempt using the frozen ``lifecycle_policy_v1`` formula."""
    if event.event_type is not LearningEventType.ANSWER_ATTEMPT:
        raise ValueError("mastery evidence scorer accepts only answer_attempt events")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("evaluated_at must be timezone-aware")
    if event.difficulty is None or event.answer_correct is None:
        raise ValueError("answer_attempt mastery evidence is incomplete")
    if not event.answer_correct and event.error_type is None:
        raise ValueError("incorrect answer_attempt mastery evidence requires error_type")

    age_seconds = (evaluated_at - event.occurred_at).total_seconds()
    if not isfinite(age_seconds):
        raise ValueError("evidence age must be finite")
    if age_seconds < 0.0:
        raise ValueError("future evidence cannot be scored")

    evidence_confidence = float(event.evidence_quality.confidence)
    difficulty = float(event.difficulty)
    difficulty_factor = (
        0.75 + 0.50 * difficulty if event.answer_correct else 1.25 - 0.50 * difficulty
    )
    error_factor = _error_factor(event)
    age_days = age_seconds / _SECONDS_PER_DAY
    time_decay = 0.5 ** (age_days / _EVIDENCE_HALF_LIFE_DAYS)
    base_weight = difficulty_factor * error_factor * time_decay

    numeric_components = (
        evidence_confidence,
        difficulty_factor,
        error_factor,
        time_decay,
        base_weight,
    )
    if not all(isfinite(component) and component >= 0.0 for component in numeric_components):
        raise ValueError("mastery evidence produced a non-finite or negative component")

    is_temporary_exception = event.evidence_quality.is_temporary_exception
    event_mass = 0.0 if is_temporary_exception else evidence_confidence * base_weight
    if not isfinite(event_mass) or event_mass < 0.0:
        raise ValueError("mastery evidence produced invalid event_mass")

    return ScoredMasteryEvidence(
        event_id=event.event_id,
        session_id=event.session_id,
        direction=direction,
        is_qualifying=not is_temporary_exception,
        exclusion_reason="temporary_exception" if is_temporary_exception else None,
        evidence_confidence=evidence_confidence,
        difficulty_factor=difficulty_factor,
        error_factor=error_factor,
        time_decay=time_decay,
        base_weight=base_weight,
        event_mass=event_mass,
    )


def aggregate_mastery_support(
    scored_events: Iterable[ScoredMasteryEvidence],
) -> MasterySupportSummary:
    """Deduplicate by event ID and aggregate support without input-order effects."""
    unique_by_event_id: dict[str, ScoredMasteryEvidence] = {}
    for scored in scored_events:
        existing = unique_by_event_id.get(scored.event_id)
        if existing is None:
            unique_by_event_id[scored.event_id] = scored
            continue
        if existing != scored:
            raise ValueError(f"event {scored.event_id!r} has conflicting scores")

    ordered = [unique_by_event_id[event_id] for event_id in sorted(unique_by_event_id)]
    current_events = [
        scored
        for scored in ordered
        if scored.is_qualifying and scored.direction is EvidenceDirection.CURRENT
    ]
    candidate_events = [
        scored
        for scored in ordered
        if scored.is_qualifying and scored.direction is EvidenceDirection.CANDIDATE
    ]

    current_mass = fsum(scored.event_mass for scored in current_events)
    candidate_mass = fsum(scored.event_mass for scored in candidate_events)
    total_mass = fsum((current_mass, candidate_mass))

    if total_mass > 0.0:
        current_support = current_mass / total_mass
        candidate_support = candidate_mass / total_mass
        support_margin = candidate_support - current_support
    else:
        current_support = None
        candidate_support = None
        support_margin = None

    return MasterySupportSummary(
        current=_summarize_direction(
            EvidenceDirection.CURRENT,
            current_events,
            mass=current_mass,
            support=current_support,
        ),
        candidate=_summarize_direction(
            EvidenceDirection.CANDIDATE,
            candidate_events,
            mass=candidate_mass,
            support=candidate_support,
        ),
        total_mass=total_mass,
        support_margin=support_margin,
    )


def evaluate_stability_gate(
    summary: MasterySupportSummary,
    *,
    direction: EvidenceDirection,
    config: LifecyclePolicyV1Config,
) -> DirectionalStabilityGate:
    """Apply the versioned event/session/confidence/margin AND gate."""
    resolved_direction = EvidenceDirection(direction)
    directional = (
        summary.candidate if resolved_direction is EvidenceDirection.CANDIDATE else summary.current
    )
    directional_margin = summary.support_margin
    if directional_margin is not None and resolved_direction is EvidenceDirection.CURRENT:
        directional_margin = -directional_margin

    event_count_met = directional.event_count >= config.minimum_directional_event_count
    session_count_met = directional.session_count >= config.minimum_session_count
    confidence_met = (
        directional.confidence is not None
        and directional.confidence >= config.minimum_candidate_confidence
    )
    margin_met = (
        directional_margin is not None and directional_margin >= config.minimum_support_margin
    )
    is_stable_winner = all(
        (
            event_count_met,
            session_count_met,
            confidence_met,
            margin_met,
        )
    )

    return DirectionalStabilityGate(
        policy_version=config.policy_version,
        direction=resolved_direction,
        event_count=directional.event_count,
        minimum_event_count=config.minimum_directional_event_count,
        event_count_met=event_count_met,
        session_count=directional.session_count,
        minimum_session_count=config.minimum_session_count,
        session_count_met=session_count_met,
        confidence=directional.confidence,
        minimum_confidence=config.minimum_candidate_confidence,
        confidence_met=confidence_met,
        directional_margin=directional_margin,
        minimum_support_margin=config.minimum_support_margin,
        margin_met=margin_met,
        is_stable_winner=is_stable_winner,
    )


def evaluate_mastery_policy(policy_input: LifecyclePolicyInput) -> MasteryPolicyEvaluation:
    """Evaluate the Stage 06 S05-S09 mastery conflict rules without I/O."""
    if policy_input.candidate.scope.memory_namespace is not MemoryNamespace.MASTERY:
        raise ValueError("mastery policy requires mastery namespace")
    if not isinstance(policy_input.candidate.proposed_value, MasteryValue):
        raise ValueError("mastery policy requires MasteryValue candidate")
    if policy_input.relation is None:
        raise ValueError("mastery conflict requires a resolved relation")
    if policy_input.relation.classification.relation is not MemoryRelation.CONTRADICTORY:
        raise ValueError("mastery conflict requires contradictory relation")

    current_snapshot, contested_snapshot = _resolve_mastery_snapshots(
        policy_input.candidate_snapshots
    )
    current_value = _mastery_value_from_snapshot(current_snapshot)
    candidate_value = (
        _mastery_value_from_snapshot(contested_snapshot)
        if contested_snapshot is not None
        else policy_input.candidate.proposed_value
    )
    if current_value.score == candidate_value.score:
        raise ValueError("mastery conflict requires distinct current and candidate scores")
    if contested_snapshot is not None:
        _validate_proposed_contested_direction(
            proposed=policy_input.candidate.proposed_value,
            current=current_value,
            contested=candidate_value,
        )

    canonical_id = policy_input.candidate.slot_key.split(":", 1)[1]
    classified_id = policy_input.relation.classification.canonical_knowledge_point_id
    if classified_id is not None and classified_id != canonical_id:
        raise ValueError("relation canonical knowledge point must match mastery slot")

    historical_by_id = {event.event_id: event for event in policy_input.historical_events}
    required_provenance = set(current_snapshot.memory.provenance)
    if contested_snapshot is not None:
        required_provenance.update(contested_snapshot.memory.provenance)
    missing_provenance = sorted(required_provenance - set(historical_by_id))
    if missing_provenance:
        raise ValueError(
            "historical events missing authoritative provenance: " + ", ".join(missing_provenance)
        )
    invalid_provenance = sorted(
        event_id
        for event_id in required_provenance
        if canonical_id not in historical_by_id[event_id].knowledge_point_ids
    )
    if invalid_provenance:
        raise ValueError(
            "authoritative mastery provenance must include the canonical slot knowledge point: "
            + ", ".join(invalid_provenance)
        )
    if canonical_id not in policy_input.event.knowledge_point_ids:
        raise ValueError("mastery evidence must include the canonical slot knowledge point")

    all_events = [
        event
        for event in policy_input.historical_events
        if canonical_id in event.knowledge_point_ids
    ]
    all_events.append(policy_input.event)
    scored_events: list[ScoredMasteryEvidence] = []
    for event in all_events:
        direction = resolve_mastery_evidence_direction(
            event,
            current=current_value,
            candidate=candidate_value,
        )
        if direction is None:
            raise ValueError("mastery evidence direction is undefined for equal scores")
        scored_events.append(
            score_mastery_event(
                event,
                direction=direction,
                evaluated_at=policy_input.evaluated_at,
            )
        )

    scored_events.sort(key=lambda scored: scored.event_id)
    support_summary = aggregate_mastery_support(scored_events)
    current_gate = evaluate_stability_gate(
        support_summary,
        direction=EvidenceDirection.CURRENT,
        config=policy_input.config,
    )
    candidate_gate = evaluate_stability_gate(
        support_summary,
        direction=EvidenceDirection.CANDIDATE,
        config=policy_input.config,
    )
    result = _decide_mastery_result(
        policy_input,
        current_snapshot=current_snapshot,
        contested_snapshot=contested_snapshot,
        support_summary=support_summary,
        current_gate=current_gate,
        candidate_gate=candidate_gate,
        current_event_direction=scored_events[
            next(
                index
                for index, scored in enumerate(scored_events)
                if scored.event_id == policy_input.event.event_id
            )
        ].direction,
    )
    return MasteryPolicyEvaluation(
        result=result,
        scored_events=tuple(scored_events),
        support_summary=support_summary,
        current_gate=current_gate,
        candidate_gate=candidate_gate,
    )


def _resolve_mastery_snapshots(
    snapshots: tuple[LifecycleCandidateSnapshot, ...],
) -> tuple[LifecycleCandidateSnapshot, LifecycleCandidateSnapshot | None]:
    active = [
        snapshot
        for snapshot in snapshots
        if snapshot.memory.lifecycle_state is LifecycleState.ACTIVE
    ]
    contested = [
        snapshot
        for snapshot in snapshots
        if snapshot.memory.lifecycle_state is LifecycleState.CONTESTED
    ]
    if len(active) != 1:
        raise ValueError("mastery conflict requires exactly one active snapshot")
    if len(contested) > 1:
        raise ValueError("lifecycle_policy_v1 supports at most one contested mastery branch")

    current_snapshot = active[0]
    contested_snapshot = contested[0] if contested else None
    if contested_snapshot is not None:
        group_id = contested_snapshot.contested_group_id
        if current_snapshot.contested_group_id != group_id:
            raise ValueError("active and contested mastery snapshots must share contested group")
    return current_snapshot, contested_snapshot


def _mastery_value_from_snapshot(snapshot: LifecycleCandidateSnapshot) -> MasteryValue:
    value = snapshot.memory.value
    if not isinstance(value, MasteryValue):
        raise ValueError("mastery snapshot must contain MasteryValue")
    return value


def _validate_proposed_contested_direction(
    *,
    proposed: MasteryValue,
    current: MasteryValue,
    contested: MasteryValue,
) -> None:
    if proposed.score == current.score:
        return
    proposed_delta = proposed.score - current.score
    contested_delta = contested.score - current.score
    if proposed_delta * contested_delta <= 0.0:
        raise ValueError("proposed mastery must align with current or contested direction")


def _decide_mastery_result(
    policy_input: LifecyclePolicyInput,
    *,
    current_snapshot: LifecycleCandidateSnapshot,
    contested_snapshot: LifecycleCandidateSnapshot | None,
    support_summary: MasterySupportSummary,
    current_gate: DirectionalStabilityGate,
    candidate_gate: DirectionalStabilityGate,
    current_event_direction: EvidenceDirection,
) -> LifecyclePolicyResult:
    event = policy_input.event
    if event.evidence_quality.is_temporary_exception:
        return _mastery_policy_result(
            policy_input,
            operation=LifecycleOperation.NO_OP,
            reason_code="temporary_exception_no_change",
            confidence=event.evidence_quality.confidence,
            targets=(current_snapshot,),
            include_expected_versions=False,
        )

    if candidate_gate.is_stable_winner:
        targets = (
            (current_snapshot, contested_snapshot)
            if contested_snapshot is not None
            else (current_snapshot,)
        )
        return _mastery_policy_result(
            policy_input,
            operation=LifecycleOperation.SUPERSEDE,
            reason_code="candidate_direction_stable",
            confidence=_directional_confidence(
                support_summary,
                EvidenceDirection.CANDIDATE,
                fallback=event.evidence_quality.confidence,
            ),
            targets=targets,
            include_expected_versions=True,
        )

    if contested_snapshot is None:
        if event.evidence_quality.confidence < policy_input.config.minimum_candidate_confidence:
            return _mastery_policy_result(
                policy_input,
                operation=LifecycleOperation.NO_OP,
                reason_code="isolated_low_confidence_no_change",
                confidence=event.evidence_quality.confidence,
                targets=(current_snapshot,),
                include_expected_versions=False,
            )
        current_value = _mastery_value_from_snapshot(current_snapshot)
        if current_value.level in {MasteryLevel.LOW, MasteryLevel.IMPROVING}:
            return _mastery_policy_result(
                policy_input,
                operation=LifecycleOperation.MERGE,
                reason_code="directional_evidence_accumulated_without_level_change",
                confidence=_directional_confidence(
                    support_summary,
                    EvidenceDirection.CANDIDATE,
                    fallback=event.evidence_quality.confidence,
                ),
                targets=(current_snapshot,),
                include_expected_versions=True,
            )
        return _mastery_policy_result(
            policy_input,
            operation=LifecycleOperation.CONTESTED,
            reason_code="mastery_conflict_below_stability_gate",
            confidence=_directional_confidence(
                support_summary,
                EvidenceDirection.CANDIDATE,
                fallback=event.evidence_quality.confidence,
            ),
            targets=(current_snapshot,),
            include_expected_versions=True,
        )

    if current_gate.is_stable_winner:
        return _mastery_policy_result(
            policy_input,
            operation=LifecycleOperation.MERGE,
            reason_code="current_direction_rewon",
            confidence=_directional_confidence(
                support_summary,
                EvidenceDirection.CURRENT,
                fallback=event.evidence_quality.confidence,
            ),
            targets=(current_snapshot, contested_snapshot),
            include_expected_versions=True,
        )

    target = (
        contested_snapshot
        if current_event_direction is EvidenceDirection.CANDIDATE
        else current_snapshot
    )
    reason_code = (
        "contested_candidate_evidence_accumulated"
        if current_event_direction is EvidenceDirection.CANDIDATE
        else "contested_current_evidence_accumulated"
    )
    return _mastery_policy_result(
        policy_input,
        operation=LifecycleOperation.MERGE,
        reason_code=reason_code,
        confidence=_directional_confidence(
            support_summary,
            current_event_direction,
            fallback=event.evidence_quality.confidence,
        ),
        targets=(target,),
        include_expected_versions=True,
    )


def _directional_confidence(
    summary: MasterySupportSummary,
    direction: EvidenceDirection,
    *,
    fallback: float,
) -> float:
    directional = summary.candidate if direction is EvidenceDirection.CANDIDATE else summary.current
    return directional.confidence if directional.confidence is not None else fallback


def _mastery_policy_result(
    policy_input: LifecyclePolicyInput,
    *,
    operation: LifecycleOperation,
    reason_code: str,
    confidence: float,
    targets: tuple[LifecycleCandidateSnapshot, ...],
    include_expected_versions: bool,
) -> LifecyclePolicyResult:
    ordered_targets = sorted(
        targets,
        key=lambda snapshot: (snapshot.memory.version, snapshot.memory.memory_id),
    )
    target_ids = [snapshot.memory.memory_id for snapshot in ordered_targets]
    expected_row_versions = (
        {snapshot.memory.memory_id: snapshot.row_version for snapshot in ordered_targets}
        if include_expected_versions
        else {}
    )
    decision = LifecycleDecision(
        operation=operation,
        target_memory_ids=target_ids,
        reason_code=reason_code,
        confidence=confidence,
        policy_version=policy_input.config.policy_version,
    )
    return LifecyclePolicyResult(
        event_id=policy_input.event.event_id,
        scope=policy_input.candidate.scope,
        slot_key=policy_input.candidate.slot_key,
        decision=decision,
        expected_row_versions=expected_row_versions,
    )


def _error_factor(event: LearningEvent) -> float:
    if event.answer_correct:
        return 1.0
    if event.error_type is ErrorType.CONCEPT_CONFUSION:
        return 1.0
    if event.error_type is ErrorType.CARELESS_ERROR:
        return 0.25
    return 0.50


def _summarize_direction(
    direction: EvidenceDirection,
    events: list[ScoredMasteryEvidence],
    *,
    mass: float,
    support: float | None,
) -> DirectionalSupport:
    base_weight_sum = fsum(scored.base_weight for scored in events)
    confidence_numerator = fsum(
        scored.evidence_confidence * scored.base_weight for scored in events
    )
    confidence = confidence_numerator / base_weight_sum if base_weight_sum > 0.0 else None
    return DirectionalSupport(
        direction=direction,
        event_count=len(events),
        session_count=len({scored.session_id for scored in events}),
        mass=mass,
        confidence=confidence,
        support=support,
    )


__all__ = [
    "DirectionalSupport",
    "DirectionalStabilityGate",
    "EvidenceDirection",
    "MasteryPolicyEvaluation",
    "MasterySupportSummary",
    "ScoredMasteryEvidence",
    "aggregate_mastery_support",
    "evaluate_mastery_policy",
    "evaluate_stability_gate",
    "resolve_mastery_evidence_direction",
    "score_mastery_event",
]
