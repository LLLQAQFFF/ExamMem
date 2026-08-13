from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
import os
from typing import Any

import pytest
from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine
from sqlalchemy.sql.schema import Table

from exam_mem.storage import load_database_settings
from exam_mem.storage.models import (
    baseline_memory_facts,
    learning_events,
    lifecycle_decisions,
    memory_change_log,
    practice_trace_spans,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.migration]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def _assert_mutation_is_rejected(
    connection: AsyncConnection,
    *,
    table: Table,
    primary_key: str,
    primary_key_value: str,
    insert_values: Mapping[str, Any],
    update_values: Mapping[str, Any],
) -> None:
    await connection.execute(insert(table).values(**insert_values))

    update_savepoint = await connection.begin_nested()
    with pytest.raises(DBAPIError, match="is append-only"):
        await connection.execute(
            update(table).where(table.c[primary_key] == primary_key_value).values(**update_values)
        )
    await update_savepoint.rollback()

    delete_savepoint = await connection.begin_nested()
    with pytest.raises(DBAPIError, match="is append-only"):
        await connection.execute(delete(table).where(table.c[primary_key] == primary_key_value))
    await delete_savepoint.rollback()

    remaining = await connection.scalar(
        select(table.c[primary_key]).where(table.c[primary_key] == primary_key_value)
    )
    assert remaining == primary_key_value


async def test_learning_events_are_append_only_in_postgresql() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _assert_mutation_is_rejected(
                    connection,
                    table=learning_events,
                    primary_key="event_id",
                    primary_key_value="append_only_event_001",
                    insert_values={
                        "event_id": "append_only_event_001",
                        "idempotency_key": "append-only-event-001",
                        "user_id": "append_only_user",
                        "exam_id": "postgraduate_entrance_exam",
                        "subject_id": "math_1",
                        "event_type": "answer_attempt",
                        "session_id": "append_only_session",
                        "question_id": "append_only_question",
                        "knowledge_point_ids": ["linear_algebra.matrix.rank"],
                        "primary_knowledge_point_id": "linear_algebra.matrix.rank",
                        "difficulty": 0.5,
                        "answer_correct": True,
                        "error_type": None,
                        "error_detail": None,
                        "evidence_quality": {
                            "confidence": 1.0,
                            "is_temporary_exception": False,
                            "reasons": [],
                        },
                        "correction_source": None,
                        "correction_statement": None,
                        "plan_transition_status": None,
                        "plan_transition_source": None,
                        "plan_transition_reason": None,
                        "raw_payload": {"source": "append-only-test"},
                        "occurred_at": datetime(2026, 8, 11, tzinfo=timezone.utc),
                        "trace_id": "append_only_trace_001",
                        "schema_version": 1,
                    },
                    update_values={"trace_id": "forbidden_mutation"},
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_baseline_memory_facts_are_append_only_in_postgresql() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                event_id = "append_only_baseline_event_001"
                slot_key = "mastery:math1.linear_algebra.matrix_rank"
                await connection.execute(
                    insert(learning_events).values(
                        event_id=event_id,
                        idempotency_key="append-only-baseline-event-001",
                        user_id="append_only_baseline_user",
                        exam_id="postgraduate_entrance_exam",
                        subject_id="math_1",
                        event_type="answer_attempt",
                        session_id="append_only_baseline_session",
                        question_id="append_only_baseline_question",
                        knowledge_point_ids=["math1.linear_algebra.matrix_rank"],
                        primary_knowledge_point_id="math1.linear_algebra.matrix_rank",
                        difficulty=0.5,
                        answer_correct=True,
                        error_type=None,
                        error_detail=None,
                        evidence_quality={
                            "confidence": 1.0,
                            "is_temporary_exception": False,
                            "reasons": [],
                        },
                        correction_source=None,
                        correction_statement=None,
                        plan_transition_status=None,
                        plan_transition_source=None,
                        plan_transition_reason=None,
                        raw_payload={"source": "append-only-baseline-test"},
                        occurred_at=datetime(2026, 8, 12, tzinfo=timezone.utc),
                        trace_id="append_only_baseline_trace_001",
                        schema_version=1,
                    )
                )
                await connection.execute(
                    insert(baseline_memory_facts).values(
                        backend_mode="append_only",
                        event_id=event_id,
                        user_id="append_only_baseline_user",
                        exam_id="postgraduate_entrance_exam",
                        subject_id="math_1",
                        memory_namespace="mastery",
                        slot_key=slot_key,
                        value={"type": "mastery", "level": "low", "score": 0.3},
                        evidence={"source": "append-only-baseline-test"},
                        content_embedding=None,
                    )
                )
                fact_predicate = (
                    (baseline_memory_facts.c.backend_mode == "append_only")
                    & (baseline_memory_facts.c.event_id == event_id)
                    & (baseline_memory_facts.c.slot_key == slot_key)
                )

                update_savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError, match="is append-only"):
                    await connection.execute(
                        update(baseline_memory_facts)
                        .where(fact_predicate)
                        .values(evidence={"source": "forbidden-mutation"})
                    )
                await update_savepoint.rollback()

                delete_savepoint = await connection.begin_nested()
                with pytest.raises(DBAPIError, match="is append-only"):
                    await connection.execute(delete(baseline_memory_facts).where(fact_predicate))
                await delete_savepoint.rollback()

                remaining = await connection.scalar(
                    select(func.count()).select_from(baseline_memory_facts).where(fact_predicate)
                )
                assert remaining == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


async def test_practice_trace_spans_are_append_only_in_postgresql() -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await _assert_mutation_is_rejected(
                    connection,
                    table=practice_trace_spans,
                    primary_key="trace_id",
                    primary_key_value="append_only_practice_trace_001",
                    insert_values={
                        "trace_id": "append_only_practice_trace_001",
                        "step_id": 1,
                        "span_name": "request_received",
                        "status": "completed",
                        "input_summary": {"practice_session_id": "practice:001"},
                        "output_summary": {"resumed_from_state": "IDLE"},
                        "versions": {},
                        "started_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
                        "completed_at": datetime(2026, 8, 12, tzinfo=timezone.utc),
                        "duration_ms": 0.0,
                        "retry_count": 0,
                        "llm_calls": 0,
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "error_code": None,
                        "related_record_ids": [],
                    },
                    update_values={"status": "failed", "error_code": "forbidden"},
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    ("table", "primary_key", "primary_key_value", "insert_values", "update_values"),
    [
        (
            lifecycle_decisions,
            "decision_id",
            "append_only_decision_001",
            {
                "decision_id": "append_only_decision_001",
                "trace_id": "append_only_trace_002",
                "event_id": "append_only_audit_event_001",
                "input_summary": {},
                "candidate_memory_ids": [],
                "operation": "NO_OP",
                "reason": "append-only contract test",
                "confidence": 1.0,
                "policy_version": "lifecycle_policy_v1",
            },
            {"reason": "forbidden mutation"},
        ),
        (
            memory_change_log,
            "change_id",
            "append_only_change_001",
            {
                "change_id": "append_only_change_001",
                "decision_id": "append_only_decision_001",
                "before_state": None,
                "after_state": None,
                "apply_state": "PLANNED",
                "memory_id": None,
                "expected_row_version": None,
                "actual_row_version": None,
                "error_code": None,
                "trace_id": "append_only_trace_002",
            },
            {"apply_state": "forbidden_mutation"},
        ),
    ],
)
async def test_audit_records_are_append_only_in_postgresql(
    table: Table,
    primary_key: str,
    primary_key_value: str,
    insert_values: Mapping[str, Any],
    update_values: Mapping[str, Any],
) -> None:
    engine = create_async_engine(_database_url_or_skip())
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                await connection.execute(
                    insert(learning_events).values(
                        event_id="append_only_audit_event_001",
                        idempotency_key="append-only-audit-event-001",
                        user_id="append_only_user",
                        exam_id="postgraduate_entrance_exam",
                        subject_id="math_1",
                        event_type="answer_attempt",
                        session_id="append_only_session",
                        question_id="append_only_audit_question",
                        knowledge_point_ids=["linear_algebra.matrix.rank"],
                        primary_knowledge_point_id="linear_algebra.matrix.rank",
                        difficulty=0.5,
                        answer_correct=True,
                        error_type=None,
                        error_detail=None,
                        evidence_quality={
                            "confidence": 1.0,
                            "is_temporary_exception": False,
                            "reasons": [],
                        },
                        correction_source=None,
                        correction_statement=None,
                        plan_transition_status=None,
                        plan_transition_source=None,
                        plan_transition_reason=None,
                        raw_payload={"source": "append-only-audit-test"},
                        occurred_at=datetime(2026, 8, 11, tzinfo=timezone.utc),
                        trace_id="append_only_trace_002",
                        schema_version=1,
                    )
                )
                if table is memory_change_log:
                    await connection.execute(
                        insert(lifecycle_decisions).values(
                            decision_id="append_only_decision_001",
                            trace_id="append_only_trace_002",
                            event_id="append_only_audit_event_001",
                            input_summary={},
                            candidate_memory_ids=[],
                            operation="NO_OP",
                            reason="append-only prerequisite",
                            confidence=1.0,
                            policy_version="lifecycle_policy_v1",
                        )
                    )
                await _assert_mutation_is_rejected(
                    connection,
                    table=table,
                    primary_key=primary_key,
                    primary_key_value=primary_key_value,
                    insert_values=insert_values,
                    update_values=update_values,
                )
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
