from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from evaluation.backend_adapters import (
    ConfiguredHostEmbeddingClient,
    DeterministicHashEmbeddingClient,
    NativeEvaluationSession,
    PostgresEvaluationSession,
    TrackedRelationCompletion,
)
from evaluation.materializer import materialize_case
from evaluation.protocols.validation import load_cases
from exam_mem.backends import BackendMode
from exam_mem.backends.native import NativeMemoryEvent
from exam_mem.contracts import MasteryValue

pytestmark = pytest.mark.asyncio


class FakeNativeClient:
    def __init__(self) -> None:
        self.events: dict[str, NativeMemoryEvent] = {}
        self.consolidations = 0

    async def append_once(self, event: NativeMemoryEvent) -> bool:
        previous = self.events.get(event.id)
        if previous is not None:
            assert previous == event
            return False
        self.events[event.id] = event
        return True

    async def consolidate_quiz(self) -> None:
        self.consolidations += 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "backend_mode": "native",
            "record_count": len(self.events),
            "native_typed_lifecycle_available": False,
        }


async def test_native_session_uses_host_port_and_keeps_typed_state_empty(tmp_path: Path) -> None:
    case = load_cases("protocol_check")[0]
    client = FakeNativeClient()
    session = NativeEvaluationSession(
        root=tmp_path,
        run_id="native-test",
        case=case,
        client=client,
    )

    initial = await session.seed(case)
    decisions, final = await session.process(materialize_case(case)[0])

    assert initial["record_count"] == len(case.initial_memory)
    assert final["record_count"] == len(case.initial_memory) + 2
    assert client.consolidations == 2
    assert decisions == []
    assert session.state_trace(final).active_memory_ids == []


async def test_feature_hash_embedding_is_frozen_normalized_and_local() -> None:
    client = DeterministicHashEmbeddingClient()

    first, second = await client.embed(["same text", "same text"])

    assert first == second
    assert len(first) == 1024
    assert sum(value * value for value in first) == pytest.approx(1.0)
    assert client.call_count == 1


async def test_configured_embedding_records_safe_identity_and_delegates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        async def embed(self, texts, *, input_type=None):  # noqa: ANN001, ANN201
            assert texts == ["query"]
            assert input_type == "search_query"
            return [[1.0, *([0.0] * 1023)]]

    monkeypatch.setattr(
        "evaluation.backend_adapters.resolve_embedding_runtime_config",
        lambda: SimpleNamespace(provider_name="ollama", model="local-model", dimension=1024),
    )
    monkeypatch.setattr(
        "evaluation.backend_adapters.get_embedding_client",
        lambda: _Client(),
    )

    client = ConfiguredHostEmbeddingClient()
    vectors = await client.embed(["query"], input_type="search_query")

    assert client.version == "ollama:local-model:1024"
    assert client.call_count == 1
    assert len(vectors[0]) == 1024


async def test_relation_call_preserves_cancellation_as_failed_trace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "evaluation.backend_adapters.resolve_llm_runtime_config",
        lambda: SimpleNamespace(provider_name="fake", model="fake"),
    )

    async def cancelled(**kwargs):  # noqa: ANN003, ANN202
        raise asyncio.CancelledError

    monkeypatch.setattr("evaluation.backend_adapters.complete", cancelled)
    completion = TrackedRelationCompletion("cancel-test")

    with pytest.raises(asyncio.CancelledError):
        await completion(
            prompt="synthetic",
            system_prompt="synthetic",
            response_format={"type": "json_object"},
            temperature=0,
        )

    calls = completion.take_calls()
    assert len(calls) == 1
    assert calls[0].succeeded is False
    assert calls[0].error is not None
    assert calls[0].error.error_type == "CancelledError"


async def test_seed_event_is_a_valid_non_scoring_provenance_placeholder() -> None:
    case = next(
        case
        for case in load_cases("protocol_check")
        if any(
            isinstance(memory.value, MasteryValue) and memory.value.score < 0.5
            for memory in case.initial_memory
        )
    )
    memory = next(
        memory
        for memory in case.initial_memory
        if isinstance(memory.value, MasteryValue) and memory.value.score < 0.5
    )
    session = PostgresEvaluationSession(
        engine=object(),  # type: ignore[arg-type]
        mode=BackendMode.APPEND_ONLY,
        run_id="seed-contract",
        case=case,
    )
    runtime_memory = session._runtime_memory(memory)
    event_id = next(
        event_id
        for event_id in runtime_memory.provenance
        if event_id not in {session._runtime_scalar_id(event.event_id) for event in case.events}
    )

    event = session._seed_event(memory=runtime_memory, event_id=event_id, offset=0)

    assert event.answer_correct is False
    assert event.error_type is not None
    assert event.error_detail is not None
    assert event.evidence_quality.is_temporary_exception is True
    assert [reason.value for reason in event.evidence_quality.reasons] == ["insufficient_context"]


async def test_postgres_retrieval_preserves_the_natural_language_query() -> None:
    case = next(case for case in load_cases("protocol_check") if case.queries)
    query = case.queries[0]

    class _Engine:
        @asynccontextmanager
        async def connect(self):
            yield object()

    class _Backend:
        def __init__(self) -> None:
            self.query: str | None = None

        async def retrieve(self, scope, text, top_k):  # noqa: ANN001, ANN201
            del scope, top_k
            self.query = text
            return []

    backend = _Backend()
    session = PostgresEvaluationSession(
        engine=_Engine(),  # type: ignore[arg-type]
        mode=BackendMode.VECTOR,
        run_id="natural-query",
        case=case,
    )

    async def resolve_backend(connection):  # noqa: ANN001, ANN202
        del connection
        return backend

    session._backend = resolve_backend  # type: ignore[method-assign]

    assert await session.retrieve(query) == []
    assert backend.query == query.text
