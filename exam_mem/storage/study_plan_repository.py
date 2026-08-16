"""PostgreSQL repository for versioned study plans and Host session links."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.domain import Taxonomy
from exam_mem.study import StudyPlanTree

from .models import (
    study_objective_sessions,
    study_plan_drafts,
    study_plan_versions,
    study_plans,
)


class StudyPlanNotFound(LookupError):
    pass


class StudyPlanConflict(RuntimeError):
    pass


class PostgresStudyPlanRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def create_draft(
        self,
        *,
        user_id: str,
        plan_id: str,
        tree: StudyPlanTree,
        source_kind: str,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        now = datetime.now(timezone.utc)
        payload = tree.model_dump(mode="json")
        content_hash = _payload_hash(payload)
        await self._connection.execute(
            insert(study_plans).values(
                plan_id=plan_id,
                user_id=user_id,
                name=tree.name,
                active_version=None,
                created_at=now,
                updated_at=now,
            )
        )
        await self._connection.execute(
            insert(study_plan_drafts).values(
                plan_id=plan_id,
                tree=payload,
                source_kind=source_kind,
                source_metadata=source_metadata,
                content_hash=content_hash,
                updated_at=now,
            )
        )
        return await self.get(user_id=user_id, plan_id=plan_id)

    async def replace_draft(
        self,
        *,
        user_id: str,
        plan_id: str,
        tree: StudyPlanTree,
        source_kind: str,
        source_metadata: dict[str, Any],
    ) -> dict[str, Any]:
        await self._owned_plan(user_id=user_id, plan_id=plan_id, for_update=True)
        now = datetime.now(timezone.utc)
        payload = tree.model_dump(mode="json")
        values = {
            "tree": payload,
            "source_kind": source_kind,
            "source_metadata": source_metadata,
            "content_hash": _payload_hash(payload),
            "updated_at": now,
        }
        await self._connection.execute(
            insert(study_plan_drafts)
            .values(plan_id=plan_id, **values)
            .on_conflict_do_update(
                index_elements=[study_plan_drafts.c.plan_id],
                set_=values,
            )
        )
        await self._connection.execute(
            update(study_plans)
            .where(study_plans.c.plan_id == plan_id)
            .values(name=tree.name, updated_at=now)
        )
        return await self.get(user_id=user_id, plan_id=plan_id)

    async def publish(self, *, user_id: str, plan_id: str) -> dict[str, Any]:
        plan = await self._owned_plan(user_id=user_id, plan_id=plan_id, for_update=True)
        draft = (
            (
                await self._connection.execute(
                    select(study_plan_drafts).where(study_plan_drafts.c.plan_id == plan_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if draft is None:
            raise StudyPlanConflict("study plan has no draft to publish")
        tree = StudyPlanTree.model_validate(draft["tree"])
        version = int(plan["active_version"] or 0) + 1
        taxonomy_versions = {
            subject.id: _taxonomy_version(plan_id, subject.order, version)
            for subject in tree.subjects
        }
        for subject in tree.subjects:
            tree.taxonomy(subject.id, taxonomy_versions[subject.id])
        await self._connection.execute(
            insert(study_plan_versions).values(
                plan_id=plan_id,
                version=version,
                tree=tree.model_dump(mode="json"),
                taxonomy_versions=taxonomy_versions,
                source_kind=draft["source_kind"],
                source_metadata=draft["source_metadata"],
                content_hash=draft["content_hash"],
            )
        )
        await self._connection.execute(
            update(study_plans)
            .where(study_plans.c.plan_id == plan_id)
            .values(active_version=version, name=tree.name, updated_at=datetime.now(timezone.utc))
        )
        await self._connection.execute(
            delete(study_plan_drafts).where(study_plan_drafts.c.plan_id == plan_id)
        )
        return await self.get(user_id=user_id, plan_id=plan_id)

    async def list(self, *, user_id: str) -> list[dict[str, Any]]:
        rows = (
            (
                await self._connection.execute(
                    select(study_plans)
                    .where(study_plans.c.user_id == user_id)
                    .order_by(study_plans.c.updated_at.desc(), study_plans.c.plan_id)
                )
            )
            .mappings()
            .all()
        )
        return [await self._hydrate(row) for row in rows]

    async def get(self, *, user_id: str, plan_id: str) -> dict[str, Any]:
        return await self._hydrate(await self._owned_plan(user_id=user_id, plan_id=plan_id))

    async def get_version(
        self, *, user_id: str, plan_id: str, version: int | None = None
    ) -> dict[str, Any]:
        plan = await self._owned_plan(user_id=user_id, plan_id=plan_id)
        resolved = int(version or plan["active_version"] or 0)
        if resolved < 1:
            raise StudyPlanConflict("study plan has not been published")
        row = (
            (
                await self._connection.execute(
                    select(study_plan_versions).where(
                        study_plan_versions.c.plan_id == plan_id,
                        study_plan_versions.c.version == resolved,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise StudyPlanNotFound("study plan version not found")
        return _version_payload(row)

    async def taxonomy(
        self,
        *,
        user_id: str,
        exam_id: str,
        subject_id: str,
        taxonomy_version: str,
    ) -> Taxonomy:
        prefix = "plan:"
        if not exam_id.startswith(prefix):
            raise StudyPlanNotFound("scope is not backed by an imported study plan")
        plan_id = exam_id[len(prefix) :]
        versions = (
            (
                await self._connection.execute(
                    select(study_plan_versions)
                    .join(study_plans, study_plans.c.plan_id == study_plan_versions.c.plan_id)
                    .where(
                        study_plans.c.user_id == user_id,
                        study_plan_versions.c.plan_id == plan_id,
                        study_plan_versions.c.taxonomy_versions[subject_id].astext
                        == taxonomy_version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if versions is None:
            raise StudyPlanNotFound("published taxonomy version not found in this Scope")
        tree = StudyPlanTree.model_validate(versions["tree"])
        return tree.taxonomy(subject_id, taxonomy_version)

    async def find_objective_session(
        self,
        *,
        user_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    select(study_objective_sessions).where(
                        study_objective_sessions.c.user_id == user_id,
                        study_objective_sessions.c.plan_id == plan_id,
                        study_objective_sessions.c.plan_version == plan_version,
                        study_objective_sessions.c.objective_id == objective_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else dict(row)

    async def bind_objective_session(
        self,
        *,
        link_id: str,
        user_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
        host_path_id: str,
        host_session_id: str,
        initial_turn_id: str,
    ) -> tuple[dict[str, Any], bool]:
        result = await self._connection.execute(
            insert(study_objective_sessions)
            .values(
                link_id=link_id,
                user_id=user_id,
                plan_id=plan_id,
                plan_version=plan_version,
                objective_id=objective_id,
                host_path_id=host_path_id,
                host_session_id=host_session_id,
                initial_turn_id=initial_turn_id,
            )
            .on_conflict_do_nothing(
                index_elements=[
                    study_objective_sessions.c.user_id,
                    study_objective_sessions.c.plan_id,
                    study_objective_sessions.c.plan_version,
                    study_objective_sessions.c.objective_id,
                ]
            )
            .returning(study_objective_sessions)
        )
        inserted = result.mappings().one_or_none()
        if inserted is not None:
            return dict(inserted), True
        existing = await self.find_objective_session(
            user_id=user_id,
            plan_id=plan_id,
            plan_version=plan_version,
            objective_id=objective_id,
        )
        if existing is None:
            raise StudyPlanConflict("objective session binding conflicted without a winner")
        return existing, False

    async def replace_objective_session(
        self,
        *,
        user_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
        host_path_id: str,
        host_session_id: str,
        initial_turn_id: str,
    ) -> dict[str, Any]:
        result = await self._connection.execute(
            update(study_objective_sessions)
            .where(
                study_objective_sessions.c.user_id == user_id,
                study_objective_sessions.c.plan_id == plan_id,
                study_objective_sessions.c.plan_version == plan_version,
                study_objective_sessions.c.objective_id == objective_id,
            )
            .values(
                host_path_id=host_path_id,
                host_session_id=host_session_id,
                initial_turn_id=initial_turn_id,
                updated_at=datetime.now(timezone.utc),
            )
            .returning(study_objective_sessions)
        )
        row = result.mappings().one_or_none()
        if row is None:
            raise StudyPlanNotFound("objective session link not found")
        return dict(row)

    async def list_objective_sessions(
        self, *, user_id: str, plan_id: str, plan_version: int
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await self._connection.execute(
                    select(study_objective_sessions)
                    .where(
                        study_objective_sessions.c.user_id == user_id,
                        study_objective_sessions.c.plan_id == plan_id,
                        study_objective_sessions.c.plan_version == plan_version,
                    )
                    .order_by(study_objective_sessions.c.created_at)
                )
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    async def lock_objective_session(
        self, *, user_id: str, plan_id: str, plan_version: int, objective_id: str
    ) -> None:
        key = "\x1f".join((user_id, plan_id, str(plan_version), objective_id))
        await self._connection.exec_driver_sql(
            "SELECT pg_advisory_xact_lock(hashtext($1))",
            (key,),
        )

    async def _owned_plan(self, *, user_id: str, plan_id: str, for_update: bool = False) -> Any:
        statement = select(study_plans).where(
            study_plans.c.plan_id == plan_id,
            study_plans.c.user_id == user_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise StudyPlanNotFound("study plan not found")
        return row

    async def _hydrate(self, plan: Any) -> dict[str, Any]:
        draft = (
            (
                await self._connection.execute(
                    select(study_plan_drafts).where(study_plan_drafts.c.plan_id == plan["plan_id"])
                )
            )
            .mappings()
            .one_or_none()
        )
        versions = (
            (
                await self._connection.execute(
                    select(study_plan_versions)
                    .where(study_plan_versions.c.plan_id == plan["plan_id"])
                    .order_by(study_plan_versions.c.version.desc())
                )
            )
            .mappings()
            .all()
        )
        active = next(
            (item for item in versions if item["version"] == plan["active_version"]),
            None,
        )
        return {
            "plan_id": plan["plan_id"],
            "name": plan["name"],
            "active_version": plan["active_version"],
            "created_at": plan["created_at"].isoformat(),
            "updated_at": plan["updated_at"].isoformat(),
            "draft": None if draft is None else _draft_payload(draft),
            "published": None if active is None else _version_payload(active),
            "versions": [_version_payload(item) for item in versions],
        }


def _draft_payload(row: Any) -> dict[str, Any]:
    return {
        "tree": StudyPlanTree.model_validate(row["tree"]).model_dump(mode="json"),
        "source_kind": row["source_kind"],
        "source_metadata": dict(row["source_metadata"]),
        "content_hash": row["content_hash"],
        "updated_at": row["updated_at"].isoformat(),
    }


def _version_payload(row: Any) -> dict[str, Any]:
    return {
        "version": row["version"],
        "tree": StudyPlanTree.model_validate(row["tree"]).model_dump(mode="json"),
        "taxonomy_versions": dict(row["taxonomy_versions"]),
        "source_kind": row["source_kind"],
        "source_metadata": dict(row["source_metadata"]),
        "content_hash": row["content_hash"],
        "published_at": row["published_at"].isoformat(),
    }


def _payload_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _taxonomy_version(plan_id: str, subject_order: int, version: int) -> str:
    prefix = hashlib.sha256(plan_id.encode()).hexdigest()[:12]
    return f"p{prefix}_s{subject_order + 1:03d}_v{version}"


__all__ = [
    "PostgresStudyPlanRepository",
    "StudyPlanConflict",
    "StudyPlanNotFound",
]
