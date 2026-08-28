from __future__ import annotations

from pathlib import Path

import pytest

from evaluation.data_builder import DATASET_VERSION, build_formal_dataset
from evaluation.data_builder_v2 import (
    DATASET_VERSION as INVALID_CROSS_SUBJECT_DATASET_VERSION,
)
from evaluation.data_builder_v2 import (
    build_cross_subject_dataset as build_invalid_cross_subject_dataset,
)
from evaluation.data_builder_v3 import (
    DATASET_VERSION as CROSS_SUBJECT_DATASET_VERSION,
)
from evaluation.data_builder_v3 import (
    TAXONOMY_VERSION as CROSS_SUBJECT_TAXONOMY_VERSION,
)
from evaluation.data_builder_v3 import (
    build_cross_subject_dataset,
)
from evaluation.protocols.validation import (
    DATASET_ROOT,
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


def test_v1_builder_still_matches_the_frozen_repository_bytes(tmp_path: Path) -> None:
    manifest = build_formal_dataset(tmp_path)
    paths = [
        f"{DATASET_VERSION}.manifest.json",
        f"{DATASET_VERSION}.questions.json",
        f"{DATASET_VERSION}.benchmark.jsonl",
        *(record.path for split in manifest.splits for record in split.files),
    ]

    assert all(
        (tmp_path / path).read_bytes() == (DATASET_ROOT / path).read_bytes() for path in paths
    )


def test_formal_dataset_rejects_a_tampered_case(tmp_path: Path) -> None:
    manifest = build_formal_dataset(tmp_path)
    dev_record = manifest.splits[0].files[0]
    case_path = tmp_path / dev_record.path
    case_path.write_bytes(case_path.read_bytes() + b"\n")

    with pytest.raises(ArtifactValidationError, match="case hash mismatch"):
        validate_formal_dataset(DATASET_VERSION, dataset_root=tmp_path)


def test_cross_subject_dataset_is_valid_isolated_and_reproducible(tmp_path: Path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"

    manifest = build_cross_subject_dataset(first)
    build_cross_subject_dataset(second)
    summary = validate_formal_dataset(CROSS_SUBJECT_DATASET_VERSION, dataset_root=first)

    assert manifest.taxonomy_version == CROSS_SUBJECT_TAXONOMY_VERSION
    assert summary["case_count"] == 120
    assert summary["question_count"] == 12
    assert summary["splits"]["dev"]["case_count"] == 40
    assert summary["splits"]["test"]["case_count"] == 80
    assert all(
        record.path.startswith(f"{CROSS_SUBJECT_DATASET_VERSION}/")
        for split in manifest.splits
        for record in split.files
    )
    assert _tree_bytes(first) == _tree_bytes(second)

    forbidden = (
        "math1",
        "math_1",
        "postgraduate_entrance_exam",
        "数学一",
        "线性代数",
        "矩阵",
        "行列式",
        "特征值",
        "特征向量",
        "向量空间",
        "二次型",
        "条件概率",
        "全概率",
        "贝叶斯",
        "分布函数",
        "先验概率",
        "后验概率",
        "P(A",
        "F(x)",
    )
    generated_text = "\n".join(
        path.read_text(encoding="utf-8") for path in first.rglob("*") if path.is_file()
    )
    assert not any(term in generated_text for term in forbidden)


def test_invalid_v2_is_retained_as_a_reproducible_audit_record(tmp_path: Path) -> None:
    manifest = build_invalid_cross_subject_dataset(tmp_path)

    assert manifest.dataset_version == INVALID_CROSS_SUBJECT_DATASET_VERSION
    assert (tmp_path / f"{INVALID_CROSS_SUBJECT_DATASET_VERSION}.manifest.json").is_file()
