"""ExamMem DTO adapter for the neutral Host Native Memory API."""

from __future__ import annotations

from pydantic import JsonValue

from deeptutor.plugins.host_services import (
    NativeMemoryHost,
    PluginDataConflict,
    PluginMemoryEvent,
)
from exam_mem.backends.baseline import BackendWriteConflict
from exam_mem.backends.native import NativeMemoryEvent


class DeepTutorNativeMemoryClient:
    def __init__(self, host: NativeMemoryHost | None = None) -> None:
        self._host = host or NativeMemoryHost()

    async def append_once(self, event: NativeMemoryEvent) -> bool:
        try:
            return await self._host.append_once(
                PluginMemoryEvent(
                    id=event.id,
                    ts=event.ts,
                    surface=event.surface,
                    kind=event.kind,
                    payload=event.payload,
                    session_id=event.session_id,
                    turn_id=event.turn_id,
                )
            )
        except PluginDataConflict as exc:
            raise BackendWriteConflict(str(exc)) from exc

    async def consolidate_quiz(self) -> None:
        await self._host.consolidate("quiz")

    def snapshot(self) -> dict[str, JsonValue]:
        payload = self._host.snapshot("quiz")
        return {
            "backend_mode": "native",
            "quiz_l2": payload["l2"],
            "l3": payload["l3"],
        }


__all__ = ["DeepTutorNativeMemoryClient"]
