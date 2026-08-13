"""Repository orchestration for deterministic L3 full rebuilds."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    MemoryNamespace,
    MemoryScope,
)
from exam_mem.projection import project_student_model

from .event_repository import LearningEventRepository
from .memory_repository import LearningMemoryRepository
from .student_model_repository import StudentModelRepository, StudentModelSnapshot

STUDENT_MODEL_PROJECTION_VERSION = 1
DEFAULT_EVENT_PAGE_SIZE = 1000


class RebuildInputError(ValueError):
    """Raised when persisted L1/L2 cannot form a rebuild input snapshot."""


@dataclass(frozen=True, slots=True)
class StudentModelRebuildResult:
    """Outcome returned before the caller decides whether to commit."""

    snapshot: StudentModelSnapshot
    previous_snapshot: StudentModelSnapshot | None
    changed_fields: tuple[str, ...]
    cleared_snapshot_count: int
    event_count: int
    memory_count: int


class StudentModelRebuildService:
    """Rebuild L3 through repositories inside one caller-owned transaction.

    The caller must use a consistent database transaction for all repositories.
    The service deliberately does not commit, so a failed save can roll back the
    L3 clear without affecting already-committed L1/L2 records.
    """

    def __init__(
        self,
        *,
        event_repository: LearningEventRepository,
        memory_repository: LearningMemoryRepository,
        student_model_repository: StudentModelRepository,
        event_page_size: int = DEFAULT_EVENT_PAGE_SIZE,
    ) -> None:
        if event_page_size < 1:
            raise ValueError("event_page_size must be greater than or equal to 1")
        self._event_repository = event_repository
        self._memory_repository = memory_repository
        self._student_model_repository = student_model_repository
        self._event_page_size = event_page_size

    async def rebuild(self, context: LearningContext) -> StudentModelRebuildResult:
        events, event_watermark = await self._read_events(context)
        memories = await self._read_memories(context)
        memory_watermark = _memory_snapshot_watermark(memories)
        model = project_student_model(
            context=context,
            events=events,
            memories=memories,
            projection_version=STUDENT_MODEL_PROJECTION_VERSION,
            source_event_watermark=event_watermark,
        )
        snapshot = StudentModelSnapshot(
            snapshot_id=_snapshot_id(
                context=context,
                event_watermark=event_watermark,
                memory_watermark=memory_watermark,
            ),
            model=model,
            source_event_watermark=event_watermark,
            source_memory_watermark=memory_watermark,
        )

        previous_snapshot = await self._student_model_repository.get_latest(context)
        cleared_snapshot_count = await self._student_model_repository.clear_projection(context)
        await self._student_model_repository.save_projection(snapshot)
        return StudentModelRebuildResult(
            snapshot=snapshot,
            previous_snapshot=previous_snapshot,
            changed_fields=_changed_model_fields(previous_snapshot, snapshot),
            cleared_snapshot_count=cleared_snapshot_count,
            event_count=len(events),
            memory_count=len(memories),
        )

    async def _read_events(
        self,
        context: LearningContext,
    ) -> tuple[list[LearningEvent], str]:
        events: list[LearningEvent] = []
        watermark: str | None = None
        while True:
            page = await self._event_repository.list_after(
                context,
                watermark,
                self._event_page_size,
            )
            events.extend(page)
            if not page:
                break
            watermark = page[-1].event_id
            if len(page) < self._event_page_size:
                break

        if watermark is None:
            raise RebuildInputError("cannot rebuild StudentModel without L1 events")
        return events, watermark

    async def _read_memories(self, context: LearningContext) -> list[LearningMemory]:
        memories: list[LearningMemory] = []
        for namespace in MemoryNamespace:
            scope = MemoryScope(
                **context.model_dump(),
                memory_namespace=namespace,
            )
            memories.extend(await self._memory_repository.snapshot(scope))
        return memories


def _memory_snapshot_watermark(memories: list[LearningMemory]) -> str:
    ordered_memories = sorted(
        memories,
        key=lambda memory: (
            memory.scope.memory_namespace.value,
            memory.slot_key,
            memory.version,
            memory.memory_id,
        ),
    )
    payload = json.dumps(
        [memory.model_dump(mode="json") for memory in ordered_memories],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _snapshot_id(
    *,
    context: LearningContext,
    event_watermark: str,
    memory_watermark: str,
) -> str:
    payload = json.dumps(
        {
            "context": context.model_dump(mode="json"),
            "event_watermark": event_watermark,
            "memory_watermark": memory_watermark,
            "projection_version": STUDENT_MODEL_PROJECTION_VERSION,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return f"student_model:{hashlib.sha256(payload).hexdigest()}"


def _changed_model_fields(
    previous: StudentModelSnapshot | None,
    current: StudentModelSnapshot,
) -> tuple[str, ...]:
    if previous is None:
        return tuple(current.model.__class__.model_fields)
    return tuple(
        field_name
        for field_name in current.model.__class__.model_fields
        if getattr(previous.model, field_name) != getattr(current.model, field_name)
    )


__all__ = [
    "DEFAULT_EVENT_PAGE_SIZE",
    "RebuildInputError",
    "STUDENT_MODEL_PROJECTION_VERSION",
    "StudentModelRebuildResult",
    "StudentModelRebuildService",
]
