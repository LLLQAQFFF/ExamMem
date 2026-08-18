from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exam_mem.contracts import (
    LearningEvent,
    LearningMemory,
    LifecycleOperation,
    MasteryValue,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle.contracts import (
    LifecycleCandidateSnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyV1Config,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
)
from exam_mem.lifecycle.policy_v1 import (
    EvidenceDirection,
    aggregate_mastery_support,
    evaluate_mastery_policy,
    evaluate_stability_gate,
    resolve_mastery_evidence_direction,
    score_mastery_event,
)

pytestmark = pytest.mark.lifecycle

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.probability.bayes"


def _answer_event(
    *,
    event_id: str = "stage06_policy_event_001",
    session_id: str = "stage06_policy_session_001",
    occurred_at: datetime = NOW,
    difficulty: float = 0.5,
    answer_correct: bool = True,
    error_type: str | None = None,
    confidence: float = 1.0,
    is_temporary_exception: bool = False,
    knowledge_point_id: str = "math1.probability.bayes",
) -> LearningEvent:
    quality_reasons: list[str] = []
    if confidence < 1.0:
        quality_reasons.append("low_grader_confidence")
    if is_temporary_exception:
        quality_reasons.append("user_reported_exception")

    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": {
                "user_id": "stage06_user",
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            "session_id": session_id,
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": [knowledge_point_id],
            "difficulty": difficulty,
            "answer_correct": answer_correct,
            "error_type": error_type,
            "error_detail": "controlled policy evidence" if error_type else None,
            "evidence_quality": {
                "confidence": confidence,
                "is_temporary_exception": is_temporary_exception,
                "reasons": quality_reasons,
            },
            "occurred_at": occurred_at,
        }
    )


def _correction_event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "stage06_policy_correction_001",
            "idempotency_key": "idem:stage06_policy_correction_001",
            "event_type": "explicit_correction",
            "context": {
                "user_id": "stage06_user",
                "exam_id": "postgraduate_entrance_exam",
                "subject_id": "math_1",
            },
            "session_id": "stage06_policy_session_001",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "correction": {
                "target_memory_ids": ["stage06_memory_001"],
                "source": "grader_audit",
                "statement": "The diagnosis was incorrect.",
            },
            "occurred_at": NOW,
        }
    )


def _mastery_value(*, level: str, score: float) -> MasteryValue:
    return MasteryValue.model_validate(
        {
            "type": "mastery",
            "level": level,
            "score": score,
        }
    )


def _scored_events(
    *,
    prefix: str,
    direction: EvidenceDirection,
    count: int,
    session_ids: tuple[str, ...],
    confidence: float = 1.0,
) -> list:
    return [
        score_mastery_event(
            _answer_event(
                event_id=f"{prefix}_{index}",
                session_id=session_ids[index % len(session_ids)],
                confidence=confidence,
            ),
            direction=direction,
            evaluated_at=NOW,
        )
        for index in range(count)
    ]


def _answer_events(
    *,
    prefix: str,
    count: int,
    session_ids: tuple[str, ...],
    answer_correct: bool,
    age_days: int = 1,
) -> list[LearningEvent]:
    return [
        _answer_event(
            event_id=f"{prefix}_{index}",
            session_id=session_ids[index % len(session_ids)],
            occurred_at=NOW - timedelta(days=age_days),
            answer_correct=answer_correct,
            error_type=None if answer_correct else "concept_confusion",
        )
        for index in range(count)
    ]


def _mastery_snapshot(
    *,
    memory_id: str,
    value: MasteryValue,
    provenance: list[str],
    state: str,
    version: int,
    row_version: int,
    contested_group_id: str | None = None,
) -> LifecycleCandidateSnapshot:
    memory = LearningMemory.model_validate(
        {
            "memory_id": memory_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "value": value.model_dump(mode="json"),
            "confidence": 1.0,
            "evidence_count": len(provenance),
            "lifecycle_state": state,
            "version": version,
            "valid_from": NOW - timedelta(days=90),
            "valid_to": None,
            "superseded_by": None,
            "provenance": provenance,
        }
    )
    return LifecycleCandidateSnapshot(
        memory=memory,
        row_version=row_version,
        contested_group_id=contested_group_id,
        policy_version="lifecycle_policy_v1",
    )


def _mastery_policy_input(
    *,
    event: LearningEvent,
    current: LifecycleCandidateSnapshot,
    candidate_value: MasteryValue,
    historical_events: list[LearningEvent],
    contested: LifecycleCandidateSnapshot | None = None,
) -> LifecyclePolicyInput:
    snapshots = [current] if contested is None else [current, contested]
    relation = ResolvedRelationClassification(
        target_memory_id=current.memory.memory_id,
        classification=RelationClassifierOutput(
            candidate_display_number=1,
            relation=MemoryRelation.CONTRADICTORY,
            canonical_knowledge_point_id="math1.probability.bayes",
            error_type=event.error_type,
            error_summary=event.error_detail,
            confidence=event.evidence_quality.confidence,
            reason="The answer evidence supports a different mastery direction.",
        ),
    )
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": candidate_value.model_dump(mode="json"),
            "evidence": {"answer_correct": event.answer_correct},
        }
    )
    return LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        candidate_snapshots=snapshots,
        relation=relation,
        historical_events=historical_events,
        evaluated_at=NOW,
    )


def test_correct_event_uses_confidence_and_difficulty_without_error_penalty() -> None:
    scored = score_mastery_event(
        _answer_event(difficulty=1.0, confidence=0.8),
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )

    assert scored.event_id == "stage06_policy_event_001"
    assert scored.session_id == "stage06_policy_session_001"
    assert scored.direction is EvidenceDirection.CANDIDATE
    assert scored.is_qualifying is True
    assert scored.evidence_confidence == pytest.approx(0.8)
    assert scored.difficulty_factor == pytest.approx(1.25)
    assert scored.error_factor == pytest.approx(1.0)
    assert scored.time_decay == pytest.approx(1.0)
    assert scored.base_weight == pytest.approx(1.25)
    assert scored.event_mass == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("error_type", "expected_error_factor"),
    [
        ("concept_confusion", 1.0),
        ("careless_error", 0.25),
        ("formula_misuse", 0.5),
        ("unknown", 0.5),
    ],
)
def test_incorrect_event_uses_controlled_error_factor(
    error_type: str,
    expected_error_factor: float,
) -> None:
    scored = score_mastery_event(
        _answer_event(
            difficulty=0.5,
            answer_correct=False,
            error_type=error_type,
        ),
        direction=EvidenceDirection.CURRENT,
        evaluated_at=NOW,
    )

    assert scored.difficulty_factor == pytest.approx(1.0)
    assert scored.error_factor == pytest.approx(expected_error_factor)
    assert scored.event_mass == pytest.approx(expected_error_factor)


def test_event_mass_has_a_deterministic_thirty_day_half_life() -> None:
    scored = score_mastery_event(
        _answer_event(occurred_at=NOW - timedelta(days=30)),
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )

    assert scored.time_decay == pytest.approx(0.5)
    assert scored.event_mass == pytest.approx(0.5)


def test_temporary_exception_is_auditable_but_not_qualifying_support() -> None:
    scored = score_mastery_event(
        _answer_event(is_temporary_exception=True),
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )

    assert scored.is_qualifying is False
    assert scored.exclusion_reason == "temporary_exception"
    assert scored.event_mass == pytest.approx(0.0)

    summary = aggregate_mastery_support([scored])
    assert summary.total_mass == pytest.approx(0.0)
    assert summary.current.event_count == 0
    assert summary.candidate.event_count == 0
    assert summary.current.support is None
    assert summary.candidate.support is None
    assert summary.support_margin is None


def test_scorer_rejects_non_mastery_or_incomplete_answer_evidence() -> None:
    with pytest.raises(ValueError, match="answer_attempt"):
        score_mastery_event(
            _correction_event(),
            direction=EvidenceDirection.CANDIDATE,
            evaluated_at=NOW,
        )

    with pytest.raises(ValueError, match="error_type"):
        score_mastery_event(
            _answer_event(answer_correct=False),
            direction=EvidenceDirection.CANDIDATE,
            evaluated_at=NOW,
        )


def test_scorer_rejects_future_evidence_instead_of_clamping_its_age() -> None:
    with pytest.raises(ValueError, match="future"):
        score_mastery_event(
            _answer_event(occurred_at=NOW + timedelta(seconds=1)),
            direction=EvidenceDirection.CANDIDATE,
            evaluated_at=NOW,
        )


def test_aggregate_is_event_idempotent_order_independent_and_session_aware() -> None:
    candidate_recent = score_mastery_event(
        _answer_event(
            event_id="candidate_recent",
            session_id="candidate_session_1",
            confidence=0.8,
        ),
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )
    candidate_old = score_mastery_event(
        _answer_event(
            event_id="candidate_old",
            session_id="candidate_session_2",
            occurred_at=NOW - timedelta(days=30),
            confidence=0.4,
        ),
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )
    current = score_mastery_event(
        _answer_event(
            event_id="current_recent",
            session_id="current_session_1",
            answer_correct=False,
            error_type="concept_confusion",
        ),
        direction=EvidenceDirection.CURRENT,
        evaluated_at=NOW,
    )

    ordered = aggregate_mastery_support([candidate_recent, candidate_old, current])
    reordered_with_duplicate = aggregate_mastery_support(
        [current, candidate_old, candidate_recent, candidate_recent]
    )

    assert reordered_with_duplicate == ordered
    assert ordered.candidate.event_count == 2
    assert ordered.candidate.session_count == 2
    assert ordered.current.event_count == 1
    assert ordered.current.session_count == 1
    assert ordered.candidate.mass == pytest.approx(1.0)
    assert ordered.current.mass == pytest.approx(1.0)
    assert ordered.total_mass == pytest.approx(2.0)
    assert ordered.candidate.confidence == pytest.approx(2 / 3)
    assert ordered.current.confidence == pytest.approx(1.0)
    assert ordered.candidate.support == pytest.approx(0.5)
    assert ordered.current.support == pytest.approx(0.5)
    assert ordered.support_margin == pytest.approx(0.0)


def test_aggregate_rejects_same_event_id_with_conflicting_scores() -> None:
    event = _answer_event(event_id="conflicting_event")
    current = score_mastery_event(
        event,
        direction=EvidenceDirection.CURRENT,
        evaluated_at=NOW,
    )
    candidate = score_mastery_event(
        event,
        direction=EvidenceDirection.CANDIDATE,
        evaluated_at=NOW,
    )

    with pytest.raises(ValueError, match="conflicting scores"):
        aggregate_mastery_support([current, candidate])


def test_stability_gate_rejects_one_event_even_with_full_support() -> None:
    summary = aggregate_mastery_support(
        _scored_events(
            prefix="one_candidate",
            direction=EvidenceDirection.CANDIDATE,
            count=1,
            session_ids=("candidate_session_1",),
        )
    )

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CANDIDATE,
        config=LifecyclePolicyV1Config(),
    )

    assert gate.event_count_met is False
    assert gate.session_count_met is False
    assert gate.confidence_met is True
    assert gate.margin_met is True
    assert gate.directional_margin == pytest.approx(1.0)
    assert gate.is_stable_winner is False


def test_stability_gate_rejects_three_events_from_only_one_session() -> None:
    summary = aggregate_mastery_support(
        _scored_events(
            prefix="same_session_candidate",
            direction=EvidenceDirection.CANDIDATE,
            count=3,
            session_ids=("candidate_session_1",),
        )
    )

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CANDIDATE,
        config=LifecyclePolicyV1Config(),
    )

    assert gate.event_count_met is True
    assert gate.session_count_met is False
    assert gate.confidence_met is True
    assert gate.margin_met is True
    assert gate.is_stable_winner is False


def test_stability_gate_accepts_three_events_across_two_sessions() -> None:
    summary = aggregate_mastery_support(
        _scored_events(
            prefix="cross_session_candidate",
            direction=EvidenceDirection.CANDIDATE,
            count=3,
            session_ids=("candidate_session_1", "candidate_session_2"),
        )
    )

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CANDIDATE,
        config=LifecyclePolicyV1Config(),
    )

    assert gate.event_count_met is True
    assert gate.session_count_met is True
    assert gate.confidence_met is True
    assert gate.margin_met is True
    assert gate.is_stable_winner is True


def test_stability_gate_rejects_low_directional_confidence() -> None:
    candidate = _scored_events(
        prefix="low_confidence_candidate",
        direction=EvidenceDirection.CANDIDATE,
        count=3,
        session_ids=("candidate_session_1", "candidate_session_2"),
        confidence=0.69,
    )
    current = _scored_events(
        prefix="weak_current",
        direction=EvidenceDirection.CURRENT,
        count=1,
        session_ids=("current_session_1",),
        confidence=0.1,
    )
    summary = aggregate_mastery_support([*candidate, *current])

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CANDIDATE,
        config=LifecyclePolicyV1Config(),
    )

    assert gate.event_count_met is True
    assert gate.session_count_met is True
    assert gate.confidence_met is False
    assert gate.margin_met is True
    assert gate.is_stable_winner is False


def test_stability_gate_rejects_insufficient_support_margin() -> None:
    candidate = _scored_events(
        prefix="narrow_candidate",
        direction=EvidenceDirection.CANDIDATE,
        count=3,
        session_ids=("candidate_session_1", "candidate_session_2"),
    )
    current = _scored_events(
        prefix="near_current",
        direction=EvidenceDirection.CURRENT,
        count=3,
        session_ids=("current_session_1", "current_session_2"),
        confidence=0.9,
    )
    summary = aggregate_mastery_support([*candidate, *current])

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CANDIDATE,
        config=LifecyclePolicyV1Config(),
    )

    assert gate.event_count_met is True
    assert gate.session_count_met is True
    assert gate.confidence_met is True
    assert gate.directional_margin == pytest.approx(3 / 5.7 - 2.7 / 5.7)
    assert gate.margin_met is False
    assert gate.is_stable_winner is False


def test_stability_gate_applies_the_same_thresholds_when_current_rewins() -> None:
    current = _scored_events(
        prefix="rewinning_current",
        direction=EvidenceDirection.CURRENT,
        count=3,
        session_ids=("current_session_1", "current_session_2"),
    )
    candidate = _scored_events(
        prefix="losing_candidate",
        direction=EvidenceDirection.CANDIDATE,
        count=1,
        session_ids=("candidate_session_1",),
        confidence=0.2,
    )
    summary = aggregate_mastery_support([*current, *candidate])

    gate = evaluate_stability_gate(
        summary,
        direction=EvidenceDirection.CURRENT,
        config=LifecyclePolicyV1Config(),
    )

    assert summary.support_margin is not None
    assert summary.support_margin < 0.0
    assert gate.directional_margin == pytest.approx(-summary.support_margin)
    assert gate.event_count_met is True
    assert gate.session_count_met is True
    assert gate.confidence_met is True
    assert gate.margin_met is True
    assert gate.is_stable_winner is True


@pytest.mark.parametrize(
    (
        "current_score",
        "candidate_score",
        "answer_correct",
        "error_type",
        "expected_direction",
    ),
    [
        (0.3, 0.8, True, None, EvidenceDirection.CANDIDATE),
        (0.3, 0.8, False, "concept_confusion", EvidenceDirection.CURRENT),
        (0.8, 0.3, True, None, EvidenceDirection.CURRENT),
        (0.8, 0.3, False, "concept_confusion", EvidenceDirection.CANDIDATE),
    ],
)
def test_mastery_evidence_direction_comes_from_scores_and_answer_fact(
    current_score: float,
    candidate_score: float,
    answer_correct: bool,
    error_type: str | None,
    expected_direction: EvidenceDirection,
) -> None:
    event = _answer_event(
        answer_correct=answer_correct,
        error_type=error_type,
    )

    direction = resolve_mastery_evidence_direction(
        event,
        current=_mastery_value(
            level="high" if current_score >= 0.7 else "low",
            score=current_score,
        ),
        candidate=_mastery_value(
            level="high" if candidate_score >= 0.7 else "low",
            score=candidate_score,
        ),
    )

    assert direction is expected_direction


@pytest.mark.parametrize("error_type", ["concept_confusion", "careless_error"])
def test_mastery_error_type_changes_weight_but_not_direction(error_type: str) -> None:
    event = _answer_event(
        answer_correct=False,
        error_type=error_type,
    )
    current = _mastery_value(level="high", score=0.9)
    candidate = _mastery_value(level="low", score=0.2)

    direction = resolve_mastery_evidence_direction(
        event,
        current=current,
        candidate=candidate,
    )
    scored = score_mastery_event(event, direction=direction, evaluated_at=NOW)

    assert direction is EvidenceDirection.CANDIDATE
    expected_factor = 1.0 if error_type == "concept_confusion" else 0.25
    assert scored.error_factor == pytest.approx(expected_factor)


def test_equal_mastery_scores_do_not_create_a_false_evidence_direction() -> None:
    direction = resolve_mastery_evidence_direction(
        _answer_event(),
        current=_mastery_value(level="improving", score=0.5),
        candidate=_mastery_value(level="improving", score=0.5),
    )

    assert direction is None


def test_mastery_direction_resolver_rejects_non_answer_events() -> None:
    with pytest.raises(ValueError, match="answer_attempt"):
        resolve_mastery_evidence_direction(
            _correction_event(),
            current=_mastery_value(level="high", score=0.9),
            candidate=_mastery_value(level="low", score=0.2),
        )


@pytest.mark.parametrize(
    ("confidence", "is_temporary_exception", "expected_reason"),
    [
        (0.69, False, "isolated_low_confidence_no_change"),
        (1.0, True, "temporary_exception_no_change"),
    ],
)
def test_s05_isolated_low_quality_mastery_evidence_is_no_op(
    confidence: float,
    is_temporary_exception: bool,
    expected_reason: str,
) -> None:
    current_events = _answer_events(
        prefix="s05_current",
        count=3,
        session_ids=("s05_current_session_1", "s05_current_session_2"),
        answer_correct=True,
    )
    current = _mastery_snapshot(
        memory_id="s05_high_v1",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=1,
        row_version=4,
    )
    event = _answer_event(
        event_id="s05_new_error",
        session_id="s05_candidate_session",
        answer_correct=False,
        error_type="concept_confusion",
        confidence=confidence,
        is_temporary_exception=is_temporary_exception,
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            candidate_value=_mastery_value(level="low", score=0.2),
            historical_events=current_events,
        )
    )

    assert evaluation.result.decision.operation is LifecycleOperation.NO_OP
    assert evaluation.result.decision.reason_code == expected_reason
    assert evaluation.result.decision.target_memory_ids == ["s05_high_v1"]
    assert evaluation.result.expected_row_versions == {}


def test_s06_one_concept_error_contests_stable_high_mastery() -> None:
    current_events = _answer_events(
        prefix="s06_current",
        count=3,
        session_ids=("s06_current_session_1", "s06_current_session_2"),
        answer_correct=True,
    )
    current = _mastery_snapshot(
        memory_id="s06_high_v3",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=3,
        row_version=8,
    )
    event = _answer_event(
        event_id="s06_single_concept_error",
        session_id="s06_candidate_session",
        answer_correct=False,
        error_type="concept_confusion",
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            candidate_value=_mastery_value(level="low", score=0.2),
            historical_events=current_events,
        )
    )

    assert evaluation.candidate_gate.event_count == 1
    assert evaluation.candidate_gate.is_stable_winner is False
    assert evaluation.current_gate.is_stable_winner is True
    assert evaluation.result.decision.operation is LifecycleOperation.CONTESTED
    assert evaluation.result.decision.reason_code == "mastery_conflict_below_stability_gate"
    assert evaluation.result.decision.target_memory_ids == ["s06_high_v3"]
    assert evaluation.result.expected_row_versions == {"s06_high_v3": 8}


@pytest.mark.parametrize(
    ("current_level", "current_score", "answer_correct", "error_type", "candidate_level", "candidate_score"),
    [
        ("low", 0.2, True, None, "high", 1.0),
        ("improving", 0.6, True, None, "high", 1.0),
        ("improving", 0.6, False, "formula_misuse", "low", 0.0),
    ],
)
def test_non_stable_mastery_accumulates_evidence_without_contested_branch(
    current_level: str,
    current_score: float,
    answer_correct: bool,
    error_type: str | None,
    candidate_level: str,
    candidate_score: float,
) -> None:
    seed = _answer_event(
        event_id="non_scoring_seed",
        answer_correct=current_score >= 0.5,
        error_type=None if current_score >= 0.5 else "concept_confusion",
        is_temporary_exception=True,
    )
    current = _mastery_snapshot(
        memory_id="non_stable_v1",
        value=_mastery_value(level=current_level, score=current_score),
        provenance=[seed.event_id],
        state="active",
        version=1,
        row_version=4,
    )
    event = _answer_event(
        event_id="first_directional_evidence",
        answer_correct=answer_correct,
        error_type=error_type,
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            candidate_value=_mastery_value(level=candidate_level, score=candidate_score),
            historical_events=[seed],
        )
    )

    assert evaluation.result.decision.operation is LifecycleOperation.MERGE
    assert evaluation.result.decision.reason_code == (
        "directional_evidence_accumulated_without_level_change"
    )
    assert evaluation.result.decision.target_memory_ids == ["non_stable_v1"]
    assert evaluation.result.expected_row_versions == {"non_stable_v1": 4}


def test_s07_three_candidate_events_in_one_session_advance_contested_branch() -> None:
    group_id = "s07_contested_group"
    current_events = _answer_events(
        prefix="s07_current",
        count=3,
        session_ids=("s07_current_session_1", "s07_current_session_2"),
        answer_correct=True,
    )
    candidate_events = _answer_events(
        prefix="s07_candidate",
        count=2,
        session_ids=("s07_candidate_session",),
        answer_correct=False,
    )
    current = _mastery_snapshot(
        memory_id="s07_high_v1",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=1,
        row_version=5,
        contested_group_id=group_id,
    )
    contested = _mastery_snapshot(
        memory_id="s07_low_v2",
        value=_mastery_value(level="low", score=0.2),
        provenance=[event.event_id for event in candidate_events],
        state="contested",
        version=2,
        row_version=6,
        contested_group_id=group_id,
    )
    event = _answer_event(
        event_id="s07_candidate_2",
        session_id="s07_candidate_session",
        answer_correct=False,
        error_type="concept_confusion",
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            contested=contested,
            candidate_value=_mastery_value(level="low", score=0.2),
            historical_events=[*current_events, *candidate_events],
        )
    )

    assert evaluation.candidate_gate.event_count == 3
    assert evaluation.candidate_gate.session_count == 1
    assert evaluation.candidate_gate.event_count_met is True
    assert evaluation.candidate_gate.session_count_met is False
    assert evaluation.result.decision.operation is LifecycleOperation.MERGE
    assert evaluation.result.decision.reason_code == "contested_candidate_evidence_accumulated"
    assert evaluation.result.decision.target_memory_ids == ["s07_low_v2"]
    assert evaluation.result.expected_row_versions == {"s07_low_v2": 6}


def test_s08_cross_session_candidate_evidence_supersedes_contested_mastery() -> None:
    group_id = "s08_contested_group"
    current_events = _answer_events(
        prefix="s08_current",
        count=3,
        session_ids=("s08_current_session_1", "s08_current_session_2"),
        answer_correct=True,
        age_days=60,
    )
    candidate_events = _answer_events(
        prefix="s08_candidate",
        count=2,
        session_ids=("s08_candidate_session_1",),
        answer_correct=False,
    )
    current = _mastery_snapshot(
        memory_id="s08_high_v4",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=4,
        row_version=10,
        contested_group_id=group_id,
    )
    contested = _mastery_snapshot(
        memory_id="s08_low_v5",
        value=_mastery_value(level="low", score=0.2),
        provenance=[event.event_id for event in candidate_events],
        state="contested",
        version=5,
        row_version=11,
        contested_group_id=group_id,
    )
    event = _answer_event(
        event_id="s08_candidate_2",
        session_id="s08_candidate_session_2",
        answer_correct=False,
        error_type="concept_confusion",
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            contested=contested,
            candidate_value=_mastery_value(level="low", score=0.2),
            historical_events=[*current_events, *candidate_events],
        )
    )

    assert evaluation.candidate_gate.is_stable_winner is True
    assert evaluation.result.decision.operation is LifecycleOperation.SUPERSEDE
    assert evaluation.result.decision.reason_code == "candidate_direction_stable"
    assert evaluation.result.decision.target_memory_ids == ["s08_high_v4", "s08_low_v5"]
    assert evaluation.result.expected_row_versions == {
        "s08_high_v4": 10,
        "s08_low_v5": 11,
    }


def test_s09_current_direction_rewins_and_closes_contested_branch_with_merge() -> None:
    group_id = "s09_contested_group"
    current_events = _answer_events(
        prefix="s09_current",
        count=2,
        session_ids=("s09_current_session_1", "s09_current_session_2"),
        answer_correct=True,
    )
    candidate_events = _answer_events(
        prefix="s09_candidate",
        count=1,
        session_ids=("s09_candidate_session",),
        answer_correct=False,
        age_days=60,
    )
    current = _mastery_snapshot(
        memory_id="s09_high_v6",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=6,
        row_version=12,
        contested_group_id=group_id,
    )
    contested = _mastery_snapshot(
        memory_id="s09_low_v7",
        value=_mastery_value(level="low", score=0.2),
        provenance=[event.event_id for event in candidate_events],
        state="contested",
        version=7,
        row_version=13,
        contested_group_id=group_id,
    )
    event = _answer_event(
        event_id="s09_current_2",
        session_id="s09_current_session_1",
        answer_correct=True,
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            contested=contested,
            candidate_value=_mastery_value(level="high", score=0.9),
            historical_events=[*current_events, *candidate_events],
        )
    )

    assert evaluation.current_gate.is_stable_winner is True
    assert evaluation.result.decision.operation is LifecycleOperation.MERGE
    assert evaluation.result.decision.reason_code == "current_direction_rewon"
    assert evaluation.result.decision.target_memory_ids == ["s09_high_v6", "s09_low_v7"]
    assert evaluation.result.expected_row_versions == {
        "s09_high_v6": 12,
        "s09_low_v7": 13,
    }


def test_mastery_policy_ignores_other_knowledge_points_in_scope_history() -> None:
    group_id = "multi_knowledge_point_contested_group"
    current_events = _answer_events(
        prefix="multi_kp_current",
        count=2,
        session_ids=("multi_kp_session_1", "multi_kp_session_2"),
        answer_correct=True,
    )
    candidate_events = _answer_events(
        prefix="multi_kp_candidate",
        count=1,
        session_ids=("multi_kp_candidate_session",),
        answer_correct=False,
        age_days=60,
    )
    unrelated_event = _answer_event(
        event_id="other_knowledge_point_event",
        knowledge_point_id="math1.calculus.derivative",
    )
    current = _mastery_snapshot(
        memory_id="multi_kp_high_v1",
        value=_mastery_value(level="high", score=0.9),
        provenance=[event.event_id for event in current_events],
        state="active",
        version=1,
        row_version=10,
        contested_group_id=group_id,
    )
    contested = _mastery_snapshot(
        memory_id="multi_kp_low_v2",
        value=_mastery_value(level="low", score=0.2),
        provenance=[event.event_id for event in candidate_events],
        state="contested",
        version=2,
        row_version=11,
        contested_group_id=group_id,
    )
    event = _answer_event(
        event_id="multi_kp_current_2",
        session_id="multi_kp_session_1",
        answer_correct=True,
    )

    evaluation = evaluate_mastery_policy(
        _mastery_policy_input(
            event=event,
            current=current,
            contested=contested,
            candidate_value=_mastery_value(level="high", score=0.9),
            historical_events=[*current_events, *candidate_events, unrelated_event],
        )
    )

    assert evaluation.result.decision.operation is LifecycleOperation.MERGE
    assert evaluation.result.decision.reason_code == "current_direction_rewon"
    assert {scored.event_id for scored in evaluation.scored_events} == {
        event.event_id,
        *(historical.event_id for historical in [*current_events, *candidate_events]),
    }


def test_mastery_policy_rejects_cross_slot_authoritative_provenance() -> None:
    unrelated_event = _answer_event(
        event_id="cross_slot_authoritative_event",
        knowledge_point_id="math1.calculus.derivative",
    )
    current = _mastery_snapshot(
        memory_id="cross_slot_provenance_high_v1",
        value=_mastery_value(level="high", score=0.9),
        provenance=[unrelated_event.event_id],
        state="active",
        version=1,
        row_version=1,
    )
    event = _answer_event(
        event_id="cross_slot_candidate_event",
        answer_correct=False,
        error_type="concept_confusion",
    )

    with pytest.raises(ValueError, match="authoritative mastery provenance"):
        evaluate_mastery_policy(
            _mastery_policy_input(
                event=event,
                current=current,
                candidate_value=_mastery_value(level="low", score=0.2),
                historical_events=[unrelated_event],
            )
        )


def test_mastery_policy_rejects_incomplete_authoritative_provenance_window() -> None:
    current = _mastery_snapshot(
        memory_id="missing_history_high_v1",
        value=_mastery_value(level="high", score=0.9),
        provenance=["missing_authoritative_event"],
        state="active",
        version=1,
        row_version=1,
    )
    event = _answer_event(
        event_id="new_candidate_event",
        answer_correct=False,
        error_type="concept_confusion",
    )

    with pytest.raises(ValueError, match="missing authoritative provenance"):
        evaluate_mastery_policy(
            _mastery_policy_input(
                event=event,
                current=current,
                candidate_value=_mastery_value(level="low", score=0.2),
                historical_events=[],
            )
        )
