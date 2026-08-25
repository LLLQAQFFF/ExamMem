"""Serial PostgreSQL ingestion for the semantic-retrieval-v2 benchmark."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import math
from time import perf_counter
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncConnection

from deeptutor.plugins.host_services import validate_embedding_batch
from evaluation.retrieval.contracts import (
    RETRIEVAL_EMBEDDING_DIMENSION,
    CorpusMemoryRecord,
    RetrievalDataset,
)
from evaluation.retrieval.metrics import (
    StorageTrialKind,
    StorageWriteObservation,
    compute_storage_metrics,
)
from exam_mem.contracts import LifecycleState
from exam_mem.storage.event_repository import AppendStatus, PostgresLearningEventRepository
from exam_mem.storage.memory_repository import PostgresLearningMemoryRepository
from exam_mem.storage.models import learning_memories, memory_provenance


class EmbeddingClient(Protocol):
    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class StorageIngestionResult:
    observations: tuple[StorageWriteObservation, ...]
    memory_count: int
    event_count: int
    embedding_dimension: int
    elapsed_ms: float

    @property
    def metrics(self):
        return compute_storage_metrics(list(self.observations))


@dataclass(frozen=True, slots=True)
class ScaleIngestionResult:
    memory_count: int
    event_count: int
    embedding_dimension: int
    elapsed_ms: float


async def embed_records(
    records: Sequence[CorpusMemoryRecord],
    embedding_client: EmbeddingClient,
    *,
    batch_size: int = 32,
    progress: Callable[[int, int], None] | None = None,
) -> list[list[float]]:
    """Embed canonical document text in bounded batches and preserve record order."""
    if batch_size < 1:
        raise ValueError("batch_size must be greater than or equal to 1")
    vectors: list[list[float]] = []
    total = len(records)
    for start in range(0, total, batch_size):
        batch = records[start : start + batch_size]
        generated = await embedding_client.embed(
            [record.embedding_text for record in batch],
            input_type="search_document",
        )
        validated = validate_embedding_batch(
            generated,
            expected_count=len(batch),
            binding="exam_mem_semantic_retrieval_v2",
            start_index=start,
        )
        for vector in validated:
            _require_production_vector(vector)
        vectors.extend(validated)
        if progress is not None:
            progress(min(start + len(batch), total), total)
    return vectors


async def ingest_dataset(
    connection: AsyncConnection,
    dataset: RetrievalDataset,
    embedding_client: EmbeddingClient,
    *,
    batch_size: int = 32,
    replay_each: bool = True,
    progress: Callable[[int, int], None] | None = None,
) -> StorageIngestionResult:
    """Write L1 then L2 in write-index order inside one caller-owned transaction."""
    started = perf_counter()
    vectors = await embed_records(
        dataset.corpus,
        embedding_client,
        batch_size=batch_size,
        progress=None,
    )
    observations: list[StorageWriteObservation] = []
    event_ids: set[str] = set()

    async with connection.begin():
        event_repository = PostgresLearningEventRepository(connection)
        memory_repository = PostgresLearningMemoryRepository(connection)
        for record, vector in zip(dataset.corpus, vectors, strict=True):
            for event in record.provenance_events:
                result = await event_repository.append(event, trace_id=event.event_id)
                if result.status is AppendStatus.CONFLICT:
                    raise RuntimeError(f"L1 identity conflict for {event.event_id}")
                event_ids.add(event.event_id)

            before = await _memory_count(connection)
            write_started = perf_counter()
            snapshot = await memory_repository.insert_version(
                record.memory,
                policy_version=dataset.policy_version,
                content_embedding=vector,
                contested_group_id=record.contested_group_id,
                provenance_relations=record.provenance_relations,
            )
            after = await _memory_count(connection)
            stored_vector = await _stored_vector(connection, record.memory.memory_id)
            stored_relations = await _stored_relations(connection, record.memory.memory_id)
            round_trip_equal = (
                snapshot.memory == record.memory
                and snapshot.policy_version == dataset.policy_version
                and snapshot.contested_group_id == record.contested_group_id
                and stored_relations == record.provenance_relations
            )
            observations.append(
                StorageWriteObservation(
                    trial_id=f"primary:{record.memory.memory_id}",
                    trial_kind=StorageTrialKind.PRIMARY_WRITE,
                    write_index=record.write_index,
                    memory_id=record.memory.memory_id,
                    provenance_event_ids=record.memory.provenance,
                    accepted=True,
                    row_count_delta=after - before,
                    round_trip_equal=round_trip_equal,
                    stored_provenance_event_ids=snapshot.memory.provenance,
                    stored_embedding_dimensions=len(stored_vector),
                    stored_embedding_all_finite=all(
                        math.isfinite(value) for value in stored_vector
                    ),
                    stored_embedding_nonzero=math.fsum(value * value for value in stored_vector)
                    > 0.0,
                    latency_ms=(perf_counter() - write_started) * 1000.0,
                )
            )

            if replay_each:
                observations.append(
                    await _replay_record(
                        connection,
                        event_repository,
                        memory_repository,
                        dataset,
                        record,
                    )
                )
            if progress is not None:
                progress(record.write_index + 1, len(dataset.corpus))

    return StorageIngestionResult(
        observations=tuple(observations),
        memory_count=len(dataset.corpus),
        event_count=len(event_ids),
        embedding_dimension=RETRIEVAL_EMBEDDING_DIMENSION,
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )


async def ingest_scale_corpus(
    connection: AsyncConnection,
    records: Sequence[CorpusMemoryRecord],
    embedding_client: EmbeddingClient,
    *,
    batch_size: int = 64,
    transaction_size: int = 240,
    policy_version: str = "semantic_retrieval_v2_scale_only",
    progress: Callable[[int, int], None] | None = None,
) -> ScaleIngestionResult:
    """Embed in batches but issue every L1/L2 Repository write serially."""
    if transaction_size < 1:
        raise ValueError("transaction_size must be greater than or equal to 1")
    started = perf_counter()
    event_ids: set[str] = set()
    written = 0
    for transaction_start in range(0, len(records), transaction_size):
        transaction_records = records[transaction_start : transaction_start + transaction_size]
        vectors = await embed_records(
            transaction_records,
            embedding_client,
            batch_size=batch_size,
        )
        async with connection.begin():
            event_repository = PostgresLearningEventRepository(connection)
            memory_repository = PostgresLearningMemoryRepository(connection)
            for record, vector in zip(transaction_records, vectors, strict=True):
                for event in record.provenance_events:
                    result = await event_repository.append(event, trace_id=event.event_id)
                    if result.status is AppendStatus.CONFLICT:
                        raise RuntimeError(f"L1 identity conflict for {event.event_id}")
                    event_ids.add(event.event_id)
                await memory_repository.insert_version(
                    record.memory,
                    policy_version=policy_version,
                    content_embedding=vector,
                    contested_group_id=record.contested_group_id,
                    provenance_relations=record.provenance_relations,
                )
                written += 1
                if progress is not None:
                    progress(written, len(records))
    return ScaleIngestionResult(
        memory_count=written,
        event_count=len(event_ids),
        embedding_dimension=RETRIEVAL_EMBEDDING_DIMENSION,
        elapsed_ms=(perf_counter() - started) * 1000.0,
    )


async def run_invariant_rejection_trials(
    connection: AsyncConnection,
    dataset: RetrievalDataset,
    valid_vector: Sequence[float],
    *,
    trial_count: int = 32,
) -> tuple[StorageWriteObservation, ...]:
    """Exercise preregistered invalid writes; every trial is rolled back independently."""
    if trial_count < 8:
        raise ValueError("trial_count must cover all eight invariant classes")
    base_record = next(
        record
        for record in dataset.corpus
        if record.memory.lifecycle_state is LifecycleState.ACTIVE
    )
    scenarios = (
        "missing_provenance",
        "cross_scope_provenance",
        "wrong_version",
        "duplicate_active",
        "terminal_open_interval",
        "wrong_dimension",
        "non_finite_vector",
        "zero_vector",
    )
    observations: list[StorageWriteObservation] = []
    for ordinal in range(trial_count):
        scenario = scenarios[ordinal % len(scenarios)]
        transaction = await connection.begin()
        try:
            before = await _memory_count(connection)
            started = perf_counter()
            error: Exception | None = None
            try:
                await _attempt_invalid_write(
                    connection,
                    dataset,
                    base_record,
                    valid_vector,
                    scenario=scenario,
                    ordinal=ordinal,
                )
            except Exception as exc:  # the observation must retain the real failure type
                error = exc
            after = await _memory_count(connection)
            observations.append(
                StorageWriteObservation(
                    trial_id=f"invalid:{scenario}:{ordinal:03d}",
                    trial_kind=StorageTrialKind.INVARIANT_REJECTION,
                    write_index=len(dataset.corpus) + ordinal,
                    memory_id=f"invalid_{scenario}_{ordinal:03d}",
                    provenance_event_ids=base_record.memory.provenance,
                    accepted=error is None,
                    row_count_delta=after - before,
                    error_type=None if error is None else type(error).__name__,
                    constraint_name=scenario,
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
            )
        finally:
            await transaction.rollback()
    return tuple(observations)


async def _replay_record(
    connection: AsyncConnection,
    event_repository: PostgresLearningEventRepository,
    memory_repository: PostgresLearningMemoryRepository,
    dataset: RetrievalDataset,
    record: CorpusMemoryRecord,
) -> StorageWriteObservation:
    before = await _memory_count(connection)
    started = perf_counter()
    statuses = [
        (await event_repository.append(event, trace_id=event.event_id)).status
        for event in record.provenance_events
    ]
    snapshot = await memory_repository.get_lifecycle_snapshot(
        record.memory.scope,
        record.memory.memory_id,
    )
    after = await _memory_count(connection)
    equal = (
        snapshot is not None
        and snapshot.memory == record.memory
        and snapshot.policy_version == dataset.policy_version
        and snapshot.contested_group_id == record.contested_group_id
        and all(status is AppendStatus.EXISTING for status in statuses)
    )
    return StorageWriteObservation(
        trial_id=f"replay:{record.memory.memory_id}",
        trial_kind=StorageTrialKind.IDEMPOTENT_REPLAY,
        write_index=record.write_index,
        memory_id=record.memory.memory_id,
        provenance_event_ids=record.memory.provenance,
        accepted=equal,
        row_count_delta=after - before,
        round_trip_equal=equal,
        error_type=None if equal else "ReplayMismatch",
        latency_ms=(perf_counter() - started) * 1000.0,
    )


async def _attempt_invalid_write(
    connection: AsyncConnection,
    dataset: RetrievalDataset,
    base_record: CorpusMemoryRecord,
    valid_vector: Sequence[float],
    *,
    scenario: str,
    ordinal: int,
) -> None:
    repository = PostgresLearningMemoryRepository(connection)
    base = base_record.memory
    memory_id = f"invalid_{scenario}_{ordinal:03d}"
    memory = base.model_copy(update={"memory_id": memory_id})
    vector = list(valid_vector)
    if scenario == "missing_provenance":
        memory = memory.model_copy(
            update={"provenance": [f"missing_event_{ordinal:03d}"], "evidence_count": 1}
        )
    elif scenario == "cross_scope_provenance":
        memory = memory.model_copy(
            update={
                "scope": base.scope.model_copy(
                    update={"user_id": f"invalid_cross_scope_user_{ordinal:03d}"}
                )
            }
        )
    elif scenario == "wrong_version":
        memory = memory.model_copy(update={"version": 999})
    elif scenario == "duplicate_active":
        memory = memory.model_copy(
            update={"version": await repository.next_version(base.scope, base.slot_key)}
        )
    elif scenario == "terminal_open_interval":
        memory = memory.model_copy(
            update={
                "lifecycle_state": LifecycleState.ARCHIVED,
                "version": await repository.next_version(base.scope, base.slot_key),
                "valid_to": None,
            }
        )
    elif scenario == "wrong_dimension":
        vector = vector[:-1]
    elif scenario == "non_finite_vector":
        vector[0] = math.nan
    elif scenario == "zero_vector":
        vector = [0.0] * RETRIEVAL_EMBEDDING_DIMENSION
    else:
        raise ValueError(f"unknown invariant scenario: {scenario}")

    await repository.insert_version(
        memory,
        policy_version=dataset.policy_version,
        content_embedding=vector,
        contested_group_id=base_record.contested_group_id,
        provenance_relations={event_id: "created_by" for event_id in memory.provenance},
    )


async def _memory_count(connection: AsyncConnection) -> int:
    return int(await connection.scalar(select(func.count()).select_from(learning_memories)) or 0)


async def _stored_vector(connection: AsyncConnection, memory_id: str) -> list[float]:
    vector = await connection.scalar(
        select(learning_memories.c.content_embedding).where(
            learning_memories.c.memory_id == memory_id
        )
    )
    if vector is None:
        raise RuntimeError(f"stored embedding is missing for {memory_id}")
    return [float(value) for value in vector]


async def _stored_relations(connection: AsyncConnection, memory_id: str) -> dict[str, str]:
    rows = (
        await connection.execute(
            select(memory_provenance.c.event_id, memory_provenance.c.relation_type).where(
                memory_provenance.c.memory_id == memory_id
            )
        )
    ).all()
    return {event_id: relation for event_id, relation in rows}


def _require_production_vector(vector: Sequence[float]) -> None:
    if len(vector) != RETRIEVAL_EMBEDDING_DIMENSION:
        raise ValueError(
            f"embedding dimension must be {RETRIEVAL_EMBEDDING_DIMENSION}; got {len(vector)}"
        )
    if math.fsum(value * value for value in vector) == 0.0:
        raise ValueError("embedding vector must be non-zero")


__all__ = [
    "ScaleIngestionResult",
    "StorageIngestionResult",
    "embed_records",
    "ingest_dataset",
    "ingest_scale_corpus",
    "run_invariant_rejection_trials",
]
