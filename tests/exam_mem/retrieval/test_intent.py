from __future__ import annotations

from exam_mem.contracts import MemoryScope
from exam_mem.domain import load_taxonomy
from exam_mem.retrieval import TaxonomyRetrievalIntentResolver

SCOPE = MemoryScope(
    user_id="retrieval_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def test_resolver_extracts_specific_taxonomy_and_mastery_constraints() -> None:
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))

    intent = resolver.resolve(
        SCOPE,
        "正定二次型存在冲突；请检索其中声称仍处在改善阶段的分支。",
    )

    assert intent.knowledge_point_ids == (
        "math1.linear_algebra.positive_definite_quadratic_form",
    )
    assert intent.mastery_level == "improving"
    assert intent.explicit_target is True
    assert intent.unknown_explicit_target is False


def test_resolver_rejects_explicit_unknown_target_and_insufficient_reference() -> None:
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))

    unknown = resolver.resolve(SCOPE, "请检查我对傅里叶级数收敛性的掌握情况。")
    ambiguous = resolver.resolve(SCOPE, "我上次说的那个错误到底是什么？")

    assert unknown.explicit_target is True
    assert unknown.unknown_explicit_target is True
    assert ambiguous.reference_sufficient is False


def test_resolver_treats_requested_error_type_as_a_hard_constraint() -> None:
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))
    scope = SCOPE.model_copy(update={"memory_namespace": "error_pattern"})

    intent = resolver.resolve(
        scope,
        "只查逆矩阵因为看错题干造成的 reading error；不要拿概念或公式错误代替。",
    )

    assert intent.knowledge_point_ids == ("math1.linear_algebra.inverse_matrix",)
    assert intent.error_type == "reading_error"


def test_resolver_does_not_apply_mastery_language_to_error_pattern_scope() -> None:
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))
    scope = SCOPE.model_copy(update={"memory_namespace": "error_pattern"})

    intent = resolver.resolve(scope, "只查矩阵的秩因为我想检查薄弱记忆。")

    assert intent.knowledge_point_ids == ("math1.linear_algebra.matrix_rank",)
    assert intent.mastery_level is None


def test_resolver_does_not_turn_incidental_symptom_terms_into_hard_filters() -> None:
    resolver = TaxonomyRetrievalIntentResolver(load_taxonomy("math1_v1"))
    scope = SCOPE.model_copy(update={"memory_namespace": "error_pattern"})

    intent = resolver.resolve(
        scope,
        "诊断这个症状：我把特征多项式写成矩阵行列式最后再减 lambda。",
    )

    assert intent.knowledge_point_ids == ()
    assert intent.explicit_target is False
