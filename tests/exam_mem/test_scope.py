from __future__ import annotations

from itertools import product

from pydantic import ValidationError
import pytest

from exam_mem.contracts import MemoryNamespace
from exam_mem.domain import (
    build_memory_scope,
    build_mvp_memory_scope,
    build_scope_query_parameters,
    scope_fingerprint,
)


@pytest.mark.scope
def test_mvp_scope_uses_fixed_exam_and_subject_identity() -> None:
    scope = build_mvp_memory_scope(
        user_id="user_001",
        memory_namespace=MemoryNamespace.MASTERY,
    )

    assert scope.model_dump(mode="json") == {
        "user_id": "user_001",
        "exam_id": "postgraduate_entrance_exam",
        "subject_id": "math_1",
        "memory_namespace": "mastery",
    }


@pytest.mark.scope
def test_memory_scope_is_an_immutable_value_object() -> None:
    scope = build_mvp_memory_scope(
        user_id="user_001",
        memory_namespace=MemoryNamespace.MASTERY,
    )

    with pytest.raises(ValidationError, match="frozen"):
        scope.user_id = "user_002"


@pytest.mark.scope
def test_scope_query_parameters_always_include_all_four_dimensions() -> None:
    scope = build_mvp_memory_scope(
        user_id="user_001",
        memory_namespace=MemoryNamespace.ERROR_PATTERN,
    )

    parameters = build_scope_query_parameters(scope)

    assert parameters == (
        ("user_id", "user_001"),
        ("exam_id", "postgraduate_entrance_exam"),
        ("subject_id", "math_1"),
        ("memory_namespace", "error_pattern"),
    )
    assert "session_id" not in dict(parameters)


@pytest.mark.scope
@pytest.mark.parametrize("field", ["user_id", "exam_id", "subject_id"])
def test_scope_rejects_blank_identity_dimensions(field: str) -> None:
    payload = {
        "user_id": "user_001",
        "exam_id": "postgraduate_entrance_exam",
        "subject_id": "math_1",
        "memory_namespace": MemoryNamespace.MASTERY,
    }
    payload[field] = "   "

    with pytest.raises(ValidationError):
        build_memory_scope(**payload)


@pytest.mark.scope
def test_cartesian_scope_combinations_remain_distinct() -> None:
    scopes = [
        build_memory_scope(
            user_id=user_id,
            exam_id=exam_id,
            subject_id=subject_id,
            memory_namespace=namespace,
        )
        for user_id, exam_id, subject_id, namespace in product(
            ("user_001", "user_002"),
            ("postgraduate_entrance_exam", "mock_exam"),
            ("math_1", "math_2"),
            (MemoryNamespace.MASTERY, MemoryNamespace.ERROR_PATTERN),
        )
    ]

    query_parameters = {build_scope_query_parameters(scope) for scope in scopes}
    fingerprints = {scope_fingerprint(scope) for scope in scopes}

    assert len(scopes) == 16
    assert len(query_parameters) == 16
    assert len(fingerprints) == 16


@pytest.mark.scope
def test_scope_fingerprint_is_stable_and_does_not_expose_raw_identity() -> None:
    scope = build_mvp_memory_scope(
        user_id="private_user_001",
        memory_namespace=MemoryNamespace.PREFERENCE,
    )

    first = scope_fingerprint(scope)
    second = scope_fingerprint(scope)

    assert first == second
    assert len(first) == 64
    assert "private_user_001" not in first
    assert "postgraduate_entrance_exam" not in first
