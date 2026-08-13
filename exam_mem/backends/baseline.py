"""Isolated append-only and vector implementations of ``MemoryBackend``."""

from __future__ import annotations

from collections.abc import Sequence
import hashlib
import json
from typing import Protocol

from pydantic import JsonValue

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleState,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)
from exam_mem.storage.baseline_fact_repository import (
    BaselineFactRecord,
    BaselineFactRepository,
)
from exam_mem.storage.event_repository import AppendStatus, LearningEventRepository

from .protocol import BackendMode


class BaselineEmbeddingClient(Protocol):
    """The existing DeepTutor embedding boundary used by the vector arm."""

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


class BackendWriteConflict(RuntimeError):
    """Raised when a stable event/fact identity is replayed with other content."""


class _BaselineBackend:
    def __init__(
        self,
        *,
        mode: BackendMode,
        event_repository: LearningEventRepository,
        fact_repository: BaselineFactRepository,
        trace_id: str | None = None,
        embedding_client: BaselineEmbeddingClient | None = None,
    ) -> None:
        if mode not in {BackendMode.APPEND_ONLY, BackendMode.VECTOR}:
            raise ValueError("baseline backend requires append_only or vector mode")
        if mode is BackendMode.VECTOR and embedding_client is None:
            raise ValueError("vector backend requires an embedding client")
        if mode is BackendMode.APPEND_ONLY and embedding_client is not None:
            raise ValueError("append_only backend must not receive an embedding client")
        self._mode = mode
        self._event_repository = event_repository
        self._fact_repository = fact_repository
        self._trace_id = trace_id
        self._embedding_client = embedding_client

    async def record_event(self, event: LearningEvent) -> None:
        result = await self._event_repository.append(
            event,
            trace_id=self._trace_id or event.event_id,
        )
        if result.status is AppendStatus.CONFLICT:
            raise BackendWriteConflict("learning event identity conflicts with stored L1")

    async def update(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> list[LifecycleDecision]:
        _validate_candidates(event, candidates)
        embeddings = await self._candidate_embeddings(candidates)
        for candidate, embedding in zip(candidates, embeddings, strict=True):
            result = await self._fact_repository.append(
                BaselineFactRecord(
                    backend_mode=self._mode,
                    candidate=candidate,
                    created_at=event.occurred_at,
                    content_embedding=embedding,
                )
            )
            if result.status is AppendStatus.CONFLICT:
                raise BackendWriteConflict("baseline fact identity conflicts with stored candidate")
        return []

    async def query_state(self, context: LearningContext) -> StudentModel | None:
        return None

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]:
        if top_k < 1:
            raise ValueError("top_k must be greater than or equal to 1")
        if self._mode is BackendMode.VECTOR:
            if not query.strip():
                raise ValueError("vector retrieval query must not be blank")
            assert self._embedding_client is not None
            embeddings = await self._embedding_client.embed(
                [query],
                input_type="search_query",
            )
            if len(embeddings) != 1:
                raise ValueError("embedding client must return exactly one query vector")
            records = await self._fact_repository.find_similar(
                scope,
                embeddings[0],
                top_k,
            )
        else:
            records = await self._fact_repository.list_scope(
                self._mode,
                scope,
                top_k,
            )
        return [_baseline_memory_view(record) for record in records]

    async def snapshot(self, context: LearningContext) -> dict[str, JsonValue]:
        records = await self._fact_repository.snapshot(self._mode, context)
        return {
            "backend_mode": self._mode.value,
            "facts": [record.model_dump(mode="json") for record in records],
        }

    async def _candidate_embeddings(
        self,
        candidates: Sequence[MemoryUpdateCandidate],
    ) -> list[Sequence[float] | None]:
        if self._mode is BackendMode.APPEND_ONLY:
            return [None] * len(candidates)
        if not candidates:
            return []
        assert self._embedding_client is not None
        embeddings = await self._embedding_client.embed(
            [_candidate_embedding_text(candidate) for candidate in candidates],
            input_type="search_document",
        )
        if len(embeddings) != len(candidates):
            raise ValueError("embedding client returned the wrong candidate vector count")
        return embeddings


class AppendOnlyMemoryBackend(_BaselineBackend):
    """Persist L1 and immutable facts without merge or invalidation."""

    def __init__(
        self,
        *,
        event_repository: LearningEventRepository,
        fact_repository: BaselineFactRepository,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(
            mode=BackendMode.APPEND_ONLY,
            event_repository=event_repository,
            fact_repository=fact_repository,
            trace_id=trace_id,
        )


class VectorMemoryBackend(_BaselineBackend):
    """Persist embedded immutable facts without lifecycle transitions."""

    def __init__(
        self,
        *,
        event_repository: LearningEventRepository,
        fact_repository: BaselineFactRepository,
        embedding_client: BaselineEmbeddingClient,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(
            mode=BackendMode.VECTOR,
            event_repository=event_repository,
            fact_repository=fact_repository,
            trace_id=trace_id,
            embedding_client=embedding_client,
        )


def _validate_candidates(
    event: LearningEvent,
    candidates: Sequence[MemoryUpdateCandidate],
) -> None:
    slots: set[tuple[MemoryScope, str]] = set()
    for candidate in candidates:
        if candidate.event_id != event.event_id:
            raise ValueError("candidate event_id must match the current event")
        if candidate.scope.model_dump(exclude={"memory_namespace"}) != event.context.model_dump():
            raise ValueError("candidate scope must match the current event context")
        identity = (candidate.scope, candidate.slot_key)
        if identity in slots:
            raise ValueError("candidate scope and slot_key must be unique per update")
        slots.add(identity)


def _candidate_embedding_text(candidate: MemoryUpdateCandidate) -> str:
    payload = {
        "slot_key": candidate.slot_key,
        "value": candidate.proposed_value.model_dump(mode="json"),
        "evidence": candidate.evidence,
    }
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _baseline_memory_view(record: BaselineFactRecord) -> LearningMemory:
    candidate = record.candidate
    identity = hashlib.sha256(candidate.slot_key.encode("utf-8")).hexdigest()[:16]
    return LearningMemory(
        memory_id=(f"baseline:{record.backend_mode.value}:{candidate.event_id}:{identity}"),
        scope=candidate.scope,
        slot_key=candidate.slot_key,
        value=candidate.proposed_value,
        confidence=1.0,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=record.created_at,
        valid_to=None,
        superseded_by=None,
        provenance=[candidate.event_id],
    )


__all__ = [
    "AppendOnlyMemoryBackend",
    "BackendWriteConflict",
    "BaselineEmbeddingClient",
    "VectorMemoryBackend",
]
