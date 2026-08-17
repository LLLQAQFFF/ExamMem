from __future__ import annotations

import pytest

from evaluation.evaluators.slot import compute_slot_metrics, evaluate_slot


@pytest.mark.protocol
@pytest.mark.slot_key
def test_slot_evaluator_reports_protocol_check_as_non_formal() -> None:
    result = evaluate_slot(split="protocol_check", taxonomy_version="math1_v1")

    assert result["case_count"] == 24
    assert result["sample_count"] == 39
    assert result["gold_revision"] == 2
    assert result["true_positive"] == 39
    assert result["false_positive"] == 0
    assert result["false_negative"] == 0
    assert result["precision"] == 1.0
    assert result["recall"] == 1.0
    assert result["f1"] == 1.0
    assert result["unknown_count"] == 0
    assert result["thresholds_calibrated"] is False
    assert result["formal_score"] is False
    assert result["report_type"] == "calibration_report"


@pytest.mark.protocol
@pytest.mark.slot_key
def test_slot_metric_counts_wrong_predictions_as_false_positive_and_negative() -> None:
    metrics = compute_slot_metrics(
        ["slot:a", "slot:b", "slot:c"],
        ["slot:a", "slot:wrong", None],
    )

    assert metrics["true_positive"] == 1
    assert metrics["false_positive"] == 1
    assert metrics["false_negative"] == 2
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == pytest.approx(1 / 3)
    assert metrics["f1"] == pytest.approx(0.4)


@pytest.mark.protocol
@pytest.mark.slot_key
def test_stage_four_slot_evaluator_rejects_future_splits() -> None:
    with pytest.raises(ValueError, match="only supports protocol_check"):
        evaluate_slot(split="dev", taxonomy_version="math1_v1")
