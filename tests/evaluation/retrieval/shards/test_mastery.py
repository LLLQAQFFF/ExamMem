from __future__ import annotations

from collections import Counter

from evaluation.retrieval.shards.mastery import build_dev, build_test
from exam_mem.contracts import LifecycleState, MasteryLevel
from exam_mem.domain import load_taxonomy


def _leaf_ids() -> set[str]:
    taxonomy = load_taxonomy("math1_v1")
    return {node.id for node in taxonomy.nodes if not taxonomy.children_of(node.id)}


def test_mastery_shards_are_deterministic_and_meet_query_quotas() -> None:
    dev = build_dev()
    test = build_test()

    assert dev.canonical_hash() == build_dev().canonical_hash()
    assert test.canonical_hash() == build_test().canonical_hash()
    assert Counter(query.expected_no_answer for query in dev.queries) == {
        False: 40,
        True: 10,
    }
    assert Counter(query.expected_no_answer for query in test.queries) == {
        False: 80,
        True: 20,
    }


def test_mastery_shards_cover_every_active_leaf_and_all_required_trajectories() -> None:
    for dataset in (build_dev(), build_test()):
        memories = {record.memory.memory_id: record.memory for record in dataset.corpus}
        answerable = [query for query in dataset.queries if not query.expected_no_answer]
        covered_slots = {
            memories[query.relevant_memory_ids[0]].slot_key.removeprefix("mastery:")
            for query in answerable
        }
        families = {query.query_family for query in answerable}
        relevant_levels = {
            memories[query.relevant_memory_ids[0]].value.level for query in answerable
        }

        assert covered_slots == _leaf_ids()
        assert {"mastered", "weak", "improving", "declining"} <= {
            trajectory
            for trajectory in ("mastered", "weak", "improving", "declining")
            if any(f"_{trajectory}_" in family for family in families)
        }
        assert {MasteryLevel.MASTERED, MasteryLevel.LOW, MasteryLevel.IMPROVING} <= relevant_levels


def test_mastery_queries_have_real_competition_and_version_safety_gold() -> None:
    for dataset in (build_dev(), build_test()):
        memories = {record.memory.memory_id: record.memory for record in dataset.corpus}
        current_by_scope = {
            memory.memory_id
            for memory in memories.values()
            if memory.scope == dataset.queries[0].scope
            and memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
        }
        assert len(current_by_scope) >= 50

        for query in dataset.queries:
            assert len(query.hard_negative_memory_ids) >= 10
            assert set(query.hard_negative_memory_ids) <= current_by_scope
            assert set(query.relevant_memory_ids) <= current_by_scope
            assert any(
                memories[memory_id].lifecycle_state is LifecycleState.ARCHIVED
                for memory_id in query.must_not_return_memory_ids
            )
            assert any(
                memories[memory_id].scope != query.scope
                for memory_id in query.must_not_return_memory_ids
            )


def test_mastery_contested_queries_never_reward_silent_adjudication() -> None:
    for dataset in (build_dev(), build_test()):
        memories = {record.memory.memory_id: record.memory for record in dataset.corpus}
        contested_group_by_id = {
            record.memory.memory_id: record.contested_group_id
            for record in dataset.corpus
            if record.contested_group_id is not None
        }
        conflict_exposure_queries = [
            query for query in dataset.queries if len(query.relevant_memory_ids) == 2
        ]
        assert conflict_exposure_queries
        for query in conflict_exposure_queries:
            groups = {contested_group_by_id[memory_id] for memory_id in query.relevant_memory_ids}
            assert len(groups) == 1
            assert "冲突" in query.text
            assert "不要" in query.text

        status_specific_contested = [
            query
            for query in dataset.queries
            if len(query.relevant_memory_ids) == 1
            and memories[query.relevant_memory_ids[0]].lifecycle_state is LifecycleState.CONTESTED
        ]
        assert status_specific_contested
        assert all(
            "冲突" in query.text and ("未解决" in query.text or "尚未裁决" in query.text)
            for query in status_specific_contested
        )


def test_mastery_ids_and_scopes_are_merge_safe() -> None:
    for dataset in (build_dev(), build_test()):
        assert dataset.policy_version.startswith("mastery_")
        assert all(record.memory.memory_id.startswith("mastery_") for record in dataset.corpus)
        assert all(record.memory.scope.user_id.startswith("mastery_") for record in dataset.corpus)
        assert all(query.query_id.startswith("mastery_") for query in dataset.queries)
        assert all(query.query_family.startswith("mastery_") for query in dataset.queries)
