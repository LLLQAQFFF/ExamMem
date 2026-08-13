from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from exam_mem.contracts import LearningMemory, LifecycleState, MemoryScope
from exam_mem.lifecycle import (
    ContestedGroupInvariantError,
    LifecycleMemorySnapshot,
    ManualReviewQueue,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.lifecycle, pytest.mark.contested]

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=timezone.utc)
SCOPE = MemoryScope(
    user_id="stage06_review_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)
SLOT_KEY = "mastery:math1.linear_algebra.matrix_rank"


class _MemoryRepository:
    def __init__(self, snapshots: tuple[LifecycleMemorySnapshot, ...]) -> None:
        self.snapshots = snapshots
        self.scopes: list[MemoryScope] = []

    async def list_contested_group_snapshots(
        self,
        scope: MemoryScope,
    ) -> list[LifecycleMemorySnapshot]:
        self.scopes.append(scope)
        return list(self.snapshots)


def _snapshot(
    *,
    memory_id: str,
    state: LifecycleState,
    version: int,
    group_id: str,
    valid_from: datetime,
    slot_key: str = SLOT_KEY,
) -> LifecycleMemorySnapshot:
    terminal = state in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
    return LifecycleMemorySnapshot(
        memory=LearningMemory.model_validate(
            {
                "memory_id": memory_id,
                "scope": SCOPE.model_dump(mode="json"),
                "slot_key": slot_key,
                "value": {"type": "mastery", "level": "high", "score": 0.9},
                "confidence": 0.9,
                "evidence_count": 1,
                "lifecycle_state": state.value,
                "version": version,
                "valid_from": valid_from,
                "valid_to": NOW if terminal else None,
                "superseded_by": None,
                "provenance": [f"event:{memory_id}"],
            }
        ),
        row_version=1,
        contested_group_id=group_id,
        policy_version="lifecycle_policy_v1",
    )


async def test_overdue_group_is_derived_from_original_group_age_without_auto_resolution() -> None:
    group_id = "stage06_review_group"
    opened_at = NOW - timedelta(days=31)
    original_active = _snapshot(
        memory_id="stage06_review_original_active_v1",
        state=LifecycleState.ARCHIVED,
        version=1,
        group_id=group_id,
        valid_from=NOW - timedelta(days=90),
    )
    original_contested = _snapshot(
        memory_id="stage06_review_original_contested_v2",
        state=LifecycleState.ARCHIVED,
        version=2,
        group_id=group_id,
        valid_from=opened_at,
    )
    active = _snapshot(
        memory_id="stage06_review_active_v3",
        state=LifecycleState.ACTIVE,
        version=3,
        group_id=group_id,
        valid_from=NOW - timedelta(days=2),
    )
    contested = _snapshot(
        memory_id="stage06_review_contested_v4",
        state=LifecycleState.CONTESTED,
        version=4,
        group_id=group_id,
        valid_from=NOW - timedelta(days=1),
    )
    repository = _MemoryRepository((original_active, original_contested, active, contested))

    items = await ManualReviewQueue(repository).list_due(SCOPE, evaluated_at=NOW)

    assert repository.scopes == [SCOPE]
    assert len(items) == 1
    assert items[0].contested_group_id == group_id
    assert items[0].active_memory_id == active.memory.memory_id
    assert items[0].contested_memory_id == contested.memory.memory_id
    assert items[0].opened_at == opened_at
    assert items[0].review_due_at == opened_at + timedelta(days=30)
    assert active.memory.lifecycle_state is LifecycleState.ACTIVE
    assert contested.memory.lifecycle_state is LifecycleState.CONTESTED


async def test_group_is_due_at_exact_deadline_but_not_before() -> None:
    opened_at = NOW - timedelta(days=30)
    snapshots = (
        _snapshot(
            memory_id="stage06_deadline_active",
            state=LifecycleState.ACTIVE,
            version=1,
            group_id="stage06_deadline_group",
            valid_from=opened_at,
        ),
        _snapshot(
            memory_id="stage06_deadline_contested",
            state=LifecycleState.CONTESTED,
            version=2,
            group_id="stage06_deadline_group",
            valid_from=opened_at,
        ),
    )
    queue = ManualReviewQueue(_MemoryRepository(snapshots))

    assert await queue.list_due(SCOPE, evaluated_at=NOW - timedelta(microseconds=1)) == ()
    assert len(await queue.list_due(SCOPE, evaluated_at=NOW)) == 1


async def test_closed_group_is_not_returned() -> None:
    closed = (
        _snapshot(
            memory_id="stage06_closed_active",
            state=LifecycleState.ARCHIVED,
            version=1,
            group_id="stage06_closed_group",
            valid_from=NOW - timedelta(days=40),
        ),
        _snapshot(
            memory_id="stage06_closed_contested",
            state=LifecycleState.ARCHIVED,
            version=2,
            group_id="stage06_closed_group",
            valid_from=NOW - timedelta(days=39),
        ),
    )

    assert (
        await ManualReviewQueue(_MemoryRepository(closed)).list_due(
            SCOPE,
            evaluated_at=NOW,
        )
        == ()
    )


async def test_malformed_open_group_is_rejected_instead_of_guessing() -> None:
    only_active = _snapshot(
        memory_id="stage06_malformed_active",
        state=LifecycleState.ACTIVE,
        version=1,
        group_id="stage06_malformed_group",
        valid_from=NOW - timedelta(days=31),
    )

    with pytest.raises(ContestedGroupInvariantError, match="one active and one contested"):
        await ManualReviewQueue(_MemoryRepository((only_active,))).list_due(
            SCOPE,
            evaluated_at=NOW,
        )


async def test_review_time_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        await ManualReviewQueue(_MemoryRepository(())).list_due(
            SCOPE,
            evaluated_at=datetime(2026, 8, 11),
        )
