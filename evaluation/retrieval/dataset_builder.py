"""Merge and freeze the independently authored semantic-retrieval-v2 shards."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
import json
from pathlib import Path

from evaluation.retrieval.contracts import (
    RETRIEVAL_EMBEDDING_DIMENSION,
    RETRIEVAL_PROTOCOL_VERSION,
    CorpusMemoryRecord,
    RetrievalDataset,
    RetrievalDatasetFileRecord,
    RetrievalDatasetManifest,
    RetrievalSplit,
    manifest_records_hash,
)
from evaluation.retrieval.metrics import metric_catalog_sha256
from evaluation.retrieval.shards import error_patterns, language_safety, mastery

DATASET_VERSION = "exam_mem_semantic_retrieval_v2"
MERGED_POLICY_VERSION = "semantic_retrieval_v2_merged"
RELEASE_SEED = 20_260_825
RELEASE_GENERATED_AT = datetime(2026, 8, 25, tzinfo=timezone.utc)
DEFAULT_RELEASE_DIRECTORY = Path("evaluation/datasets/semantic_retrieval_v2")

_BUILDERS: dict[RetrievalSplit, tuple[Callable[[], RetrievalDataset], ...]] = {
    RetrievalSplit.DEV: (
        mastery.build_dev,
        error_patterns.build_dev,
        language_safety.build_dev,
    ),
    RetrievalSplit.TEST: (
        mastery.build_test,
        error_patterns.build_test,
        language_safety.build_test,
    ),
}


def build_merged_dataset(split: RetrievalSplit) -> RetrievalDataset:
    """Combine shards without changing their corpus contents or relevance Gold."""
    shards = [builder() for builder in _BUILDERS[split]]
    corpus = [
        record.model_copy(update={"write_index": write_index})
        for write_index, record in enumerate(record for shard in shards for record in shard.corpus)
    ]
    queries = [query for shard in shards for query in shard.queries]
    return RetrievalDataset(
        protocol_version=RETRIEVAL_PROTOCOL_VERSION,
        dataset_version=DATASET_VERSION,
        split=split,
        seed=RELEASE_SEED,
        policy_version=MERGED_POLICY_VERSION,
        embedding_dimension=RETRIEVAL_EMBEDDING_DIMENSION,
        document_input_type="search_document",
        query_input_type="search_query",
        corpus=corpus,
        queries=queries,
    )


def build_scale_corpus(target_size: int = 10_000) -> list[CorpusMemoryRecord]:
    """Return non-Gold records used only for storage and HNSW scale measurements."""
    return language_safety.build_scale_corpus(target_size)


def build_manifest(
    dev: RetrievalDataset,
    test: RetrievalDataset,
    *,
    release_directory: Path = DEFAULT_RELEASE_DIRECTORY,
) -> RetrievalDatasetManifest:
    """Pin the two semantic datasets and the preregistered metric catalog."""
    records = [
        _file_record(dev, release_directory / "dev.json"),
        _file_record(test, release_directory / "test.json"),
    ]
    return RetrievalDatasetManifest(
        protocol_version=RETRIEVAL_PROTOCOL_VERSION,
        dataset_version=DATASET_VERSION,
        seed=RELEASE_SEED,
        generated_at=RELEASE_GENERATED_AT,
        records=records,
        aggregate_sha256=manifest_records_hash(records),
        frozen_test_sha256=test.canonical_hash(),
        metric_catalog_sha256=metric_catalog_sha256(),
        construction_notes=[
            "Gold data merges three independently authored shards without score-based tuning.",
            "The deterministic 10,000-record scale corpus is excluded from relevance Gold.",
            "Vectors are generated at evaluation time from canonical embedding_text.",
        ],
    )


def write_release(
    release_directory: Path = DEFAULT_RELEASE_DIRECTORY,
) -> RetrievalDatasetManifest:
    """Materialize canonical dev/test JSON and their hash manifest."""
    dev = build_merged_dataset(RetrievalSplit.DEV)
    test = build_merged_dataset(RetrievalSplit.TEST)
    manifest = build_manifest(dev, test, release_directory=release_directory)
    release_directory.mkdir(parents=True, exist_ok=True)
    _write_json(release_directory / "dev.json", dev.model_dump(mode="json"))
    _write_json(release_directory / "test.json", test.model_dump(mode="json"))
    _write_json(release_directory / "manifest.json", manifest.model_dump(mode="json"))
    return manifest


def _file_record(dataset: RetrievalDataset, path: Path) -> RetrievalDatasetFileRecord:
    return RetrievalDatasetFileRecord(
        path=path.as_posix(),
        split=dataset.split,
        dataset_sha256=dataset.canonical_hash(),
        corpus_count=len(dataset.corpus),
        query_count=len(dataset.queries),
        query_family_ids=sorted({query.query_family for query in dataset.queries}),
    )


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    written = write_release()
    print(written.model_dump_json(indent=2))


__all__ = [
    "DEFAULT_RELEASE_DIRECTORY",
    "build_manifest",
    "build_merged_dataset",
    "build_scale_corpus",
    "write_release",
]
