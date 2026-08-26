"""Build editable study-plan drafts from one immutable textbook scope."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable
import unicodedata

from exam_mem.study import ImportedOutline, StudyPlanTree, materialize_outline

_NON_CONTENT_SECTION_TITLES = frozenset(
    {
        "作者简介",
        "内容简介",
        "前言",
        "序",
        "序言",
        "目录",
        "出版说明",
        "索引",
        "致谢",
        "后记",
    }
)


def _is_non_content_section(title: object) -> bool:
    """Identify common book front/back matter that should not be objectives."""
    normalized = "".join(str(title or "").split()).casefold()
    return normalized in _NON_CONTENT_SECTION_TITLES or normalized.startswith("参考文献")


def _label_key(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split()).casefold()


@dataclass(frozen=True, slots=True)
class TextbookPlanCandidate:
    objective_id: str
    textbook_section_id: str


@dataclass(frozen=True, slots=True)
class TextbookStudyPlanDraft:
    tree: StudyPlanTree
    candidates: tuple[TextbookPlanCandidate, ...]
    scope_section_ids: tuple[str, ...]


def recommend_textbook_mappings(
    *, tree: StudyPlanTree, sections: Iterable[dict[str, Any]]
) -> tuple[TextbookPlanCandidate, ...]:
    """Recommend only uniquely identifiable objective-to-section matches."""

    available = [dict(section) for section in sections]
    recommendations: list[TextbookPlanCandidate] = []
    for subject in tree.subjects:
        for module in subject.modules:
            module_key = _label_key(module.name)
            for objective in module.knowledge_points:
                objective_path = tuple(
                    _label_key(part) for part in objective.name.split(" / ") if part.strip()
                )
                if not objective_path:
                    continue
                exact_path = [
                    section
                    for section in available
                    if len(objective_path) > 1
                    and tuple(_label_key(part) for part in section.get("path") or ())[
                        -len(objective_path) :
                    ]
                    == objective_path
                ]
                matches = exact_path or [
                    section
                    for section in available
                    if _label_key(section.get("title")) == objective_path[-1]
                ]
                if len(matches) > 1:
                    matches = [
                        section
                        for section in matches
                        if module_key
                        in tuple(_label_key(part) for part in section.get("path") or ())[:-1]
                    ]
                if len(matches) != 1 or not matches[0].get("section_id"):
                    continue
                recommendations.append(
                    TextbookPlanCandidate(
                        objective_id=objective.id,
                        textbook_section_id=str(matches[0]["section_id"]),
                    )
                )
    return tuple(recommendations)


def build_textbook_study_plan(
    *,
    plan_id: str,
    plan_name: str,
    textbook_title: str,
    sections: Iterable[dict[str, Any]],
    scope_section_id: str | None,
) -> TextbookStudyPlanDraft:
    """Project a textbook subtree into a separate, editable exam taxonomy."""

    ordered = sorted((dict(section) for section in sections), key=lambda item: item["order"])
    by_id = {str(section["section_id"]): section for section in ordered}
    if not ordered:
        raise ValueError("completed textbook version has no sections")
    if scope_section_id is not None and scope_section_id not in by_id:
        raise ValueError("selected section is not in this textbook version")

    children: dict[str | None, list[dict[str, Any]]] = {}
    for section in ordered:
        parent_id = section.get("parent_section_id")
        children.setdefault(parent_id, []).append(section)

    if scope_section_id is None:
        roots = [section for section in ordered if section.get("parent_section_id") not in by_id]
    else:
        roots = [by_id[scope_section_id]]

    module_specs: list[tuple[str, list[tuple[str, tuple[str, ...]]]]] = []
    # Taxonomy validates labels globally, including subject/module labels.
    # Reserve those labels before assigning objective names so a leaf cannot
    # collide with its module (or with a label from another module).
    used_names: set[str] = {
        " ".join(label.split()).casefold()
        for label in [textbook_title, *(str(root["title"]) for root in roots)]
    }
    total_objectives = 0
    for root in roots:
        descendants = _descendants(root, children)
        descendant_ids = {str(section["section_id"]) for section in descendants}
        leaves = [
            section
            for section in descendants
            if not any(
                str(child["section_id"]) in descendant_ids
                for child in children.get(str(section["section_id"]), [])
            )
        ]
        objectives: list[tuple[str, tuple[str, ...]]] = []
        for leaf in leaves:
            if _is_non_content_section(leaf.get("title")):
                continue
            name = str(leaf["title"])
            normalized = " ".join(name.split()).casefold()
            if normalized in used_names:
                name = " / ".join(str(part) for part in leaf.get("path") or (name,))
                normalized = " ".join(name.split()).casefold()
            if normalized in used_names:
                name = f"{name}（{int(leaf['order']) + 1}）"
                normalized = " ".join(name.split()).casefold()
            used_names.add(normalized)
            objectives.append(
                (
                    name,
                    _ancestry_within_scope(
                        leaf_id=str(leaf["section_id"]),
                        root_id=str(root["section_id"]),
                        by_id=by_id,
                    ),
                )
            )
        total_objectives += len(objectives)
        if not objectives:
            continue
        if total_objectives > 2_000:
            raise ValueError("selected textbook scope exceeds 2000 learning objectives")
        for offset in range(0, len(objectives), 200):
            batch = objectives[offset : offset + 200]
            label = str(root["title"])
            if len(objectives) > 200:
                label = f"{label}（{offset // 200 + 1}）"
            module_specs.append((label, batch))

    if not module_specs:
        raise ValueError("selected textbook scope has no content sections")

    outline = ImportedOutline.model_validate(
        {
            "name": plan_name,
            "subjects": [
                {
                    "name": textbook_title,
                    "modules": [
                        {
                            "name": module_name,
                            "knowledge_points": [
                                {"name": objective_name, "type": "concept"}
                                for objective_name, _ in objectives
                            ],
                        }
                        for module_name, objectives in module_specs
                    ],
                }
            ],
        }
    )
    tree = materialize_outline(plan_id, outline)
    candidates: list[TextbookPlanCandidate] = []
    for module, (_, objective_specs) in zip(tree.subjects[0].modules, module_specs, strict=True):
        for objective, (_, section_ids) in zip(
            module.knowledge_points, objective_specs, strict=True
        ):
            candidates.extend(
                TextbookPlanCandidate(
                    objective_id=objective.id,
                    textbook_section_id=section_id,
                )
                for section_id in section_ids
            )
    return TextbookStudyPlanDraft(
        tree=tree,
        candidates=tuple(candidates),
        scope_section_ids=tuple(
            str(section["section_id"]) for root in roots for section in _descendants(root, children)
        ),
    )


def _descendants(
    root: dict[str, Any], children: dict[str | None, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    stack = [root]
    while stack:
        current = stack.pop()
        result.append(current)
        stack.extend(reversed(children.get(str(current["section_id"]), [])))
    return result


def _ancestry_within_scope(
    *, leaf_id: str, root_id: str, by_id: dict[str, dict[str, Any]]
) -> tuple[str, ...]:
    result: list[str] = []
    current_id: str | None = leaf_id
    while current_id is not None:
        result.append(current_id)
        if current_id == root_id:
            break
        parent_id = by_id[current_id].get("parent_section_id")
        current_id = str(parent_id) if parent_id in by_id else None
    if not result or result[-1] != root_id:
        raise ValueError("selected textbook section tree is disconnected")
    return tuple(reversed(result))


__all__ = [
    "TextbookPlanCandidate",
    "TextbookStudyPlanDraft",
    "build_textbook_study_plan",
    "recommend_textbook_mappings",
]
