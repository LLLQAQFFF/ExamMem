"""Versioned textbook binding, mapping, and source-snapshot repository."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.study import StudyPlanTree

from .models import (
    assessment_source_snapshots,
    learning_source_snapshots,
    objective_textbook_section_mappings,
    study_plan_textbook_bindings,
    study_plan_versions,
    study_plans,
    textbook_sections,
    textbook_versions,
    textbooks,
)


class GroundedLearningNotFound(LookupError):
    pass


class GroundedLearningConflict(RuntimeError):
    pass


class PostgresGroundedLearningRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def set_binding(
        self,
        *,
        binding_id: str,
        user_id: str,
        plan_id: str,
        plan_version: int,
        textbook_version_id: str,
        role: str,
        priority: int,
        status: str,
    ) -> dict[str, Any]:
        replay = (
            (
                await self._connection.execute(
                    select(study_plan_textbook_bindings).where(
                        study_plan_textbook_bindings.c.binding_id == binding_id,
                        study_plan_textbook_bindings.c.user_id == user_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if replay is not None:
            payload = _binding_payload(replay)
            if (
                payload["plan_id"],
                payload["plan_version"],
                payload["textbook_version_id"],
                payload["role"],
                payload["priority"],
                payload["status"],
            ) != (plan_id, plan_version, textbook_version_id, role, priority, status):
                raise GroundedLearningConflict("binding idempotency key conflicts")
            return payload
        await self._plan_version(user_id, plan_id, plan_version, require_active=True)
        version = await self._owned_textbook_version(user_id, textbook_version_id)
        textbook = await self._owned_textbook(user_id, version["textbook_id"])
        if textbook["archived_at"] is not None:
            raise GroundedLearningConflict("archived textbook versions cannot be newly bound")
        if version["status"] != "completed":
            raise GroundedLearningConflict("only completed textbook versions can be bound")
        current = await self.list_bindings(
            user_id=user_id, plan_id=plan_id, plan_version=plan_version
        )
        if (
            status == "confirmed"
            and role == "primary"
            and any(
                item["status"] == "confirmed"
                and item["role"] == "primary"
                and item["textbook_version_id"] != textbook_version_id
                for item in current
            )
        ):
            raise GroundedLearningConflict(
                "a plan version can have only one confirmed primary textbook"
            )
        revision = (
            int(
                (
                    await self._connection.execute(
                        select(func.max(study_plan_textbook_bindings.c.revision)).where(
                            study_plan_textbook_bindings.c.plan_id == plan_id,
                            study_plan_textbook_bindings.c.plan_version == plan_version,
                            study_plan_textbook_bindings.c.textbook_version_id
                            == textbook_version_id,
                        )
                    )
                ).scalar_one()
                or 0
            )
            + 1
        )
        now = datetime.now(timezone.utc)
        row = (
            (
                await self._connection.execute(
                    insert(study_plan_textbook_bindings)
                    .values(
                        binding_id=binding_id,
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        textbook_version_id=textbook_version_id,
                        revision=revision,
                        role=role,
                        priority=priority,
                        status=status,
                        confirmed_by=user_id if status == "confirmed" else None,
                        confirmed_at=now if status == "confirmed" else None,
                    )
                    .returning(study_plan_textbook_bindings)
                )
            )
            .mappings()
            .one()
        )
        return _binding_payload(row)

    async def list_bindings(
        self, *, user_id: str, plan_id: str, plan_version: int
    ) -> list[dict[str, Any]]:
        await self._plan_version(user_id, plan_id, plan_version)
        rows = (
            (
                await self._connection.execute(
                    select(study_plan_textbook_bindings)
                    .where(
                        study_plan_textbook_bindings.c.user_id == user_id,
                        study_plan_textbook_bindings.c.plan_id == plan_id,
                        study_plan_textbook_bindings.c.plan_version == plan_version,
                    )
                    .order_by(
                        study_plan_textbook_bindings.c.textbook_version_id,
                        study_plan_textbook_bindings.c.revision.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        latest: dict[str, Any] = {}
        for row in rows:
            latest.setdefault(row["textbook_version_id"], row)
        return sorted(
            (_binding_payload(row) for row in latest.values()),
            key=lambda item: (item["priority"], item["textbook_version_id"]),
        )

    async def set_mapping(
        self,
        *,
        mapping_id: str,
        user_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
        textbook_section_id: str,
        confidence: float,
        created_via: str,
        status: str,
    ) -> dict[str, Any]:
        replay = (
            (
                await self._connection.execute(
                    select(objective_textbook_section_mappings).where(
                        objective_textbook_section_mappings.c.mapping_id == mapping_id,
                        objective_textbook_section_mappings.c.user_id == user_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if replay is not None:
            payload = _mapping_payload(replay)
            if (
                payload["plan_id"],
                payload["plan_version"],
                payload["objective_id"],
                payload["textbook_section_id"],
                payload["confidence"],
                payload["created_via"],
                payload["status"],
            ) != (
                plan_id,
                plan_version,
                objective_id,
                textbook_section_id,
                confidence,
                created_via,
                status,
            ):
                raise GroundedLearningConflict("mapping idempotency key conflicts")
            return payload
        version = await self._plan_version(user_id, plan_id, plan_version, require_active=True)
        tree = StudyPlanTree.model_validate(version["tree"])
        if tree.objective(objective_id) is None:
            raise GroundedLearningNotFound("objective is not in this study-plan version")
        section = await self._owned_section(user_id, textbook_section_id)
        bindings = await self.list_bindings(
            user_id=user_id, plan_id=plan_id, plan_version=plan_version
        )
        binding = next(
            (
                item
                for item in bindings
                if item["textbook_version_id"] == section["version_id"]
                and item["status"] != "inactive"
            ),
            None,
        )
        if binding is None:
            raise GroundedLearningConflict(
                "section textbook version is not bound to this plan version"
            )
        if status == "confirmed" and binding["status"] != "confirmed":
            raise GroundedLearningConflict(
                "a confirmed mapping requires a confirmed textbook binding"
            )
        mapping_version = (
            int(
                (
                    await self._connection.execute(
                        select(
                            func.max(objective_textbook_section_mappings.c.mapping_version)
                        ).where(
                            objective_textbook_section_mappings.c.plan_id == plan_id,
                            objective_textbook_section_mappings.c.plan_version == plan_version,
                            objective_textbook_section_mappings.c.objective_id == objective_id,
                            objective_textbook_section_mappings.c.textbook_section_id
                            == textbook_section_id,
                        )
                    )
                ).scalar_one()
                or 0
            )
            + 1
        )
        now = datetime.now(timezone.utc)
        row = (
            (
                await self._connection.execute(
                    insert(objective_textbook_section_mappings)
                    .values(
                        mapping_id=mapping_id,
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        objective_id=objective_id,
                        textbook_section_id=textbook_section_id,
                        mapping_version=mapping_version,
                        confidence=confidence,
                        created_via=created_via,
                        status=status,
                        confirmed_by=user_id if status == "confirmed" else None,
                        confirmed_at=now if status == "confirmed" else None,
                    )
                    .returning(objective_textbook_section_mappings)
                )
            )
            .mappings()
            .one()
        )
        return _mapping_payload(row)

    async def list_mappings(
        self, *, user_id: str, plan_id: str, plan_version: int, objective_id: str | None = None
    ) -> list[dict[str, Any]]:
        await self._plan_version(user_id, plan_id, plan_version)
        statement = select(objective_textbook_section_mappings).where(
            objective_textbook_section_mappings.c.user_id == user_id,
            objective_textbook_section_mappings.c.plan_id == plan_id,
            objective_textbook_section_mappings.c.plan_version == plan_version,
        )
        if objective_id:
            statement = statement.where(
                objective_textbook_section_mappings.c.objective_id == objective_id
            )
        rows = (
            (
                await self._connection.execute(
                    statement.order_by(
                        objective_textbook_section_mappings.c.objective_id,
                        objective_textbook_section_mappings.c.textbook_section_id,
                        objective_textbook_section_mappings.c.mapping_version.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        latest: dict[tuple[str, str], Any] = {}
        for row in rows:
            latest.setdefault((row["objective_id"], row["textbook_section_id"]), row)
        return [_mapping_payload(row) for row in latest.values()]

    async def grounding_scope(
        self, *, user_id: str, plan_id: str, plan_version: int, objective_id: str
    ) -> list[dict[str, Any]]:
        bindings = [
            item
            for item in await self.list_bindings(
                user_id=user_id, plan_id=plan_id, plan_version=plan_version
            )
            if item["status"] == "confirmed"
        ]
        mappings = [
            item
            for item in await self.list_mappings(
                user_id=user_id,
                plan_id=plan_id,
                plan_version=plan_version,
                objective_id=objective_id,
            )
            if item["status"] == "confirmed"
        ]
        output: list[dict[str, Any]] = []
        for binding in bindings:
            version = await self._owned_textbook_version(user_id, binding["textbook_version_id"])
            textbook = await self._owned_textbook(user_id, version["textbook_id"])
            if textbook["archived_at"] is not None:
                continue
            sections = []
            for mapping in mappings:
                section = await self._owned_section(user_id, mapping["textbook_section_id"])
                if section["version_id"] == version["version_id"]:
                    sections.append({**_section_source_payload(section), "mapping": mapping})
            if sections:
                output.append(
                    {
                        **binding,
                        "textbook_id": textbook["textbook_id"],
                        "textbook_title": textbook["title"],
                        "textbook_version": version["version"],
                        "index_ref": version["host_index_ref"],
                        "index_version": version["index_version"],
                        "sections": sections,
                    }
                )
        return sorted(output, key=lambda item: (item["priority"], item["textbook_title"]))

    async def create_learning_snapshot(
        self,
        *,
        snapshot_id: str,
        user_id: str,
        idempotency_key: str,
        host_session_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
        mode: str,
        sources: list[dict[str, Any]],
        index_versions: dict[str, str],
    ) -> tuple[dict[str, Any], bool]:
        existing = (
            (
                await self._connection.execute(
                    select(learning_source_snapshots).where(
                        learning_source_snapshots.c.user_id == user_id,
                        learning_source_snapshots.c.idempotency_key == idempotency_key,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if existing is not None:
            payload = _snapshot_payload(existing)
            if (
                payload["host_session_id"],
                payload["plan_id"],
                payload["plan_version"],
                payload["objective_id"],
                payload["mode"],
                payload["sources"],
                payload["index_versions"],
            ) != (
                host_session_id,
                plan_id,
                plan_version,
                objective_id,
                mode,
                sources,
                index_versions,
            ):
                raise GroundedLearningConflict("source snapshot idempotency key conflicts")
            return payload, False
        row = (
            (
                await self._connection.execute(
                    insert(learning_source_snapshots)
                    .values(
                        snapshot_id=snapshot_id,
                        user_id=user_id,
                        idempotency_key=idempotency_key,
                        host_session_id=host_session_id,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        objective_id=objective_id,
                        mode=mode,
                        sources=sources,
                        index_versions=index_versions,
                    )
                    .returning(learning_source_snapshots)
                )
            )
            .mappings()
            .one()
        )
        return _snapshot_payload(row), True

    async def find_learning_snapshot(
        self, *, user_id: str, host_session_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    select(learning_source_snapshots).where(
                        learning_source_snapshots.c.user_id == user_id,
                        learning_source_snapshots.c.host_session_id == host_session_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _snapshot_payload(row)

    async def create_assessment_snapshot(
        self,
        *,
        snapshot_id: str,
        user_id: str,
        idempotency_key: str,
        assessment_id: str,
        assessment_version: int,
        evidence: list[dict[str, Any]],
        index_versions: dict[str, str],
    ) -> dict[str, Any]:
        row = (
            (
                await self._connection.execute(
                    insert(assessment_source_snapshots)
                    .values(
                        snapshot_id=snapshot_id,
                        user_id=user_id,
                        idempotency_key=idempotency_key,
                        assessment_id=assessment_id,
                        assessment_version=assessment_version,
                        evidence=evidence,
                        index_versions=index_versions,
                    )
                    .on_conflict_do_nothing(
                        index_elements=[
                            assessment_source_snapshots.c.user_id,
                            assessment_source_snapshots.c.idempotency_key,
                        ]
                    )
                    .returning(assessment_source_snapshots)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            row = (
                (
                    await self._connection.execute(
                        select(assessment_source_snapshots).where(
                            assessment_source_snapshots.c.user_id == user_id,
                            assessment_source_snapshots.c.idempotency_key == idempotency_key,
                        )
                    )
                )
                .mappings()
                .one()
            )
        payload = _assessment_snapshot_payload(row)
        if (
            payload["assessment_id"],
            payload["assessment_version"],
            payload["evidence"],
            payload["index_versions"],
        ) != (assessment_id, assessment_version, evidence, index_versions):
            raise GroundedLearningConflict("assessment source idempotency key conflicts")
        return payload

    async def find_assessment_snapshot(
        self, *, user_id: str, assessment_id: str, assessment_version: int
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    select(assessment_source_snapshots).where(
                        assessment_source_snapshots.c.user_id == user_id,
                        assessment_source_snapshots.c.assessment_id == assessment_id,
                        assessment_source_snapshots.c.assessment_version == assessment_version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return _assessment_snapshot_payload(row)

    async def _plan_version(
        self, user_id: str, plan_id: str, version: int, *, require_active: bool = False
    ) -> Any:
        row = (
            (
                await self._connection.execute(
                    select(study_plan_versions)
                    .join(study_plans, study_plans.c.plan_id == study_plan_versions.c.plan_id)
                    .where(
                        study_plans.c.user_id == user_id,
                        study_plan_versions.c.plan_id == plan_id,
                        study_plan_versions.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GroundedLearningNotFound("study-plan version not found")
        plan = (
            (
                await self._connection.execute(
                    select(study_plans).where(
                        study_plans.c.user_id == user_id, study_plans.c.plan_id == plan_id
                    )
                )
            )
            .mappings()
            .one()
        )
        if require_active and plan["archived_at"] is not None:
            raise GroundedLearningConflict("archived study plan cannot change textbook grounding")
        return row

    async def _owned_textbook_version(self, user_id: str, version_id: str) -> Any:
        row = (
            (
                await self._connection.execute(
                    select(textbook_versions)
                    .join(textbooks, textbooks.c.textbook_id == textbook_versions.c.textbook_id)
                    .where(
                        textbooks.c.user_id == user_id, textbook_versions.c.version_id == version_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GroundedLearningNotFound("textbook version not found")
        return row

    async def _owned_textbook(self, user_id: str, textbook_id: str) -> Any:
        row = (
            (
                await self._connection.execute(
                    select(textbooks).where(
                        textbooks.c.user_id == user_id, textbooks.c.textbook_id == textbook_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GroundedLearningNotFound("textbook not found")
        return row

    async def _owned_section(self, user_id: str, section_id: str) -> Any:
        row = (
            (
                await self._connection.execute(
                    select(textbook_sections)
                    .join(
                        textbook_versions,
                        textbook_versions.c.version_id == textbook_sections.c.version_id,
                    )
                    .join(textbooks, textbooks.c.textbook_id == textbook_versions.c.textbook_id)
                    .where(
                        textbooks.c.user_id == user_id, textbook_sections.c.section_id == section_id
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise GroundedLearningNotFound("textbook section not found")
        return row


def _binding_payload(row: Any) -> dict[str, Any]:
    return {
        "binding_id": row["binding_id"],
        "plan_id": row["plan_id"],
        "plan_version": row["plan_version"],
        "textbook_version_id": row["textbook_version_id"],
        "revision": row["revision"],
        "role": row["role"],
        "priority": row["priority"],
        "status": row["status"],
        "confirmed_at": None if row["confirmed_at"] is None else row["confirmed_at"].isoformat(),
        "created_at": row["created_at"].isoformat(),
    }


def _mapping_payload(row: Any) -> dict[str, Any]:
    return {
        "mapping_id": row["mapping_id"],
        "plan_id": row["plan_id"],
        "plan_version": row["plan_version"],
        "objective_id": row["objective_id"],
        "textbook_section_id": row["textbook_section_id"],
        "mapping_version": row["mapping_version"],
        "confidence": row["confidence"],
        "created_via": row["created_via"],
        "status": row["status"],
        "confirmed_at": None if row["confirmed_at"] is None else row["confirmed_at"].isoformat(),
        "created_at": row["created_at"].isoformat(),
    }


def _section_source_payload(row: Any) -> dict[str, Any]:
    return {
        "section_id": row["section_id"],
        "section_key": row["section_key"],
        "title": row["title"],
        "path": list(row["path"]),
        "start_page": row["start_page"],
        "end_page": row["end_page"],
        "host_content_ref": row["host_content_ref"],
    }


def _snapshot_payload(row: Any) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "host_session_id": row["host_session_id"],
        "plan_id": row["plan_id"],
        "plan_version": row["plan_version"],
        "objective_id": row["objective_id"],
        "mode": row["mode"],
        "sources": list(row["sources"]),
        "index_versions": dict(row["index_versions"]),
        "created_at": row["created_at"].isoformat(),
    }


def _assessment_snapshot_payload(row: Any) -> dict[str, Any]:
    return {
        "snapshot_id": row["snapshot_id"],
        "assessment_id": row["assessment_id"],
        "assessment_version": row["assessment_version"],
        "evidence": list(row["evidence"]),
        "index_versions": dict(row["index_versions"]),
        "created_at": row["created_at"].isoformat(),
    }


__all__ = [
    "GroundedLearningConflict",
    "GroundedLearningNotFound",
    "PostgresGroundedLearningRepository",
]
