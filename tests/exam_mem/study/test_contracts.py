from __future__ import annotations

from pydantic import ValidationError
import pytest

from exam_mem.study import ImportedOutline, materialize_outline


def _outline() -> ImportedOutline:
    return ImportedOutline.model_validate(
        {
            "name": "全国硕士研究生招生考试",
            "subjects": [
                {
                    "name": "数学一",
                    "modules": [
                        {
                            "name": "高等数学",
                            "knowledge_points": [
                                {"name": "函数极限", "type": "concept"},
                                {"name": "洛必达法则", "type": "procedure"},
                            ],
                        }
                    ],
                }
            ],
        }
    )


def test_materialized_ids_are_stable_and_labels_can_be_edited() -> None:
    first = materialize_outline("plan-1", _outline())
    renamed = materialize_outline(
        "plan-1",
        _outline().model_copy(update={"name": "2027 数学备考"}),
    )

    assert first.subjects[0].id == renamed.subjects[0].id
    assert first.subjects[0].modules[0].knowledge_points[0].id == (
        renamed.subjects[0].modules[0].knowledge_points[0].id
    )
    assert first.subjects[0].modules[0].knowledge_points[0].id.endswith(".k001")


def test_published_subject_projects_to_one_strict_taxonomy() -> None:
    tree = materialize_outline("plan-1", _outline())
    subject = tree.subjects[0]

    taxonomy = tree.taxonomy(subject.id, "p123_s001_v1")

    assert taxonomy.taxonomy_version == "p123_s001_v1"
    assert [node.name_zh for node in taxonomy.nodes] == [
        "数学一",
        "高等数学",
        "函数极限",
        "洛必达法则",
    ]
    assert taxonomy.nodes[-1].parent_id == subject.modules[0].id


def test_outline_rejects_duplicate_sibling_labels() -> None:
    payload = _outline().model_dump(mode="json")
    payload["subjects"][0]["modules"][0]["knowledge_points"].append(
        {"name": " 函数极限 ", "type": "concept"}
    )

    with pytest.raises(ValidationError, match="duplicate knowledge point"):
        ImportedOutline.model_validate(payload)
