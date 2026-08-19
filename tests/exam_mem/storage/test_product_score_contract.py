from exam_mem.practice import GradeResult
from exam_mem.storage.product_repository import _grade_summary


def _grade(score: float, version: str) -> GradeResult:
    return GradeResult(
        correct=score > 0,
        score=score,
        matched_rubric_items=[],
        missed_rubric_items=[],
        evidence=["evidence"],
        grader_version=version,
    )


def test_product_score_summary_fails_closed_for_invalid_legacy_scale() -> None:
    assert _grade_summary([_grade(1.0, "answer_grader_v2"), _grade(0.0, "answer_grader_v2")]) == (
        0.5,
        False,
    )
    assert _grade_summary([_grade(100.0, "answer_grader_v1")]) == (None, True)
