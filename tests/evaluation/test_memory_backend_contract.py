from __future__ import annotations

from typing import Any

from pydantic import ValidationError
import pytest

from exam_mem.backends import BackendMode, MemoryBackend
from exam_mem.contracts import ErrorPatternValue, MemoryUpdateCandidate, StudentModel


def _candidate_payload() -> dict[str, Any]:
    return {
        "event_id": "event_001",
        "scope": {
            "user_id": "user_001",
            "exam_id": "postgraduate_math_1",
            "subject_id": "linear_algebra",
            "memory_namespace": "mastery",
        },
        "slot_key": "mastery:linear_algebra:eigenvalue",
        "proposed_value": {
            "type": "mastery",
            "level": "improving",
            "score": 0.68,
        },
        "evidence": {"answer_correct": True, "question_id": "question_001"},
    }


@pytest.mark.protocol
@pytest.mark.schema
def test_candidate_preserves_typed_value_at_backend_boundary() -> None:
    candidate = MemoryUpdateCandidate.model_validate(_candidate_payload())

    assert candidate.proposed_value.type == "mastery"
    assert candidate.model_dump(mode="json")["evidence"]["answer_correct"] is True


@pytest.mark.protocol
@pytest.mark.schema
def test_candidate_rejects_value_from_another_namespace() -> None:
    payload = _candidate_payload()
    payload["proposed_value"] = {
        "type": "plan",
        "goal": "review eigenvalues",
        "status": "planned",
        "progress": 0.0,
    }

    with pytest.raises(ValidationError, match="candidate value type must match"):
        MemoryUpdateCandidate.model_validate(payload)


@pytest.mark.protocol
@pytest.mark.schema
def test_student_model_rejects_contradictory_projection() -> None:
    payload = {
        "context": {
            "user_id": "user_001",
            "exam_id": "postgraduate_math_1",
            "subject_id": "linear_algebra",
        },
        "weak_points": ["linear_algebra.eigenvalue"],
        "mastered_points": ["linear_algebra.eigenvalue"],
        "stable_error_patterns": [],
        "active_plans": [],
        "projection_version": 1,
        "source_watermark": "event_001",
    }

    with pytest.raises(ValidationError, match="must be disjoint"):
        StudentModel.model_validate(payload)


@pytest.mark.protocol
def test_backend_modes_are_frozen_and_protocol_is_structural() -> None:
    class StructurallyCompatibleBackend:
        async def record_event(self, event: object) -> None: ...

        async def update(self, event: object, candidates: list[object]) -> list[object]:
            return []

        async def query_state(self, context: object) -> None:
            return None

        async def retrieve(self, scope: object, query: str, top_k: int) -> list[object]:
            return []

        async def snapshot(self, context: object) -> dict[str, object]:
            return {}

    assert {mode.value for mode in BackendMode} == {
        "none",
        "native",
        "append_only",
        "vector",
        "lifecycle",
    }
    assert isinstance(StructurallyCompatibleBackend(), MemoryBackend)


@pytest.mark.protocol
@pytest.mark.schema
def test_error_pattern_preserves_details_beyond_prompt_projection_limit() -> None:
    value = ErrorPatternValue.model_validate(
        {
            "type": "error_pattern",
            "error_type": "condition_omission",
            "summary": "忽略条件事件",
            "details": [f"canonical detail {index}" for index in range(6)],
        }
    )

    assert len(value.details) == 6
