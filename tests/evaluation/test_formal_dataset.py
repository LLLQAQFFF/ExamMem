from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.data_builder import DATASET_VERSION, build_formal_dataset
from evaluation.protocols.validation import (
    ArtifactValidationError,
    validate_formal_dataset,
)

pytestmark = [pytest.mark.protocol, pytest.mark.schema]


def _tree_bytes(root: Path) -> dict[str, bytes]:
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_formal_dataset_matches_frozen_counts_and_holdout_policy(tmp_path: Path) -> None:
    build_formal_dataset(tmp_path)

    summary = validate_formal_dataset(DATASET_VERSION, dataset_root=tmp_path)

    assert summary["case_count"] == 120
    assert summary["question_count"] == 12
    assert summary["benchmark_entry_count"] == 120
    assert summary["splits"]["dev"]["case_count"] == 40
    assert summary["splits"]["test"]["case_count"] == 80
    assert set(summary["splits"]["dev"]["scenario_counts"].values()) == {3, 4}
    assert set(summary["splits"]["test"]["scenario_counts"].values()) == {6, 7}
    assert summary["dev_gold_replayed_step_count"] == 120
    assert summary["test_gold_replayed_step_count"] == 0
    assert summary["splits"]["test"]["case_content_disclosed"] is False


def test_formal_dataset_generation_is_byte_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    build_formal_dataset(first)
    build_formal_dataset(second)

    assert _tree_bytes(first) == _tree_bytes(second)


def test_formal_dataset_rejects_a_tampered_case(tmp_path: Path) -> None:
    manifest = build_formal_dataset(tmp_path)
    dev_record = manifest.splits[0].files[0]
    case_path = tmp_path / dev_record.path
    case_path.write_bytes(case_path.read_bytes() + b"\n")

    with pytest.raises(ArtifactValidationError, match="case hash mismatch"):
        validate_formal_dataset(DATASET_VERSION, dataset_root=tmp_path)
