"""Narrow LLM adapter for Stage 06 memory-relation classification."""

from __future__ import annotations

from collections.abc import Sequence
import json
import logging
from typing import Protocol

from deeptutor.plugins.host_services import complete, extract_json_object
from exam_mem.contracts import MemoryNamespace, MemoryUpdateCandidate
from exam_mem.domain.slot_key import validate_slot_key
from exam_mem.lifecycle.contracts import (
    LifecycleCandidateSnapshot,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    resolve_relation_output,
)

_SYSTEM_PROMPT = """You are a constrained Learning Memory relation classifier.
Compare the new candidate with exactly one item from existing_candidates.
Return only one JSON object matching the supplied JSON Schema.
relation must be one of: duplicate, complementary, contradictory, unrelated.
Obey allowed_relations from the request; never return a relation outside that list.
candidate_display_number must be a displayed 1-based number from this request.
Never invent or request database IDs, row versions, provenance, or lifecycle operations.
Do not decide ADD, NO_OP, MERGE, SUPERSEDE, INVALIDATE, or CONTESTED.
Use null for optional fields when the candidate does not contain that concept.
"""
_MAX_CLASSIFICATION_ATTEMPTS = 2
logger = logging.getLogger(__name__)


class RelationClassificationError(RuntimeError):
    """Raised after bounded strict classification attempts are exhausted."""

    error_code = "relation_classifier_failed"


class RelationClassifier(Protocol):
    """Semantic classifier port consumed before deterministic policy evaluation."""

    async def classify(
        self,
        candidate: MemoryUpdateCandidate,
        candidate_snapshots: Sequence[LifecycleCandidateSnapshot],
    ) -> ResolvedRelationClassification: ...


class RelationCompletion(Protocol):
    """Subset of DeepTutor's non-streaming completion boundary used here."""

    async def __call__(
        self,
        *,
        prompt: str,
        system_prompt: str,
        response_format: dict[str, object],
        temperature: float,
    ) -> str: ...


class DeepTutorRelationClassifierAdapter:
    """Call DeepTutor's configured provider and strictly validate its signal."""

    def __init__(self, completion: RelationCompletion | None = None) -> None:
        self._completion = completion or complete

    async def classify(
        self,
        candidate: MemoryUpdateCandidate,
        candidate_snapshots: Sequence[LifecycleCandidateSnapshot],
    ) -> ResolvedRelationClassification:
        ordered = validate_relation_candidate_pool(candidate, candidate_snapshots)
        allowed_relations = _allowed_relations(candidate)
        output_schema = _relation_output_schema(allowed_relations)
        prompt = _build_user_prompt(
            candidate,
            ordered,
            allowed_relations=allowed_relations,
            output_schema=output_schema,
        )
        last_error: Exception | None = None
        for attempt in range(1, _MAX_CLASSIFICATION_ATTEMPTS + 1):
            try:
                raw_output = await self._completion(
                    prompt=prompt,
                    system_prompt=_SYSTEM_PROMPT,
                    response_format=_relation_response_format(output_schema),
                    temperature=0.0,
                )
                classification = RelationClassifierOutput.model_validate(
                    extract_json_object(raw_output)
                )
                if classification.relation not in allowed_relations:
                    raise ValueError("relation is outside the candidate slot contract")
                return resolve_relation_output(classification, ordered)
            except Exception as exc:
                last_error = exc
                logger.warning(
                    "Relation classification attempt %s/%s failed (%s)",
                    attempt,
                    _MAX_CLASSIFICATION_ATTEMPTS,
                    type(exc).__name__,
                )
        assert last_error is not None
        raise RelationClassificationError(
            "strict relation classification failed after bounded retries"
        ) from last_error


def validate_relation_candidate_pool(
    candidate: MemoryUpdateCandidate,
    candidate_snapshots: Sequence[LifecycleCandidateSnapshot],
) -> tuple[LifecycleCandidateSnapshot, ...]:
    """Validate and sort the only authoritative candidates the model may choose."""
    slot_key = str(validate_slot_key(candidate.slot_key))
    if slot_key.partition(":")[0] != candidate.scope.memory_namespace.value:
        raise ValueError("candidate slot_key namespace must match candidate scope")
    if not candidate_snapshots:
        raise ValueError("relation classifier requires at least one candidate snapshot")

    memory_ids: set[str] = set()
    for snapshot in candidate_snapshots:
        memory = snapshot.memory
        if memory.memory_id in memory_ids:
            raise ValueError("relation candidate memory IDs must be unique")
        memory_ids.add(memory.memory_id)
        if memory.scope != candidate.scope:
            raise ValueError("relation candidate snapshot must match candidate scope")
        if memory.slot_key != slot_key:
            raise ValueError("relation candidate snapshot must match candidate slot_key")

    return tuple(
        sorted(
            candidate_snapshots,
            key=lambda snapshot: (
                snapshot.memory.version,
                snapshot.memory.memory_id,
            ),
        )
    )


def resolve_validated_relation_output(
    candidate: MemoryUpdateCandidate,
    candidate_snapshots: Sequence[LifecycleCandidateSnapshot],
    classification: RelationClassifierOutput,
) -> ResolvedRelationClassification:
    """Resolve a fixture or adapter output through the same safe candidate pool."""
    ordered = validate_relation_candidate_pool(candidate, candidate_snapshots)
    return resolve_relation_output(classification, ordered)


def _build_user_prompt(
    candidate: MemoryUpdateCandidate,
    ordered: Sequence[LifecycleCandidateSnapshot],
    *,
    allowed_relations: tuple[MemoryRelation, ...],
    output_schema: dict[str, object],
) -> str:
    payload = {
        "output_json_schema": output_schema,
        "allowed_relations": [relation.value for relation in allowed_relations],
        "new_candidate": {
            "slot_key": candidate.slot_key,
            "proposed_value": candidate.proposed_value.model_dump(mode="json"),
        },
        "existing_candidates": [
            {
                "candidate_display_number": display_number,
                "value": snapshot.memory.value.model_dump(mode="json"),
                "confidence": snapshot.memory.confidence,
                "lifecycle_state": snapshot.memory.lifecycle_state.value,
            }
            for display_number, snapshot in enumerate(ordered, start=1)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _allowed_relations(candidate: MemoryUpdateCandidate) -> tuple[MemoryRelation, ...]:
    if candidate.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN:
        return (MemoryRelation.DUPLICATE, MemoryRelation.COMPLEMENTARY)
    return tuple(MemoryRelation)


def _relation_output_schema(
    allowed_relations: tuple[MemoryRelation, ...],
) -> dict[str, object]:
    schema = RelationClassifierOutput.model_json_schema()
    relation_definition = schema["$defs"]["MemoryRelation"]
    relation_definition["enum"] = [relation.value for relation in allowed_relations]
    return schema


def _relation_response_format(schema: dict[str, object]) -> dict[str, object]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "exam_mem_relation_classifier_output",
            "strict": True,
            "schema": schema,
        },
    }


__all__ = [
    "DeepTutorRelationClassifierAdapter",
    "RelationClassificationError",
    "RelationClassifier",
    "RelationCompletion",
    "resolve_validated_relation_output",
    "validate_relation_candidate_pool",
]
