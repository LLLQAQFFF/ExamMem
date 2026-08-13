from __future__ import annotations

from pydantic import TypeAdapter, ValidationError
import pytest

from exam_mem.contracts import ErrorType
from exam_mem.domain import (
    SlotKey,
    Taxonomy,
    build_error_pattern_slot_key,
    build_mastery_slot_key,
    build_plan_slot_key,
    build_preference_slot_key,
    build_profile_slot_key,
    load_taxonomy,
    validate_slot_key,
)


@pytest.fixture(scope="module")
def math1_taxonomy() -> Taxonomy:
    return load_taxonomy("math1_v1")


@pytest.mark.slot_key
def test_builds_all_five_slot_key_formats(math1_taxonomy: Taxonomy) -> None:
    assert (
        build_mastery_slot_key(
            math1_taxonomy,
            "math1.linear_algebra.eigenvalue",
        )
        == "mastery:math1.linear_algebra.eigenvalue"
    )
    assert (
        build_error_pattern_slot_key(
            math1_taxonomy,
            "math1.probability.bayes",
            ErrorType.CONCEPT_CONFUSION,
        )
        == "error_pattern:math1.probability.bayes:concept_confusion"
    )
    assert (
        build_plan_slot_key("postgraduate_entrance_exam", "math_1")
        == "plan:postgraduate_entrance_exam:math_1"
    )
    assert build_profile_slot_key("target_school") == "profile:target_school"
    assert build_preference_slot_key("explanation_style") == "preference:explanation_style"


@pytest.mark.slot_key
def test_slot_key_remains_a_json_string() -> None:
    adapter = TypeAdapter(SlotKey)

    value = adapter.validate_python("mastery:math1.linear_algebra.eigenvalue")

    assert isinstance(value, str)
    assert adapter.dump_json(value) == b'"mastery:math1.linear_algebra.eigenvalue"'


@pytest.mark.slot_key
def test_mastery_and_error_pattern_require_active_taxonomy_leaves(
    math1_taxonomy: Taxonomy,
) -> None:
    with pytest.raises(ValueError, match="unknown canonical"):
        build_mastery_slot_key(math1_taxonomy, "math1.linear_algebra.unknown")
    with pytest.raises(ValueError, match="diagnostic leaf"):
        build_mastery_slot_key(math1_taxonomy, "math1.linear_algebra")


@pytest.mark.slot_key
def test_deprecated_taxonomy_node_cannot_create_a_new_slot() -> None:
    taxonomy = Taxonomy.model_validate(
        {
            "taxonomy_version": "demo_v1",
            "nodes": [
                {
                    "id": "demo",
                    "name_zh": "示例",
                    "parent_id": None,
                    "aliases": [],
                    "prerequisites": [],
                    "status": "active",
                    "replaced_by": None,
                },
                {
                    "id": "demo.old",
                    "name_zh": "旧节点",
                    "parent_id": "demo",
                    "aliases": [],
                    "prerequisites": [],
                    "status": "deprecated",
                    "replaced_by": "demo.new",
                },
                {
                    "id": "demo.new",
                    "name_zh": "新节点",
                    "parent_id": "demo",
                    "aliases": [],
                    "prerequisites": [],
                    "status": "active",
                    "replaced_by": None,
                },
            ],
        }
    )

    with pytest.raises(ValueError, match="is not active"):
        build_mastery_slot_key(taxonomy, "demo.old")


@pytest.mark.slot_key
@pytest.mark.parametrize(
    "value",
    [
        "mastery:math1.linear_algebra.eigenvalue:extra",
        "mastery:user_001:math1.linear_algebra.eigenvalue",
        "mastery:math1.linear_algebra.eigenvalue,math1.linear_algebra.eigenvector",
        "error_pattern:math1.probability.bayes:中文错因",
        "plan:postgraduate entrance exam:math_1",
        "profile:Target School",
        "unsupported:attribute",
    ],
)
def test_slot_key_rejects_scope_segments_free_text_and_combined_knowledge_points(
    value: str,
) -> None:
    with pytest.raises((ValidationError, ValueError)):
        validate_slot_key(value)


@pytest.mark.slot_key
def test_plan_slot_is_fixed_to_stage_four_mvp_identity() -> None:
    with pytest.raises(ValueError, match="stage-four MVP"):
        build_plan_slot_key("postgraduate_math_1", "probability_theory")


@pytest.mark.slot_key
def test_multi_knowledge_point_order_does_not_change_independent_slots(
    math1_taxonomy: Taxonomy,
) -> None:
    knowledge_point_ids = [
        "math1.linear_algebra.eigenvalue",
        "math1.linear_algebra.eigenvector",
    ]

    forward = {
        build_mastery_slot_key(math1_taxonomy, knowledge_point_id)
        for knowledge_point_id in knowledge_point_ids
    }
    reverse = {
        build_mastery_slot_key(math1_taxonomy, knowledge_point_id)
        for knowledge_point_id in reversed(knowledge_point_ids)
    }

    assert forward == reverse
    assert len(forward) == 2
