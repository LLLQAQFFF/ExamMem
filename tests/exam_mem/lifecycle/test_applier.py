from __future__ import annotations

from collections.abc import Mapping, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timedelta, timezone
from types import TracebackType

import pytest

from exam_mem.contracts import (
    ErrorPatternValue,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
)
from exam_mem.domain.candidate_query import CandidateQuery
from exam_mem.lifecycle import (
    AuditAppendStatus,
    LifecycleApplicationConflict,
    LifecycleApplier,
    LifecycleApplyState,
    LifecycleCandidateSnapshot,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_applier_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


class _NestedTransaction(AbstractAsyncContextManager[None]):
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        return None


class _FakeConnection:
    def __init__(self) -> None:
        self.savepoint_count = 0

    def begin_nested(self) -> _NestedTransaction:
        self.savepoint_count += 1
        return _NestedTransaction()


class _FakeAuditRepository:
    def __init__(self) -> None:
        self.decisions: dict[str, LifecycleDecisionAuditRecord] = {}
        self.changes: dict[str, LifecycleChangeAuditRecord] = {}

    async def append_decision(
        self,
        record: LifecycleDecisionAuditRecord,
    ) -> AuditAppendStatus:
        existing = self.decisions.get(record.decision_id)
        if existing is None:
            self.decisions[record.decision_id] = record
            return AuditAppendStatus.CREATED
        return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus:
        existing = self.changes.get(record.change_id)
        if existing is None:
            self.changes[record.change_id] = record
            return AuditAppendStatus.CREATED
        return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def get_decision(self, decision_id: str) -> LifecycleDecisionAuditRecord | None:
        return self.decisions.get(decision_id)

    async def list_decisions_by_trace(
        self,
        trace_id: str,
    ) -> list[LifecycleDecisionAuditRecord]:
        return [record for record in self.decisions.values() if record.trace_id == trace_id]

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]:
        return [record for record in self.changes.values() if record.decision_id == decision_id]


class _FakeMemoryRepository:
    def __init__(self, authoritative: Sequence[LifecycleMemorySnapshot] = ()) -> None:
        self.snapshots = {snapshot.memory.memory_id: snapshot for snapshot in authoritative}
        self.provenance_relations: dict[str, dict[str, str]] = {}
        self.content_embeddings: dict[str, tuple[float, ...] | None] = {}

    async def next_version(self, scope: MemoryScope, slot_key: str) -> int:
        versions = [
            snapshot.memory.version
            for snapshot in self.snapshots.values()
            if snapshot.memory.scope == scope and snapshot.memory.slot_key == slot_key
        ]
        return max(versions, default=0) + 1

    async def find_candidate_snapshots(
        self,
        query: CandidateQuery,
        *,
        for_update: bool = False,
    ) -> list[LifecycleCandidateSnapshot]:
        del for_update
        return [
            LifecycleCandidateSnapshot.model_validate(snapshot.model_dump())
            for snapshot in self.snapshots.values()
            if snapshot.memory.scope == query.scope
            and snapshot.memory.slot_key == query.slot_key
            and snapshot.memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        ]

    async def event_was_applied(
        self,
        scope: MemoryScope,
        slot_key: str,
        event_id: str,
    ) -> bool:
        return any(
            snapshot.memory.scope == scope
            and snapshot.memory.slot_key == slot_key
            and event_id in snapshot.memory.provenance
            for snapshot in self.snapshots.values()
        )

    async def insert_version(
        self,
        memory: LearningMemory,
        *,
        policy_version: str,
        content_embedding: Sequence[float] | None = None,
        contested_group_id: str | None = None,
        provenance_relations: Mapping[str, str] | None = None,
    ) -> LifecycleMemorySnapshot:
        snapshot = LifecycleMemorySnapshot(
            memory=memory,
            row_version=1,
            contested_group_id=contested_group_id,
            policy_version=policy_version,
        )
        self.snapshots[memory.memory_id] = snapshot
        self.provenance_relations[memory.memory_id] = dict(provenance_relations or {})
        self.content_embeddings[memory.memory_id] = (
            None if content_embedding is None else tuple(content_embedding)
        )
        return snapshot

    async def get_lifecycle_snapshot(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> LifecycleMemorySnapshot | None:
        snapshot = self.snapshots.get(memory_id)
        return snapshot if snapshot is not None and snapshot.memory.scope == scope else None

    async def cas_transition(
        self,
        scope: MemoryScope,
        slot_key: str,
        memory_id: str,
        *,
        expected_row_version: int,
        to_state: LifecycleState,
        valid_to: datetime | None,
        superseded_by: str | None = None,
        contested_group_id: str | None = None,
        provenance_event_id: str | None = None,
        provenance_relation: str | None = None,
    ) -> LifecycleMemorySnapshot | None:
        current = self.snapshots.get(memory_id)
        if (
            current is None
            or current.memory.scope != scope
            or current.memory.slot_key != slot_key
            or current.row_version != expected_row_version
            or current.memory.lifecycle_state
            not in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        ):
            return None
        provenance = list(current.memory.provenance)
        if provenance_event_id is not None:
            provenance.append(provenance_event_id)
            self.provenance_relations.setdefault(memory_id, {})[provenance_event_id] = (
                provenance_relation or ""
            )
        updated_memory = LearningMemory.model_validate(
            {
                **current.memory.model_dump(mode="json"),
                "lifecycle_state": to_state.value,
                "valid_to": valid_to,
                "superseded_by": superseded_by,
                "provenance": provenance,
                "evidence_count": len(provenance),
            }
        )
        updated = LifecycleMemorySnapshot(
            memory=updated_memory,
            row_version=current.row_version + 1,
            contested_group_id=contested_group_id,
            policy_version=current.policy_version,
        )
        self.snapshots[memory_id] = updated
        return updated


def _event(event_id: str = "stage06_applier_event_002") -> LearningEvent:
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
            "session_id": "stage06_applier_session",
            "question_id": "stage06_applier_question",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "new detail",
            "occurred_at": NOW,
        }
    )


def _value(*, details: list[str] | None = None) -> ErrorPatternValue:
    return ErrorPatternValue(
        error_type="concept_confusion",
        summary="Confuses conditional probability",
        details=details or ["old detail"],
    )


def _snapshot(
    *,
    memory_id: str = "stage06_applier_memory_v1",
    row_version: int = 1,
    state: LifecycleState = LifecycleState.ACTIVE,
    version: int = 1,
    group_id: str | None = None,
    provenance: list[str] | None = None,
) -> LifecycleCandidateSnapshot:
    events = provenance or ["stage06_applier_event_001"]
    memory = LearningMemory(
        memory_id=memory_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        value=_value(),
        confidence=0.8,
        evidence_count=len(events),
        lifecycle_state=state,
        version=version,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        superseded_by=None,
        provenance=events,
    )
    return LifecycleCandidateSnapshot(
        memory=memory,
        row_version=row_version,
        contested_group_id=group_id,
        policy_version="lifecycle_policy_v1",
    )


def _case(
    operation: LifecycleOperation,
    *,
    targets: tuple[LifecycleCandidateSnapshot, ...] = (),
    candidate_snapshots: tuple[LifecycleCandidateSnapshot, ...] | None = None,
    reason_code: str = "controlled_applier_case",
    event_id: str = "stage06_applier_event_002",
) -> tuple[LifecyclePolicyInput, LifecyclePolicyResult]:
    event = _event(event_id)
    candidate = MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value=_value(details=["new detail"]),
        evidence={"source": "controlled_applier_test"},
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        candidate_snapshots=(targets if candidate_snapshots is None else candidate_snapshots),
        evaluated_at=NOW,
    )
    target_ids = [target.memory.memory_id for target in targets]
    expected = (
        {target.memory.memory_id: target.row_version for target in targets}
        if operation
        in {
            LifecycleOperation.MERGE,
            LifecycleOperation.SUPERSEDE,
            LifecycleOperation.INVALIDATE,
            LifecycleOperation.CONTESTED,
        }
        else {}
    )
    return policy_input, LifecyclePolicyResult(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        decision=LifecycleDecision(
            operation=operation,
            target_memory_ids=target_ids,
            reason_code=reason_code,
            confidence=0.8,
            policy_version="lifecycle_policy_v1",
        ),
        expected_row_versions=expected,
    )


def _applier(
    memory_repository: _FakeMemoryRepository,
    audit_repository: _FakeAuditRepository | None = None,
) -> tuple[LifecycleApplier, _FakeConnection, _FakeAuditRepository]:
    connection = _FakeConnection()
    audit = audit_repository or _FakeAuditRepository()
    return (
        LifecycleApplier(
            connection,  # type: ignore[arg-type]
            memory_repository=memory_repository,
            audit_repository=audit,
            event_repository=None,  # type: ignore[arg-type]
        ),
        connection,
        audit,
    )


async def test_add_creates_version_one_with_new_event_and_audit() -> None:
    policy_input, policy_result = _case(LifecycleOperation.ADD)
    memory_repository = _FakeMemoryRepository()
    applier, connection, audit = _applier(memory_repository)

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_add_decision",
        trace_id="stage06_add_trace",
        applied_at=NOW,
    )

    after = result.changes[0].after_state
    assert result.apply_state is LifecycleApplyState.APPLIED
    assert after is not None
    assert after.memory.version == 1
    assert after.memory.lifecycle_state is LifecycleState.ACTIVE
    assert after.memory.provenance == [policy_input.event.event_id]
    assert len(audit.decisions) == 1
    assert {change.apply_state for change in audit.changes.values()} == {
        LifecycleApplyState.PLANNED,
        LifecycleApplyState.APPLIED,
    }
    assert connection.savepoint_count == 2


async def test_add_embeds_the_final_memory_before_insert() -> None:
    class _EmbeddingClient:
        def __init__(self) -> None:
            self.calls: list[tuple[list[str], str | None]] = []

        async def embed(self, texts, *, input_type=None):  # noqa: ANN001, ANN201
            self.calls.append((texts, input_type))
            return [[1.0, *([0.0] * 1023)]]

    policy_input, policy_result = _case(LifecycleOperation.ADD)
    memory_repository = _FakeMemoryRepository()
    connection = _FakeConnection()
    audit = _FakeAuditRepository()
    embedding = _EmbeddingClient()
    applier = LifecycleApplier(
        connection,  # type: ignore[arg-type]
        memory_repository=memory_repository,
        audit_repository=audit,
        event_repository=None,  # type: ignore[arg-type]
        embedding_client=embedding,
    )

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_embedded_add_decision",
        trace_id="stage06_embedded_add_trace",
        applied_at=NOW,
    )

    memory_id = result.changes[0].memory_id
    assert memory_id is not None
    assert memory_repository.content_embeddings[memory_id] == (1.0, *([0.0] * 1023))
    assert embedding.calls[0][1] == "search_document"
    assert policy_input.candidate.slot_key in embedding.calls[0][0][0]


async def test_replay_no_op_is_idempotent_and_reuses_terminal_audit() -> None:
    target = _snapshot(provenance=["stage06_applier_event_002"])
    policy_input, policy_result = _case(
        LifecycleOperation.NO_OP,
        targets=(target,),
        reason_code="already_applied_replay",
    )
    memory_repository = _FakeMemoryRepository((target,))
    applier, _, audit = _applier(memory_repository)

    first = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_replay_decision",
        trace_id="stage06_replay_trace",
        applied_at=NOW,
    )
    second = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_replay_decision",
        trace_id="stage06_replay_trace",
        applied_at=NOW,
    )

    assert first == second
    assert first.apply_state is LifecycleApplyState.IDEMPOTENT
    assert len(memory_repository.snapshots) == 1
    assert len(audit.changes) == 2


@pytest.mark.parametrize("operation", [LifecycleOperation.MERGE, LifecycleOperation.SUPERSEDE])
async def test_replacement_archives_old_and_creates_next_version(
    operation: LifecycleOperation,
) -> None:
    target = _snapshot()
    policy_input, policy_result = _case(operation, targets=(target,))
    memory_repository = _FakeMemoryRepository((target,))
    applier, _, _ = _applier(memory_repository)

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id=f"stage06_{operation.value.lower()}_decision",
        trace_id=f"stage06_{operation.value.lower()}_trace",
        applied_at=NOW,
    )

    archived = memory_repository.snapshots[target.memory.memory_id]
    inserted = max(
        memory_repository.snapshots.values(),
        key=lambda snapshot: snapshot.memory.version,
    )
    assert result.apply_state is LifecycleApplyState.APPLIED
    assert archived.memory.lifecycle_state is LifecycleState.ARCHIVED
    assert archived.memory.superseded_by == inserted.memory.memory_id
    assert inserted.memory.version == 2
    assert inserted.memory.lifecycle_state is LifecycleState.ACTIVE
    assert inserted.memory.provenance == [
        "stage06_applier_event_001",
        "stage06_applier_event_002",
    ]
    if operation is LifecycleOperation.MERGE:
        assert isinstance(inserted.memory.value, ErrorPatternValue)
        assert inserted.memory.value.details == ["old detail", "new detail"]


async def test_invalidate_terminates_without_creating_replacement() -> None:
    target = _snapshot()
    policy_input, policy_result = _case(LifecycleOperation.INVALIDATE, targets=(target,))
    memory_repository = _FakeMemoryRepository((target,))
    applier, _, _ = _applier(memory_repository)

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_invalidate_decision",
        trace_id="stage06_invalidate_trace",
        applied_at=NOW,
    )

    after = memory_repository.snapshots[target.memory.memory_id]
    assert result.apply_state is LifecycleApplyState.APPLIED
    assert len(memory_repository.snapshots) == 1
    assert after.memory.lifecycle_state is LifecycleState.INVALIDATED
    assert after.memory.valid_to == NOW
    assert after.memory.provenance[-1] == policy_input.event.event_id
    assert (
        memory_repository.provenance_relations[target.memory.memory_id][policy_input.event.event_id]
        == "invalidated_by"
    )


async def test_contested_preserves_active_and_creates_grouped_branch() -> None:
    target = _snapshot()
    policy_input, policy_result = _case(LifecycleOperation.CONTESTED, targets=(target,))
    memory_repository = _FakeMemoryRepository((target,))
    applier, _, _ = _applier(memory_repository)

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_contested_decision",
        trace_id="stage06_contested_trace",
        applied_at=NOW,
    )

    active = memory_repository.snapshots[target.memory.memory_id]
    branch = max(
        memory_repository.snapshots.values(),
        key=lambda snapshot: snapshot.memory.version,
    )
    assert result.apply_state is LifecycleApplyState.CONTESTED
    assert active.memory.lifecycle_state is LifecycleState.ACTIVE
    assert branch.memory.lifecycle_state is LifecycleState.CONTESTED
    assert active.contested_group_id == branch.contested_group_id
    assert branch.memory.provenance == [policy_input.event.event_id]
    assert memory_repository.provenance_relations[branch.memory.memory_id] == {
        policy_input.event.event_id: "contradicted_by"
    }


@pytest.mark.contested
@pytest.mark.parametrize(
    ("target_state", "expected_new_state"),
    [
        (LifecycleState.ACTIVE, LifecycleState.ACTIVE),
        (LifecycleState.CONTESTED, LifecycleState.CONTESTED),
    ],
)
async def test_single_branch_merge_advances_branch_without_closing_contested_group(
    target_state: LifecycleState,
    expected_new_state: LifecycleState,
) -> None:
    group_id = "stage06_open_contested_group"
    active = _snapshot(
        memory_id="stage06_group_active_v1",
        state=LifecycleState.ACTIVE,
        version=1,
        group_id=group_id,
    )
    contested = _snapshot(
        memory_id="stage06_group_contested_v2",
        state=LifecycleState.CONTESTED,
        version=2,
        group_id=group_id,
    )
    target = active if target_state is LifecycleState.ACTIVE else contested
    policy_input, policy_result = _case(
        LifecycleOperation.MERGE,
        targets=(target,),
        candidate_snapshots=(active, contested),
    )
    memory_repository = _FakeMemoryRepository((active, contested))
    applier, _, _ = _applier(memory_repository)

    await applier.apply(
        policy_input,
        policy_result,
        decision_id=f"stage06_advance_{target_state.value}_branch",
        trace_id=f"stage06_advance_{target_state.value}_trace",
        applied_at=NOW,
    )

    inserted = max(
        memory_repository.snapshots.values(),
        key=lambda snapshot: snapshot.memory.version,
    )
    other = contested if target is active else active
    assert memory_repository.snapshots[target.memory.memory_id].memory.lifecycle_state is (
        LifecycleState.ARCHIVED
    )
    assert memory_repository.snapshots[other.memory.memory_id] == other
    assert inserted.memory.lifecycle_state is expected_new_state
    assert inserted.contested_group_id == group_id


@pytest.mark.contested
@pytest.mark.parametrize(
    "operation",
    [LifecycleOperation.MERGE, LifecycleOperation.SUPERSEDE],
)
async def test_two_branch_resolution_closes_group_with_one_stable_active(
    operation: LifecycleOperation,
) -> None:
    group_id = "stage06_resolved_contested_group"
    active = _snapshot(
        memory_id="stage06_resolved_active_v3",
        state=LifecycleState.ACTIVE,
        version=3,
        group_id=group_id,
    )
    contested = _snapshot(
        memory_id="stage06_resolved_contested_v4",
        state=LifecycleState.CONTESTED,
        version=4,
        group_id=group_id,
    )
    targets = (active, contested)
    policy_input, policy_result = _case(operation, targets=targets)
    memory_repository = _FakeMemoryRepository(targets)
    applier, _, _ = _applier(memory_repository)

    await applier.apply(
        policy_input,
        policy_result,
        decision_id=f"stage06_resolve_group_{operation.value.lower()}",
        trace_id=f"stage06_resolve_group_{operation.value.lower()}_trace",
        applied_at=NOW,
    )

    inserted = max(
        memory_repository.snapshots.values(),
        key=lambda snapshot: snapshot.memory.version,
    )
    assert all(
        memory_repository.snapshots[target.memory.memory_id].memory.lifecycle_state
        is LifecycleState.ARCHIVED
        for target in targets
    )
    assert inserted.memory.lifecycle_state is LifecycleState.ACTIVE
    assert inserted.contested_group_id is None


async def test_stale_cas_exhaustion_records_versions_without_l2_write() -> None:
    policy_snapshot = _snapshot(row_version=1)
    authoritative = LifecycleMemorySnapshot(
        memory=policy_snapshot.memory,
        row_version=2,
        policy_version=policy_snapshot.policy_version,
    )
    policy_input, policy_result = _case(
        LifecycleOperation.SUPERSEDE,
        targets=(policy_snapshot,),
    )
    policy_input = policy_input.model_copy(
        update={"config": policy_input.config.model_copy(update={"maximum_cas_recomputations": 0})}
    )
    memory_repository = _FakeMemoryRepository((authoritative,))
    applier, _, audit = _applier(memory_repository)

    result = await applier.apply(
        policy_input,
        policy_result,
        decision_id="stage06_stale_decision",
        trace_id="stage06_stale_trace",
        applied_at=NOW,
    )

    assert result.apply_state is LifecycleApplyState.FAILED
    assert result.changes[0].error_code == "cas_recompute_exhausted"
    assert len(memory_repository.snapshots) == 1
    assert {change.apply_state for change in audit.changes.values()} == {
        LifecycleApplyState.PLANNED,
        LifecycleApplyState.STALE,
        LifecycleApplyState.FAILED,
    }
    stale = next(
        change
        for change in audit.changes.values()
        if change.apply_state is LifecycleApplyState.STALE
    )
    assert stale.expected_row_version == 1
    assert stale.actual_row_version == 2


async def test_mutating_decision_rejects_missing_cas_version() -> None:
    target = _snapshot()
    policy_input, policy_result = _case(LifecycleOperation.MERGE, targets=(target,))
    invalid_result = policy_result.model_copy(update={"expected_row_versions": {}})
    applier, _, _ = _applier(_FakeMemoryRepository((target,)))

    with pytest.raises(LifecycleApplicationConflict, match="one CAS version per target"):
        await applier.apply(
            policy_input,
            invalid_result,
            decision_id="stage06_invalid_decision",
            trace_id="stage06_invalid_trace",
            applied_at=NOW,
        )
