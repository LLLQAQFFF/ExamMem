from __future__ import annotations

from itertools import product

from pydantic import ValidationError
import pytest

from exam_mem.contracts import ErrorType, LifecycleState, MemoryNamespace
from exam_mem.domain import (
    CANDIDATE_LIFECYCLE_STATES,
    CandidateMatchReason,
    build_candidate_query,
    build_candidate_query_predicates,
    build_error_pattern_slot_key,
    build_mastery_slot_key,
    build_memory_scope,
    load_taxonomy,
)


@pytest.mark.scope
@pytest.mark.slot_key
def test_candidate_query_builds_filters_in_the_required_order() -> None:
    taxonomy = load_taxonomy("math1_v1")
    scope = build_memory_scope(
        user_id="user_1",
        exam_id="postgraduate_entrance_exam",
        subject_id="math_1",
        memory_namespace=MemoryNamespace.MASTERY,
    )
    slot_key = build_mastery_slot_key(
        taxonomy,
        "math1.probability.conditional_probability",
    )

    query = build_candidate_query(
        scope=scope,
        slot_key=slot_key,
        match_reason=CandidateMatchReason.ALIAS_NORMALIZED,
        current_memory_id="memory_current",
    )

    assert build_candidate_query_predicates(query) == (
        ("user_id", "=", "user_1"),
        ("exam_id", "=", "postgraduate_entrance_exam"),
        ("subject_id", "=", "math_1"),
        ("memory_namespace", "=", "mastery"),
        ("lifecycle_state", "IN", ("active", "contested")),
        (
            "slot_key",
            "=",
            "mastery:math1.probability.conditional_probability",
        ),
        ("memory_id", "!=", "memory_current"),
    )
    assert query.match_reason is CandidateMatchReason.ALIAS_NORMALIZED


@pytest.mark.scope
def test_candidate_query_never_includes_archived_records() -> None:
    assert CANDIDATE_LIFECYCLE_STATES == (
        LifecycleState.ACTIVE,
        LifecycleState.CONTESTED,
    )
    assert LifecycleState.ARCHIVED not in CANDIDATE_LIFECYCLE_STATES


@pytest.mark.scope
@pytest.mark.slot_key
def test_candidate_query_rejects_scope_and_slot_namespace_mismatch() -> None:
    taxonomy = load_taxonomy("math1_v1")
    error_scope = build_memory_scope(
        user_id="user_1",
        exam_id="postgraduate_entrance_exam",
        subject_id="math_1",
        memory_namespace=MemoryNamespace.ERROR_PATTERN,
    )
    mastery_slot = build_mastery_slot_key(
        taxonomy,
        "math1.linear_algebra.matrix_rank",
    )

    with pytest.raises(ValidationError, match="namespace must match"):
        build_candidate_query(
            scope=error_scope,
            slot_key=mastery_slot,
            match_reason=CandidateMatchReason.EXACT_SLOT,
        )


@pytest.mark.scope
@pytest.mark.slot_key
def test_candidate_queries_remain_distinct_across_the_full_scope() -> None:
    taxonomy = load_taxonomy("math1_v1")
    namespace_slots = {
        MemoryNamespace.MASTERY: build_mastery_slot_key(
            taxonomy,
            "math1.linear_algebra.matrix_rank",
        ),
        MemoryNamespace.ERROR_PATTERN: build_error_pattern_slot_key(
            taxonomy,
            "math1.linear_algebra.matrix_rank",
            ErrorType.CALCULATION_ERROR,
        ),
    }
    predicate_sets = set()

    for user_id, exam_id, subject_id, namespace in product(
        ("user_1", "user_2"),
        ("exam_1", "exam_2"),
        ("subject_1", "subject_2"),
        tuple(namespace_slots),
    ):
        scope = build_memory_scope(
            user_id=user_id,
            exam_id=exam_id,
            subject_id=subject_id,
            memory_namespace=namespace,
        )
        query = build_candidate_query(
            scope=scope,
            slot_key=namespace_slots[namespace],
            match_reason=CandidateMatchReason.EXACT_SLOT,
        )
        predicate_sets.add(build_candidate_query_predicates(query))

    assert len(predicate_sets) == 16


@pytest.mark.scope
def test_candidate_query_without_current_memory_omits_only_the_exclusion() -> None:
    taxonomy = load_taxonomy("math1_v1")
    scope = build_memory_scope(
        user_id="user_1",
        exam_id="postgraduate_entrance_exam",
        subject_id="math_1",
        memory_namespace=MemoryNamespace.MASTERY,
    )
    query = build_candidate_query(
        scope=scope,
        slot_key=build_mastery_slot_key(
            taxonomy,
            "math1.linear_algebra.matrix_rank",
        ),
        match_reason=CandidateMatchReason.EMBEDDING_REVIEWED,
    )

    predicates = build_candidate_query_predicates(query)

    assert [predicate[0] for predicate in predicates] == [
        "user_id",
        "exam_id",
        "subject_id",
        "memory_namespace",
        "lifecycle_state",
        "slot_key",
    ]
    assert all(predicate[0] != "memory_id" for predicate in predicates)
