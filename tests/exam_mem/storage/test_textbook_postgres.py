from __future__ import annotations

import os
import uuid

import pytest
from sqlalchemy import update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import create_async_engine

from exam_mem.storage import PostgresTextbookRepository, TextbookConflict, TextbookNotFound, load_database_settings
from exam_mem.storage.models import textbook_versions

pytestmark = [pytest.mark.asyncio, pytest.mark.database, pytest.mark.repository]


def _database_url_or_skip() -> str:
    if not os.environ.get("EXAM_MEM_DATABASE_URL"):
        pytest.skip("EXAM_MEM_DATABASE_URL is required for PostgreSQL integration tests")
    return load_database_settings().sqlalchemy_url()


async def test_textbook_versions_jobs_sections_idempotency_and_permissions() -> None:
    engine = create_async_engine(_database_url_or_skip())
    token = uuid.uuid4().hex
    user_id = f"user-{token}"
    textbook_id = f"textbook-{token}"
    version_id = f"version-{token}"
    job_id = f"job-{token}"
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            repository = PostgresTextbookRepository(connection)
            try:
                version, created = await repository.create_ingestion(
                    user_id=user_id,
                    textbook_id=textbook_id,
                    version_id=version_id,
                    job_id=job_id,
                    idempotency_key=f"upload-{token}",
                    title="公开许可微积分讲义",
                    metadata={"author": "fixture"},
                    filename="calculus.md",
                    mime_type="text/markdown",
                    size_bytes=2048,
                    content_hash="a" * 64,
                    host_source_ref="source:" + "a" * 64,
                )
                replay, replay_created = await repository.create_ingestion(
                    user_id=user_id,
                    textbook_id="ignored",
                    version_id="ignored",
                    job_id="ignored",
                    idempotency_key=f"upload-{token}",
                    title="ignored",
                    metadata={},
                    filename="calculus.md",
                    mime_type="text/markdown",
                    size_bytes=2048,
                    content_hash="a" * 64,
                    host_source_ref="source:" + "a" * 64,
                )
                assert created is True and replay_created is False
                assert replay["version_id"] == version["version_id"]
                with pytest.raises(TextbookConflict, match="different content"):
                    await repository.create_ingestion(
                        user_id=user_id, textbook_id="x", version_id="y", job_id="z",
                        idempotency_key=f"upload-{token}", title="x", metadata={}, filename="x.md",
                        mime_type="text/markdown", size_bytes=1, content_hash="b" * 64,
                        host_source_ref="source:" + "b" * 64,
                    )
                section = {
                    "section_id": f"section-{token}", "section_key": "s1", "parent_section_id": None,
                    "level": 1, "order": 0, "title": "第一章", "path": ["第一章"],
                    "start_page": 1, "end_page": 20, "content_hash": "c" * 64,
                    "confidence": 1.0, "inferred": False,
                }
                await repository.replace_sections(user_id=user_id, version_id=version_id, sections=(section,))
                failed = await repository.fail_job(user_id=user_id, job_id=job_id, error_code="index_failed", message="fixture failure")
                assert failed["stage"] == "failed"
                retried = await repository.prepare_retry(user_id=user_id, job_id=job_id)
                assert retried["retry_count"] == 1
                await repository.advance_job(
                    user_id=user_id, job_id=job_id, stage="completed", progress=100,
                    checkpoint={"safe_stage": "completed"}, host_index_ref="structured-" + "d" * 32,
                    index_version="version-fixture",
                )
                stored = await repository.get_version(user_id=user_id, version_id=version_id)
                assert stored["status"] == "completed"
                assert stored["sections"][0]["path"] == ["第一章"]
                with pytest.raises(TextbookNotFound):
                    await repository.get_version(user_id="another-user", version_id=version_id)
                with pytest.raises(DBAPIError):
                    async with connection.begin_nested():
                        await connection.execute(update(textbook_versions).where(textbook_versions.c.version_id == version_id).values(content_hash="f" * 64))
                archived = await repository.archive(user_id=user_id, textbook_id=textbook_id)
                assert archived["archived_at"] is not None
                assert await repository.list(user_id=user_id) == []
                assert len(await repository.list(user_id=user_id, archived=True)) == 1
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
