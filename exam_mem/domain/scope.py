"""Four-dimensional Memory Scope construction and query-boundary helpers."""

from __future__ import annotations

import hashlib
import json
from typing import Literal, TypeAlias

from exam_mem.contracts import MemoryNamespace, MemoryScope

MVP_EXAM_ID = "postgraduate_entrance_exam"
MVP_SUBJECT_ID = "math_1"

ScopeFieldName: TypeAlias = Literal[
    "user_id",
    "exam_id",
    "subject_id",
    "memory_namespace",
]
ScopeQueryParameters: TypeAlias = tuple[tuple[ScopeFieldName, str], ...]


def build_memory_scope(
    *,
    user_id: str,
    exam_id: str,
    subject_id: str,
    memory_namespace: MemoryNamespace,
) -> MemoryScope:
    """Construct the complete immutable boundary required by L2 operations."""
    return MemoryScope(
        user_id=user_id,
        exam_id=exam_id,
        subject_id=subject_id,
        memory_namespace=memory_namespace,
    )


def build_mvp_memory_scope(
    *,
    user_id: str,
    memory_namespace: MemoryNamespace,
) -> MemoryScope:
    """Construct the fixed Math 1 scope used by the stage-four MVP."""
    return build_memory_scope(
        user_id=user_id,
        exam_id=MVP_EXAM_ID,
        subject_id=MVP_SUBJECT_ID,
        memory_namespace=memory_namespace,
    )


def build_scope_query_parameters(scope: MemoryScope) -> ScopeQueryParameters:
    """Return all four predicates in one immutable, repository-ready value."""
    return (
        ("user_id", scope.user_id),
        ("exam_id", scope.exam_id),
        ("subject_id", scope.subject_id),
        ("memory_namespace", scope.memory_namespace.value),
    )


def scope_fingerprint(scope: MemoryScope) -> str:
    """Return a deterministic trace-safe hash without exposing scope values."""
    payload = json.dumps(
        build_scope_query_parameters(scope),
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "MVP_EXAM_ID",
    "MVP_SUBJECT_ID",
    "ScopeFieldName",
    "ScopeQueryParameters",
    "build_memory_scope",
    "build_mvp_memory_scope",
    "build_scope_query_parameters",
    "scope_fingerprint",
]
