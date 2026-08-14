"""Scope-safe L1/L2/L3 read model for the ExamMem learning archive."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Sequence

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.contracts import LearningContext, LifecycleState, MemoryNamespace, MemoryScope

from .memory_repository import PostgresLearningMemoryRepository
from .models import (
    assessment_attempts,
    assessments,
    event_correction_targets,
    event_plan_transition_targets,
    learning_events,
    memory_provenance,
)
from .student_model_repository import PostgresStudentModelRepository


class PostgresLearningArchiveRepository:
    """Build product views without changing formal Learning Memory semantics."""

    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def read(
        self,
        context: LearningContext,
        *,
        taxonomy_version: str | None = None,
        knowledge_point_ids: Sequence[str] = (),
        namespaces: Sequence[MemoryNamespace] = (),
        lifecycle_states: Sequence[LifecycleState] = (),
    ) -> dict[str, Any]:
        required_knowledge = set(knowledge_point_ids)
        l1 = await self._l1(
            context,
            taxonomy_version=taxonomy_version,
            required_knowledge=required_knowledge,
        )
        l2 = await self._l2(
            context,
            taxonomy_version=taxonomy_version,
            required_knowledge=required_knowledge,
            namespaces=namespaces,
            lifecycle_states=lifecycle_states,
        )
        snapshot = await PostgresStudentModelRepository(self._connection).get_latest(context)
        l3_payload = None
        if snapshot is not None:
            l3_payload = snapshot.model_dump(mode="json")
            l3_payload["model"]["context"].pop("user_id", None)
        return {
            "scope": {
                "exam_id": context.exam_id,
                "subject_id": context.subject_id,
                "taxonomy_version": taxonomy_version,
            },
            "l1": l1,
            "l2": l2,
            "l3": l3_payload,
            "counts": {
                "l1": len(l1),
                "l2": len(l2),
                "l3": 0 if snapshot is None else _student_model_item_count(snapshot.model),
            },
        }

    async def _l1(
        self,
        context: LearningContext,
        *,
        taxonomy_version: str | None,
        required_knowledge: set[str],
    ) -> list[dict[str, Any]]:
        rows = (
            (
                await self._connection.execute(
                    select(
                        learning_events.c.raw_payload,
                        learning_events.c.created_at,
                        assessment_attempts.c.attempt_id,
                        assessment_attempts.c.assessment_version,
                        assessments.c.assessment_id,
                        assessments.c.title.label("assessment_title"),
                        assessments.c.taxonomy_version,
                    )
                    .outerjoin(
                        assessment_attempts,
                        and_(
                            assessment_attempts.c.user_id == learning_events.c.user_id,
                            assessment_attempts.c.practice_session_id
                            == learning_events.c.session_id,
                        ),
                    )
                    .outerjoin(
                        assessments,
                        and_(
                            assessments.c.user_id == learning_events.c.user_id,
                            assessments.c.assessment_id == assessment_attempts.c.assessment_id,
                        ),
                    )
                    .where(
                        learning_events.c.user_id == context.user_id,
                        learning_events.c.exam_id == context.exam_id,
                        learning_events.c.subject_id == context.subject_id,
                    )
                    .order_by(
                        learning_events.c.created_at.desc(),
                        learning_events.c.event_id.desc(),
                    )
                )
            )
            .mappings()
            .all()
        )
        inherited_taxonomies = await self._inherited_event_taxonomies(
            context,
            [row["raw_payload"]["event_id"] for row in rows if row["taxonomy_version"] is None],
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            payload = dict(row["raw_payload"])
            payload["context"] = dict(payload.get("context") or {})
            payload["context"].pop("user_id", None)
            event_taxonomies = inherited_taxonomies.get(payload["event_id"], set())
            if row["taxonomy_version"] is not None:
                event_taxonomies.add(row["taxonomy_version"])
            if taxonomy_version is not None and taxonomy_version not in event_taxonomies:
                continue
            if required_knowledge and not required_knowledge.intersection(
                payload.get("knowledge_point_ids") or ()
            ):
                continue
            output.append(
                {
                    "event": payload,
                    "created_at": row["created_at"].isoformat(),
                    "source": (
                        None
                        if row["assessment_id"] is None
                        else {
                            "assessment_id": row["assessment_id"],
                            "assessment_title": row["assessment_title"],
                            "assessment_version": row["assessment_version"],
                            "attempt_id": row["attempt_id"],
                            "taxonomy_version": row["taxonomy_version"],
                        }
                    ),
                }
            )
        return output

    async def _inherited_event_taxonomies(
        self,
        context: LearningContext,
        event_ids: Sequence[str],
    ) -> dict[str, set[str]]:
        if not event_ids:
            return {}
        source_events = learning_events.alias("archive_source_events")
        source_attempts = assessment_attempts.alias("archive_source_attempts")
        source_assessments = assessments.alias("archive_source_assessments")
        output: dict[str, set[str]] = defaultdict(set)
        for targets in (event_correction_targets, event_plan_transition_targets):
            rows = (
                (
                    await self._connection.execute(
                        select(
                            targets.c.event_id,
                            source_assessments.c.taxonomy_version,
                        )
                        .join(
                            memory_provenance,
                            memory_provenance.c.memory_id == targets.c.memory_id,
                        )
                        .join(
                            source_events,
                            source_events.c.event_id == memory_provenance.c.event_id,
                        )
                        .join(
                            source_attempts,
                            and_(
                                source_attempts.c.user_id == source_events.c.user_id,
                                source_attempts.c.practice_session_id == source_events.c.session_id,
                            ),
                        )
                        .join(
                            source_assessments,
                            and_(
                                source_assessments.c.user_id == source_events.c.user_id,
                                source_assessments.c.assessment_id
                                == source_attempts.c.assessment_id,
                            ),
                        )
                        .where(
                            targets.c.event_id.in_(tuple(event_ids)),
                            source_events.c.user_id == context.user_id,
                            source_events.c.exam_id == context.exam_id,
                            source_events.c.subject_id == context.subject_id,
                        )
                    )
                )
                .mappings()
                .all()
            )
            for row in rows:
                output[row["event_id"]].add(row["taxonomy_version"])
        return dict(output)

    async def _l2(
        self,
        context: LearningContext,
        *,
        taxonomy_version: str | None,
        required_knowledge: set[str],
        namespaces: Sequence[MemoryNamespace],
        lifecycle_states: Sequence[LifecycleState],
    ) -> list[dict[str, Any]]:
        selected_namespaces = tuple(namespaces) or tuple(MemoryNamespace)
        memories = []
        repository = PostgresLearningMemoryRepository(self._connection)
        for namespace in selected_namespaces:
            scope = MemoryScope(
                **context.model_dump(),
                memory_namespace=namespace,
            )
            memories.extend(await repository.snapshot(scope))
        sources = await self._memory_sources(context, [item.memory_id for item in memories])
        allowed_states = set(lifecycle_states)
        output = []
        for memory in memories:
            memory_sources = sources.get(memory.memory_id, [])
            if allowed_states and memory.lifecycle_state not in allowed_states:
                continue
            if taxonomy_version is not None and not any(
                item.get("taxonomy_version") == taxonomy_version for item in memory_sources
            ):
                continue
            if required_knowledge and not any(
                required_knowledge.intersection(item.get("knowledge_point_ids") or ())
                for item in memory_sources
            ):
                continue
            output.append(
                {
                    "memory": memory.model_dump(mode="json", exclude={"scope": {"user_id"}}),
                    "sources": memory_sources,
                }
            )
        output.sort(
            key=lambda item: (
                item["memory"]["slot_key"],
                item["memory"]["version"],
                item["memory"]["memory_id"],
            )
        )
        return output

    async def _memory_sources(
        self,
        context: LearningContext,
        memory_ids: Sequence[str],
    ) -> dict[str, list[dict[str, Any]]]:
        if not memory_ids:
            return {}
        rows = (
            (
                await self._connection.execute(
                    select(
                        memory_provenance.c.memory_id,
                        memory_provenance.c.relation_type,
                        learning_events.c.raw_payload,
                        assessment_attempts.c.attempt_id,
                        assessment_attempts.c.assessment_version,
                        assessments.c.assessment_id,
                        assessments.c.title.label("assessment_title"),
                        assessments.c.taxonomy_version,
                    )
                    .join(
                        learning_events,
                        learning_events.c.event_id == memory_provenance.c.event_id,
                    )
                    .outerjoin(
                        assessment_attempts,
                        and_(
                            assessment_attempts.c.user_id == learning_events.c.user_id,
                            assessment_attempts.c.practice_session_id
                            == learning_events.c.session_id,
                        ),
                    )
                    .outerjoin(
                        assessments,
                        and_(
                            assessments.c.user_id == learning_events.c.user_id,
                            assessments.c.assessment_id == assessment_attempts.c.assessment_id,
                        ),
                    )
                    .where(
                        memory_provenance.c.memory_id.in_(tuple(memory_ids)),
                        learning_events.c.user_id == context.user_id,
                        learning_events.c.exam_id == context.exam_id,
                        learning_events.c.subject_id == context.subject_id,
                    )
                    .order_by(
                        memory_provenance.c.memory_id,
                        learning_events.c.created_at,
                        learning_events.c.event_id,
                    )
                )
            )
            .mappings()
            .all()
        )
        output: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            event = dict(row["raw_payload"])
            output[row["memory_id"]].append(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "relation_type": row["relation_type"],
                    "session_id": event["session_id"],
                    "knowledge_point_ids": list(event.get("knowledge_point_ids") or ()),
                    "assessment_id": row["assessment_id"],
                    "assessment_title": row["assessment_title"],
                    "assessment_version": row["assessment_version"],
                    "attempt_id": row["attempt_id"],
                    "taxonomy_version": row["taxonomy_version"],
                }
            )
        return dict(output)


def _student_model_item_count(model: Any) -> int:
    return sum(
        len(getattr(model, field))
        for field in (
            "weak_points",
            "mastered_points",
            "stable_error_patterns",
            "active_plans",
        )
    )


__all__ = ["PostgresLearningArchiveRepository"]
