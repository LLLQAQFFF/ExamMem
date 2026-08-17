"""Protocol-check evaluator for canonical knowledge-point slot keys."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from evaluation.contracts.case import (
    PROTOCOL_VERSION,
    DatasetSplit,
    GoldOperation,
)
from evaluation.protocols.validation import load_cases, validate_dataset
from exam_mem.contracts import ErrorType, MemoryNamespace
from exam_mem.domain import (
    MVP_EXAM_ID,
    MVP_SUBJECT_ID,
    UNKNOWN_KNOWLEDGE_POINT_ID,
    RuleBasedKnowledgePointNormalizer,
    Taxonomy,
    build_error_pattern_slot_key,
    build_mastery_slot_key,
    build_plan_slot_key,
    load_normalization_policy,
    load_taxonomy,
)


def compute_slot_metrics(
    gold_slot_keys: Sequence[str],
    predicted_slot_keys: Sequence[str | None],
) -> dict[str, float | int]:
    """Compute exact-match slot metrics, counting a wrong key as FP and FN."""
    if len(gold_slot_keys) != len(predicted_slot_keys):
        raise ValueError("gold and predicted slot collections must have equal lengths")
    if not gold_slot_keys:
        raise ValueError("slot evaluation requires at least one sample")

    true_positive = sum(
        predicted == gold
        for gold, predicted in zip(gold_slot_keys, predicted_slot_keys, strict=True)
    )
    false_positive = sum(
        predicted is not None and predicted != gold
        for gold, predicted in zip(gold_slot_keys, predicted_slot_keys, strict=True)
    )
    false_negative = len(gold_slot_keys) - true_positive
    precision_denominator = true_positive + false_positive
    precision = true_positive / precision_denominator if precision_denominator else 0.0
    recall = true_positive / (true_positive + false_negative)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": precision,
        "recall": recall,
        "f1": f1,
    }


def evaluate_slot(
    *,
    split: str,
    taxonomy_version: str,
) -> dict[str, Any]:
    """Evaluate stage-four rule normalization against existing protocol Gold."""
    split_value = DatasetSplit(split)
    if split_value is not DatasetSplit.PROTOCOL_CHECK:
        raise ValueError("stage-four slot evaluation only supports protocol_check")

    validate_dataset(split_value, protocol_version=PROTOCOL_VERSION)
    cases = load_cases(split_value)
    taxonomy = load_taxonomy(taxonomy_version)
    policy = load_normalization_policy("slot_normalizer_v1")
    normalizer = RuleBasedKnowledgePointNormalizer(taxonomy)

    gold_slot_keys: list[str] = []
    predicted_slot_keys: list[str | None] = []
    for case in cases:
        for operation in case.gold_operations:
            gold_slot_keys.append(operation.slot_key)
            predicted_slot_keys.append(
                predict_slot_key(
                    operation=operation,
                    taxonomy=taxonomy,
                    normalizer=normalizer,
                )
            )

    gold_revisions = {case.metadata.gold_revision for case in cases}
    if len(gold_revisions) != 1:
        raise ValueError("slot evaluation requires one Gold revision per split")

    metrics = compute_slot_metrics(gold_slot_keys, predicted_slot_keys)
    return {
        "split": split_value.value,
        "taxonomy_version": taxonomy.taxonomy_version,
        "normalization_policy": policy.normalization_policy,
        "gold_revision": gold_revisions.pop(),
        "case_count": len(cases),
        "sample_count": len(gold_slot_keys),
        "unknown_count": sum(slot_key is None for slot_key in predicted_slot_keys),
        "thresholds_calibrated": policy.is_calibrated,
        "formal_score": False,
        "report_type": "calibration_report",
        "warnings": [
            "protocol_check verifies contracts only; it is not the formal dev/test score",
            "embedding thresholds remain uncalibrated and were not used",
        ],
        **metrics,
    }


def predict_slot_key(
    *,
    operation: GoldOperation,
    taxonomy: Taxonomy,
    normalizer: RuleBasedKnowledgePointNormalizer,
) -> str | None:
    extracted_ids = operation.extracted_fields.knowledge_point_ids
    if len(extracted_ids) != 1:
        raise ValueError("protocol_check slot operations must contain exactly one extracted ID")

    normalized = normalizer.normalize(
        extracted_ids[0],
        operation.extracted_fields.evidence_quality.confidence,
    )
    if normalized.knowledge_point_id == UNKNOWN_KNOWLEDGE_POINT_ID:
        return None

    namespace = MemoryNamespace(operation.slot_key.partition(":")[0])
    if namespace is MemoryNamespace.MASTERY:
        return build_mastery_slot_key(taxonomy, normalized.knowledge_point_id)
    if namespace is MemoryNamespace.ERROR_PATTERN:
        error_type = operation.extracted_fields.error_type
        if error_type is None:
            error_type = ErrorType(operation.slot_key.rpartition(":")[2])
        return build_error_pattern_slot_key(
            taxonomy,
            normalized.knowledge_point_id,
            error_type,
        )
    if namespace is MemoryNamespace.PLAN:
        return build_plan_slot_key(MVP_EXAM_ID, MVP_SUBJECT_ID)
    raise ValueError(f"unsupported protocol_check slot namespace: {namespace.value}")


__all__ = ["compute_slot_metrics", "evaluate_slot", "predict_slot_key"]
