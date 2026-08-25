from __future__ import annotations

from collections import Counter

from evaluation.retrieval.contracts import RetrievalDataset, canonical_embedding_text
from evaluation.retrieval.shards.error_patterns import build_dev, build_test
from exam_mem.contracts import LifecycleState
from exam_mem.domain.taxonomy import load_taxonomy


def _slot_parts(slot_key: str) -> tuple[str, str]:
    _, knowledge_point_id, error_type = slot_key.split(":")
    return knowledge_point_id, error_type


def test_error_pattern_shards_have_dense_auditable_counts() -> None:
    dev = build_dev()
    test = build_test()

    assert len(dev.corpus) == len(test.corpus) == 240
    assert sum(not query.expected_no_answer for query in dev.queries) == 60
    assert sum(query.expected_no_answer for query in dev.queries) == 10
    assert sum(not query.expected_no_answer for query in test.queries) == 90
    assert sum(query.expected_no_answer for query in test.queries) == 20

    for dataset in (dev, test):
        scope = next(query.scope for query in dataset.queries)
        state_counts = Counter(
            record.memory.lifecycle_state
            for record in dataset.corpus
            if record.memory.scope == scope
        )
        assert state_counts[LifecycleState.ACTIVE] == 90
        assert state_counts[LifecycleState.CONTESTED] == 0
        assert state_counts[LifecycleState.ARCHIVED] == 30
        assert state_counts[LifecycleState.INVALIDATED] == 30
        assert all(query.top_k == 5 for query in dataset.queries)
        assert all(len(query.hard_negative_memory_ids) >= 10 for query in dataset.queries)
        assert all(
            record.contested_group_id is None
            and record.memory.lifecycle_state is not LifecycleState.CONTESTED
            for record in dataset.corpus
        )


def test_error_pattern_queries_cover_every_active_leaf_and_split_families() -> None:
    taxonomy = load_taxonomy("math1_v1")
    active_leaves = {
        node.id
        for node in taxonomy.nodes
        if node.status.value == "active" and not taxonomy.children_of(node.id)
    }
    dev = build_dev()
    test = build_test()

    def covered(dataset: RetrievalDataset) -> set[str]:
        memory_by_id = {record.memory.memory_id: record.memory for record in dataset.corpus}
        return {
            _slot_parts(memory_by_id[query.relevant_memory_ids[0]].slot_key)[0]
            for query in dataset.queries
            if not query.expected_no_answer
        }

    assert covered(dev) == active_leaves
    assert covered(test) == active_leaves
    assert {query.query_family for query in dev.queries}.isdisjoint(
        {query.query_family for query in test.queries}
    )


def test_answerable_gold_contains_two_kinds_of_hard_negative() -> None:
    for dataset in (build_dev(), build_test()):
        memory_by_id = {record.memory.memory_id: record.memory for record in dataset.corpus}
        for query in dataset.queries:
            if query.expected_no_answer:
                continue
            relevant = memory_by_id[query.relevant_memory_ids[0]]
            relevant_kp, relevant_error = _slot_parts(relevant.slot_key)
            hard_parts = [
                _slot_parts(memory_by_id[memory_id].slot_key)
                for memory_id in query.hard_negative_memory_ids
            ]
            assert any(kp == relevant_kp and error != relevant_error for kp, error in hard_parts)
            assert any(kp != relevant_kp and error == relevant_error for kp, error in hard_parts)
            assert query.text != relevant.value.summary


def test_must_not_gold_covers_terminal_and_cross_scope_near_duplicates() -> None:
    for dataset in (build_dev(), build_test()):
        memory_by_id = {record.memory.memory_id: record.memory for record in dataset.corpus}
        for query in dataset.queries:
            forbidden = [memory_by_id[memory_id] for memory_id in query.must_not_return_memory_ids]
            assert any(
                memory.scope == query.scope
                and memory.lifecycle_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
                for memory in forbidden
            )
            assert any(memory.scope != query.scope for memory in forbidden)


def test_error_pattern_shards_are_deterministic_and_pin_embedding_input() -> None:
    for builder in (build_dev, build_test):
        first = builder()
        second = builder()
        assert first.canonical_hash() == second.canonical_hash()
        assert all(
            record.embedding_text == canonical_embedding_text(record.memory)
            for record in first.corpus
        )
        assert all(record.memory.memory_id.startswith("errors_") for record in first.corpus)
        assert all(
            record.memory.scope.user_id.startswith("errors_")
            and record.memory.scope.exam_id.startswith("errors_")
            and record.memory.scope.subject_id.startswith("errors_")
            for record in first.corpus
        )
        assert all(
            event.event_id.startswith("errors_")
            and event.idempotency_key.startswith("errors_")
            and event.session_id.startswith("errors_")
            and event.question_id is not None
            and event.question_id.startswith("errors_")
            for record in first.corpus
            for event in record.provenance_events
        )
