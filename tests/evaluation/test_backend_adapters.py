from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from evaluation.backend_adapters import (
    DeterministicHashEmbeddingClient,
    NativeEvaluationSession,
)
from evaluation.materializer import materialize_case
from evaluation.protocols.validation import load_cases
from exam_mem.backends.native import NativeMemoryEvent

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
