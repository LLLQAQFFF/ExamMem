from __future__ import annotations

from dataclasses import asdict

import pytest

from deeptutor.plugins.host_services import PluginDataConflict, PluginMemoryEvent
from deeptutor_plugins.exam_mem.native_adapter import DeepTutorNativeMemoryClient
from exam_mem.backends.baseline import BackendWriteConflict
from exam_mem.backends.native import NativeMemoryEvent


class FakeHost:
    def __init__(self) -> None:
        self.event: PluginMemoryEvent | None = None
        self.surface: str | None = None

    async def append_once(self, event: PluginMemoryEvent) -> bool:
        self.event = event
        return True

    async def consolidate(self, surface: str) -> None:
        self.surface = surface

    def snapshot(self, surface: str):
        self.surface = surface
        return {"backend_mode": "native", "l2": "quiz", "l3": "snapshot"}


def _event() -> NativeMemoryEvent:
    return NativeMemoryEvent(
        id="event-1",
        ts="2026-08-13T00:00:00+00:00",
        surface="quiz",
        kind="learning_event",
        payload={"value": 1},
        session_id="session-1",
        turn_id="trace-1",
    )


@pytest.mark.asyncio
async def test_native_adapter_converts_dto_and_preserves_surface_contract() -> None:
    host = FakeHost()
    client = DeepTutorNativeMemoryClient(host)  # type: ignore[arg-type]

    assert await client.append_once(_event()) is True
    await client.consolidate_quiz()
    snapshot = client.snapshot()

    assert host.event == PluginMemoryEvent(**asdict(_event()))
    assert host.surface == "quiz"
    assert snapshot == {"backend_mode": "native", "quiz_l2": "quiz", "l3": "snapshot"}


@pytest.mark.asyncio
async def test_native_adapter_maps_host_identity_conflict() -> None:
    class ConflictingHost(FakeHost):
        async def append_once(self, event: PluginMemoryEvent) -> bool:
            raise PluginDataConflict(event.id)

    client = DeepTutorNativeMemoryClient(ConflictingHost())  # type: ignore[arg-type]

    with pytest.raises(BackendWriteConflict):
        await client.append_once(_event())
