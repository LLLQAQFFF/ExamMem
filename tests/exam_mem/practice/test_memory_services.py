from __future__ import annotations

from datetime import datetime, timezone

import pytest

from exam_mem.contracts import LearningEvent, LifecycleDecision, MemoryScope
from exam_mem.domain import load_taxonomy
from exam_mem.lifecycle import ProjectionRefreshRequest
from exam_mem.practice import (
    DiagnosisResult,
    GradeResult,
    MemoryReader,
    MemoryWriter,
    PracticeMemoryCandidateBuilder,
)

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, tzinfo=timezone.utc)
KNOWLEDGE_POINT_ID = "math1.linear_algebra.matrix_rank"
SCOPE = MemoryScope(
    user_id="practice_memory_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
    memory_namespace="mastery",
)


def _event(*, correct: bool = False) -> LearningEvent:
    return LearningEvent.model_validate(
        {
            "event_id": "practice_memory_event_001",
            "idempotency_key": "practice-memory-idempotency-001",
            "event_type": "answer_attempt",
            "context": SCOPE.model_dump(exclude={"memory_namespace"}),
            "session_id": "practice_memory_session",
            "question_id": "practice_memory_question",
            "knowledge_point_ids": [KNOWLEDGE_POINT_ID],
            "difficulty": 0.5,
            "answer_correct": correct,
            "error_type": None if correct else "concept_confusion",
            "error_detail": None if correct else "rank definition was confused",
            "occurred_at": NOW,
        }
    )


def _grade(*, correct: bool = False) -> GradeResult:
    return GradeResult(
        correct=correct,
        score=1.0 if correct else 0.2,
        matched_rubric_items=["definition"] if correct else [],
        missed_rubric_items=[] if correct else ["definition"],
        evidence=["used the correct rank definition"] if correct else ["used row count"],
        grader_version="answer_grader_v1",
    )


def _diagnosis(*, correct: bool = False) -> DiagnosisResult:
    return DiagnosisResult(
        knowledge_point_ids=[KNOWLEDGE_POINT_ID],
        error_type=None if correct else "concept_confusion",
        explanation="No stable error" if correct else "Confused rank with row count",
        confidence=0.9,
        analyzer_version="error_analyzer_v1",
    )


class FakeProjectionBackend:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.request = ProjectionRefreshRequest(
            decision_id="practice_memory_decision_001",
            context=_event().context,
        )

    async def record_event(self, event):  # noqa: ANN001, ANN201
        self.calls.append(f"record:{event.event_id}")

    async def update(self, event, candidates):  # noqa: ANN001, ANN201
        self.calls.append(f"update:{event.event_id}:{len(candidates)}")
        return [
            LifecycleDecision(
                operation="ADD",
                target_memory_ids=[],
                reason_code="test_add",
                confidence=1.0,
                policy_version="lifecycle_policy_v1",
            )
        ]

    async def query_state(self, context):  # noqa: ANN001, ANN201
        self.calls.append("query_state")
        return None

    async def retrieve(self, scope, query, top_k):  # noqa: ANN001, ANN201
        self.calls.append(f"retrieve:{top_k}")
        return []

    async def snapshot(self, context):  # noqa: ANN001, ANN201
        self.calls.append("snapshot")
        return {"backend_mode": "fake"}

    def take_projection_requests(self) -> tuple[ProjectionRefreshRequest, ...]:
        return (self.request,)


class FakeProjectionRefresher:
    def __init__(self) -> None:
        self.requests: list[ProjectionRefreshRequest] = []

    async def refresh(self, request: ProjectionRefreshRequest) -> None:
        self.requests.append(request)


async def test_memory_writer_preserves_backend_order_and_post_commit_boundary() -> None:
    backend = FakeProjectionBackend()
    refresher = FakeProjectionRefresher()
    writer = MemoryWriter(backend, projection_refresher=refresher)
    candidates = PracticeMemoryCandidateBuilder(load_taxonomy("math1_v1")).build(
        event=_event(),
        grade=_grade(),
        diagnosis=_diagnosis(),
    )

    result = await writer.write(_event(), candidates)

    assert backend.calls == [
        "record:practice_memory_event_001",
        "update:practice_memory_event_001:2",
    ]
    assert refresher.requests == []
    assert len(result.decisions) == 1
    assert result.projection_requests == (backend.request,)

    await writer.refresh_after_commit(result)
    assert refresher.requests == [backend.request]


async def test_memory_reader_delegates_without_cross_scope_reconstruction() -> None:
    backend = FakeProjectionBackend()
    reader = MemoryReader(backend)

    assert await reader.query_state(_event().context) is None
    assert await reader.retrieve(SCOPE, "query", 3) == []
    assert await reader.snapshot(_event().context) == {"backend_mode": "fake"}
    assert backend.calls == ["query_state", "retrieve:3", "snapshot"]


@pytest.mark.parametrize(
    ("correct", "expected_slots"),
    [
        (True, [f"mastery:{KNOWLEDGE_POINT_ID}"]),
        (
            False,
            [
                f"mastery:{KNOWLEDGE_POINT_ID}",
                f"error_pattern:{KNOWLEDGE_POINT_ID}:concept_confusion",
            ],
        ),
    ],
)
async def test_candidate_builder_is_deterministic_and_taxonomy_bound(
    correct: bool,
    expected_slots: list[str],
) -> None:
    candidates = PracticeMemoryCandidateBuilder(load_taxonomy("math1_v1")).build(
        event=_event(correct=correct),
        grade=_grade(correct=correct),
        diagnosis=_diagnosis(correct=correct),
    )

    assert [candidate.slot_key for candidate in candidates] == expected_slots
    assert all(candidate.event_id == _event().event_id for candidate in candidates)
    assert all(candidate.scope.user_id == SCOPE.user_id for candidate in candidates)
