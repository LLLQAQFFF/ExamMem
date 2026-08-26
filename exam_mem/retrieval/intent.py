"""Conservative taxonomy-aware parsing for Learning Memory retrieval."""

from __future__ import annotations

import re
import unicodedata

from exam_mem.contracts import ErrorType, MasteryLevel, MemoryNamespace, MemoryScope
from exam_mem.domain.taxonomy import KnowledgePointStatus, Taxonomy

from .contracts import RetrievalIntent

_EXPLICIT_TARGET_PATTERNS = (
    re.compile(r"对(?P<target>[^，。；！？!?]+?)的掌握"),
    re.compile(r"关于(?P<target>[^，。；！？!?]+?)的错误记忆"),
    re.compile(r"只查\s*(?P<target>.+?)\s*因为"),
    re.compile(r"(?P<target>[^，。；！？!?]+?)存在(?:未解决的状态)?冲突"),
)
_INSUFFICIENT_REFERENCE_MARKERS = (
    "上次说的那个",
    "只记得是第",
    "那个错误到底",
    "之前那个错误",
)
_ERROR_TYPE_ALIASES: tuple[tuple[ErrorType, tuple[str, ...]], ...] = (
    (ErrorType.READING_ERROR, ("reading error", "看错题干", "审题错误")),
    (ErrorType.CONDITION_OMISSION, ("condition omission", "条件遗漏", "遗漏前提", "缺失前提")),
    (ErrorType.FORMULA_MISUSE, ("formula misuse", "公式误用", "公式使用错误")),
    (ErrorType.CONCEPT_CONFUSION, ("concept confusion", "概念混淆", "概念错误")),
    (ErrorType.CALCULATION_ERROR, ("calculation error", "计算错误", "计算失误")),
    (ErrorType.REASONING_GAP, ("reasoning gap", "推理断层", "推理缺口")),
    (ErrorType.CARELESS_ERROR, ("careless error", "粗心错误", "粗心")),
)
_MASTERY_LEVEL_ALIASES: tuple[tuple[MasteryLevel, tuple[str, ...]], ...] = (
    (MasteryLevel.MASTERED, ("完全掌握", "已经掌握", "稳定掌握", "mastered")),
    (MasteryLevel.HIGH, ("掌握较好", "较高掌握", "high mastery")),
    (MasteryLevel.IMPROVING, ("改善阶段", "正在进步", "逐步改善", "improving")),
    (MasteryLevel.LOW, ("重点补强", "仍需补强", "薄弱", "low mastery")),
)


class TaxonomyRetrievalIntentResolver:
    """Extract only explicit, auditable constraints; never invent taxonomy IDs."""

    def __init__(self, taxonomy: Taxonomy) -> None:
        self._taxonomy = taxonomy
        labels: list[tuple[str, str]] = []
        for node in taxonomy.nodes:
            if node.status is not KnowledgePointStatus.ACTIVE or node.parent_id is None:
                continue
            for label in (node.id, node.name_zh, *node.aliases):
                normalized = _normalize(label)
                if normalized:
                    labels.append((normalized, node.id))
        self._labels = tuple(sorted(set(labels), key=lambda item: (-len(item[0]), item[1])))

    def resolve(self, scope: MemoryScope, query: str) -> RetrievalIntent:
        normalized = _normalize(query)
        explicit_targets = tuple(
            match.group("target").strip()
            for pattern in _EXPLICIT_TARGET_PATTERNS
            if (match := pattern.search(normalized)) is not None
        )
        knowledge_point_ids = tuple(
            sorted(
                {
                    knowledge_point_id
                    for target in explicit_targets
                    for knowledge_point_id in self._matched_knowledge_points(target)
                }
            )
        )
        unknown_explicit_target = bool(explicit_targets) and not knowledge_point_ids
        return RetrievalIntent(
            knowledge_point_ids=knowledge_point_ids,
            error_type=_first_alias_match(normalized, _ERROR_TYPE_ALIASES),
            mastery_level=(
                _first_alias_match(normalized, _MASTERY_LEVEL_ALIASES)
                if scope.memory_namespace is MemoryNamespace.MASTERY
                else None
            ),
            explicit_target=bool(knowledge_point_ids or explicit_targets),
            unknown_explicit_target=unknown_explicit_target,
            reference_sufficient=not any(
                marker in normalized for marker in _INSUFFICIENT_REFERENCE_MARKERS
            ),
            return_all_contested_branches=(
                "同时检索" in normalized
                and any(marker in normalized for marker in ("两个", "互相矛盾", "所有分支"))
            ),
        )

    def label_for(self, knowledge_point_id: str) -> str:
        node = self._taxonomy.get(knowledge_point_id)
        return node.name_zh if node is not None else knowledge_point_id

    def _matched_knowledge_points(self, normalized_query: str) -> tuple[str, ...]:
        occurrences = [
            (match.start(), match.end(), label, knowledge_point_id)
            for label, knowledge_point_id in self._labels
            for match in re.finditer(re.escape(label), normalized_query)
        ]
        most_specific = {
            knowledge_point_id
            for start, end, label, knowledge_point_id in occurrences
            if not any(
                other_start <= start
                and end <= other_end
                and len(other_label) > len(label)
                for other_start, other_end, other_label, _ in occurrences
            )
        }
        return tuple(sorted(most_specific))


def _first_alias_match(normalized: str, groups):  # noqa: ANN001, ANN202
    for value, aliases in groups:
        if any(_normalize(alias) in normalized for alias in aliases):
            return value
    return None


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split()).casefold()


__all__ = ["TaxonomyRetrievalIntentResolver"]
