"""Typed slot-key grammar and taxonomy-aware construction helpers."""

from __future__ import annotations

import re
from typing import Annotated, TypeAlias

from pydantic import AfterValidator, StringConstraints, TypeAdapter, ValidationError

from exam_mem.contracts import ErrorType, MemoryNamespace

from .scope import MVP_EXAM_ID, MVP_SUBJECT_ID
from .taxonomy import (
    CanonicalKnowledgePointId,
    KnowledgePointStatus,
    Taxonomy,
)

_IDENTITY_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_CANONICAL_ID_ADAPTER = TypeAdapter(CanonicalKnowledgePointId)


def _validate_slot_key_syntax(value: str) -> str:
    parts = value.split(":")
    try:
        namespace = MemoryNamespace(parts[0])
    except (IndexError, ValueError) as exc:
        raise ValueError("slot_key must start with a supported memory namespace") from exc

    if namespace is MemoryNamespace.MASTERY:
        if len(parts) != 2:
            raise ValueError("mastery slot_key must be mastery:<canonical_knowledge_point_id>")
        _validate_canonical_id_syntax(parts[1])
    elif namespace is MemoryNamespace.ERROR_PATTERN:
        if len(parts) != 3:
            raise ValueError(
                "error_pattern slot_key must be "
                "error_pattern:<canonical_knowledge_point_id>:<error_type>"
            )
        _validate_canonical_id_syntax(parts[1])
        try:
            ErrorType(parts[2])
        except ValueError as exc:
            raise ValueError(f"unsupported error_type in slot_key: {parts[2]!r}") from exc
    elif namespace is MemoryNamespace.PLAN:
        if len(parts) != 3:
            raise ValueError("plan slot_key must be plan:<exam_id>:<subject_id>")
        _validate_identity_segment(parts[1], label="exam_id")
        _validate_identity_segment(parts[2], label="subject_id")
    else:
        if len(parts) != 2:
            raise ValueError(f"{namespace.value} slot_key must contain exactly one attribute")
        _validate_identity_segment(parts[1], label="attribute")
    return value


SlotKey: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    AfterValidator(_validate_slot_key_syntax),
]
_SLOT_KEY_ADAPTER = TypeAdapter(SlotKey)


def validate_slot_key(value: str) -> SlotKey:
    """Validate the stable string grammar without changing its JSON shape."""
    return _SLOT_KEY_ADAPTER.validate_python(value)


def build_mastery_slot_key(taxonomy: Taxonomy, knowledge_point_id: str) -> SlotKey:
    canonical_id = _require_active_leaf(taxonomy, knowledge_point_id)
    return validate_slot_key(f"{MemoryNamespace.MASTERY.value}:{canonical_id}")


def build_error_pattern_slot_key(
    taxonomy: Taxonomy,
    knowledge_point_id: str,
    error_type: ErrorType,
) -> SlotKey:
    canonical_id = _require_active_leaf(taxonomy, knowledge_point_id)
    try:
        resolved_error_type = ErrorType(error_type)
    except ValueError as exc:
        legal_values = ", ".join(item.value for item in ErrorType)
        raise ValueError(f"unsupported error_type; expected one of: {legal_values}") from exc
    return validate_slot_key(
        f"{MemoryNamespace.ERROR_PATTERN.value}:{canonical_id}:{resolved_error_type.value}"
    )


def build_plan_slot_key(exam_id: str, subject_id: str) -> SlotKey:
    if exam_id != MVP_EXAM_ID or subject_id != MVP_SUBJECT_ID:
        raise ValueError(
            "stage-four MVP plan slots require "
            f"exam_id={MVP_EXAM_ID!r} and subject_id={MVP_SUBJECT_ID!r}"
        )
    return validate_slot_key(f"{MemoryNamespace.PLAN.value}:{exam_id}:{subject_id}")


def build_profile_slot_key(attribute: str) -> SlotKey:
    return validate_slot_key(f"{MemoryNamespace.PROFILE.value}:{attribute}")


def build_preference_slot_key(attribute: str) -> SlotKey:
    return validate_slot_key(f"{MemoryNamespace.PREFERENCE.value}:{attribute}")


def _validate_canonical_id_syntax(value: str) -> None:
    try:
        _CANONICAL_ID_ADAPTER.validate_python(value)
    except ValidationError as exc:
        raise ValueError(f"invalid canonical knowledge point id: {value!r}") from exc


def _validate_identity_segment(value: str, *, label: str) -> None:
    if not _IDENTITY_SEGMENT_RE.fullmatch(value):
        raise ValueError(f"slot_key {label} must be a lowercase snake_case identifier")


def _require_active_leaf(taxonomy: Taxonomy, knowledge_point_id: str) -> str:
    node = taxonomy.get(knowledge_point_id)
    if node is None:
        raise ValueError(f"unknown canonical knowledge point id: {knowledge_point_id!r}")
    if node.status is not KnowledgePointStatus.ACTIVE:
        raise ValueError(f"canonical knowledge point is not active: {knowledge_point_id!r}")
    if taxonomy.children_of(node.id):
        raise ValueError(f"knowledge point must be a diagnostic leaf: {knowledge_point_id!r}")
    return node.id


__all__ = [
    "MVP_EXAM_ID",
    "MVP_SUBJECT_ID",
    "SlotKey",
    "build_error_pattern_slot_key",
    "build_mastery_slot_key",
    "build_plan_slot_key",
    "build_preference_slot_key",
    "build_profile_slot_key",
    "validate_slot_key",
]
