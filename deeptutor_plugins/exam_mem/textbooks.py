"""ExamMem textbook ingestion orchestration over neutral Host services."""

from __future__ import annotations

from typing import Any

from deeptutor.plugins.host_services import PluginKnowledgeIndexHost, PluginSourceHost
from exam_mem.textbooks import build_section_tree, section_documents


class TextbookIngestionService:
    def __init__(
        self,
        runtime_provider: Any,
        *,
        source_host: PluginSourceHost | None = None,
        index_host: PluginKnowledgeIndexHost | None = None,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._sources = source_host or PluginSourceHost()
        self._indexes = index_host or PluginKnowledgeIndexHost()

    async def run(self, *, user_id: str, version_id: str) -> None:
        job_id = ""
        try:
            version = await self._version(user_id, version_id)
            job = version["job"]
            if job is None or job["stage"] == "completed":
                return
            job_id = job["job_id"]
            await self._advance(user_id, job_id, "parsing", 20, {"safe_stage": "saved"})
            parsed = await self._sources.parse_saved_source(version["host_source_ref"])
            await self._advance(
                user_id,
                job_id,
                "structuring",
                40,
                {"safe_stage": "parsing", "parser_signature": parsed["parser_signature"]},
                parser_signature=parsed["parser_signature"],
            )
            sections = build_section_tree(
                version_id=version_id,
                markdown=str(parsed["markdown"]),
                blocks=parsed["blocks"],
            )
            async with self._runtime_provider.open_product() as runtime:
                await runtime.textbooks.replace_sections(
                    user_id=user_id, version_id=version_id, sections=sections
                )
                await runtime.textbooks.advance_job(
                    user_id=user_id,
                    job_id=job_id,
                    stage="chunking",
                    progress=60,
                    checkpoint={"safe_stage": "structuring", "section_count": len(sections)},
                    output_refs={"section_count": len(sections)},
                )
                await runtime.connection.commit()
            documents = section_documents(
                textbook_id=version["textbook_id"],
                version_id=version_id,
                source_ref=version["host_source_ref"],
                sections=sections,
            )
            if not documents:
                raise ValueError("parsed textbook contains no indexable section text")
            index_ref = self._indexes.index_ref(version_id)
            await self._advance(
                user_id,
                job_id,
                "indexing",
                80,
                {
                    "safe_stage": "chunking",
                    "section_count": len(sections),
                    "chunk_count": len(documents),
                },
                output_refs={"chunk_count": len(documents), "index_ref": index_ref},
            )
            index = await self._indexes.build(index_ref=index_ref, documents=documents)
            await self._advance(
                user_id,
                job_id,
                "completed",
                100,
                {
                    "safe_stage": "completed",
                    "section_count": len(sections),
                    "chunk_count": len(documents),
                },
                output_refs=index,
                host_index_ref=index["index_ref"],
                index_version=index["index_version"],
            )
        except Exception as exc:
            if job_id:
                async with self._runtime_provider.open_product() as runtime:
                    await runtime.textbooks.fail_job(
                        user_id=user_id,
                        job_id=job_id,
                        error_code=_error_code(exc),
                        message=str(exc) or exc.__class__.__name__,
                    )
                    await runtime.connection.commit()

    async def _version(self, user_id: str, version_id: str) -> dict[str, Any]:
        async with self._runtime_provider.open_product() as runtime:
            return await runtime.textbooks.get_version(user_id=user_id, version_id=version_id)

    async def _advance(
        self,
        user_id: str,
        job_id: str,
        stage: str,
        progress: int,
        checkpoint: dict[str, Any],
        **kwargs: Any,
    ) -> None:
        async with self._runtime_provider.open_product() as runtime:
            await runtime.textbooks.advance_job(
                user_id=user_id,
                job_id=job_id,
                stage=stage,
                progress=progress,
                checkpoint=checkpoint,
                **kwargs,
            )
            await runtime.connection.commit()


def _error_code(exc: Exception) -> str:
    name = exc.__class__.__name__.lower()
    if "parser" in name:
        return "parse_failed"
    if "index" in name or "embedding" in str(exc).lower():
        return "index_failed"
    return "ingestion_failed"


__all__ = ["TextbookIngestionService"]
