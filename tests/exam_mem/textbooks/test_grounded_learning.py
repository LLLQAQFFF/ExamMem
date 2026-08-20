from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from deeptutor_plugins.exam_mem.grounded_learning import (
    GroundedLearningService,
    render_grounding_prompt,
)


class GroundedRepository:
    async def grounding_scope(self, **_kwargs):  # noqa: ANN003, ANN201
        return [
            {
                "textbook_id": "book-primary",
                "textbook_title": "主教材",
                "textbook_version_id": "v1",
                "textbook_version": 1,
                "role": "primary",
                "priority": 0,
                "index_ref": "structured-" + "a" * 32,
                "index_version": "index-a",
                "sections": [{"section_id": "s1", "section_key": "limits", "path": ["极限"]}],
            },
            {
                "textbook_id": "book-supplement",
                "textbook_title": "辅教材",
                "textbook_version_id": "v2",
                "textbook_version": 2,
                "role": "supplement",
                "priority": 1,
                "index_ref": "structured-" + "b" * 32,
                "index_version": "index-b",
                "sections": [{"section_id": "s2", "section_key": "limits-b", "path": ["极限补充"]}],
            },
        ]


class Provider:
    @asynccontextmanager
    async def open_product(self):
        yield SimpleNamespace(grounded_learning=GroundedRepository())


class IndexHost:
    async def search(self, *, index_ref, query, metadata_filters, top_k):  # noqa: ANN001, ANN201
        key = metadata_filters["section_key"][0]
        return {
            "index_version": "index-a" if key == "limits" else "index-b",
            "sources": [
                {
                    "chunk_id": key,
                    "content": "高阶无穷小可忽略" if key == "limits" else "仅在给定条件下可忽略",
                    "score": 0.9,
                    "metadata": {
                        "section_id": "s",
                        "section_key": key,
                        "section_path": "极限",
                        "start_page": 12,
                        "end_page": 13,
                        "source_ref": "source",
                    },
                }
            ],
        }

    def status(self, index_ref):  # noqa: ANN001, ANN201
        return {
            "available": True,
            "index_version": "index-a" if index_ref.endswith("a" * 32) else "index-b",
        }


class EmptyIndexHost(IndexHost):
    async def search(self, *, index_ref, query, metadata_filters, top_k):  # noqa: ANN001, ANN201
        result = await super().search(
            index_ref=index_ref,
            query=query,
            metadata_filters=metadata_filters,
            top_k=top_k,
        )
        return {**result, "sources": []}


class UnavailableIndexHost(IndexHost):
    def status(self, index_ref):  # noqa: ANN001, ANN201
        return {"available": False, "index_version": None}


class ChangedIndexHost(IndexHost):
    def status(self, index_ref):  # noqa: ANN001, ANN201
        return {"available": True, "index_version": "new-index-version"}


@pytest.mark.asyncio
async def test_evidence_is_filtered_grouped_prioritized_and_conflict_visible() -> None:
    package = await GroundedLearningService(Provider(), index_host=IndexHost()).evidence_package(
        user_id="user",
        plan_id="plan",
        plan_version=1,
        objective_id="objective",
        query="极限",
        mode="compare",
    )
    assert [item["role"] for item in package["sources"]] == ["primary", "supplement"]
    assert package["filters"]["structured-" + "a" * 32] == {"section_key": ("limits",)}
    assert package["conflict_state"] == "comparison_required"
    prompt = render_grounding_prompt(package, language="zh")
    assert "主教材 v1" in prompt and "第 12-13 页" in prompt
    assert "不得自行宣布唯一真相" in prompt


@pytest.mark.asyncio
async def test_no_results_remains_explicit_and_source_versions_stay_visible() -> None:
    package = await GroundedLearningService(
        Provider(), index_host=EmptyIndexHost()
    ).evidence_package(
        user_id="user",
        plan_id="plan",
        plan_version=1,
        objective_id="objective",
        query="不存在的术语",
        mode="primary",
    )

    assert [item["retrieval_state"] for item in package["sources"]] == [
        "no_results",
        "no_results",
    ]
    prompt = render_grounding_prompt(package, language="zh")
    assert "主教材 v1" in prompt
    assert "辅教材 v2" in prompt
    assert prompt.count("未检索到证据") == 2


def test_snapshot_validation_rejects_missing_or_changed_index() -> None:
    service = GroundedLearningService(Provider(), index_host=IndexHost())
    snapshot = {
        "sources": [{"index_ref": "structured-" + "a" * 32, "section_keys": ["limits"]}],
        "index_versions": {"structured-" + "a" * 32: "index-a"},
    }
    knowledge_bases, filters = service.validate_snapshot(snapshot)
    assert knowledge_bases == ("structured-" + "a" * 32,)
    assert filters[knowledge_bases[0]]["section_key"] == ("limits",)
    with pytest.raises(RuntimeError, match="unavailable"):
        GroundedLearningService(Provider(), index_host=UnavailableIndexHost()).validate_snapshot(
            snapshot
        )
    with pytest.raises(RuntimeError, match="version changed"):
        GroundedLearningService(Provider(), index_host=ChangedIndexHost()).validate_snapshot(
            snapshot
        )
