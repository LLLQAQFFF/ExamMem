from __future__ import annotations

from collections import Counter

import pytest

from evaluation.retrieval.contracts import canonical_embedding_text
from evaluation.retrieval.shards.language_safety import (
    build_dev,
    build_scale_corpus,
    build_test,
)
from exam_mem.contracts import LifecycleState


@pytest.mark.parametrize(
    ("builder", "answerable_count", "no_answer_count"),
    [(build_dev, 40, 10), (build_test, 80, 20)],
)
def test_language_split_is_dense_and_covers_language_and_safety_cases(
    builder,
    answerable_count: int,
    no_answer_count: int,
) -> None:
    dataset = builder()
    memories = {record.memory.memory_id: record.memory for record in dataset.corpus}
    answerable = [query for query in dataset.queries if not query.expected_no_answer]
    no_answer = [query for query in dataset.queries if query.expected_no_answer]

    assert len(answerable) == answerable_count
    assert len(no_answer) == no_answer_count
    assert len(dataset.corpus) >= 50
    assert len({query.query_family for query in answerable}) == 4
    assert {query.query_family.rpartition("_")[2] for query in no_answer} == {
        "answer",
        "reference",
    }

    for query in dataset.queries:
        candidates = [
            memory
            for memory in memories.values()
            if memory.scope == query.scope
            and memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        ]
        forbidden = [memories[memory_id] for memory_id in query.must_not_return_memory_ids]
        assert len(candidates) >= 50
        assert len(query.hard_negative_memory_ids) >= 10
        assert any(
            memory.scope == query.scope
            and memory.lifecycle_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
            for memory in forbidden
        )
        assert any(memory.scope.user_id != query.scope.user_id for memory in forbidden)
        assert any(memory.scope.exam_id != query.scope.exam_id for memory in forbidden)
        assert any(
            memory.scope.memory_namespace != query.scope.memory_namespace for memory in forbidden
        )

    assert all(
        record.embedding_text == canonical_embedding_text(record.memory)
        for record in dataset.corpus
    )
    broad_queries = [
        query
        for query in answerable
        if query.query_family.rpartition("_")[2] in {"colloquial", "name", "followup", "lookup"}
    ]
    assert broad_queries
    for query in broad_queries:
        relevant = [memories[memory_id] for memory_id in query.relevant_memory_ids]
        assert len(relevant) == 3
        assert len({memory.slot_key.split(":")[1] for memory in relevant}) == 1
        assert set(query.relevant_memory_ids).isdisjoint(query.hard_negative_memory_ids)

    for query in answerable:
        for memory_id in query.relevant_memory_ids:
            assert memories[memory_id].value.summary not in query.text

    assert all(
        record.memory.lifecycle_state is not LifecycleState.CONTESTED for record in dataset.corpus
    )
    assert all(
        value.startswith("language_")
        for record in dataset.corpus
        for value in (
            record.memory.memory_id,
            record.memory.scope.user_id,
            record.memory.scope.exam_id,
            record.memory.scope.subject_id,
        )
    )


def test_dev_and_test_are_deterministic_and_query_families_are_disjoint() -> None:
    first_dev = build_dev()
    second_dev = build_dev()
    test = build_test()

    assert first_dev.canonical_hash() == second_dev.canonical_hash()
    assert {query.query_family for query in first_dev.queries}.isdisjoint(
        query.query_family for query in test.queries
    )
    assert {query.query_id for query in first_dev.queries}.isdisjoint(
        query.query_id for query in test.queries
    )


def test_scale_corpus_is_deterministic_non_gold_and_respects_active_slot_uniqueness() -> None:
    corpus = build_scale_corpus(10_000)
    repeated_prefix = build_scale_corpus(25)

    assert len(corpus) == 10_000
    assert [record.model_dump(mode="json") for record in corpus[:25]] == [
        record.model_dump(mode="json") for record in repeated_prefix
    ]
    assert all(record.memory.memory_id.startswith("hnsw_scale_memory_") for record in corpus)
    assert all(record.memory.lifecycle_state is LifecycleState.ACTIVE for record in corpus)
    assert all("不参与相关性 Gold" in record.embedding_text for record in corpus)
    assert len({record.memory.scope for record in corpus}) == 1
    assert corpus[0].memory.scope.memory_namespace.value == "profile"

    active_slots = Counter(
        (
            record.memory.scope,
            record.memory.slot_key,
        )
        for record in corpus
    )
    assert max(active_slots.values()) == 1


@pytest.mark.parametrize("target_size", [0, -1])
def test_scale_corpus_rejects_non_positive_size(target_size: int) -> None:
    with pytest.raises(ValueError, match="greater than or equal to 1"):
        build_scale_corpus(target_size)
