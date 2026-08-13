from __future__ import annotations

from datetime import datetime, timezone

from pydantic import ValidationError
import pytest

from exam_mem.backends import BackendMode
from exam_mem.contracts import MemoryScope, MemoryUpdateCandidate
from exam_mem.storage import (
    LEARNING_MEMORY_EMBEDDING_DIMENSION,
    BaselineFactRecord,
    BaselineFactRepository,
    PostgresBaselineFactRepository,
)

pytestmark = pytest.mark.repository

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)


def _candidate(
    *,
    slot_key: str = "mastery:math1.linear_algebra.matrix_rank",
) -> MemoryUpdateCandidate:
    return MemoryUpdateCandidate(
        event_id="baseline_fact_event_001",
        scope=MemoryScope(
            user_id="baseline_fact_user",
            exam_id="postgraduate_entrance_exam",
            subject_id="math_1",
            memory_namespace="mastery",
        ),
        slot_key=slot_key,
        proposed_value={"type": "mastery", "level": "low", "score": 0.3},
        evidence={"source": "stage07_repository_test"},
    )


def _basis_vector() -> tuple[float, ...]:
    return (1.0, *(0.0 for _ in range(LEARNING_MEMORY_EMBEDDING_DIMENSION - 1)))


def test_repository_implements_the_baseline_fact_port() -> None:
    repository = PostgresBaselineFactRepository(object())  # type: ignore[arg-type]

    assert isinstance(repository, BaselineFactRepository)


def test_append_only_and_vector_records_have_disjoint_embedding_shapes() -> None:
    append_only = BaselineFactRecord(
        backend_mode=BackendMode.APPEND_ONLY,
        candidate=_candidate(),
        created_at=NOW,
    )
    vector = BaselineFactRecord(
        backend_mode=BackendMode.VECTOR,
        candidate=_candidate(),
        created_at=NOW,
        content_embedding=_basis_vector(),
    )

    assert append_only.content_embedding is None
    assert vector.content_embedding == _basis_vector()

    with pytest.raises(ValidationError, match="must not contain an embedding"):
        BaselineFactRecord(
            backend_mode=BackendMode.APPEND_ONLY,
            candidate=_candidate(),
            created_at=NOW,
            content_embedding=_basis_vector(),
        )
    with pytest.raises(ValidationError, match="require an embedding"):
        BaselineFactRecord(
            backend_mode=BackendMode.VECTOR,
            candidate=_candidate(),
            created_at=NOW,
        )


@pytest.mark.parametrize(
    "backend_mode",
    [BackendMode.NONE, BackendMode.NATIVE, BackendMode.LIFECYCLE],
)
def test_non_baseline_modes_cannot_create_baseline_facts(backend_mode: BackendMode) -> None:
    with pytest.raises(ValidationError, match="require append_only or vector"):
        BaselineFactRecord(
            backend_mode=backend_mode,
            candidate=_candidate(),
            created_at=NOW,
        )


def test_record_rejects_slot_namespace_drift() -> None:
    with pytest.raises(ValidationError, match="slot_key namespace"):
        BaselineFactRecord(
            backend_mode=BackendMode.APPEND_ONLY,
            candidate=_candidate(slot_key="preference:format"),
            created_at=NOW,
        )


def test_vector_record_rejects_wrong_dimension_and_zero_vector() -> None:
    with pytest.raises(ValidationError, match="exactly 1024 dimensions"):
        BaselineFactRecord(
            backend_mode=BackendMode.VECTOR,
            candidate=_candidate(),
            created_at=NOW,
            content_embedding=(1.0,),
        )
    with pytest.raises(ValidationError, match="must be non-zero"):
        BaselineFactRecord(
            backend_mode=BackendMode.VECTOR,
            candidate=_candidate(),
            created_at=NOW,
            content_embedding=(0.0,) * LEARNING_MEMORY_EMBEDDING_DIMENSION,
        )
