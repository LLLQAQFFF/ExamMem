"""Repository-backed query service for the actionable learning profile."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    MemoryNamespace,
    MemoryScope,
)
from exam_mem.domain import Taxonomy
from exam_mem.storage.student_model_repository import StudentModelSnapshot

from .learning_profile import LearningProfile, build_learning_profile


class ProfileEventRepository(Protocol):
    async def list_after(
        self, context: LearningContext, watermark: str | None, limit: int
    ) -> list[LearningEvent]: ...


class ProfileMemoryRepository(Protocol):
    async def snapshot(self, scope: MemoryScope) -> list[LearningMemory]: ...


class ProfileModelRepository(Protocol):
    async def get_latest(self, context: LearningContext) -> StudentModelSnapshot | None: ...


class LearningProfileQueryService:
    """Read every authoritative layer and derive a disposable product view."""

    def __init__(
        self,
        *,
        event_repository: ProfileEventRepository,
        memory_repository: ProfileMemoryRepository,
        model_repository: ProfileModelRepository,
        event_page_size: int = 500,
    ) -> None:
        if event_page_size < 1:
            raise ValueError("event_page_size must be positive")
        self._events = event_repository
        self._memories = memory_repository
        self._models = model_repository
        self._event_page_size = event_page_size

    async def get(
        self,
        *,
        context: LearningContext,
        taxonomy: Taxonomy,
        evaluated_at: datetime,
    ) -> LearningProfile:
        events = await self._read_events(context)
        memories: list[LearningMemory] = []
        for namespace in (
            MemoryNamespace.MASTERY,
            MemoryNamespace.ERROR_PATTERN,
            MemoryNamespace.PLAN,
        ):
            memories.extend(
                await self._memories.snapshot(
                    MemoryScope(**context.model_dump(), memory_namespace=namespace)
                )
            )
        snapshot = await self._models.get_latest(context)
        return build_learning_profile(
            context=context,
            taxonomy=taxonomy,
            events=events,
            memories=memories,
            model=None if snapshot is None else snapshot.model,
            evaluated_at=evaluated_at,
        )

    async def _read_events(self, context: LearningContext) -> list[LearningEvent]:
        output: list[LearningEvent] = []
        watermark: str | None = None
        while True:
            page = await self._events.list_after(
                context,
                watermark,
                self._event_page_size,
            )
            if not page:
                return output
            output.extend(page)
            watermark = page[-1].event_id


__all__ = [
    "LearningProfileQueryService",
    "ProfileEventRepository",
    "ProfileMemoryRepository",
    "ProfileModelRepository",
]
