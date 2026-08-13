from __future__ import annotations

from datetime import datetime, timezone
import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.contracts import LearningEvent, LearningMemory, MemoryScope, MemoryUpdateCandidate
from exam_mem.lifecycle import (
    AuditAppendStatus,
    LifecycleApplyState,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    decide_lifecycle,
)
from exam_mem.storage import (
    AppendStatus,
    AuditLinkError,
    LifecycleAuditRepository,
    PostgresLearningEventRepository,
    PostgresLifecycleAuditRepository,
    load_database_settings,
)
from exam_mem.storage.audit_repository import (
    AuditRepositoryInvariantError,
    _change_from_row,
    _change_row,
    _decision_from_row,
    _decision_row,
)

pytestmark = [pytest.mark.database, pytest.mark.repository]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_audit_repository_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


def _event() -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "stage06_audit_repository_event",
            "idempotency_key": "idem:stage06_audit_repository_event",
            "event_type": "answer_attempt",
            "context": {
                "user_id": SCOPE.user_id,
                "exam_id": SCOPE.exam_id,
                "subject_id": SCOPE.subject_id,
            },
            "session_id": "stage06_audit_repository_session",
            "question_id": "stage06_audit_repository_question",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": "reversed conditional direction",
            "occurred_at": NOW,
        }
    )


def _decision_record() -> LifecycleDecisionAuditRecord:
    event = _event()
    candidate = MemoryUpdateCandidate.model_validate(
        {
            "event_id": event.event_id,
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "proposed_value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses conditional direction",
                "details": ["reverses prior and posterior"],
            },
            "evidence": {"source": "repository_test"},
        }
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        evaluated_at=NOW,
    )
    return LifecycleDecisionAuditRecord(
        decision_id="stage06_audit_repository_decision",
        trace_id="stage06_audit_repository_trace",
        policy_input=policy_input,
        policy_result=decide_lifecycle(policy_input),
        created_at=NOW,
    )


def _planned_change() -> LifecycleChangeAuditRecord:
    return LifecycleChangeAuditRecord(
        change_id="stage06_audit_repository_change",
        decision_id="stage06_audit_repository_decision",
        trace_id="stage06_audit_repository_trace",
        apply_state=LifecycleApplyState.PLANNED,
        recorded_at=NOW,
    )


def _applied_change() -> LifecycleChangeAuditRecord:
    memory = LearningMemory.model_validate(
        {
            "memory_id": "stage06_audit_repository_memory",
            "scope": SCOPE.model_dump(mode="json"),
            "slot_key": SLOT_KEY,
            "value": {
                "type": "error_pattern",
                "error_type": "concept_confusion",
                "summary": "Confuses conditional direction",
                "details": ["reverses prior and posterior"],
            },
            "confidence": 0.8,
            "evidence_count": 1,
            "lifecycle_state": "active",
            "version": 1,
            "valid_from": NOW,
            "valid_to": None,
            "superseded_by": None,
            "provenance": [_event().event_id],
        }
    )
    snapshot = LifecycleMemorySnapshot(
        memory=memory,
        row_version=1,
        policy_version="lifecycle_policy_v1",
    )
    return LifecycleChangeAuditRecord(
        change_id="stage06_audit_repository_applied_change",
        decision_id="stage06_audit_repository_decision",
        trace_id="stage06_audit_repository_trace",
        apply_state=LifecycleApplyState.APPLIED,
        memory_id=memory.memory_id,
        after_state=snapshot,
        actual_row_version=snapshot.row_version,
        recorded_at=NOW,
    )


def test_audit_row_mapping_round_trips_strict_contracts() -> None:
    decision = _decision_record()
    change = _planned_change()
    applied = _applied_change()

    assert _decision_from_row(_decision_row(decision)) == decision
    assert _change_from_row(_change_row(change)) == change
    assert _change_from_row(_change_row(applied)) == applied


def test_decision_row_mapping_detects_denormalized_column_drift() -> None:
    row = _decision_row(_decision_record())
    row["operation"] = "SUPERSEDE"

    with pytest.raises(AuditRepositoryInvariantError, match="disagree"):
        _decision_from_row(row)


@pytest.mark.asyncio
async def test_postgres_audit_repository_appends_idempotently_and_queries_trace() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_status = await PostgresLearningEventRepository(connection).append(_event())
                assert event_status.status is AppendStatus.CREATED

                repository = PostgresLifecycleAuditRepository(connection)
                assert isinstance(repository, LifecycleAuditRepository)
                decision = _decision_record()
                assert await repository.append_decision(decision) is AuditAppendStatus.CREATED
                assert await repository.append_decision(decision) is AuditAppendStatus.EXISTING
                conflicting = decision.model_copy(update={"trace_id": "different_trace"})
                assert await repository.append_decision(conflicting) is AuditAppendStatus.CONFLICT

                change = _planned_change()
                assert await repository.append_change(change) is AuditAppendStatus.CREATED
                assert await repository.append_change(change) is AuditAppendStatus.EXISTING

                trail = await repository.get_trace(decision.trace_id)
                assert trail.decisions == (decision,)
                assert trail.changes == (change,)
                assert await repository.list_changes_by_decision(decision.decision_id) == [change]
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_postgres_audit_repository_rejects_unlinked_change() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                repository = PostgresLifecycleAuditRepository(connection)
                with pytest.raises(AuditLinkError, match="does not exist"):
                    await repository.append_change(_planned_change())
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
