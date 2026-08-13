"""The common backend boundary used by every ExamMem experiment arm."""

from __future__ import annotations

from enum import Enum
from typing import Protocol, runtime_checkable

from pydantic import JsonValue

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)


class BackendMode(str, Enum):
    NONE = "none"
    NATIVE = "native"
    APPEND_ONLY = "append_only"
    VECTOR = "vector"
    LIFECYCLE = "lifecycle"


@runtime_checkable
class MemoryBackend(Protocol):
    """Async port shared by all five baselines.

    This protocol deliberately contains no persistence or lifecycle policy.
    Implementations may differ internally, while the evaluation call site and
    observable result types remain fixed.
    """

    async def record_event(self, event: LearningEvent) -> None: ...

    async def update(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> list[LifecycleDecision]: ...

    async def query_state(self, context: LearningContext) -> StudentModel | None: ...

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]: ...

    async def snapshot(self, context: LearningContext) -> dict[str, JsonValue]: ...


__all__ = ["BackendMode", "MemoryBackend"]
