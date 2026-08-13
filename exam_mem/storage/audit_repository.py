"""Append-only PostgreSQL repository for lifecycle Decision and Change audit."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.lifecycle.audit import (
    AuditAppendStatus,
    LifecycleAuditTrail,
    LifecycleChangeAuditRecord,
    LifecycleDecisionAuditRecord,
)

from .models import lifecycle_decisions, memory_change_log


class AuditRepositoryInvariantError(RuntimeError):
    """Raised when persisted audit columns disagree with their strict payload."""


class AuditLinkError(ValueError):
    """Raised when a Change cannot be linked to its Decision and trace."""


@runtime_checkable
class LifecycleAuditRepository(Protocol):
    async def append_decision(
        self,
        record: LifecycleDecisionAuditRecord,
    ) -> AuditAppendStatus: ...

    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus: ...

    async def get_decision(
        self,
        decision_id: str,
    ) -> LifecycleDecisionAuditRecord | None: ...

    async def list_decisions_by_trace(
        self,
        trace_id: str,
    ) -> list[LifecycleDecisionAuditRecord]: ...

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]: ...

    async def get_trace(self, trace_id: str) -> LifecycleAuditTrail: ...


class PostgresLifecycleAuditRepository:
    """Persist audit rows on a caller-owned transaction without committing."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def append_decision(
        self,
        record: LifecycleDecisionAuditRecord,
    ) -> AuditAppendStatus:
        row = _decision_row(record)
        async with self._connection.begin_nested():
            inserted_id = await self._connection.scalar(
                postgresql_insert(lifecycle_decisions)
                .values(**row)
                .on_conflict_do_nothing()
                .returning(lifecycle_decisions.c.decision_id)
            )
            if inserted_id is not None:
                return AuditAppendStatus.CREATED

            existing = await self.get_decision(record.decision_id)
            if existing is None:
                return AuditAppendStatus.CONFLICT
            return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def append_change(
        self,
        record: LifecycleChangeAuditRecord,
    ) -> AuditAppendStatus:
        async with self._connection.begin_nested():
            decision = await self.get_decision(record.decision_id)
            if decision is None:
                raise AuditLinkError("change decision_id does not exist")
            if decision.trace_id != record.trace_id:
                raise AuditLinkError("change trace_id must match its decision")
            _validate_change_against_decision(record, decision)

            inserted_id = await self._connection.scalar(
                postgresql_insert(memory_change_log)
                .values(**_change_row(record))
                .on_conflict_do_nothing()
                .returning(memory_change_log.c.change_id)
            )
            if inserted_id is not None:
                return AuditAppendStatus.CREATED

            existing_row = (
                (
                    await self._connection.execute(
                        select(memory_change_log).where(
                            memory_change_log.c.change_id == record.change_id
                        )
                    )
                )
                .mappings()
                .one_or_none()
            )
            if existing_row is None:
                return AuditAppendStatus.CONFLICT
            existing = _change_from_row(dict(existing_row))
            return AuditAppendStatus.EXISTING if existing == record else AuditAppendStatus.CONFLICT

    async def get_decision(
        self,
        decision_id: str,
    ) -> LifecycleDecisionAuditRecord | None:
        row = (
            (
                await self._connection.execute(
                    select(lifecycle_decisions).where(
                        lifecycle_decisions.c.decision_id == decision_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return _decision_from_row(dict(row)) if row is not None else None

    async def list_decisions_by_trace(
        self,
        trace_id: str,
    ) -> list[LifecycleDecisionAuditRecord]:
        rows = (
            await self._connection.execute(
                select(lifecycle_decisions)
                .where(lifecycle_decisions.c.trace_id == trace_id)
                .order_by(
                    lifecycle_decisions.c.created_at,
                    lifecycle_decisions.c.decision_id,
                )
            )
        ).mappings()
        return [_decision_from_row(dict(row)) for row in rows]

    async def list_changes_by_decision(
        self,
        decision_id: str,
    ) -> list[LifecycleChangeAuditRecord]:
        rows = (
            await self._connection.execute(
                select(memory_change_log)
                .where(memory_change_log.c.decision_id == decision_id)
                .order_by(
                    memory_change_log.c.created_at,
                    memory_change_log.c.change_id,
                )
            )
        ).mappings()
        return [_change_from_row(dict(row)) for row in rows]

    async def get_trace(self, trace_id: str) -> LifecycleAuditTrail:
        decisions = await self.list_decisions_by_trace(trace_id)
        rows = (
            await self._connection.execute(
                select(memory_change_log)
                .where(memory_change_log.c.trace_id == trace_id)
                .order_by(
                    memory_change_log.c.created_at,
                    memory_change_log.c.change_id,
                )
            )
        ).mappings()
        changes = [_change_from_row(dict(row)) for row in rows]
        return LifecycleAuditTrail(
            trace_id=trace_id,
            decisions=tuple(decisions),
            changes=tuple(changes),
        )


def _decision_row(record: LifecycleDecisionAuditRecord) -> dict[str, Any]:
    policy_input = record.policy_input
    policy_result = record.policy_result
    decision = policy_result.decision
    return {
        "decision_id": record.decision_id,
        "trace_id": record.trace_id,
        "event_id": policy_input.event.event_id,
        "input_summary": {
            "policy_input": policy_input.model_dump(mode="json"),
            "policy_result": policy_result.model_dump(mode="json"),
        },
        "candidate_memory_ids": [
            snapshot.memory.memory_id
            for snapshot in sorted(
                policy_input.candidate_snapshots,
                key=lambda snapshot: (
                    snapshot.memory.version,
                    snapshot.memory.memory_id,
                ),
            )
        ],
        "operation": decision.operation.value,
        "reason": decision.reason_code,
        "confidence": decision.confidence,
        "policy_version": decision.policy_version,
        "created_at": record.created_at,
    }


def _decision_from_row(row: dict[str, Any]) -> LifecycleDecisionAuditRecord:
    try:
        input_summary = row["input_summary"]
        record = LifecycleDecisionAuditRecord.model_validate(
            {
                "decision_id": row["decision_id"],
                "trace_id": row["trace_id"],
                "policy_input": input_summary["policy_input"],
                "policy_result": input_summary["policy_result"],
                "created_at": row["created_at"],
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditRepositoryInvariantError("invalid persisted lifecycle decision") from exc

    expected = _decision_row(record)
    persisted_columns = (
        "event_id",
        "candidate_memory_ids",
        "operation",
        "reason",
        "confidence",
        "policy_version",
    )
    if any(row[column] != expected[column] for column in persisted_columns):
        raise AuditRepositoryInvariantError(
            "persisted lifecycle decision columns disagree with input_summary"
        )
    return record


def _change_row(record: LifecycleChangeAuditRecord) -> dict[str, Any]:
    return {
        "change_id": record.change_id,
        "decision_id": record.decision_id,
        "apply_state": record.apply_state.value,
        "memory_id": record.memory_id,
        "before_state": (
            record.before_state.model_dump(mode="json") if record.before_state is not None else None
        ),
        "after_state": (
            record.after_state.model_dump(mode="json") if record.after_state is not None else None
        ),
        "expected_row_version": record.expected_row_version,
        "actual_row_version": record.actual_row_version,
        "error_code": record.error_code,
        "trace_id": record.trace_id,
        "created_at": record.recorded_at,
    }


def _change_from_row(row: dict[str, Any]) -> LifecycleChangeAuditRecord:
    try:
        return LifecycleChangeAuditRecord.model_validate(
            {
                "change_id": row["change_id"],
                "decision_id": row["decision_id"],
                "trace_id": row["trace_id"],
                "apply_state": row["apply_state"],
                "memory_id": row["memory_id"],
                "before_state": row["before_state"],
                "after_state": row["after_state"],
                "expected_row_version": row["expected_row_version"],
                "actual_row_version": row["actual_row_version"],
                "error_code": row["error_code"],
                "recorded_at": row["created_at"],
            }
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise AuditRepositoryInvariantError("invalid persisted lifecycle change") from exc


def _validate_change_against_decision(
    change: LifecycleChangeAuditRecord,
    decision: LifecycleDecisionAuditRecord,
) -> None:
    for state in (change.before_state, change.after_state):
        if state is None:
            continue
        if state.memory.scope != decision.policy_result.scope:
            raise AuditLinkError("change state scope must match its decision")
        if state.memory.slot_key != decision.policy_result.slot_key:
            raise AuditLinkError("change state slot_key must match its decision")

    if change.expected_row_version is not None:
        if change.memory_id is None:
            raise AuditLinkError("expected row version requires change memory_id")
        expected = decision.policy_result.expected_row_versions.get(change.memory_id)
        if expected != change.expected_row_version:
            raise AuditLinkError("change expected row version must match its decision")


__all__ = [
    "AuditLinkError",
    "AuditRepositoryInvariantError",
    "LifecycleAuditRepository",
    "PostgresLifecycleAuditRepository",
]
