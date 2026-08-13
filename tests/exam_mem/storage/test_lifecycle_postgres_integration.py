from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta, timezone
import os
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.schema import CreateSchema, DropSchema

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
from exam_mem.domain.candidate_query import (
    CandidateMatchReason,
    CandidateQuery,
    build_candidate_query,
)
from exam_mem.lifecycle import (
    AuditAppendStatus,
    CompensationService,
    LifecycleApplier,
    LifecycleApplyState,
    LifecycleChangeAuditRecord,
    LifecyclePolicyInput,
    LifecyclePolicyResult,
)
from exam_mem.storage import (
    AppendStatus,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    PostgresLifecycleAuditRepository,
    load_database_settings,
    metadata,
)
from exam_mem.storage.models import (
    learning_events,
    learning_memories,
    lifecycle_decisions,
    memory_change_log,
    memory_provenance,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.lifecycle]

NOW = datetime(2026, 8, 12, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_postgres_lifecycle_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="error_pattern",
)
SLOT_KEY = "error_pattern:math1.probability.bayes:concept_confusion"


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def _set_test_schema(connection: AsyncConnection, schema_name: str) -> None:
    await connection.execute(text(f'SET LOCAL search_path TO "{schema_name}", public'))


async def _create_test_schema(connection: AsyncConnection, schema_name: str) -> None:
    await connection.execute(CreateSchema(schema_name))
    await _set_test_schema(connection, schema_name)
    await connection.run_sync(
        lambda sync_connection: metadata.create_all(sync_connection, checkfirst=False)
    )


def _event(event_id: str, *, detail: str) -> LearningEvent:
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
            "session_id": f"session:{event_id}",
            "question_id": f"question:{event_id}",
            "knowledge_point_ids": ["math1.probability.bayes"],
            "difficulty": 0.6,
            "answer_correct": False,
            "error_type": "concept_confusion",
            "error_detail": detail,
            "occurred_at": NOW,
        }
    )


def _value(summary: str, detail: str) -> ErrorPatternValue:
    return ErrorPatternValue(
        error_type="concept_confusion",
        summary=summary,
        details=[detail],
    )


def _memory(event: LearningEvent) -> LearningMemory:
    return LearningMemory(
        memory_id="stage06_postgres_lifecycle_memory_v1",
        scope=SCOPE,
        slot_key=SLOT_KEY,
        value=_value("Verified prior diagnosis", "verified detail"),
        confidence=0.9,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        superseded_by=None,
        provenance=[event.event_id],
    )


async def _seed_initial_memory(connection: AsyncConnection) -> LearningMemory:
    event = _event("stage06_postgres_lifecycle_event_001", detail="verified detail")
    event_result = await PostgresLearningEventRepository(connection).append(event)
    assert event_result.status is AppendStatus.CREATED
    memory = _memory(event)
    await PostgresLearningMemoryRepository(connection).insert_version(
        memory,
        policy_version="lifecycle_policy_v1",
    )
    return memory


async def _replacement_case(
    connection: AsyncConnection,
    *,
    operation: LifecycleOperation,
) -> tuple[LifecyclePolicyInput, LifecyclePolicyResult]:
    event = _event("stage06_postgres_lifecycle_event_002", detail="wrong new detail")
    event_result = await PostgresLearningEventRepository(connection).append(event)
    assert event_result.status is AppendStatus.CREATED
    memory_repository = PostgresLearningMemoryRepository(connection)
    candidates = tuple(await memory_repository.find_candidate_snapshots(_candidate_query()))
    candidate = MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        proposed_value=_value("Incorrect replacement diagnosis", "wrong new detail"),
        evidence={"source": "postgres_lifecycle_integration"},
    )
    policy_input = LifecyclePolicyInput(
        event=event,
        candidate=candidate,
        candidate_snapshots=candidates,
        evaluated_at=NOW,
    )
    policy_result = LifecyclePolicyResult(
        event_id=event.event_id,
        scope=SCOPE,
        slot_key=SLOT_KEY,
        decision=LifecycleDecision(
            operation=operation,
            target_memory_ids=[snapshot.memory.memory_id for snapshot in candidates],
            reason_code="controlled_postgres_replacement",
            confidence=0.8,
            policy_version="lifecycle_policy_v1",
        ),
        expected_row_versions={
            snapshot.memory.memory_id: snapshot.row_version for snapshot in candidates
        },
    )
    return policy_input, policy_result


def _candidate_query() -> CandidateQuery:
    return build_candidate_query(
        scope=SCOPE,
        slot_key=SLOT_KEY,
        match_reason=CandidateMatchReason.EXACT_SLOT,
    )


def _applier(
    connection: AsyncConnection,
    *,
    memory_repository: PostgresLearningMemoryRepository | None = None,
    audit_repository: PostgresLifecycleAuditRepository | None = None,
) -> LifecycleApplier:
    events = PostgresLearningEventRepository(connection)
    return LifecycleApplier(
        connection,
        memory_repository=memory_repository or PostgresLearningMemoryRepository(connection),
        audit_repository=audit_repository or PostgresLifecycleAuditRepository(connection),
        event_repository=events,
    )


async def _apply_source_replacement(
    connection: AsyncConnection,
) -> tuple[LifecyclePolicyInput, LifecyclePolicyResult]:
    policy_input, policy_result = await _replacement_case(
        connection,
        operation=LifecycleOperation.SUPERSEDE,
    )
    result = await _applier(connection).apply(
        policy_input,
        policy_result,
        decision_id="stage06_postgres_wrong_decision",
        trace_id="stage06_postgres_wrong_trace",
        applied_at=NOW,
    )
    assert result.apply_state is LifecycleApplyState.APPLIED
    return policy_input, policy_result


async def test_postgres_compensation_dry_run_then_appends_recovery_version() -> None:
    schema_name = f"exammem_compensation_{uuid4().hex}"
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    try:
        async with engine.begin() as setup_connection:
            await _create_test_schema(setup_connection, schema_name)
            schema_created = True
            original = await _seed_initial_memory(setup_connection)
            await _apply_source_replacement(setup_connection)

        async with engine.connect() as preview_connection:
            preview_transaction = await preview_connection.begin()
            await _set_test_schema(preview_connection, schema_name)
            events = PostgresLearningEventRepository(preview_connection)
            memories = PostgresLearningMemoryRepository(preview_connection)
            audit = PostgresLifecycleAuditRepository(preview_connection)
            service = CompensationService(
                audit_repository=audit,
                memory_repository=memories,
                event_repository=events,
                applier=LifecycleApplier(
                    preview_connection,
                    memory_repository=memories,
                    audit_repository=audit,
                    event_repository=events,
                ),
            )
            preview = await service.plan(
                source_decision_id="stage06_postgres_wrong_decision",
                scope=SCOPE,
                operator="stage06_postgres_admin",
                reason="Restore verified prior diagnosis",
                compensated_at=NOW + timedelta(minutes=1),
            )
            preview_result = await service.apply(
                preview,
                apply_token=preview.apply_token,
                applied_at=NOW + timedelta(minutes=1),
            )
            assert preview_result.apply_state is LifecycleApplyState.APPLIED
            await preview_transaction.rollback()

        async with engine.begin() as after_preview:
            await _set_test_schema(after_preview, schema_name)
            assert (
                await after_preview.scalar(select(func.count()).select_from(learning_events)) == 2
            )
            assert (
                await after_preview.scalar(select(func.count()).select_from(learning_memories)) == 2
            )
            assert (
                await after_preview.scalar(select(func.count()).select_from(lifecycle_decisions))
                == 1
            )

        async with engine.connect() as apply_connection:
            apply_transaction = await apply_connection.begin()
            await _set_test_schema(apply_connection, schema_name)
            events = PostgresLearningEventRepository(apply_connection)
            memories = PostgresLearningMemoryRepository(apply_connection)
            audit = PostgresLifecycleAuditRepository(apply_connection)
            service = CompensationService(
                audit_repository=audit,
                memory_repository=memories,
                event_repository=events,
                applier=LifecycleApplier(
                    apply_connection,
                    memory_repository=memories,
                    audit_repository=audit,
                    event_repository=events,
                ),
            )
            apply_plan = await service.plan(
                source_decision_id="stage06_postgres_wrong_decision",
                scope=SCOPE,
                operator="stage06_postgres_admin",
                reason="Restore verified prior diagnosis",
                compensated_at=NOW + timedelta(minutes=2),
            )
            assert apply_plan.apply_token == preview.apply_token
            result = await service.apply(
                apply_plan,
                apply_token=preview.apply_token,
                applied_at=NOW + timedelta(minutes=2),
            )
            assert result.apply_state is LifecycleApplyState.APPLIED
            await apply_transaction.commit()

        async with engine.begin() as verification:
            await _set_test_schema(verification, schema_name)
            memories = await PostgresLearningMemoryRepository(verification).list_slot_snapshots(
                SCOPE,
                SLOT_KEY,
            )
            assert [snapshot.memory.version for snapshot in memories] == [1, 2, 3]
            assert [snapshot.memory.lifecycle_state for snapshot in memories] == [
                LifecycleState.ARCHIVED,
                LifecycleState.ARCHIVED,
                LifecycleState.ACTIVE,
            ]
            assert memories[0].memory.value == original.value
            assert memories[2].memory.value == original.value
            assert memories[0].memory.superseded_by == memories[1].memory.memory_id
            assert memories[1].memory.superseded_by == memories[2].memory.memory_id
            assert len(memories[2].memory.provenance) == 3
            assert await verification.scalar(select(func.count()).select_from(learning_events)) == 3
            assert (
                await verification.scalar(select(func.count()).select_from(lifecycle_decisions))
                == 2
            )
            assert (
                await verification.scalar(select(func.count()).select_from(memory_change_log)) == 6
            )
    finally:
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(
                    DropSchema(schema_name, cascade=True, if_exists=True)
                )
        await engine.dispose()


class _FaultInjectingMemoryRepository(PostgresLearningMemoryRepository):
    def __init__(self, connection: AsyncConnection, fail_at: str) -> None:
        super().__init__(connection)
        self._fail_at = fail_at

    async def cas_transition(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        result = await super().cas_transition(*args, **kwargs)
        if self._fail_at == "archive":
            raise RuntimeError("injected PostgreSQL failure at archive")
        return result

    async def insert_version(
        self,
        memory: LearningMemory,
        *,
        policy_version: str,
        content_embedding: Sequence[float] | None = None,
        contested_group_id: str | None = None,
        provenance_relations: Mapping[str, str] | None = None,
    ):
        if self._fail_at == "insert":
            raise RuntimeError("injected PostgreSQL failure at insert")
        result = await super().insert_version(
            memory,
            policy_version=policy_version,
            content_embedding=content_embedding,
            contested_group_id=contested_group_id,
            provenance_relations=provenance_relations,
        )
        if self._fail_at == "provenance":
            raise RuntimeError("injected PostgreSQL failure at provenance")
        return result


class _FaultInjectingAuditRepository(PostgresLifecycleAuditRepository):
    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus:
        status = await super().append_change(record)
        if record.apply_state is not LifecycleApplyState.PLANNED:
            raise RuntimeError("injected PostgreSQL failure at change_log")
        return status


@pytest.mark.fault_injection
@pytest.mark.parametrize("fail_at", ["archive", "insert", "provenance", "change_log"])
async def test_postgres_failure_rolls_back_l2_provenance_and_audit(fail_at: str) -> None:
    schema_name = f"exammem_fault_{fail_at}_{uuid4().hex}"
    engine = create_async_engine(_database_url_or_skip())
    schema_created = False
    try:
        async with engine.begin() as setup_connection:
            await _create_test_schema(setup_connection, schema_name)
            schema_created = True
            await _seed_initial_memory(setup_connection)

        async with engine.connect() as failing_connection:
            transaction = await failing_connection.begin()
            await _set_test_schema(failing_connection, schema_name)
            policy_input, policy_result = await _replacement_case(
                failing_connection,
                operation=LifecycleOperation.SUPERSEDE,
            )
            memory_repository = _FaultInjectingMemoryRepository(
                failing_connection,
                fail_at,
            )
            audit_repository = (
                _FaultInjectingAuditRepository(failing_connection)
                if fail_at == "change_log"
                else PostgresLifecycleAuditRepository(failing_connection)
            )
            with pytest.raises(RuntimeError, match=f"failure at {fail_at}"):
                await _applier(
                    failing_connection,
                    memory_repository=memory_repository,
                    audit_repository=audit_repository,
                ).apply(
                    policy_input,
                    policy_result,
                    decision_id=f"stage06_postgres_fault_{fail_at}",
                    trace_id=f"stage06_postgres_fault_{fail_at}_trace",
                    applied_at=NOW,
                )
            await transaction.rollback()

        async with engine.begin() as verification:
            await _set_test_schema(verification, schema_name)
            row = (
                await verification.execute(
                    select(
                        learning_memories.c.lifecycle_state,
                        learning_memories.c.version,
                        learning_memories.c.row_version,
                    )
                )
            ).one()
            assert row == (LifecycleState.ACTIVE.value, 1, 1)
            assert (
                await verification.scalar(select(func.count()).select_from(memory_provenance)) == 1
            )
            assert (
                await verification.scalar(select(func.count()).select_from(lifecycle_decisions))
                == 0
            )
            assert (
                await verification.scalar(select(func.count()).select_from(memory_change_log)) == 0
            )
            assert await verification.scalar(select(func.count()).select_from(learning_events)) == 1
    finally:
        if schema_created:
            async with engine.begin() as cleanup_connection:
                await cleanup_connection.execute(
                    DropSchema(schema_name, cascade=True, if_exists=True)
                )
        await engine.dispose()
