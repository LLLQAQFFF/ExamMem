from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from evaluation.contracts.case import PROTOCOL_SEED, PROTOCOL_VERSION, DatasetSplit
from evaluation.contracts.dataset import (
    BenchmarkEntry,
    ControlledQuestion,
    DatasetManifest,
)

pytestmark = [pytest.mark.protocol, pytest.mark.schema]


def test_controlled_question_requires_auditable_correct_and_wrong_answers() -> None:
    question = ControlledQuestion.model_validate(
        {
            "question_id": "eval:matrix:001",
            "knowledge_point_id": "math1.linear_algebra.matrix_multiplication",
            "subject_area": "linear_algebra",
            "difficulty": 0.4,
            "prompt_zh": "计算两个二阶矩阵的乘积。",
            "reference_answer_zh": "按行乘列计算。",
            "rubric_items": ["使用行乘列规则"],
            "answer_forms": [
                {"answer_id": "correct", "text_zh": "正确答案", "correct": True},
                {
                    "answer_id": "wrong",
                    "text_zh": "错误答案",
                    "correct": False,
                    "error_type": "calculation_error",
                    "error_detail": "乘法计算错误",
                },
            ],
        }
    )

    assert question.answer_forms[1].error_type.value == "calculation_error"


@pytest.mark.parametrize(
    "answer_forms",
    [
        [
            {"answer_id": "correct", "text_zh": "正确", "correct": True},
            {"answer_id": "wrong", "text_zh": "错误", "correct": False},
        ],
        [
            {"answer_id": "a", "text_zh": "正确一", "correct": True},
            {"answer_id": "b", "text_zh": "正确二", "correct": True},
        ],
    ],
)
def test_controlled_question_rejects_unverifiable_answer_forms(
    answer_forms: list[dict[str, object]],
) -> None:
    with pytest.raises(ValidationError):
        ControlledQuestion.model_validate(
            {
                "question_id": "eval:matrix:001",
                "knowledge_point_id": "math1.linear_algebra.matrix_multiplication",
                "subject_area": "linear_algebra",
                "difficulty": 0.4,
                "prompt_zh": "计算两个二阶矩阵的乘积。",
                "reference_answer_zh": "按行乘列计算。",
                "rubric_items": ["使用行乘列规则"],
                "answer_forms": answer_forms,
            }
        )


def test_benchmark_entry_rejects_unversioned_extra_fields() -> None:
    with pytest.raises(ValidationError):
        BenchmarkEntry.model_validate(
            {
                "case_id": "case-1",
                "profile": {
                    "profile_id": "profile-1",
                    "background_zh": "备考学生",
                    "learning_goal_zh": "掌握矩阵乘法",
                    "known_well_zh": [],
                    "partial_knowledge_zh": [],
                    "beliefs_zh": ["矩阵乘法可以交换"],
                },
                "task_title_zh": "纠正矩阵乘法误区",
                "initial_message_zh": "我觉得 AB 和 BA 总是一样。",
                "target_knowledge_point_ids": ["math1.linear_algebra.matrix_multiplication"],
                "success_criteria_zh": ["能说明乘法通常不可交换"],
                "trajectory_family": "non_commutative_matrix_product",
                "answer_by_event_id": {"event-1": "wrong"},
                "unexpected": True,
            }
        )


def test_dataset_manifest_pins_both_splits_and_frozen_hash() -> None:
    digest = "a" * 64
    manifest = DatasetManifest.model_validate(
        {
            "dataset_version": "exam_mem_controlled_v1",
            "protocol_version": PROTOCOL_VERSION,
            "seed": PROTOCOL_SEED,
            "generated_at": datetime(2026, 8, 17, tzinfo=timezone.utc),
            "question_bank_sha256": digest,
            "benchmark_entries_sha256": digest,
            "splits": [
                {
                    "split": split.value,
                    "case_count": case_count,
                    "aggregate_sha256": digest,
                    "files": [
                        {
                            "path": f"{split.value}/case-{index:03d}.json",
                            "split": split.value,
                            "case_id": f"{split.value}-case-{index:03d}",
                            "scenario_type": "semantic_duplicate",
                            "sha256": digest,
                        }
                        for index in range(case_count)
                    ],
                }
                for split, case_count in (
                    (DatasetSplit.DEV, 40),
                    (DatasetSplit.TEST, 80),
                )
            ],
            "frozen_test_sha256": digest,
            "construction_notes": ["test split is not scored during Stage 08"],
        }
    )

    assert [split.split for split in manifest.splits] == [
        DatasetSplit.DEV,
        DatasetSplit.TEST,
    ]
