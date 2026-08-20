"""PostgreSQL repository for ExamMem textbook facts and ingestion checkpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncConnection

from .models import textbook_ingestion_jobs, textbook_sections, textbook_versions, textbooks


class TextbookNotFound(LookupError):
    pass


class TextbookConflict(RuntimeError):
    pass


class PostgresTextbookRepository:
    def __init__(self, connection: AsyncConnection) -> None:
        self._connection = connection

    async def create_ingestion(
        self,
        *,
        user_id: str,
        textbook_id: str,
        version_id: str,
        job_id: str,
        idempotency_key: str,
        title: str,
        metadata: dict[str, Any],
        filename: str,
        mime_type: str,
        size_bytes: int,
        content_hash: str,
        host_source_ref: str,
        existing_textbook_id: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        replay = await self._job_by_idempotency(user_id, idempotency_key)
        if replay is not None:
            if replay["input_hash"] != content_hash:
                raise TextbookConflict("idempotency key was already used for different content")
            return await self.get_version(user_id=user_id, version_id=replay["version_id"]), False

        resolved_textbook_id = existing_textbook_id or textbook_id
        if existing_textbook_id:
            textbook = await self._owned_textbook(user_id, existing_textbook_id, for_update=True)
            if textbook["archived_at"] is not None:
                raise TextbookConflict("archived textbook cannot receive a new version")
            duplicate = (
                await self._connection.execute(
                    select(textbook_versions.c.version_id).where(
                        textbook_versions.c.textbook_id == existing_textbook_id,
                        textbook_versions.c.content_hash == content_hash,
                    )
                )
            ).scalar_one_or_none()
            if duplicate:
                return await self.get_version(user_id=user_id, version_id=duplicate), False
        else:
            await self._connection.execute(
                insert(textbooks).values(
                    textbook_id=resolved_textbook_id,
                    user_id=user_id,
                    title=title,
                    metadata=metadata,
                )
            )
        latest = (
            await self._connection.execute(
                select(func.max(textbook_versions.c.version)).where(
                    textbook_versions.c.textbook_id == resolved_textbook_id
                )
            )
        ).scalar_one()
        version_number = int(latest or 0) + 1
        await self._connection.execute(
            insert(textbook_versions).values(
                version_id=version_id,
                textbook_id=resolved_textbook_id,
                version=version_number,
                content_hash=content_hash,
                filename=filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                host_source_ref=host_source_ref,
                status="queued",
                warnings=[],
            )
        )
        await self._connection.execute(
            insert(textbook_ingestion_jobs).values(
                job_id=job_id,
                user_id=user_id,
                textbook_id=resolved_textbook_id,
                version_id=version_id,
                idempotency_key=idempotency_key,
                stage="saved",
                progress=10,
                checkpoint={"safe_stage": "saved"},
                input_hash=content_hash,
                output_refs={"source_ref": host_source_ref},
                retry_count=0,
                started_at=datetime.now(timezone.utc),
            )
        )
        return await self.get_version(user_id=user_id, version_id=version_id), True

    async def list(self, *, user_id: str, archived: bool | None = False) -> list[dict[str, Any]]:
        statement = select(textbooks).where(textbooks.c.user_id == user_id)
        if archived is True:
            statement = statement.where(textbooks.c.archived_at.is_not(None))
        elif archived is False:
            statement = statement.where(textbooks.c.archived_at.is_(None))
        rows = (await self._connection.execute(statement.order_by(textbooks.c.updated_at.desc()))).mappings().all()
        return [await self._hydrate_textbook(row) for row in rows]

    async def get(self, *, user_id: str, textbook_id: str) -> dict[str, Any]:
        return await self._hydrate_textbook(await self._owned_textbook(user_id, textbook_id))

    async def get_version(self, *, user_id: str, version_id: str) -> dict[str, Any]:
        row = (
            await self._connection.execute(
                select(textbook_versions)
                .join(textbooks, textbooks.c.textbook_id == textbook_versions.c.textbook_id)
                .where(textbook_versions.c.version_id == version_id, textbooks.c.user_id == user_id)
            )
        ).mappings().one_or_none()
        if row is None:
            raise TextbookNotFound("textbook version not found")
        sections = (
            await self._connection.execute(
                select(textbook_sections)
                .where(textbook_sections.c.version_id == version_id)
                .order_by(textbook_sections.c.position)
            )
        ).mappings().all()
        job = (
            await self._connection.execute(
                select(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.version_id == version_id)
            )
        ).mappings().one_or_none()
        return {**_version_payload(row), "sections": [_section_payload(item) for item in sections], "job": None if job is None else _job_payload(job)}

    async def replace_sections(self, *, user_id: str, version_id: str, sections: tuple[dict[str, Any], ...]) -> None:
        version = await self.get_version(user_id=user_id, version_id=version_id)
        if version["status"] == "completed":
            raise TextbookConflict("completed textbook version is immutable")
        await self._connection.execute(delete(textbook_sections).where(textbook_sections.c.version_id == version_id))
        if sections:
            await self._connection.execute(
                insert(textbook_sections),
                [
                    {
                        "section_id": item["section_id"],
                        "version_id": version_id,
                        "section_key": item["section_key"],
                        "parent_section_id": item.get("parent_section_id"),
                        "level": item["level"],
                        "position": item["order"],
                        "title": item["title"],
                        "path": item["path"],
                        "start_page": item.get("start_page"),
                        "end_page": item.get("end_page"),
                        "host_content_ref": f"{version['host_source_ref']}#section={item['section_key']}",
                        "content_hash": item["content_hash"],
                        "confidence": item["confidence"],
                        "inferred": item["inferred"],
                    }
                    for item in sections
                ],
            )

    async def advance_job(
        self,
        *,
        user_id: str,
        job_id: str,
        stage: str,
        progress: int,
        checkpoint: dict[str, Any],
        output_refs: dict[str, Any] | None = None,
        parser_signature: str | None = None,
        host_index_ref: str | None = None,
        index_version: str | None = None,
    ) -> dict[str, Any]:
        job = await self._owned_job(user_id, job_id)
        now = datetime.now(timezone.utc)
        refs = {**dict(job["output_refs"]), **(output_refs or {})}
        await self._connection.execute(
            update(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.job_id == job_id).values(
                stage=stage,
                progress=progress,
                checkpoint=checkpoint,
                output_refs=refs,
                error_code=None,
                error_message=None,
                completed_at=now if stage == "completed" else None,
                updated_at=now,
            )
        )
        values: dict[str, Any] = {"status": "completed" if stage == "completed" else "processing"}
        if parser_signature is not None:
            values["parser_signature"] = parser_signature
        if host_index_ref is not None:
            values["host_index_ref"] = host_index_ref
        if index_version is not None:
            values["index_version"] = index_version
        if stage == "completed":
            values["completed_at"] = now
        await self._connection.execute(update(textbook_versions).where(textbook_versions.c.version_id == job["version_id"]).values(**values))
        return _job_payload(await self._owned_job(user_id, job_id))

    async def fail_job(self, *, user_id: str, job_id: str, error_code: str, message: str) -> dict[str, Any]:
        job = await self._owned_job(user_id, job_id)
        checkpoint = dict(job["checkpoint"])
        checkpoint.setdefault("safe_stage", "saved")
        await self._connection.execute(
            update(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.job_id == job_id).values(
                stage="failed", error_code=error_code, error_message=message[:1000], checkpoint=checkpoint, updated_at=datetime.now(timezone.utc)
            )
        )
        await self._connection.execute(update(textbook_versions).where(textbook_versions.c.version_id == job["version_id"]).values(status="failed"))
        return _job_payload(await self._owned_job(user_id, job_id))

    async def prepare_retry(self, *, user_id: str, job_id: str) -> dict[str, Any]:
        job = await self._owned_job(user_id, job_id)
        if job["stage"] != "failed":
            raise TextbookConflict("only failed ingestion jobs can be retried")
        safe_stage = str(dict(job["checkpoint"]).get("safe_stage") or "saved")
        progress = {"saved": 10, "parsing": 25, "structuring": 50, "chunking": 70, "indexing": 85}.get(safe_stage, 10)
        await self._connection.execute(
            update(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.job_id == job_id).values(
                stage=safe_stage, progress=progress, retry_count=int(job["retry_count"]) + 1, error_code=None, error_message=None, updated_at=datetime.now(timezone.utc)
            )
        )
        await self._connection.execute(update(textbook_versions).where(textbook_versions.c.version_id == job["version_id"]).values(status="processing"))
        return _job_payload(await self._owned_job(user_id, job_id))

    async def archive(self, *, user_id: str, textbook_id: str) -> dict[str, Any]:
        textbook = await self._owned_textbook(user_id, textbook_id, for_update=True)
        when = textbook["archived_at"] or datetime.now(timezone.utc)
        await self._connection.execute(update(textbooks).where(textbooks.c.textbook_id == textbook_id).values(archived_at=when, updated_at=when))
        return {"textbook_id": textbook_id, "archived_at": when.isoformat()}

    async def _owned_textbook(self, user_id: str, textbook_id: str, *, for_update: bool = False) -> Any:
        statement = select(textbooks).where(textbooks.c.user_id == user_id, textbooks.c.textbook_id == textbook_id)
        if for_update:
            statement = statement.with_for_update()
        row = (await self._connection.execute(statement)).mappings().one_or_none()
        if row is None:
            raise TextbookNotFound("textbook not found")
        return row

    async def _owned_job(self, user_id: str, job_id: str) -> Any:
        row = (await self._connection.execute(select(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.user_id == user_id, textbook_ingestion_jobs.c.job_id == job_id))).mappings().one_or_none()
        if row is None:
            raise TextbookNotFound("textbook ingestion job not found")
        return row

    async def _job_by_idempotency(self, user_id: str, key: str) -> Any:
        return (await self._connection.execute(select(textbook_ingestion_jobs).where(textbook_ingestion_jobs.c.user_id == user_id, textbook_ingestion_jobs.c.idempotency_key == key))).mappings().one_or_none()

    async def _hydrate_textbook(self, row: Any) -> dict[str, Any]:
        versions = (await self._connection.execute(select(textbook_versions).where(textbook_versions.c.textbook_id == row["textbook_id"]).order_by(textbook_versions.c.version.desc()))).mappings().all()
        return {
            "textbook_id": row["textbook_id"], "title": row["title"], "metadata": dict(row["metadata"]),
            "archived_at": _iso(row["archived_at"]), "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
            "versions": [_version_payload(item) for item in versions],
        }


def _version_payload(row: Any) -> dict[str, Any]:
    return {key: (_iso(row[key]) if key in {"created_at", "completed_at"} else list(row[key]) if key == "warnings" else row[key]) for key in ("version_id", "textbook_id", "version", "content_hash", "filename", "mime_type", "size_bytes", "host_source_ref", "parser_signature", "host_index_ref", "index_version", "status", "warnings", "created_at", "completed_at")}


def _section_payload(row: Any) -> dict[str, Any]:
    return {"section_id": row["section_id"], "section_key": row["section_key"], "parent_section_id": row["parent_section_id"], "level": row["level"], "order": row["position"], "title": row["title"], "path": list(row["path"]), "start_page": row["start_page"], "end_page": row["end_page"], "host_content_ref": row["host_content_ref"], "content_hash": row["content_hash"], "confidence": row["confidence"], "inferred": row["inferred"]}


def _job_payload(row: Any) -> dict[str, Any]:
    return {"job_id": row["job_id"], "textbook_id": row["textbook_id"], "version_id": row["version_id"], "stage": row["stage"], "progress": row["progress"], "checkpoint": dict(row["checkpoint"]), "output_refs": dict(row["output_refs"]), "error_code": row["error_code"], "error_message": row["error_message"], "retry_count": row["retry_count"], "started_at": _iso(row["started_at"]), "completed_at": _iso(row["completed_at"]), "updated_at": _iso(row["updated_at"])}


def _iso(value: Any) -> str | None:
    return None if value is None else value.isoformat()


__all__ = ["PostgresTextbookRepository", "TextbookConflict", "TextbookNotFound"]
