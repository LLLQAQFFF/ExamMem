from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from types import TracebackType

import pytest

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.lifecycle import (
    AuditAppendStatus,
    LifecycleApplier,
    LifecycleApplyState,
    LifecycleCandidateSnapshot,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    decide_lifecycle,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle, pytest.mark.fault_injection]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_fault_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


class _TransactionalState:
    def __init__(self, initial: LifecycleCandidateSnapshot) -> None:
        self.memories: dict[str, LifecycleMemorySnapshot] = {initial.memory.memory_id: initial}
        self.decisions: dict[str, LifecycleDecisionAuditRecord] = {}
        self.changes: dict[str, LifecycleChangeAuditRecord] = {}

    def snapshot(self) -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
        return (
            dict(self.memories),
            dict(self.decisions),
            dict(self.changes),
        )

    def restore(
        self,
        snapshot: tuple[dict[str, object], dict[str, object], dict[str, object]],
    ) -> None:
        memories, decisions, changes = snapshot
        self.memories = dict(memories)  # type: ignore[assignment]
        self.decisions = dict(decisions)  # type: ignore[assignment]
        self.changes = dict(changes)  # type: ignore[assignment]


class _Savepoint(AbstractAsyncContextManager[None]):
    def __init__(self, state: _TransactionalState) -> None:
        self._state = state
        self._snapshot: tuple[dict[str, object], dict[str, object], dict[str, object]] | None = None

    async def __aenter__(self) -> None:
        self._snapshot = self._state.snapshot()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        if exc_type is not None:
            assert self._snapshot is not None
            self._state.restore(self._snapshot)


class _Connection:
    def __init__(self, state: _TransactionalState) -> None:
        self._state = state

    def begin_nested(self) -> _Savepoint:
        return _Savepoint(self._state)


class _AuditRepository:
    def __init__(self, state: _TransactionalState, fail_at: str) -> None:
        self._state = state
        self._fail_at = fail_at

    async def append_decision(
        self,
        record: LifecycleDecisionAuditRecord,
    ) -> AuditAppendStatus:
        self._state.decisions[record.decision_id] = record
        return AuditAppendStatus.CREATED

    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus:
        if self._fail_at == "change_log" and record.apply_state is not LifecycleApplyState.PLANNED:
            raise RuntimeError("injected failure at change_log")
        self._state.changes[record.change_id] = record
        return AuditAppendStatus.CREATED

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]:
        return [
            change for change in self._state.changes.values() if change.decision_id == decision_id
        ]


class _MemoryRepository:
    def __init__(self, state: _TransactionalState, fail_at: str) -> None:
        self._state = state
        self._fail_at = fail_at

    async def next_version(self, scope: MemoryScope, slot_key: str) -> int:
        return (
            max(
                snapshot.memory.version
                for snapshot in self._state.memories.values()
                if snapshot.memory.scope == scope and snapshot.memory.slot_key == slot_key
            )
            + 1
        )

    async def cas_transition(
        self,
        scope: MemoryScope,
        slot_key: str,
        memory_id: str,
        *,
        expected_row_version: int,
        valid_to: datetime | None,
        superseded_by: str | None = None,
        contested_group_id: str | None = None,
        **_: object,
    ) -> LifecycleMemorySnapshot | None:
        current = self._state.memories[memory_id]
        assert current.memory.scope == scope
        assert current.memory.slot_key == slot_key
        assert current.row_version == expected_row_version
        archived = LifecycleMemorySnapshot(
            memory=current.memory.model_copy(
                update={
                    "lifecycle_state": LifecycleState.ARCHIVED,
                    "valid_to": valid_to,
                    "superseded_by": superseded_by,
                }
            ),
            row_version=current.row_version + 1,
            contested_group_id=contested_group_id,
            policy_version=current.policy_version,
        )
        self._state.memories[memory_id] = archived
        if self._fail_at == "archive":
            raise RuntimeError("injected failure at archive")
        return archived

    async def insert_version(
        self,
        memory: LearningMemory,
        *,
        policy_version: str,
        contested_group_id: str | None = None,
        **_: object,
    ) -> LifecycleMemorySnapshot:
        if self._fail_at == "insert":
            raise RuntimeError("injected failure at insert")
        inserted = LifecycleMemorySnapshot(
            memory=memory,
            row_version=1,
            contested_group_id=contested_group_id,
            policy_version=policy_version,
        )
        self._state.memories[memory.memory_id] = inserted
        if self._fail_at == "provenance":
            raise RuntimeError("injected failure at provenance")
        return inserted


def _case() -> tuple[LifecycleCandidateSnapshot, LifecyclePolicyInput, object]:
    event = LearningEvent.model_validate(
        {
            "event_id": "stage06_fault_event_002",
            "idempotency_key": "idem:stage06_fault_event_002",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_fault_session",
            "question_id": "stage06_fault_question",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.7,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "new detail",
            "occurred_at": NOW,
        }
    )
    old_value = ErrorPatternValue(
        error_type="concept_confusion",
        summary="Confuses conditional probability",
        details=["old detail"],
    )
    memory = LearningMemory(
        memory_id="stage06_fault_memory_v1",
        scope=SCOPE,
        slot_key=SLOT_KEY,
        value=old_value,
        confidence=0.8,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        superseded_by=None,
        provenance=["stage06_fault_event_001"],
    )
    snapshot = LifecycleCandidateSnapshot(
        memory=memory,
        row_version=1,
        policy_version="lifecycle_policy_v1",
    )
    candidate = MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value=old_value.model_copy(update={"details": ["new detail"]}),
        evidence={"source": "fault_injection_test"},
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        candidate_snapshots=(snapshot,),
        relation=ResolvedRelationClassification(
            target_memory_id=memory.memory_id,
            classification=RelationClassifierOutput(
                candidate_display_number=1,
                relation=MemoryRelation.COMPLEMENTARY,
                canonical_knowledge_point_id="math1.probability.bayes",
                error_type="concept_confusion",
                error_summary="Adds another manifestation",
                confidence=0.8,
                reason="The new evidence adds a distinct detail.",
            ),
        ),
        evaluated_at=NOW,
    )
    return snapshot, policy_input, decide_lifecycle(policy_input)


@pytest.mark.parametrize("fail_at", ["archive", "insert", "provenance", "change_log"])
async def test_injected_failure_rolls_back_l2_and_audit_savepoint(fail_at: str) -> None:
    initial, policy_input, policy_result = _case()
    state = _TransactionalState(initial)
    applier = LifecycleApplier(
        _Connection(state),  # type: ignore[arg-type]
        memory_repository=_MemoryRepository(state, fail_at),
        audit_repository=_AuditRepository(state, fail_at),
        event_repository=None,  # type: ignore[arg-type]
    )

    with pytest.raises(RuntimeError, match=f"injected failure at {fail_at}"):
        await applier.apply(
            policy_input,
            policy_result,  # type: ignore[arg-type]
            decision_id=f"stage06_fault_{fail_at}_decision",
            trace_id=f"stage06_fault_{fail_at}_trace",
            applied_at=NOW,
        )

    assert state.memories == {initial.memory.memory_id: initial}
    assert state.decisions == {}
    assert state.changes == {}
