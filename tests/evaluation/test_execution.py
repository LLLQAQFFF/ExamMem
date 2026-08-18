from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from evaluation.contracts.case import DatasetSplit
from evaluation.execution import _claim_frozen_test_release, execute_evaluation
from exam_mem.backends import BackendMode

pytestmark = pytest.mark.asyncio


async def test_partial_execution_writes_required_audit_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "evaluation.execution.resolve_llm_runtime_config",
        lambda: SimpleNamespace(provider_name="offline", model="none"),
    )
    monkeypatch.setattr("evaluation.execution._assert_evaluation_sources_clean", lambda: None)

    manifest = await execute_evaluation(
        experiment_id="offline-none",
        split=DatasetSplit.PROTOCOL_CHECK,
        modes=[BackendMode.NONE],
        output_root=tmp_path,
        database_url=None,
        timeout_seconds=2,
    )

    output = tmp_path / "offline-none"
    assert manifest["complete_five_arm_report"] is False
    assert manifest["selected_case_count"] == 24
    assert (output / "manifest.json").is_file()
    assert (output / "config.json").is_file()
    assert len((output / "cases.jsonl").read_text().splitlines()) == 24
    assert (output / "traces.jsonl").is_file()
    assert (output / "snapshots").is_dir()
    metrics = json.loads((output / "metrics.json").read_text())
    assert len(metrics["none"]) == 25
    assert (output / "metrics.csv").is_file()
    assert (output / "confusion_matrix.json").is_file()
    assert (output / "bad_cases.jsonl").is_file()
    assert "Partial" in (output / "report.md").read_text()


async def test_execution_refuses_frozen_test_rollout(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="schema/hash verified"):
        await execute_evaluation(
            experiment_id="forbidden-test",
            split=DatasetSplit.TEST,
            modes=[BackendMode.NONE],
            output_root=tmp_path,
            database_url=None,
        )


async def test_frozen_test_release_requires_complete_unfiltered_five_arm_run(
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="all five backends"):
        await execute_evaluation(
            experiment_id="incomplete-test-release",
            split=DatasetSplit.TEST,
            modes=[BackendMode.NONE],
            output_root=tmp_path,
            database_url=None,
            allow_frozen_test=True,
        )


def test_frozen_test_release_is_single_run_and_resume_only(tmp_path: Path) -> None:
    release = {
        "dataset_hash": "frozen-hash",
        "experiment_id": "stage09-test-final",
        "code_sha": "code-sha",
        "embedding_mode": "configured",
    }

    _claim_frozen_test_release(tmp_path, release, resume=False)
    _claim_frozen_test_release(tmp_path, release, resume=True)

    changed_run = {**release, "experiment_id": "another-test-run"}
    with pytest.raises(ValueError, match="already claimed"):
        _claim_frozen_test_release(tmp_path, changed_run, resume=False)
    with pytest.raises(ValueError, match="does not match"):
        _claim_frozen_test_release(tmp_path, changed_run, resume=True)


async def test_execution_rejects_unknown_case_filter(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown case IDs"):
        await execute_evaluation(
            experiment_id="unknown-filter",
            split=DatasetSplit.PROTOCOL_CHECK,
            modes=[BackendMode.NONE],
            output_root=tmp_path,
            database_url=None,
            case_ids=["does_not_exist"],
        )
