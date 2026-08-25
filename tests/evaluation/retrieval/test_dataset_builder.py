from __future__ import annotations

import json

from evaluation.retrieval.contracts import (
    RetrievalDataset,
    RetrievalDatasetManifest,
    RetrievalSplit,
)
from evaluation.retrieval.dataset_builder import (
    build_manifest,
    build_merged_dataset,
    build_scale_corpus,
    write_release,
)


def test_merged_release_meets_preregistered_sample_counts_and_is_deterministic() -> None:
    dev = build_merged_dataset(RetrievalSplit.DEV)
    test = build_merged_dataset(RetrievalSplit.TEST)

    assert (len(dev.corpus), len(dev.queries)) == (396, 170)
    assert (len(test.corpus), len(test.queries)) == (396, 310)
    assert sum(not query.expected_no_answer for query in dev.queries) == 140
    assert sum(query.expected_no_answer for query in dev.queries) == 30
    assert sum(not query.expected_no_answer for query in test.queries) == 250
    assert sum(query.expected_no_answer for query in test.queries) == 60
    assert dev.canonical_hash() == build_merged_dataset(RetrievalSplit.DEV).canonical_hash()
    assert test.canonical_hash() == build_merged_dataset(RetrievalSplit.TEST).canonical_hash()


def test_merge_preserves_independent_identity_and_has_no_split_family_leakage() -> None:
    dev = build_merged_dataset(RetrievalSplit.DEV)
    test = build_merged_dataset(RetrievalSplit.TEST)
    prefixes = {"mastery", "errors", "language"}

    for dataset in (dev, test):
        assert {record.memory.memory_id.partition("_")[0] for record in dataset.corpus} == prefixes
        assert {query.query_id.partition("_")[0] for query in dataset.queries} == prefixes
        assert [record.write_index for record in dataset.corpus] == list(range(len(dataset.corpus)))

    assert {query.query_family for query in dev.queries}.isdisjoint(
        query.query_family for query in test.queries
    )


def test_scale_corpus_is_separate_from_semantic_gold() -> None:
    test = build_merged_dataset(RetrievalSplit.TEST)
    scale = build_scale_corpus(10_000)
    gold_ids = {record.memory.memory_id for record in test.corpus}
    scale_ids = {record.memory.memory_id for record in scale}

    assert len(scale) == len(scale_ids) == 10_000
    assert gold_ids.isdisjoint(scale_ids)
    assert not any(
        memory_id in scale_ids
        for query in test.queries
        for memory_id in (
            query.relevant_memory_ids
            + query.hard_negative_memory_ids
            + query.must_not_return_memory_ids
        )
    )


def test_manifest_pins_data_and_metric_hashes() -> None:
    dev = build_merged_dataset(RetrievalSplit.DEV)
    test = build_merged_dataset(RetrievalSplit.TEST)
    manifest = build_manifest(dev, test)

    assert manifest.frozen_test_sha256 == test.canonical_hash()
    assert manifest.metric_catalog_sha256
    assert {record.dataset_sha256 for record in manifest.records} == {
        dev.canonical_hash(),
        test.canonical_hash(),
    }


def test_write_release_round_trips_frozen_contracts(tmp_path) -> None:
    release_directory = tmp_path / "semantic_retrieval_v2"
    manifest = write_release(release_directory)
    loaded_dev = RetrievalDataset.model_validate_json(
        (release_directory / "dev.json").read_text(encoding="utf-8")
    )
    loaded_test = RetrievalDataset.model_validate_json(
        (release_directory / "test.json").read_text(encoding="utf-8")
    )
    loaded_manifest = RetrievalDatasetManifest.model_validate_json(
        (release_directory / "manifest.json").read_text(encoding="utf-8")
    )

    assert loaded_dev.canonical_hash() == build_merged_dataset(RetrievalSplit.DEV).canonical_hash()
    assert loaded_test.canonical_hash() == manifest.frozen_test_sha256
    assert loaded_manifest == manifest
    assert json.loads((release_directory / "manifest.json").read_text(encoding="utf-8"))
