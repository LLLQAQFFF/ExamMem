from __future__ import annotations

import json

import pytest

from evaluation.cli import main
from evaluation.protocols.validation import GoldReplayError, load_cases, replay_case


@pytest.mark.protocol
@pytest.mark.schema
def test_protocol_validate_cli_reports_frozen_dimensions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "protocol",
            "validate",
            "--version",
            "evaluation_protocol_v1",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["scenario_count"] == 12
    assert output["metric_count"] == 25
    assert output["backend_count"] == 5


@pytest.mark.protocol
@pytest.mark.schema
def test_dataset_validate_cli_reports_completed_independent_reviews(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["dataset", "validate", "--split", "protocol_check"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["case_count"] == 24
    assert len(output["scenario_counts"]) == 12
    assert set(output["scenario_counts"].values()) == {2}
    assert output["review_count"] == 12
    assert output["pending_review_count"] == 0
    assert output["pending_blind_review_count"] == 0
    assert output["completed_independent_human_review_count"] == 12
    assert output["independent_review_agreed_case_count"] == 24


@pytest.mark.protocol
@pytest.mark.schema
def test_dataset_verify_cli_keeps_frozen_test_content_hidden(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "dataset",
            "verify",
            "--dataset-version",
            "exam_mem_controlled_v1",
            "--no-content-output",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["case_count"] == 120
    assert output["splits"]["dev"]["case_count"] == 40
    assert output["splits"]["test"]["case_count"] == 80
    assert output["splits"]["test"]["case_content_disclosed"] is False
    assert output["test_gold_replayed_step_count"] == 0


@pytest.mark.protocol
@pytest.mark.schema
def test_gold_replay_cli_reconstructs_every_declared_state(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(["gold", "replay", "--split", "protocol_check"])
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["case_count"] == 24
    assert output["step_count"] >= 24


@pytest.mark.protocol
@pytest.mark.slot_key
def test_evaluate_slot_cli_reports_non_formal_protocol_metrics(
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code = main(
        [
            "evaluate-slot",
            "--split",
            "protocol_check",
            "--taxonomy",
            "math1_v1",
        ]
    )
    output = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert output["status"] == "ok"
    assert output["command"] == "evaluate-slot"
    assert output["sample_count"] == 39
    assert output["f1"] == 1.0
    assert output["thresholds_calibrated"] is False
    assert output["formal_score"] is False


@pytest.mark.protocol
@pytest.mark.schema
def test_gold_replay_detects_a_tampered_state() -> None:
    case = load_cases("protocol_check")[0].model_copy(deep=True)
    case.gold_states[0].active_memory_ids.append("impossible_extra_memory")

    with pytest.raises(GoldReplayError, match="state mismatch"):
        replay_case(case)
