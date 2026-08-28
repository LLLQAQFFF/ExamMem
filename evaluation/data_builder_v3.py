"""Build the fully rewritten computer-science controlled holdout dataset."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evaluation.contracts.dataset import ControlledQuestion, DatasetManifest
from evaluation.data_builder import _build_dataset
from evaluation.data_builder_v2 import (
    TAXONOMY_VERSION,
)
from evaluation.data_builder_v2 import (
    _question_bank as _v2_question_bank,
)
from evaluation.protocols.validation import DATASET_ROOT

DATASET_VERSION = "exam_mem_controlled_v3"
_GENERATED_AT = datetime(2026, 8, 28, 3, 0, tzinfo=timezone.utc)


def _question_bank() -> list[ControlledQuestion]:
    return [
        question.model_copy(
            update={"question_id": question.question_id.replace("controlled2:", "controlled3:")}
        )
        for question in _v2_question_bank()
    ]


def _rewrite_memory_value(value: dict[str, Any] | None, *, topic_name: str) -> None:
    if value is None:
        return
    value_type = value.get("type")
    if value_type == "error_pattern":
        error_type = value.get("error_type", "unknown")
        value["summary"] = f"{topic_name}的 {error_type} 型受控错误"
        value["details"] = [f"{topic_name}学习轨迹中的可复核错误证据"]
    elif value_type == "plan":
        value["goal"] = f"完成{topic_name}专项学习计划"


def _rewrite_named_prose(value: Any, *, topic_name: str) -> None:
    if isinstance(value, list):
        for item in value:
            _rewrite_named_prose(item, topic_name=topic_name)
        return
    if not isinstance(value, dict):
        return
    if "statement" in value:
        value["statement"] = f"关于{topic_name}的既有记录需要按用户确认进行更正。"
    if "reason" in value:
        value["reason"] = f"用户明确确认了关于{topic_name}的学习状态变更。"
    for item in value.values():
        _rewrite_named_prose(item, topic_name=topic_name)


def _rewrite_exam_ids(payload: dict[str, Any]) -> None:
    found: set[str] = set()

    def collect(value: Any, *, field_name: str = "") -> None:
        if isinstance(value, list):
            for item in value:
                collect(item, field_name=field_name)
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(item, field_name=key)
        elif field_name == "exam_id" and isinstance(value, str):
            found.add(value)

    collect(payload)
    replacements = {
        old: f"computer_science_exam_{index:02d}"
        for index, old in enumerate(sorted(found), start=1)
    }

    def replace(value: Any, *, field_name: str = "") -> Any:
        if isinstance(value, list):
            return [replace(item, field_name=field_name) for item in value]
        if isinstance(value, dict):
            return {key: replace(item, field_name=key) for key, item in value.items()}
        if field_name == "exam_id" and isinstance(value, str):
            return replacements[value]
        return value

    rewritten = replace(payload)
    payload.clear()
    payload.update(rewritten)


def _rewrite_residual_identifiers(
    value: Any,
    *,
    question: ControlledQuestion,
) -> Any:
    if isinstance(value, list):
        return [_rewrite_residual_identifiers(item, question=question) for item in value]
    if isinstance(value, dict):
        return {
            key: _rewrite_residual_identifiers(item, question=question)
            for key, item in value.items()
        }
    if not isinstance(value, str):
        return value
    interference_topic = (
        "cs.algorithms.dfs"
        if question.knowledge_point_id != "cs.algorithms.dfs"
        else "cs.data_structures.queue"
    )
    replacements = {
        "math1.probability.independence": interference_topic,
        "postgraduate_entrance_exam": "computer_science_exam",
        "math_1": question.subject_area,
    }
    result = value
    for original, replacement in replacements.items():
        result = result.replace(original, replacement)
    return result


def _cross_subject_transform(
    source: dict[str, Any],
    topic_name: str,
    question: ControlledQuestion,
) -> dict[str, Any]:
    payload = deepcopy(source)
    scenario = str(payload["scenario_type"])
    wrong_answer = next(answer for answer in question.answer_forms if not answer.correct)
    base_error = str(wrong_answer.error_detail)
    distinct_error_scenarios = {"complementary_evidence", "multi_value_error_pattern"}
    event_error_by_id: dict[str, str | None] = {}
    wrong_index = 0

    for event in payload["events"]:
        if event["event_type"] == "answer_attempt" and event["answer_correct"] is False:
            wrong_index += 1
            detail = base_error
            if scenario in distinct_error_scenarios:
                detail = f"{base_error}；独立表现 {wrong_index}。"
            elif scenario == "accidental_error":
                detail = f"{base_error}；本次证据被标记为偶发。"
            event["error_detail"] = detail
        event_error_by_id[event["event_id"]] = event.get("error_detail")
        _rewrite_named_prose(event, topic_name=topic_name)

    for memory in payload["initial_memory"]:
        _rewrite_memory_value(memory.get("value"), topic_name=topic_name)
    for operation in payload["gold_operations"]:
        extracted = operation["extracted_fields"]
        if extracted["event_type"] == "answer_attempt":
            extracted["error_detail"] = event_error_by_id[operation["event_id"]]
        _rewrite_named_prose(extracted, topic_name=topic_name)
        _rewrite_memory_value(operation.get("expected_result_value"), topic_name=topic_name)
        operation["reason_code"] = (
            f"{scenario}_{str(operation['operation']).lower()}_controlled_gold"
        )
    for action in payload["gold_actions"]:
        action["reason_code"] = f"{scenario}_{action['action_type']}_controlled_gold"
    for query in payload["queries"]:
        query["text"] = f"检索{topic_name}当前有效且与复习决策相关的学习记忆。"

    _rewrite_named_prose(payload["initial_memory"], topic_name=topic_name)
    payload = _rewrite_residual_identifiers(payload, question=question)
    _rewrite_exam_ids(payload)
    return payload


def build_cross_subject_dataset(output_root: Path = DATASET_ROOT) -> DatasetManifest:
    """Materialize the corrected, subject-pure 40/80 CS split."""
    questions = _question_bank()
    topic_order = tuple(question.knowledge_point_id for question in questions)
    return _build_dataset(
        output_root=output_root,
        dataset_version=DATASET_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        questions=questions,
        topic_order=topic_order,
        case_prefix="holdout3",
        case_root=Path(DATASET_VERSION),
        generated_at=_GENERATED_AT,
        learner_background_zh=(
            "我正在学习计算机基础中的数据结构与算法，正在通过练习检查长期掌握情况。"
        ),
        construction_notes=[
            "The recommendation policy was frozen before this corrected dataset was scored.",
            "All semantic prose, questions, answers, IDs, scopes, and queries use computer science.",
            "A forbidden-vocabulary gate rejects residual mathematics content before release.",
            "The reviewed v1 lifecycle shapes are reused, but their subject semantics are not.",
            "Dev contains 40 cases and the one-time frozen test contains 80 cases using seed 20260806.",
            "exam_mem_controlled_v2 is retained only as an invalid transformation audit record.",
        ],
        subject_transform=_cross_subject_transform,
    )


__all__ = ["DATASET_VERSION", "TAXONOMY_VERSION", "build_cross_subject_dataset"]
