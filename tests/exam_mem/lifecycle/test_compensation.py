from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exam_mem.contracts import (
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import (
    CompensationService,
    CompensationTokenError,
    CompensationValidationError,
    LifecycleApplicationResult,
    LifecycleApplyState,
    LifecycleCandidateSnapshot,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
)
from exam_mem.storage import AppendResult, AppendStatus

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_compensation_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


def _event(event_id: str) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": event_id,
            "idempotency_key": f"idem:{event_id}",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_compensation_session",
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "controlled compensation evidence",
            "occurred_at": NOW,
        }
    )


def _snapshot(
    *,
    memory_id: str,
    version: int,
    state: LifecycleState,
    summary: str,
    row_version: int,
    valid_to: datetime | None = None,
    superseded_by: str | None = None,
) -> LifecycleMemorySnapshot:
    return LifecycleMemorySnapshot(
        memory=LearningMemory.model_validate(
            {
                "memory_id": memory_id,
                "scope": SCOPE.model_dump(mode="json"),
                "slot_key": SLOT_KEY,
                "value": {
                    "type": "error_pattern",
                    "error_type": "concept_confusion",
                    "summary": summary,
                    "details": [summary],
                },
                "confidence": 0.8,
                "evidence_count": version,
                "lifecycle_state": state.value,
                "version": version,
                "valid_from": NOW - timedelta(days=3 - version),
                "valid_to": valid_to,
                "superseded_by": superseded_by,
                "provenance": [f"event:{index}" for index in range(1, version + 1)],
            }
        ),
        row_version=row_version,
        policy_version="lifecycle_policy_v1",
    )


def _source_audit() -> tuple[
    LifecycleDecisionAuditRecord,
    list[LifecycleChangeAuditRecord],
    LifecycleMemorySnapshot,
    LifecycleCandidateSnapshot,
]:
    source_event = _event("stage06_wrong_supersede_event")
    prior = _snapshot(
        memory_id="stage06_compensation_prior_v1",
        version=1,
        state=LifecycleState.ACTIVE,
        summary="Correct prior diagnosis",
        row_version=1,
    )
    archived = LifecycleMemorySnapshot.model_validate(
        {
            **prior.model_dump(mode="python"),
            "memory": {
                **prior.memory.model_dump(mode="python"),
                "lifecycle_state": "archived",
                "valid_to": NOW,
                "superseded_by": "stage06_compensation_wrong_v2",
            },
            "row_version": 2,
        }
    )
    current = LifecycleCandidateSnapshot.model_validate(
        _snapshot(
            memory_id="stage06_compensation_wrong_v2",
            version=2,
            state=LifecycleState.ACTIVE,
            summary="Wrong replacement diagnosis",
            row_version=1,
        ).model_dump()
    )
    candidate = MemoryUpdateCandidate(
        event_id=source_event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value=current.memory.value,
        evidence={"source": "controlled_wrong_supersede"},
    )
    policy_input = LifecyclePolicyInput(
        event=source_event,
        candidate=candidate,
        candidate_snapshots=(LifecycleCandidateSnapshot.model_validate(prior.model_dump()),),
        evaluated_at=NOW,
    )
    policy_result = LifecyclePolicyResult(
        event_id=source_event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        decision=LifecycleDecision(
            operation=LifecycleOperation.SUPERSEDE,
            target_memory_ids=[prior.memory.memory_id],
            reason_code="controlled_wrong_supersede",
            confidence=0.8,
            policy_version="lifecycle_policy_v1",
        ),
        expected_row_versions={prior.memory.memory_id: prior.row_version},
    )
    decision = LifecycleDecisionAuditRecord(
        decision_id="stage06_wrong_supersede_decision",
        trace_id="stage06_wrong_supersede_trace",
        policy_input=policy_input,
        policy_result=policy_result,
        created_at=NOW,
    )
    changes = [
        LifecycleChangeAuditRecord(
            change_id="stage06_wrong_supersede:old",
            decision_id=decision.decision_id,
            trace_id=decision.trace_id,
            apply_state=LifecycleApplyState.APPLIED,
            memory_id=prior.memory.memory_id,
            before_state=prior,
            after_state=archived,
            expected_row_version=1,
            actual_row_version=2,
            recorded_at=NOW,
        ),
        LifecycleChangeAuditRecord(
            change_id="stage06_wrong_supersede:new",
            decision_id=decision.decision_id,
            trace_id=decision.trace_id,
            apply_state=LifecycleApplyState.APPLIED,
            memory_id=current.memory.memory_id,
            after_state=current,
            actual_row_version=1,
            recorded_at=NOW,
        ),
    ]
    return decision, changes, archived, current


class _AuditRepository:
    def __init__(
        self,
        decision: LifecycleDecisionAuditRecord | None,
        changes: list[LifecycleChangeAuditRecord],
    ) -> None:
        self.decision = decision
        self.changes = changes

    async def get_decision(self, decision_id: str) -> LifecycleDecisionAuditRecord | None:
        if self.decision is not None and self.decision.decision_id == decision_id:
            return self.decision
        return None

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]:
        return list(self.changes) if self.decision is not None else []


class _MemoryRepository:
    def __init__(
        self,
        current: tuple[LifecycleCandidateSnapshot, ...],
        chain: tuple[LifecycleMemorySnapshot, ...],
    ) -> None:
        self.current = current
        self.chain = chain

    async def find_candidate_snapshots(self, query):  # noqa: ANN001, ANN201
        return list(self.current)

    async def list_slot_snapshots(self, scope, slot_key):  # noqa: ANN001, ANN201
        return list(self.chain)


class _EventRepository:
    def __init__(self) -> None:
        self.events: list[LearningEvent] = []

    async def append(self, event: LearningEvent) -> AppendResult:
        self.events.append(event)
        return AppendResult(status=AppendStatus.CREATED, event=event)


class _Applier:
    def __init__(self, result: LifecycleApplicationResult) -> None:
        self.result = result
        self.calls = []

    async def apply(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        self.calls.append((args, kwargs))
        return self.result


def _service(
    *,
    decision: LifecycleDecisionAuditRecord | None = None,
    changes: list[LifecycleChangeAuditRecord] | None = None,
    current: LifecycleCandidateSnapshot | None = None,
    chain: tuple[LifecycleMemorySnapshot, ...] | None = None,
):
    source, source_changes, archived, source_current = _source_audit()
    selected_decision = source if decision is None else decision
    selected_changes = source_changes if changes is None else changes
    selected_current = source_current if current is None else current
    application = LifecycleApplicationResult(
        decision=source,
        changes=(source_changes[-1],),
    )
    event_repository = _EventRepository()
    applier = _Applier(application)
    service = CompensationService(
        audit_repository=_AuditRepository(selected_decision, selected_changes),  # type: ignore[arg-type]
        memory_repository=_MemoryRepository(
            (selected_current,),
            chain or (archived, selected_current),
        ),  # type: ignore[arg-type]
        event_repository=event_repository,  # type: ignore[arg-type]
        applier=applier,  # type: ignore[arg-type]
    )
    return service, event_repository, applier, source


async def _plan(service: CompensationService, source_id: str):
    return await service.plan(
        source_decision_id=source_id,
        scope=SCOPE,
        operator="stage06_admin",
        reason="Restore the verified prior diagnosis",
        compensated_at=NOW + timedelta(minutes=1),
    )


async def test_plan_restores_prior_active_and_binds_current_chain_tail() -> None:
    service, _, _, source = _service()

    first = await _plan(service, source.decision_id)
    second = await service.plan(
        source_decision_id=source.decision_id,
        scope=SCOPE,
        operator="stage06_admin",
        reason="Restore the verified prior diagnosis",
        compensated_at=NOW + timedelta(minutes=2),
    )

    assert first.apply_token.startswith("sha256:")
    assert first.apply_token == second.apply_token
    assert first.decision_id == second.decision_id
    assert first.restore_from_memory_id == "stage06_compensation_prior_v1"
    assert first.policy_input.candidate.proposed_value.summary == "Correct prior diagnosis"
    assert first.policy_input.config.maximum_cas_recomputations == 0
    assert first.policy_result.decision.operation is LifecycleOperation.SUPERSEDE
    assert first.policy_result.decision.target_memory_ids == ["stage06_compensation_wrong_v2"]
    assert first.policy_input.candidate.evidence == {
        "source_decision_id": source.decision_id,
        "operator": "stage06_admin",
        "reason": "Restore the verified prior diagnosis",
    }


async def test_plan_rejects_scope_mismatch_and_later_dependent_version() -> None:
    service, _, _, source = _service()
    wrong_scope = SCOPE.model_copy(update={"user_id": "another_user"})
    with pytest.raises(CompensationValidationError, match="Scope"):
        await service.plan(
            source_decision_id=source.decision_id,
            scope=wrong_scope,
            operator="stage06_admin",
            reason="Restore prior",
            compensated_at=NOW,
        )

    later = LifecycleCandidateSnapshot.model_validate(
        _snapshot(
            memory_id="stage06_later_dependent_v3",
            version=3,
            state=LifecycleState.ACTIVE,
            summary="Later dependent",
            row_version=1,
        ).model_dump()
    )
    stale_service, _, _, _ = _service(current=later)
    with pytest.raises(CompensationValidationError, match="later dependent"):
        await _plan(stale_service, source.decision_id)


async def test_apply_requires_exact_dry_run_token_before_appending_l1() -> None:
    service, event_repository, applier, source = _service()
    plan = await _plan(service, source.decision_id)

    with pytest.raises(CompensationTokenError, match="does not match"):
        await service.apply(
            plan,
            apply_token="sha256:not-the-preview",
            applied_at=NOW + timedelta(minutes=1),
        )

    assert event_repository.events == []
    assert applier.calls == []

    result = await service.apply(
        plan,
        apply_token=plan.apply_token,
        applied_at=NOW + timedelta(minutes=1),
    )

    assert result is applier.result
    assert event_repository.events == [plan.policy_input.event]
    assert len(applier.calls) == 1
