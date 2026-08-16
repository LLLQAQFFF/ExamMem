from __future__ import annotations

import base64
import json

import pytest

from deeptutor_plugins.exam_mem.study_plan import StudyPlanOutlineImporter

pytestmark = pytest.mark.asyncio


class FakeSourceHost:
    def extract_attachment(self, *, filename: str, content: bytes) -> str:
        assert filename == "outline.txt"
        assert content == b"source syllabus"
        return "extracted syllabus"

    async def fetch_url(self, url: str) -> dict[str, object]:
        return {
            "text": "remote syllabus",
            "url": url,
            "title": "Official outline",
            "truncated": False,
        }


async def _completion(**kwargs) -> str:  # noqa: ANN003
    prompt = json.loads(kwargs["prompt"])
    assert prompt["required_plan_name"] == "数学一"
    assert "source_text" in prompt
    return json.dumps(
        {
            "name": "model supplied name is ignored",
            "subjects": [
                {
                    "name": "数学一",
                    "modules": [
                        {
                            "name": "高等数学",
                            "knowledge_points": [{"name": "函数极限", "type": "concept"}],
                        }
                    ],
                }
            ],
        },
        ensure_ascii=False,
    )


async def test_file_import_extracts_only_hierarchy_and_records_provenance() -> None:
    importer = StudyPlanOutlineImporter(source_host=FakeSourceHost(), completion=_completion)

    imported = await importer.from_file(
        plan_id="plan-1",
        plan_name="数学一",
        filename="outline.txt",
        mime_type="text/plain",
        encoded=base64.b64encode(b"source syllabus").decode(),
    )

    assert imported.tree.name == "数学一"
    assert imported.tree.subjects[0].modules[0].knowledge_points[0].name == "函数极限"
    assert imported.source_kind == "file"
    assert imported.source_metadata["filename"] == "outline.txt"
    assert "source syllabus" not in imported.source_metadata.values()


async def test_url_import_keeps_url_metadata_but_not_raw_source() -> None:
    importer = StudyPlanOutlineImporter(source_host=FakeSourceHost(), completion=_completion)

    imported = await importer.from_url(
        plan_id="plan-1",
        plan_name="数学一",
        url="https://example.edu/outline",
    )

    assert imported.source_kind == "url"
    assert imported.source_metadata["url"] == "https://example.edu/outline"
    assert "remote syllabus" not in imported.source_metadata.values()


async def test_file_import_rejects_invalid_base64_before_host_extraction() -> None:
    importer = StudyPlanOutlineImporter(source_host=FakeSourceHost(), completion=_completion)

    with pytest.raises(ValueError, match="base64"):
        await importer.from_file(
            plan_id="plan-1",
            plan_name="数学一",
            filename="outline.txt",
            mime_type="text/plain",
            encoded="not base64!",
        )
