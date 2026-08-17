"""Deterministically materialize the formal Stage 08 controlled dataset."""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import random
from typing import Any

from evaluation.contracts.case import (
    PROTOCOL_SEED,
    PROTOCOL_VERSION,
    ActionType,
    DatasetSplit,
    EvaluationCase,
    ScenarioType,
)
from evaluation.contracts.dataset import (
    BenchmarkEntry,
    ControlledQuestion,
    DatasetFileRecord,
    DatasetManifest,
    LearnerProfile,
    SplitManifest,
)
from evaluation.protocols.validation import DATASET_ROOT
from exam_mem.domain.taxonomy import load_taxonomy

DATASET_VERSION = "exam_mem_controlled_v1"
MANIFEST_PATH = DATASET_ROOT / f"{DATASET_VERSION}.manifest.json"
QUESTION_BANK_PATH = DATASET_ROOT / f"{DATASET_VERSION}.questions.json"
BENCHMARK_ENTRIES_PATH = DATASET_ROOT / f"{DATASET_VERSION}.benchmark.jsonl"
_GENERATED_AT = datetime(2026, 8, 6, tzinfo=timezone.utc)


class DatasetBuildError(ValueError):
    """Raised when a frozen template cannot produce an auditable formal case."""


_QUESTION_SPECS: dict[str, tuple[str, str, str, str, float]] = {
    "math1.linear_algebra.matrix_multiplication": (
        "设 A=[[1,2],[0,1]]，B=[[2,0],[1,3]]，计算 AB。",
        "AB=[[4,6],[1,3]]，按 A 的行与 B 的列逐项相乘求和。",
        "AB=[[2,2],[1,3]]。",
        "没有按行乘列计算矩阵乘积。",
        0.35,
    ),
    "math1.linear_algebra.determinant": (
        "计算二阶行列式 |2 1; 3 4|。",
        "行列式为 2×4-1×3=5。",
        "行列式为 2×4+1×3=11。",
        "二阶行列式主对角积与副对角积的符号使用错误。",
        0.25,
    ),
    "math1.linear_algebra.matrix_rank": (
        "三阶单位矩阵 I₃ 的秩是多少？",
        "I₃ 有三个线性无关的行（列），因此秩为 3。",
        "秩为 1，因为它只有一个主对角线。",
        "把主对角线误当成一个线性无关向量。",
        0.2,
    ),
    "math1.linear_algebra.eigenvalue": (
        "对角矩阵 diag(2,3) 的全部特征值是什么？",
        "特征值为 2 和 3，因为对角矩阵的特征值就是对角元。",
        "特征值只有 5，因为 2+3=5。",
        "把矩阵的迹误认为唯一特征值。",
        0.3,
    ),
    "math1.linear_algebra.eigenvector": (
        "向量 v 成为矩阵 A 对应特征值 λ 的特征向量需要满足什么条件？",
        "v 必须非零，并满足 Av=λv。",
        "只需满足 Av=v，且允许 v=0。",
        "遗漏非零条件并把任意特征值固定为 1。",
        0.35,
    ),
    "math1.linear_algebra.linear_independence": (
        "向量组线性无关的等价判据是什么？",
        "线性组合等于零时，所有系数只能全为零。",
        "存在一组不全为零的系数使线性组合等于零。",
        "把线性相关的判据当成线性无关。",
        0.4,
    ),
    "math1.linear_algebra.vector_space": (
        "R² 中经过原点的一条直线是否构成 R² 的线性子空间？",
        "是；它包含零向量，并对向量加法和数乘封闭。",
        "否；任何真子集都不可能是线性子空间。",
        "忽略了子空间只需满足封闭性而不必等于整个空间。",
        0.35,
    ),
    "math1.linear_algebra.quadratic_form": (
        "二次型 x²+2xy+y² 的对称矩阵是什么？",
        "矩阵为 [[1,1],[1,1]]，因为交叉项系数等于两个对称元之和。",
        "矩阵为 [[1,2],[2,1]]。",
        "没有将交叉项系数平均分配到两个对称位置。",
        0.5,
    ),
    "math1.probability.conditional_probability": (
        "若 P(A∩B)=0.2、P(B)=0.5，求 P(A|B)。",
        "P(A|B)=P(A∩B)/P(B)=0.2/0.5=0.4。",
        "P(A|B)=0.2×0.5=0.1。",
        "条件概率公式中误用了乘法。",
        0.3,
    ),
    "math1.probability.total_probability": (
        "B₁、B₂ 构成样本空间划分时，如何用它们表示 P(A)？",
        "P(A)=P(A|B₁)P(B₁)+P(A|B₂)P(B₂)。",
        "P(A)=P(A|B₁)+P(A|B₂)。",
        "遗漏了各分支的先验概率权重。",
        0.4,
    ),
    "math1.probability.bayes": (
        "已知 P(A|B)、P(B) 和 P(A)，如何求 P(B|A)？",
        "P(B|A)=P(A|B)P(B)/P(A)，其中 P(A)>0。",
        "P(B|A)=P(A|B)P(A)/P(B)。",
        "贝叶斯公式中的先验概率与证据概率位置颠倒。",
        0.45,
    ),
    "math1.probability.distribution_function": (
        "随机变量 X 的分布函数 F(x) 如何定义？",
        "F(x)=P(X≤x)，它单调不减、右连续，且两端极限分别为 0 和 1。",
        "F(x)=P(X=x)，它必须处处连续。",
        "把分布函数误写成点概率并遗漏右连续性质。",
        0.35,
    ),
}
_TOPIC_ORDER = tuple(_QUESTION_SPECS)


def _canonical_json_bytes(value: Any, *, newline: bool = True) -> bytes:
    suffix = "\n" if newline else ""
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + suffix).encode(
        "utf-8"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _question_bank() -> list[ControlledQuestion]:
    taxonomy = load_taxonomy("math1_v1")
    questions: list[ControlledQuestion] = []
    for knowledge_point_id, (prompt, reference, wrong, error_detail, difficulty) in sorted(
        _QUESTION_SPECS.items()
    ):
        node = taxonomy.get(knowledge_point_id)
        if node is None:
            raise DatasetBuildError(
                f"question references unknown taxonomy leaf: {knowledge_point_id}"
            )
        subject_area = (
            "linear_algebra" if ".linear_algebra." in knowledge_point_id else "probability_theory"
        )
        slug = knowledge_point_id.rsplit(".", 1)[-1]
        questions.append(
            ControlledQuestion(
                question_id=f"controlled:{slug}:v1",
                knowledge_point_id=knowledge_point_id,
                subject_area=subject_area,
                difficulty=difficulty,
                prompt_zh=prompt,
                reference_answer_zh=reference,
                rubric_items=["结论正确", "关键公式或判据正确", "理由可复核"],
                answer_forms=[
                    {
                        "answer_id": "correct",
                        "text_zh": reference,
                        "correct": True,
                    },
                    {
                        "answer_id": "wrong",
                        "text_zh": wrong,
                        "correct": False,
                        "error_type": "concept_confusion",
                        "error_detail": error_detail,
                    },
                ],
            )
        )
    return questions


def _qualify_identifiers(value: Any, case_id: str, *, field_name: str = "") -> Any:
    if isinstance(value, list):
        return [_qualify_identifiers(item, case_id, field_name=field_name) for item in value]
    if isinstance(value, dict):
        return {
            key: _qualify_identifiers(item, case_id, field_name=key) for key, item in value.items()
        }
    if not isinstance(value, str):
        return value
    untouched = {
        "knowledge_point_ids",
        "canonical_knowledge_point_ids",
        "slot_key",
        "subject_id",
        "question_id",
    }
    identity_lists = {
        "active_memory_ids",
        "archived_memory_ids",
        "candidate_memory_ids",
        "contested_memory_ids",
        "evidence_event_ids",
        "invalidated_memory_ids",
        "provenance",
        "target_memory_ids",
    }
    if field_name in untouched:
        return value
    if field_name.endswith("_id") or field_name in identity_lists | {
        "idempotency_key",
        "superseded_by",
    }:
        return f"{case_id}:{value}"
    return value


def _memory_slot_map(case: dict[str, Any]) -> dict[str, str]:
    slots = {memory["memory_id"]: memory["slot_key"] for memory in case["initial_memory"]}
    slots.update(
        {
            operation["result_memory_id"]: operation["slot_key"]
            for operation in case["gold_operations"]
            if operation["result_memory_id"] is not None
        }
    )
    return slots


def _replace_knowledge_points(value: Any, replacements: dict[str, str]) -> Any:
    if isinstance(value, list):
        return [_replace_knowledge_points(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_knowledge_points(item, replacements) for key, item in value.items()}
    if not isinstance(value, str):
        return value
    result = value
    for original, replacement in sorted(
        replacements.items(), key=lambda item: len(item[0]), reverse=True
    ):
        result = result.replace(original, replacement)
    return result


def _remap_topic(
    payload: dict[str, Any],
    *,
    target_knowledge_point_id: str,
    topic_name: str,
) -> dict[str, Any]:
    original_ids = {
        knowledge_point_id
        for operation in payload["gold_operations"]
        for knowledge_point_id in operation["canonical_knowledge_point_ids"]
    }
    replacements = {
        knowledge_point_id: target_knowledge_point_id for knowledge_point_id in original_ids
    }
    main_subject = payload["events"][0]["context"]["subject_id"]
    target_subject = (
        "linear_algebra"
        if ".linear_algebra." in target_knowledge_point_id
        else "probability_theory"
    )
    remapped = _replace_knowledge_points(payload, replacements)

    def replace_subjects(value: Any, *, field_name: str = "") -> Any:
        if isinstance(value, list):
            return [replace_subjects(item, field_name=field_name) for item in value]
        if isinstance(value, dict):
            return {key: replace_subjects(item, field_name=key) for key, item in value.items()}
        if field_name != "subject_id" or not isinstance(value, str):
            return value
        return target_subject if value == main_subject else f"interference_{target_subject}"

    remapped = replace_subjects(remapped)

    def update_values(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                update_values(item)
            return
        if not isinstance(value, dict):
            return
        if value.get("type") == "error_pattern":
            error_type = value.get("error_type", "unknown")
            value["summary"] = f"{topic_name}的 {error_type} 型受控错误"
            value["details"] = [f"{topic_name}轨迹中的可复核错误证据"]
        elif value.get("type") == "plan":
            value["goal"] = f"完成{topic_name}专项学习计划"
        for item in value.values():
            update_values(item)

    update_values(remapped)
    return remapped


def _append_low_quality_no_op(
    case: dict[str, Any],
    *,
    question_by_kp: dict[str, ControlledQuestion],
    step_number: int,
) -> None:
    case_id = case["case_id"]
    canonical_ids = case["gold_operations"][0]["canonical_knowledge_point_ids"]
    knowledge_point_id = canonical_ids[0]
    question = question_by_kp[knowledge_point_id]
    event_id = f"{case_id}:padding_event:{step_number}"
    step_id = f"{case_id}:padding_step:{step_number}"
    occurred_at = datetime.fromisoformat(case["events"][-1]["occurred_at"].replace("Z", "+00:00"))
    occurred_at += timedelta(days=7)
    context = deepcopy(case["events"][0]["context"])
    event = {
        "event_id": event_id,
        "idempotency_key": f"{case_id}:padding_idempotency:{step_number}",
        "event_type": "answer_attempt",
        "context": context,
        "session_id": f"{case_id}:session:{step_number}",
        "question_id": question.question_id,
        "knowledge_point_ids": [knowledge_point_id],
        "difficulty": question.difficulty,
        "answer_correct": False,
        "error_type": "unknown",
        "error_detail": "本次作答受外部中断影响，不能作为稳定能力证据。",
        "evidence_quality": {
            "confidence": 0.1,
            "is_temporary_exception": True,
            "reasons": ["external_disruption"],
        },
        "correction": None,
        "plan_transition": None,
        "occurred_at": occurred_at.isoformat(),
    }
    extracted = {
        "event_type": "answer_attempt",
        "knowledge_point_ids": [knowledge_point_id],
        "answer_correct": False,
        "error_type": "unknown",
        "error_detail": event["error_detail"],
        "evidence_quality": event["evidence_quality"],
        "correction": None,
        "plan_transition": None,
    }
    last_state = case["gold_states"][-1]
    slots = _memory_slot_map(case)
    state_ids = [
        *last_state["active_memory_ids"],
        *last_state["contested_memory_ids"],
    ]
    for operation_index, slot_key in enumerate(
        (
            f"mastery:{knowledge_point_id}",
            f"error_pattern:{knowledge_point_id}:unknown",
        ),
        start=1,
    ):
        candidates = [memory_id for memory_id in state_ids if slots.get(memory_id) == slot_key]
        case["gold_operations"].append(
            {
                "operation_id": f"{case_id}:padding_operation:{step_number}:{operation_index}",
                "step_id": step_id,
                "event_id": event_id,
                "extracted_fields": extracted,
                "canonical_knowledge_point_ids": [knowledge_point_id],
                "slot_key": slot_key,
                "candidate_memory_ids": candidates,
                "operation": "NO_OP",
                "target_memory_ids": candidates,
                "result_memory_id": None,
                "expected_result_value": None,
                "reason_code": "temporary_low_confidence_evidence_is_not_persisted",
                "evidence_event_ids": [event_id],
            }
        )
    case["events"].append(event)
    copied_state = deepcopy(last_state)
    copied_state["step_id"] = step_id
    case["gold_states"].append(copied_state)
    case["gold_actions"].append(
        {
            "step_id": step_id,
            "action_type": ActionType.NO_ACTION.value,
            "knowledge_point_ids": [],
            "reason_code": "temporary_low_confidence_evidence_requires_no_action",
        }
    )


def _formal_case(
    template: EvaluationCase,
    *,
    case_id: str,
    split: DatasetSplit,
    question_by_kp: dict[str, ControlledQuestion],
    target_knowledge_point_id: str,
) -> EvaluationCase:
    payload = _qualify_identifiers(
        template.model_dump(mode="json"),
        case_id,
    )
    payload["case_id"] = case_id
    payload["metadata"]["split"] = split.value
    payload["metadata"]["gold_revision"] = 3
    payload["metadata"]["policy_parameters"] = {
        "formal_dataset_version": DATASET_VERSION,
        "template_case_id": template.case_id,
        "padding_policy": "temporary_low_confidence_no_op",
        "target_knowledge_point_id": target_knowledge_point_id,
    }
    taxonomy = load_taxonomy("math1_v1")
    target_node = taxonomy.get(target_knowledge_point_id)
    if target_node is None:
        raise DatasetBuildError(f"unknown formal target: {target_knowledge_point_id}")
    payload = _remap_topic(
        payload,
        target_knowledge_point_id=target_knowledge_point_id,
        topic_name=target_node.name_zh,
    )

    for event_index, event in enumerate(payload["events"], start=1):
        event["session_id"] = f"{case_id}:session:{event_index}"
        if event["event_type"] == "answer_attempt":
            knowledge_point_id = event["knowledge_point_ids"][0]
            question = question_by_kp[knowledge_point_id]
            event["question_id"] = question.question_id
            event["difficulty"] = question.difficulty

    while len(payload["events"]) < 3:
        _append_low_quality_no_op(
            payload,
            question_by_kp=question_by_kp,
            step_number=len(payload["events"]) + 1,
        )
    return EvaluationCase.model_validate(payload)


def _answer_id_for_event(event: Any) -> str:
    return "correct" if event.answer_correct else "wrong"


def _benchmark_entry(case: EvaluationCase) -> BenchmarkEntry:
    knowledge_point_ids = sorted(
        {
            knowledge_point_id
            for operation in case.gold_operations
            for knowledge_point_id in operation.canonical_knowledge_point_ids
        }
    )
    taxonomy = load_taxonomy("math1_v1")
    names = [taxonomy.get(knowledge_point_id).name_zh for knowledge_point_id in knowledge_point_ids]
    topic_text = "、".join(names)
    profile_id = f"profile:{case.case_id}"
    return BenchmarkEntry(
        case_id=case.case_id,
        profile=LearnerProfile(
            profile_id=profile_id,
            background_zh="我正在准备数学一，已经完成基础概念学习，正在通过练习检查长期掌握情况。",
            learning_goal_zh=f"识别并修正我在{topic_text}上的稳定薄弱点。",
            known_well_zh=["能够阅读题目并写出基本计算步骤"],
            partial_knowledge_zh=names,
            beliefs_zh=["一次答错不一定代表稳定退步，多次一致证据才应改变长期记忆。"],
        ),
        task_title_zh=f"{topic_text}学习记忆轨迹",
        initial_message_zh=f"请结合我接下来关于{topic_text}的多次作答，判断哪些信息值得进入长期学习记忆。",
        target_knowledge_point_ids=knowledge_point_ids,
        success_criteria_zh=[
            "知识点和四维 Scope 不串扰",
            "低置信度临时异常不污染稳定记忆",
            "生命周期操作和最终状态与 Gold 一致",
        ],
        trajectory_family=f"{case.scenario_type.value}:{knowledge_point_ids[0]}",
        answer_by_event_id={
            event.event_id: _answer_id_for_event(event)
            for event in case.events
            if event.event_type.value == "answer_attempt"
        },
    )


def _aggregate_hash(records: Iterable[DatasetFileRecord]) -> str:
    lines = [f"{record.path}\0{record.sha256}\n" for record in records]
    return _sha256("".join(lines).encode("utf-8"))


def build_formal_dataset(output_root: Path = DATASET_ROOT) -> DatasetManifest:
    """Write the same 120 cases and hashes for every invocation."""
    questions = _question_bank()
    question_by_kp = {question.knowledge_point_id: question for question in questions}
    template_dir = DATASET_ROOT / DatasetSplit.PROTOCOL_CHECK.value
    templates = {
        scenario: [
            EvaluationCase.model_validate_json(path.read_text(encoding="utf-8"))
            for path in sorted(template_dir.glob(f"{scenario.value}_*.json"))
        ]
        for scenario in ScenarioType
    }
    if any(len(cases) != 2 for cases in templates.values()):
        raise DatasetBuildError("every scenario requires exactly two reviewed templates")

    records_by_split: dict[DatasetSplit, list[DatasetFileRecord]] = {
        DatasetSplit.DEV: [],
        DatasetSplit.TEST: [],
    }
    cases: list[EvaluationCase] = []
    rng = random.Random(PROTOCOL_SEED)
    four_dev_scenarios = set(list(ScenarioType)[:4])
    for scenario_index, scenario in enumerate(ScenarioType):
        variant_order = list(range(1, 11))
        rng.shuffle(variant_order)
        dev_count = 4 if scenario in four_dev_scenarios else 3
        dev_variants = set(variant_order[:dev_count])
        for variant in range(1, 11):
            split = DatasetSplit.DEV if variant in dev_variants else DatasetSplit.TEST
            case_id = f"formal:{scenario.value}:{variant:02d}"
            case = _formal_case(
                templates[scenario][(variant - 1) % 2],
                case_id=case_id,
                split=split,
                question_by_kp=question_by_kp,
                target_knowledge_point_id=_TOPIC_ORDER[
                    (scenario_index + variant - 1) % len(_TOPIC_ORDER)
                ],
            )
            cases.append(case)

    for split in (DatasetSplit.DEV, DatasetSplit.TEST):
        split_dir = output_root / split.value
        split_dir.mkdir(parents=True, exist_ok=True)
        split_cases = sorted(
            (case for case in cases if case.metadata.split is split),
            key=lambda case: case.case_id,
        )
        for case in split_cases:
            filename = case.case_id.replace(":", "_") + ".json"
            relative_path = f"{split.value}/{filename}"
            payload = _canonical_json_bytes(case.model_dump(mode="json"))
            path = output_root / relative_path
            path.write_bytes(payload)
            records_by_split[split].append(
                DatasetFileRecord(
                    path=relative_path,
                    split=split,
                    case_id=case.case_id,
                    scenario_type=case.scenario_type,
                    sha256=_sha256(payload),
                )
            )

    question_payload = _canonical_json_bytes(
        [question.model_dump(mode="json") for question in questions]
    )
    question_bank_path = output_root / QUESTION_BANK_PATH.name
    question_bank_path.write_bytes(question_payload)

    entries = sorted((_benchmark_entry(case) for case in cases), key=lambda item: item.case_id)
    entry_payload = b"".join(
        json.dumps(
            entry.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        + b"\n"
        for entry in entries
    )
    benchmark_entries_path = output_root / BENCHMARK_ENTRIES_PATH.name
    benchmark_entries_path.write_bytes(entry_payload)

    split_manifests = [
        SplitManifest(
            split=split,
            case_count=len(records_by_split[split]),
            aggregate_sha256=_aggregate_hash(records_by_split[split]),
            files=records_by_split[split],
        )
        for split in (DatasetSplit.DEV, DatasetSplit.TEST)
    ]
    manifest = DatasetManifest(
        dataset_version=DATASET_VERSION,
        protocol_version=PROTOCOL_VERSION,
        seed=PROTOCOL_SEED,
        generated_at=_GENERATED_AT,
        question_bank_sha256=_sha256(question_payload),
        benchmark_entries_sha256=_sha256(entry_payload),
        splits=split_manifests,
        frozen_test_sha256=split_manifests[1].aggregate_sha256,
        construction_notes=[
            "The 24 independently reviewed protocol-check cases are semantic templates only.",
            "Every formal case has isolated identifiers, at least three events, and at least two sessions.",
            "Each scenario uses ten distinct knowledge-point tasks; trajectory_family is the split-leakage key.",
            "Dev contains 40 cases and frozen test contains 80 cases using seed 20260806.",
            "TutorBench-inspired learner profiles are sidecars and do not alter deterministic Gold state.",
            "The frozen test split is hash-verifiable but must not be scored during Stage 08 development.",
        ],
    )
    manifest_path = output_root / MANIFEST_PATH.name
    manifest_path.write_bytes(_canonical_json_bytes(manifest.model_dump(mode="json")))
    return manifest


__all__ = [
    "BENCHMARK_ENTRIES_PATH",
    "DATASET_VERSION",
    "MANIFEST_PATH",
    "QUESTION_BANK_PATH",
    "DatasetBuildError",
    "build_formal_dataset",
]
