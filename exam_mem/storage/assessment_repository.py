"""Versioned assessment catalog and attempt repository."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from exam_mem.practice import Question

from .models import assessment_attempts, assessment_versions, assessments


class AssessmentNotFound(LookupError):
    pass


class AssessmentConflict(RuntimeError):
    pass


class PostgresAssessmentRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def create_version(
        self,
        *,
        assessment_id: str,
        user_id: str,
        exam_id: str,
        subject_id: str,
        taxonomy_version: str,
        title: str,
        knowledge_point_ids: Sequence[str],
        questions: Sequence[Question],
        generation: dict[str, Any],
    ) -> dict[str, Any]:
        statement = (
            select(assessments)
            .where(assessments.c.assessment_id == assessment_id)
            .with_for_update()
        )
        existing = (await self._connection.execute(statement)).mappings().one_or_none()
        if existing is not None and existing["user_id"] != user_id:
            raise AssessmentConflict("assessment identity is unavailable")
        if existing is not None and existing["archived_at"] is not None:
            raise AssessmentConflict("archived assessment cannot receive new versions")
        normalized_kps = list(dict.fromkeys(knowledge_point_ids))
        if not normalized_kps:
            raise ValueError("assessment requires at least one knowledge point")
        if existing is None:
            version = 1
            await self._connection.execute(
                insert(assessments).values(
                    assessment_id=assessment_id,
                    user_id=user_id,
                    exam_id=exam_id,
                    subject_id=subject_id,
                    taxonomy_version=taxonomy_version,
                    title=title,
                    knowledge_point_ids=normalized_kps,
                    latest_version=version,
                )
            )
        else:
            expected = (
                existing["exam_id"],
                existing["subject_id"],
                existing["taxonomy_version"],
                list(existing["knowledge_point_ids"]),
            )
            actual = (exam_id, subject_id, taxonomy_version, normalized_kps)
            if expected != actual:
                raise AssessmentConflict(
                    "new assessment versions must keep the original Scope and blueprint"
                )
            version = int(existing["latest_version"]) + 1
            await self._connection.execute(
                update(assessments)
                .where(assessments.c.assessment_id == assessment_id)
                .values(
                    latest_version=version,
                    title=title,
                    updated_at=datetime.now(timezone.utc),
                )
            )
        catalog = [question.model_dump(mode="json") for question in questions]
        await self._connection.execute(
            insert(assessment_versions).values(
                assessment_id=assessment_id,
                version=version,
                question_catalog=catalog,
                generation=generation,
                content_hash=_payload_hash(catalog),
            )
        )
        return {
            "assessment_id": assessment_id,
            "version": version,
            "question_count": len(catalog),
        }

    async def start_attempt(
        self,
        *,
        attempt_id: str,
        user_id: str,
        assessment_id: str,
        version: int,
        practice_session_id: str,
        trace_id: str,
    ) -> dict[str, Any]:
        await self.get_version(user_id=user_id, assessment_id=assessment_id, version=version)
        row = (
            (
                await self._connection.execute(
                    insert(assessment_attempts)
                    .values(
                        attempt_id=attempt_id,
                        user_id=user_id,
                        assessment_id=assessment_id,
                        assessment_version=version,
                        practice_session_id=practice_session_id,
                        trace_id=trace_id,
                        status="in_progress",
                    )
                    .returning(assessment_attempts)
                )
            )
            .mappings()
            .one()
        )
        return dict(row)

    async def complete_attempt(
        self, *, user_id: str, practice_session_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    update(assessment_attempts)
                    .where(
                        assessment_attempts.c.user_id == user_id,
                        assessment_attempts.c.practice_session_id == practice_session_id,
                        assessment_attempts.c.status == "in_progress",
                    )
                    .values(status="completed", completed_at=datetime.now(timezone.utc))
                    .returning(assessment_attempts)
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else dict(row)

    async def fail_attempt(self, *, user_id: str, practice_session_id: str) -> None:
        await self._connection.execute(
            update(assessment_attempts)
            .where(
                assessment_attempts.c.user_id == user_id,
                assessment_attempts.c.practice_session_id == practice_session_id,
                assessment_attempts.c.status == "in_progress",
            )
            .values(status="failed", completed_at=None)
        )

    async def get_version(
        self, *, user_id: str, assessment_id: str, version: int
    ) -> dict[str, Any]:
        assessment = await self._assessment(user_id=user_id, assessment_id=assessment_id)
        if assessment["archived_at"] is not None:
            raise AssessmentConflict("archived assessment cannot be attempted")
        row = (
            (
                await self._connection.execute(
                    select(assessment_versions).where(
                        assessment_versions.c.assessment_id == assessment_id,
                        assessment_versions.c.version == version,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise AssessmentNotFound("assessment version not found")
        return {
            "assessment": dict(assessment),
            "version": version,
            "questions": tuple(Question.model_validate(item) for item in row["question_catalog"]),
            "generation": dict(row["generation"]),
            "content_hash": row["content_hash"],
        }

    async def list(self, *, user_id: str, archived: bool | None = False) -> list[dict[str, Any]]:
        statement = select(assessments).where(assessments.c.user_id == user_id)
        if archived is True:
            statement = statement.where(assessments.c.archived_at.is_not(None))
        elif archived is False:
            statement = statement.where(assessments.c.archived_at.is_(None))
        rows = (
            (await self._connection.execute(statement.order_by(assessments.c.updated_at.desc())))
            .mappings()
            .all()
        )
        output = []
        for row in rows:
            attempts = (
                (
                    await self._connection.execute(
                        select(assessment_attempts)
                        .where(
                            assessment_attempts.c.user_id == user_id,
                            assessment_attempts.c.assessment_id == row["assessment_id"],
                        )
                        .order_by(assessment_attempts.c.started_at.desc())
                    )
                )
                .mappings()
                .all()
            )
            output.append(
                {
                    "assessment_id": row["assessment_id"],
                    "title": row["title"],
                    "exam_id": row["exam_id"],
                    "subject_id": row["subject_id"],
                    "taxonomy_version": row["taxonomy_version"],
                    "knowledge_point_ids": list(row["knowledge_point_ids"]),
                    "latest_version": row["latest_version"],
                    "archived_at": (
                        None if row["archived_at"] is None else row["archived_at"].isoformat()
                    ),
                    "attempts": [_public_attempt(item) for item in attempts],
                }
            )
        return output

    async def archive(self, *, user_id: str, assessment_id: str) -> dict[str, Any]:
        assessment = await self._assessment(
            user_id=user_id,
            assessment_id=assessment_id,
            for_update=True,
        )
        archived_at = assessment["archived_at"]
        if archived_at is None:
            archived_at = datetime.now(timezone.utc)
            await self._connection.execute(
                update(assessments)
                .where(assessments.c.assessment_id == assessment_id)
                .values(archived_at=archived_at, updated_at=archived_at)
            )
        await self._connection.execute(
            update(assessment_attempts)
            .where(
                assessment_attempts.c.user_id == user_id,
                assessment_attempts.c.assessment_id == assessment_id,
                assessment_attempts.c.status == "in_progress",
            )
            .values(status="failed", completed_at=None)
        )
        return {
            "assessment_id": assessment_id,
            "archived_at": archived_at.isoformat(),
        }

    async def restore(self, *, user_id: str, assessment_id: str) -> dict[str, Any]:
        assessment = await self._assessment(
            user_id=user_id,
            assessment_id=assessment_id,
            for_update=True,
        )
        if assessment["archived_at"] is not None:
            await self._connection.execute(
                update(assessments)
                .where(assessments.c.assessment_id == assessment_id)
                .values(archived_at=None, updated_at=datetime.now(timezone.utc))
            )
        return {"assessment_id": assessment_id, "archived_at": None}

    async def require_practice_active(self, *, user_id: str, practice_session_id: str) -> None:
        archived_at = await self._connection.scalar(
            select(assessments.c.archived_at)
            .join(
                assessment_attempts,
                assessment_attempts.c.assessment_id == assessments.c.assessment_id,
            )
            .where(
                assessment_attempts.c.user_id == user_id,
                assessment_attempts.c.practice_session_id == practice_session_id,
            )
        )
        if archived_at is not None:
            raise AssessmentConflict("archived assessment cannot be continued")

    async def attempt_for_practice(
        self, *, user_id: str, practice_session_id: str
    ) -> dict[str, Any] | None:
        row = (
            (
                await self._connection.execute(
                    select(assessment_attempts).where(
                        assessment_attempts.c.user_id == user_id,
                        assessment_attempts.c.practice_session_id == practice_session_id,
                    )
                )
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else _public_attempt(row)

    async def _assessment(
        self,
        *,
        user_id: str,
        assessment_id: str,
        for_update: bool = False,
        required: bool = True,
    ) -> Any:
        statement = select(assessments).where(
            assessments.c.user_id == user_id,
            assessments.c.assessment_id == assessment_id,
        )
        if for_update:
            statement = statement.with_for_update()
        row = (await self._connection.execute(statement)).mappings().one_or_none()
        if row is None and required:
            raise AssessmentNotFound("assessment not found")
        return row


def _payload_hash(payload: list[dict[str, Any]]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _public_attempt(row: Any) -> dict[str, Any]:
    return {
        "attempt_id": row["attempt_id"],
        "assessment_version": row["assessment_version"],
        "practice_session_id": row["practice_session_id"],
        "trace_id": row["trace_id"],
        "status": row["status"],
        "started_at": row["started_at"].isoformat(),
        "completed_at": (None if row["completed_at"] is None else row["completed_at"].isoformat()),
    }


__all__ = [
    "AssessmentConflict",
    "AssessmentNotFound",
    "PostgresAssessmentRepository",
]
