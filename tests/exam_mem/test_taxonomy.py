from __future__ import annotations

from copy import deepcopy

from pydantic import ValidationError
import pytest

from exam_mem.domain import Taxonomy, load_taxonomy


def _minimal_taxonomy_payload() -> dict:
    return {
        "taxonomy_version": "test_v1",
        "nodes": [
            {
                "id": "test",
                "name_zh": "测试根节点",
                "parent_id": None,
                "aliases": [],
                "prerequisites": [],
                "status": "active",
                "replaced_by": None,
            },
            {
                "id": "test.child",
                "name_zh": "测试子节点",
                "parent_id": "test",
                "aliases": ["子节点别名"],
                "prerequisites": [],
                "status": "active",
                "replaced_by": None,
            },
        ],
    }


@pytest.mark.taxonomy
def test_math1_v1_has_confirmed_domains_and_leaf_count() -> None:
    taxonomy = load_taxonomy("math1_v1")
    leaves = [node for node in taxonomy.nodes if not taxonomy.children_of(node.id)]

    assert taxonomy.taxonomy_version == "math1_v1"
    assert taxonomy.get("math1.linear_algebra") is not None
    assert taxonomy.get("math1.probability") is not None
    assert len(leaves) == 30
    assert all(node.status == "active" for node in taxonomy.nodes)


@pytest.mark.taxonomy
@pytest.mark.parametrize(
    "knowledge_point_id",
    [
        "math1.linear_algebra.matrix_rank",
        "math1.linear_algebra.linear_equation_solution_structure",
        "math1.linear_algebra.eigenvalue",
        "math1.linear_algebra.eigenvector",
        "math1.linear_algebra.similarity_diagonalization",
        "math1.linear_algebra.quadratic_form",
        "math1.linear_algebra.vector_space",
        "math1.probability.random_event",
        "math1.probability.conditional_probability",
        "math1.probability.total_probability",
        "math1.probability.bayes",
        "math1.probability.random_variable_distribution",
        "math1.probability.expectation",
        "math1.probability.law_large_numbers",
        "math1.probability.central_limit_theorem",
        "math1.probability.parameter_estimation",
    ],
)
def test_math1_v1_covers_stage_four_required_knowledge(knowledge_point_id: str) -> None:
    assert load_taxonomy("math1_v1").get(knowledge_point_id) is not None


@pytest.mark.taxonomy
def test_taxonomy_rejects_duplicate_ids() -> None:
    payload = _minimal_taxonomy_payload()
    payload["nodes"].append(deepcopy(payload["nodes"][1]))

    with pytest.raises(ValidationError, match="duplicate taxonomy node id"):
        Taxonomy.model_validate(payload)


@pytest.mark.taxonomy
def test_taxonomy_rejects_unknown_parent() -> None:
    payload = _minimal_taxonomy_payload()
    payload["nodes"][1]["parent_id"] = "missing"

    with pytest.raises(ValidationError, match="unknown parent"):
        Taxonomy.model_validate(payload)


@pytest.mark.taxonomy
def test_taxonomy_rejects_parent_cycle() -> None:
    payload = _minimal_taxonomy_payload()
    payload["nodes"][0]["parent_id"] = "test.child"

    with pytest.raises(ValidationError, match="parent cycle"):
        Taxonomy.model_validate(payload)


@pytest.mark.taxonomy
def test_taxonomy_rejects_alias_conflict_after_unicode_normalization() -> None:
    payload = _minimal_taxonomy_payload()
    payload["nodes"].append(
        {
            "id": "test.sibling",
            "name_zh": "另一个子节点",
            "parent_id": "test",
            "aliases": ["子节点别名 "],
            "prerequisites": [],
            "status": "active",
            "replaced_by": None,
        }
    )

    with pytest.raises(ValidationError, match="conflicts between"):
        Taxonomy.model_validate(payload)


@pytest.mark.taxonomy
def test_deprecated_node_requires_an_active_replacement() -> None:
    payload = _minimal_taxonomy_payload()
    payload["nodes"][1].update({"status": "deprecated", "replaced_by": None})

    with pytest.raises(ValidationError, match="must declare replaced_by"):
        Taxonomy.model_validate(payload)


@pytest.mark.taxonomy
def test_loader_rejects_unknown_or_unsafe_version() -> None:
    with pytest.raises(ValueError, match="does not exist"):
        load_taxonomy("missing_v1")
    with pytest.raises(ValueError, match="invalid taxonomy version"):
        load_taxonomy("../math1_v1")
