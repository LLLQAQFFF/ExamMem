from __future__ import annotations

from pydantic import ValidationError
import pytest

from exam_mem.domain import (
    UNKNOWN_KNOWLEDGE_POINT_ID,
    RuleBasedKnowledgePointNormalizer,
    load_taxonomy,
)


@pytest.fixture
def normalizer() -> RuleBasedKnowledgePointNormalizer:
    return RuleBasedKnowledgePointNormalizer(load_taxonomy("math1_v1"))


@pytest.mark.taxonomy
@pytest.mark.parametrize(
    ("candidate_name", "expected_id"),
    [
        (
            "math1.probability.conditional_probability",
            "math1.probability.conditional_probability",
        ),
        ("条件概率公式", "math1.probability.conditional_probability"),
        ("  【ＣＤＦ】  ", "math1.probability.distribution_function"),
        ("先验后验混淆", "math1.probability.bayes"),
        ("特征值", "math1.linear_algebra.eigenvalue"),
        ("特征向量", "math1.linear_algebra.eigenvector"),
    ],
)
def test_rule_normalizer_maps_exact_alias_and_controlled_rules(
    normalizer: RuleBasedKnowledgePointNormalizer,
    candidate_name: str,
    expected_id: str,
) -> None:
    result = normalizer.normalize(candidate_name, 0.82)

    assert result.knowledge_point_id == expected_id
    assert result.confidence == 0.82


@pytest.mark.taxonomy
@pytest.mark.parametrize(
    "candidate_name",
    [
        "math1.linear_algebra",
        "线代",
        "",
        "  ，！？  ",
        "锟斤拷",
        "尚未收录的知识点",
    ],
)
def test_rule_normalizer_conservatively_returns_unknown(
    normalizer: RuleBasedKnowledgePointNormalizer,
    candidate_name: str,
) -> None:
    result = normalizer.normalize(candidate_name, 0.31)

    assert result.knowledge_point_id == UNKNOWN_KNOWLEDGE_POINT_ID
    assert result.confidence == 0.31


@pytest.mark.taxonomy
def test_similar_eigenvalue_and_eigenvector_concepts_remain_distinct(
    normalizer: RuleBasedKnowledgePointNormalizer,
) -> None:
    eigenvalue = normalizer.normalize("矩阵特征值", 0.9)
    eigenvector = normalizer.normalize("矩阵特征向量", 0.9)

    assert eigenvalue.knowledge_point_id == "math1.linear_algebra.eigenvalue"
    assert eigenvector.knowledge_point_id == "math1.linear_algebra.eigenvector"
    assert eigenvalue.knowledge_point_id != eigenvector.knowledge_point_id


@pytest.mark.taxonomy
def test_multi_knowledge_point_output_is_order_independent_and_deduplicated(
    normalizer: RuleBasedKnowledgePointNormalizer,
) -> None:
    primary = ("条件概率公式", 0.95)
    secondary = [
        ("特征向量", 0.72),
        ("矩阵特征值", 0.81),
        ("矩阵特征向量", 0.68),
        ("条件概率", 0.77),
    ]

    forward = normalizer.normalize_many(primary=primary, secondary=secondary)
    reversed_input = normalizer.normalize_many(
        primary=primary,
        secondary=reversed(secondary),
    )

    assert forward == reversed_input
    assert forward.primary_knowledge_point_id == ("math1.probability.conditional_probability")
    assert forward.primary_confidence == 0.95
    assert forward.secondary_knowledge_point_ids == (
        "math1.linear_algebra.eigenvalue",
        "math1.linear_algebra.eigenvector",
    )
    assert forward.secondary_confidences == (0.81, 0.72)


@pytest.mark.taxonomy
def test_normalizer_rejects_confidence_outside_probability_range(
    normalizer: RuleBasedKnowledgePointNormalizer,
) -> None:
    with pytest.raises(ValidationError):
        normalizer.normalize("条件概率", 1.01)
