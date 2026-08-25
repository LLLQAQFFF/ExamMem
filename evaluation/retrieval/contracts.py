"""Data contracts for the independent ExamMem semantic-retrieval-v2 benchmark.

The corpus deliberately stores the production L1/L2 domain objects, not an
evaluation-only approximation. Embeddings are runtime-derived artifacts: the
dataset pins their canonical input text but never freezes provider-specific
floating-point vectors.
"""

from __future__ import annotations

from collections import defaultdict
from enum import Enum
import hashlib
import json
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from evaluation.contracts.rollout import Sha256Digest
from exam_mem.contracts import LearningEvent, LearningMemory, LifecycleState, MemoryScope
from exam_mem.domain.slot_key import validate_slot_key

RETRIEVAL_PROTOCOL_VERSION = "semantic_retrieval_v2"
RETRIEVAL_EMBEDDING_DIMENSION = 1024
MINIMUM_CANDIDATES_PER_QUERY = 50
MINIMUM_HARD_NEGATIVES_PER_QUERY = 10

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
DatasetVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, pattern=r"^exam_mem_semantic_retrieval_v[0-9]+$"),
]
RelevanceGrade = Annotated[int, Field(ge=1, le=3)]
ProvenanceRelation = Literal[
    "created_by",
    "merged_from",
    "contradicted_by",
    "invalidated_by",
]


class StrictRetrievalModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class RetrievalSplit(str, Enum):
    DEV = "dev"
    TEST = "test"


def canonical_sha256(payload: Any) -> str:
    """Hash a JSON-compatible payload using the benchmark's canonical encoding."""
    if isinstance(payload, BaseModel):
        payload = payload.model_dump(mode="json")
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_embedding_text(memory: LearningMemory) -> str:
    """Return the exact semantic payload embedded by the lifecycle backend."""
    return json.dumps(
        {
            "slot_key": memory.slot_key,
            "value": memory.value.model_dump(mode="json"),
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class CorpusMemoryRecord(StrictRetrievalModel):
    """One ordered L2 write and the complete L1 evidence it must reference."""

    write_index: Annotated[int, Field(ge=0)]
    memory: LearningMemory
    provenance_events: Annotated[list[LearningEvent], Field(min_length=1)]
    embedding_text: NonEmptyString
    contested_group_id: NonEmptyString | None = None
    provenance_relations: dict[NonEmptyString, ProvenanceRelation]

    @model_validator(mode="after")
    def validate_production_write_contract(self) -> CorpusMemoryRecord:
        event_ids = [event.event_id for event in self.provenance_events]
        if len(event_ids) != len(set(event_ids)):
            raise ValueError("provenance event IDs must be unique within a corpus record")
        if event_ids != self.memory.provenance:
            raise ValueError("memory.provenance must exactly match provenance event order")
        if set(self.provenance_relations) != set(event_ids):
            raise ValueError("provenance_relations keys must exactly match provenance event IDs")
        if self.memory.evidence_count != len(event_ids):
            raise ValueError("memory.evidence_count must equal provenance event count")
        if (
            self.memory.lifecycle_state is LifecycleState.CONTESTED
            and self.contested_group_id is None
        ):
            raise ValueError("a contested memory requires contested_group_id")

        expected_context = (
            self.memory.scope.user_id,
            self.memory.scope.exam_id,
            self.memory.scope.subject_id,
        )
        for event in self.provenance_events:
            actual_context = (
                event.context.user_id,
                event.context.exam_id,
                event.context.subject_id,
            )
            if actual_context != expected_context:
                raise ValueError("every provenance event must share the memory's L1 context")

        slot_key = str(validate_slot_key(self.memory.slot_key))
        if slot_key.partition(":")[0] != self.memory.scope.memory_namespace.value:
            raise ValueError("slot_key namespace must match memory scope")
        if self.embedding_text != canonical_embedding_text(self.memory):
            raise ValueError("embedding_text must equal canonical slot_key + value JSON")
        return self


class RetrievalQuery(StrictRetrievalModel):
    """One query with direct, query-level relevance judgments."""

    query_id: NonEmptyString
    query_family: NonEmptyString
    text: NonEmptyString
    scope: MemoryScope
    top_k: Annotated[int, Field(ge=1, le=100)]
    relevant_memory_ids: list[NonEmptyString] = Field(default_factory=list)
    relevance_grades: dict[NonEmptyString, RelevanceGrade] = Field(default_factory=dict)
    hard_negative_memory_ids: list[NonEmptyString] = Field(default_factory=list)
    must_not_return_memory_ids: list[NonEmptyString] = Field(default_factory=list)
    expected_no_answer: bool = False

    @model_validator(mode="after")
    def validate_gold(self) -> RetrievalQuery:
        relevant = self.relevant_memory_ids
        hard_negatives = self.hard_negative_memory_ids
        forbidden = self.must_not_return_memory_ids
        if len(relevant) != len(set(relevant)):
            raise ValueError("relevant_memory_ids must be unique")
        if len(forbidden) != len(set(forbidden)):
            raise ValueError("must_not_return_memory_ids must be unique")
        if len(hard_negatives) != len(set(hard_negatives)):
            raise ValueError("hard_negative_memory_ids must be unique")
        if set(self.relevance_grades) != set(relevant):
            raise ValueError("relevance_grades keys must exactly match relevant_memory_ids")
        if set(relevant) & set(forbidden):
            raise ValueError("relevant and must-not-return memory IDs must be disjoint")
        if set(relevant) & set(hard_negatives):
            raise ValueError("relevant and hard-negative memory IDs must be disjoint")
        if set(hard_negatives) & set(forbidden):
            raise ValueError("hard-negative and must-not-return memory IDs must be disjoint")
        if self.expected_no_answer and relevant:
            raise ValueError("an expected-no-answer query cannot declare relevant memories")
        if not self.expected_no_answer and not relevant:
            raise ValueError("an answerable query requires at least one relevant memory")
        return self


class RetrievalDataset(StrictRetrievalModel):
    """A self-contained corpus/query split suitable for isolated DB ingestion."""

    protocol_version: Literal[RETRIEVAL_PROTOCOL_VERSION]
    dataset_version: DatasetVersion
    split: RetrievalSplit
    seed: Annotated[int, Field(ge=0)]
    policy_version: NonEmptyString
    embedding_dimension: Literal[RETRIEVAL_EMBEDDING_DIMENSION]
    document_input_type: Literal["search_document"]
    query_input_type: Literal["search_query"]
    corpus: Annotated[list[CorpusMemoryRecord], Field(min_length=1)]
    queries: Annotated[list[RetrievalQuery], Field(min_length=1)]

    def canonical_hash(self) -> str:
        return canonical_sha256(self)

    @model_validator(mode="after")
    def validate_retrieval_dataset(self) -> RetrievalDataset:
        write_indexes = [record.write_index for record in self.corpus]
        if write_indexes != list(range(len(self.corpus))):
            raise ValueError("write_index values must be contiguous and ordered from zero")

        memories = {record.memory.memory_id: record.memory for record in self.corpus}
        if len(memories) != len(self.corpus):
            raise ValueError("corpus memory_id values must be unique")
        query_ids = [query.query_id for query in self.queries]
        if len(query_ids) != len(set(query_ids)):
            raise ValueError("query_id values must be unique")
        if not any(query.expected_no_answer for query in self.queries):
            raise ValueError("each split requires at least one expected-no-answer query")
        if not any(not query.expected_no_answer for query in self.queries):
            raise ValueError("each split requires at least one answerable query")

        event_payloads: dict[str, dict[str, Any]] = {}
        for record in self.corpus:
            for event in record.provenance_events:
                payload = event.model_dump(mode="json")
                previous = event_payloads.setdefault(event.event_id, payload)
                if previous != payload:
                    raise ValueError("reused provenance event IDs must have identical payloads")

        versions: dict[tuple[str, str, str, str, str], list[tuple[int, int]]] = defaultdict(list)
        active_counts: dict[tuple[str, str, str, str, str], int] = defaultdict(int)
        for record in self.corpus:
            memory = record.memory
            slot_identity = (
                memory.scope.user_id,
                memory.scope.exam_id,
                memory.scope.subject_id,
                memory.scope.memory_namespace.value,
                memory.slot_key,
            )
            versions[slot_identity].append((memory.version, record.write_index))
            if memory.lifecycle_state is LifecycleState.ACTIVE:
                active_counts[slot_identity] += 1
        if any(count > 1 for count in active_counts.values()):
            raise ValueError("a scope and slot may contain at most one active memory")
        for version_rows in versions.values():
            ordered = sorted(version_rows)
            if [version for version, _ in ordered] != list(range(1, len(ordered) + 1)):
                raise ValueError(
                    "corpus must contain a contiguous version history per scope and slot"
                )
            if [index for _, index in ordered] != sorted(index for _, index in ordered):
                raise ValueError("memory versions must appear in write order")

        for record in self.corpus:
            memory = record.memory
            terminal = memory.lifecycle_state in {
                LifecycleState.ARCHIVED,
                LifecycleState.INVALIDATED,
            }
            if terminal != (memory.valid_to is not None):
                raise ValueError("terminal memories require valid_to; current memories forbid it")
            if memory.lifecycle_state is LifecycleState.ARCHIVED:
                if memory.superseded_by is None:
                    raise ValueError("an archived memory requires superseded_by")
                successor = memories.get(memory.superseded_by)
                if successor is None:
                    raise ValueError("superseded_by must resolve inside the corpus")
                if successor.scope != memory.scope or successor.slot_key != memory.slot_key:
                    raise ValueError("superseded_by must reference the same scope and slot")
                if successor.version <= memory.version:
                    raise ValueError("superseded_by must reference a later version")
                if memory.valid_to is not None and successor.valid_from < memory.valid_to:
                    raise ValueError(
                        "a successor cannot become valid before its predecessor closes"
                    )
            elif memory.superseded_by is not None:
                raise ValueError("only archived memories may declare superseded_by")

        for query in self.queries:
            candidate_ids = {
                memory_id
                for memory_id, memory in memories.items()
                if memory.scope == query.scope
                and memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
            }
            minimum_candidate_count = max(
                MINIMUM_CANDIDATES_PER_QUERY,
                10 * query.top_k,
            )
            if len(candidate_ids) < minimum_candidate_count:
                raise ValueError(
                    f"every query requires at least {minimum_candidate_count} "
                    "retrievable in-scope candidates"
                )
            if len(query.hard_negative_memory_ids) < MINIMUM_HARD_NEGATIVES_PER_QUERY:
                raise ValueError(
                    f"every query requires at least {MINIMUM_HARD_NEGATIVES_PER_QUERY} "
                    "explicit hard negatives"
                )
            relevant = set(query.relevant_memory_ids)
            hard_negatives = set(query.hard_negative_memory_ids)
            forbidden = set(query.must_not_return_memory_ids)
            unknown = (relevant | hard_negatives | forbidden) - memories.keys()
            if unknown:
                raise ValueError("query gold IDs must resolve to corpus memories")
            if not relevant <= candidate_ids:
                raise ValueError("relevant memories must be retrievable and exactly in query scope")
            if not hard_negatives <= candidate_ids:
                raise ValueError("hard negatives must be retrievable and exactly in query scope")
            for memory_id in forbidden:
                memory = memories[memory_id]
                if memory.scope == query.scope and memory.lifecycle_state in {
                    LifecycleState.ACTIVE,
                    LifecycleState.CONTESTED,
                }:
                    raise ValueError(
                        "must-not-return IDs are reserved for terminal or cross-scope safety cases"
                    )
        must_not_pairs = [
            (query, memories[memory_id])
            for query in self.queries
            for memory_id in query.must_not_return_memory_ids
        ]
        if not any(
            memory.scope == query.scope
            and memory.lifecycle_state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
            for query, memory in must_not_pairs
        ):
            raise ValueError("each split requires an archived/invalidated must-not-return case")
        if not any(memory.scope != query.scope for query, memory in must_not_pairs):
            raise ValueError("each split requires a cross-scope must-not-return case")
        return self


class RetrievalDatasetFileRecord(StrictRetrievalModel):
    path: NonEmptyString
    split: RetrievalSplit
    dataset_sha256: Sha256Digest
    corpus_count: Annotated[int, Field(ge=MINIMUM_CANDIDATES_PER_QUERY)]
    query_count: Annotated[int, Field(ge=1)]
    query_family_ids: Annotated[list[NonEmptyString], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_query_families(self) -> RetrievalDatasetFileRecord:
        if len(self.query_family_ids) != len(set(self.query_family_ids)):
            raise ValueError("query_family_ids must be unique within a dataset file")
        return self


def manifest_records_hash(records: list[RetrievalDatasetFileRecord]) -> str:
    ordered = sorted(records, key=lambda record: (record.split.value, record.path))
    return canonical_sha256([record.model_dump(mode="json") for record in ordered])


class RetrievalDatasetManifest(StrictRetrievalModel):
    protocol_version: Literal[RETRIEVAL_PROTOCOL_VERSION]
    dataset_version: DatasetVersion
    seed: Annotated[int, Field(ge=0)]
    generated_at: AwareDatetime
    records: Annotated[list[RetrievalDatasetFileRecord], Field(min_length=2, max_length=2)]
    aggregate_sha256: Sha256Digest
    frozen_test_sha256: Sha256Digest
    metric_catalog_sha256: Sha256Digest
    construction_notes: Annotated[list[NonEmptyString], Field(min_length=1)]

    @model_validator(mode="after")
    def validate_manifest(self) -> RetrievalDatasetManifest:
        by_split = {record.split: record for record in self.records}
        if len(by_split) != len(self.records) or set(by_split) != set(RetrievalSplit):
            raise ValueError("manifest must contain exactly one dev and one test dataset file")
        dev_families = set(by_split[RetrievalSplit.DEV].query_family_ids)
        test_families = set(by_split[RetrievalSplit.TEST].query_family_ids)
        if dev_families & test_families:
            raise ValueError("dev and test query families must be disjoint")
        if self.aggregate_sha256 != manifest_records_hash(self.records):
            raise ValueError("aggregate_sha256 must match canonical file records")
        if self.frozen_test_sha256 != by_split[RetrievalSplit.TEST].dataset_sha256:
            raise ValueError("frozen_test_sha256 must pin the test dataset hash")
        return self


__all__ = [
    "CorpusMemoryRecord",
    "DatasetVersion",
    "MINIMUM_CANDIDATES_PER_QUERY",
    "MINIMUM_HARD_NEGATIVES_PER_QUERY",
    "RETRIEVAL_EMBEDDING_DIMENSION",
    "RETRIEVAL_PROTOCOL_VERSION",
    "RetrievalDataset",
    "RetrievalDatasetFileRecord",
    "RetrievalDatasetManifest",
    "RetrievalQuery",
    "RetrievalSplit",
    "canonical_embedding_text",
    "canonical_sha256",
    "manifest_records_hash",
]
