"""Small, frozen question catalog for the Stage 07 browser practice entry.

This is deliberately not a general question-bank implementation.  It gives the
Stage 07 Web/API entry a server-side source of validated ``Question`` objects
without exposing reference answers or grading rubrics to the browser.
"""

from __future__ import annotations

from .contracts import Question

_STAGE07_QUESTIONS = (
    Question(
        question_id="stage07:linear-algebra:matrix-multiplication:001",
        stem="设 A=[[1,2],[0,1]]，B=[[2,0],[3,1]]，计算 AB。",
        knowledge_point_ids=["math1.linear_algebra.matrix_multiplication"],
        difficulty=0.35,
        reference_answer="AB=[[8,2],[3,1]]。",
        grading_rubric={
            "required_steps": [
                {"id": "row_by_column", "description": "按行乘列计算每个元素"},
                {"id": "matrix_result", "description": "得到矩阵 [[8,2],[3,1]]"},
            ]
        },
    ),
    Question(
        question_id="stage07:linear-algebra:matrix-multiplication:002",
        stem=(
            "已知 A 是 2×3 矩阵，B 是 3×2 矩阵。说明 AB 与 BA 是否都有定义，并分别写出它们的阶数。"
        ),
        knowledge_point_ids=["math1.linear_algebra.matrix_multiplication"],
        difficulty=0.55,
        reference_answer="AB 与 BA 都有定义；AB 是 2×2 矩阵，BA 是 3×3 矩阵。",
        grading_rubric={
            "required_steps": [
                {"id": "dimension_rule", "description": "使用前矩阵列数等于后矩阵行数"},
                {"id": "ab_shape", "description": "判断 AB 为 2×2"},
                {"id": "ba_shape", "description": "判断 BA 为 3×3"},
            ]
        },
    ),
    Question(
        question_id="stage07:probability:bayes:001",
        stem="已知 P(A)=0.3，P(B|A)=0.8，P(B)=0.5，求 P(A|B)。",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.4,
        reference_answer="P(A|B)=P(B|A)P(A)/P(B)=0.48。",
        grading_rubric={
            "required_steps": [
                {"id": "identify_prior", "description": "识别先验概率 P(A)"},
                {"id": "apply_bayes", "description": "正确应用贝叶斯公式"},
                {"id": "calculate", "description": "计算得到 0.48"},
            ]
        },
    ),
    Question(
        question_id="stage07:probability:bayes:002",
        stem=(
            "某病患病率为 1%，检测对患者呈阳性的概率为 95%，对非患者误报阳性的"
            "概率为 5%。某人检测呈阳性，求其患病概率。"
        ),
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.65,
        reference_answer=("P(患病|阳性)=0.95×0.01/(0.95×0.01+0.05×0.99)≈0.161。"),
        grading_rubric={
            "required_steps": [
                {"id": "positive_probability", "description": "用全概率计算阳性概率"},
                {"id": "apply_bayes", "description": "正确应用贝叶斯公式"},
                {"id": "calculate", "description": "计算结果约为 0.161"},
            ]
        },
    ),
)


def stage07_practice_questions() -> tuple[Question, ...]:
    """Return the immutable server-side catalog used by the Stage 07 entry."""

    return _STAGE07_QUESTIONS


def stage07_question(question_id: str) -> Question | None:
    """Resolve one public question ID without accepting client question data."""

    normalized = question_id.strip()
    return next(
        (question for question in _STAGE07_QUESTIONS if question.question_id == normalized),
        None,
    )


__all__ = ["stage07_practice_questions", "stage07_question"]
