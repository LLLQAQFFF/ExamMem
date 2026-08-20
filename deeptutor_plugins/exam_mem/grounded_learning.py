"""Build bounded, provenance-preserving textbook evidence for learning sessions."""

from __future__ import annotations

from typing import Any

from deeptutor.plugins.host_services import PluginKnowledgeIndexHost


class GroundedLearningService:
    def __init__(
        self, runtime_provider: Any, *, index_host: PluginKnowledgeIndexHost | None = None
    ) -> None:
        self._runtime_provider = runtime_provider
        self._indexes = index_host or PluginKnowledgeIndexHost()

    async def evidence_package(
        self,
        *,
        user_id: str,
        plan_id: str,
        plan_version: int,
        objective_id: str,
        query: str,
        mode: str,
    ) -> dict[str, Any]:
        async with self._runtime_provider.open_product() as runtime:
            scope = await runtime.grounded_learning.grounding_scope(
                user_id=user_id,
                plan_id=plan_id,
                plan_version=plan_version,
                objective_id=objective_id,
            )
        if not scope:
            return {
                "mode": "unbound",
                "sources": [],
                "knowledge_bases": [],
                "filters": {},
                "index_versions": {},
                "conflict_state": "none",
            }
        groups: list[dict[str, Any]] = []
        filters: dict[str, dict[str, tuple[str, ...]]] = {}
        index_versions: dict[str, str] = {}
        for item in scope:
            index_ref = str(item.get("index_ref") or "")
            if not index_ref:
                continue
            section_keys = tuple(section["section_key"] for section in item["sections"])
            filters[index_ref] = {"section_key": section_keys}
            result = await self._indexes.search(
                index_ref=index_ref,
                query=query,
                metadata_filters=filters[index_ref],
                top_k=4,
            )
            index_versions[index_ref] = str(result["index_version"])
            evidence = []
            for source in result.get("sources") or []:
                metadata = dict(source.get("metadata") or {})
                evidence.append(
                    {
                        "chunk_id": source.get("chunk_id"),
                        "content": str(source.get("content") or "")[:1200],
                        "score": source.get("score"),
                        "section_id": metadata.get("section_id"),
                        "section_key": metadata.get("section_key"),
                        "section_path": metadata.get("section_path"),
                        "start_page": metadata.get("start_page"),
                        "end_page": metadata.get("end_page"),
                        "source_ref": metadata.get("source_ref"),
                    }
                )
            groups.append(
                {
                    "textbook_id": item["textbook_id"],
                    "textbook_title": item["textbook_title"],
                    "textbook_version_id": item["textbook_version_id"],
                    "textbook_version": item["textbook_version"],
                    "role": item["role"],
                    "priority": item["priority"],
                    "index_ref": index_ref,
                    "section_keys": list(section_keys),
                    "sections": item["sections"],
                    "evidence": evidence,
                    "retrieval_state": "grounded" if evidence else "no_results",
                }
            )
        groups.sort(key=lambda item: (item["priority"], item["textbook_title"]))
        return {
            "mode": mode if groups else "unbound",
            "sources": groups,
            "knowledge_bases": [item["index_ref"] for item in groups],
            "filters": filters,
            "index_versions": index_versions,
            "conflict_state": _conflict_state(groups),
        }

    def validate_snapshot(
        self, snapshot: dict[str, Any]
    ) -> tuple[tuple[str, ...], dict[str, dict[str, tuple[str, ...]]]]:
        knowledge_bases: list[str] = []
        filters: dict[str, dict[str, tuple[str, ...]]] = {}
        for source in snapshot["sources"]:
            index_ref = str(source["index_ref"])
            status = self._indexes.status(index_ref)
            if not status["available"]:
                raise RuntimeError(
                    "a source snapshot index is unavailable; rebuild it before resuming"
                )
            expected = snapshot["index_versions"].get(index_ref)
            if expected and status["index_version"] != expected:
                raise RuntimeError(
                    "a source snapshot index version changed; explicit recovery is required"
                )
            knowledge_bases.append(index_ref)
            filters[index_ref] = {"section_key": tuple(source["section_keys"])}
        return tuple(knowledge_bases), filters


def render_grounding_prompt(package: dict[str, Any], *, language: str) -> str:
    zh = language.lower().startswith("zh")
    if not package["sources"]:
        return (
            "未绑定教材。本次讲解可以使用通用模型知识，但必须明确标记为模型补充。"
            if zh
            else "No textbook is bound. General model knowledge may be used, but label it explicitly as model-supplied."
        )
    lines = [
        "[固定教材来源｜禁止切换版本]"
        if zh
        else "[Pinned textbook sources | do not switch versions]",
        (
            "按主教材口径教学；如证据有差异，分别引用并说明，不得静默融合。"
            if package["mode"] == "primary"
            else "比较各教材观点；同级冲突不得自行宣布唯一真相。"
        )
        if zh
        else (
            "Teach to the primary source; cite differences separately and never merge them silently."
            if package["mode"] == "primary"
            else "Compare the sources; do not declare one truth for same-authority conflicts."
        ),
    ]
    for group in package["sources"]:
        lines.append(
            f"\n- {group['textbook_title']} v{group['textbook_version']} [{group['role']}, priority={group['priority']}]"
        )
        if not group["evidence"]:
            lines.append("  - 未检索到证据" if zh else "  - No evidence retrieved")
        for item in group["evidence"]:
            page = _page_label(item, zh=zh)
            lines.append(
                f"  - {item.get('section_path') or item.get('section_key')} {page}: {item['content']}"
            )
    return "\n".join(lines)


def _conflict_state(groups: list[dict[str, Any]]) -> str:
    grounded = [group for group in groups if group["evidence"]]
    if len(grounded) < 2:
        return "none"
    excerpts = {" ".join(group["evidence"][0]["content"].casefold().split()) for group in grounded}
    return "aligned" if len(excerpts) == 1 else "comparison_required"


def _page_label(item: dict[str, Any], *, zh: bool) -> str:
    start = item.get("start_page")
    end = item.get("end_page")
    if start is None:
        return "[无页码]" if zh else "[no page]"
    value = str(start) if end in (None, start) else f"{start}-{end}"
    return f"[第 {value} 页]" if zh else f"[pp. {value}]"


__all__ = ["GroundedLearningService", "render_grounding_prompt"]
