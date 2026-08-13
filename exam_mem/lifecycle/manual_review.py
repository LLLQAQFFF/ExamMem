"""Derived manual-review queue for unresolved contested groups."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from exam_mem.contracts import LifecycleState, MemoryScope

from .contracts import LifecycleMemorySnapshot, LifecyclePolicyV1Config

if TYPE_CHECKING:
    from exam_mem.storage.memory_repository import LearningMemoryRepository


class ContestedGroupInvariantError(RuntimeError):
    """Raised when persisted rows cannot form one open two-branch group."""


@dataclass(frozen=True, slots=True)
class ManualReviewItem:
    """Minimal derived view of a contested group that exceeded its deadline."""

    scope: MemoryScope
    slot_key: str
    contested_group_id: str
    active_memory_id: str
    contested_memory_id: str
    opened_at: datetime
    review_due_at: datetime
    reason_code: str = "contested_timeout"


class ManualReviewQueue:
    """Derive overdue groups from L2; the queue is not a second truth store."""

    def __init__(
        self,
        memory_repository: LearningMemoryRepository,
        *,
        config: LifecyclePolicyV1Config | None = None,
    ) -> None:
        self._memory_repository = memory_repository
        self._config = config or LifecyclePolicyV1Config()

    async def list_due(
        self,
        scope: MemoryScope,
        *,
        evaluated_at: datetime,
    ) -> tuple[ManualReviewItem, ...]:
        if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
            raise ValueError("evaluated_at must include timezone information")

        snapshots = await self._memory_repository.list_contested_group_snapshots(scope)
        groups: dict[str, list[LifecycleMemorySnapshot]] = defaultdict(list)
        for snapshot in snapshots:
            group_id = snapshot.contested_group_id
            if group_id is None:
                raise ContestedGroupInvariantError("group query returned an ungrouped snapshot")
            if snapshot.memory.scope != scope:
                raise ContestedGroupInvariantError("contested group snapshot crossed Scope")
            groups[group_id].append(snapshot)

        due_items: list[ManualReviewItem] = []
        review_after = timedelta(days=self._config.manual_review_after_days)
        for group_id, members in groups.items():
            current = [
                member
                for member in members
                if member.memory.lifecycle_state
                in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
            ]
            if not current:
                continue
            active = [
                member
                for member in current
                if member.memory.lifecycle_state is LifecycleState.ACTIVE
            ]
            contested = [
                member
                for member in current
                if member.memory.lifecycle_state is LifecycleState.CONTESTED
            ]
            slot_keys = {member.memory.slot_key for member in members}
            if len(slot_keys) != 1 or len(active) != 1 or len(contested) != 1:
                raise ContestedGroupInvariantError(
                    f"open contested group {group_id!r} must contain one active and one "
                    "contested branch in one slot"
                )

            ordered_members = sorted(
                members,
                key=lambda member: (member.memory.version, member.memory.memory_id),
            )
            versions = [member.memory.version for member in ordered_members]
            if len(ordered_members) < 2 or len(versions) != len(set(versions)):
                raise ContestedGroupInvariantError(
                    f"contested group {group_id!r} must have a unique version chain"
                )
            # The first grouped row is the pre-existing active branch. The second
            # is the branch created when CONTESTED opened. Both remain in the
            # append-only version history even after either branch advances.
            opened_at = ordered_members[1].memory.valid_from
            review_due_at = opened_at + review_after
            if evaluated_at < review_due_at:
                continue
            due_items.append(
                ManualReviewItem(
                    scope=scope,
                    slot_key=next(iter(slot_keys)),
                    contested_group_id=group_id,
                    active_memory_id=active[0].memory.memory_id,
                    contested_memory_id=contested[0].memory.memory_id,
                    opened_at=opened_at,
                    review_due_at=review_due_at,
                )
            )

        return tuple(
            sorted(
                due_items,
                key=lambda item: (
                    item.review_due_at,
                    item.slot_key,
                    item.contested_group_id,
                ),
            )
        )


__all__ = [
    "ContestedGroupInvariantError",
    "ManualReviewItem",
    "ManualReviewQueue",
]
