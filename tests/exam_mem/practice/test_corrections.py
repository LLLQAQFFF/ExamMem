from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from exam_mem.backends.lifecycle import CorrectionTargetNotCurrent
from exam_mem.contracts import (
    ErrorPatternValue,
    ErrorType,
    LearningContext,
    LearningMemory,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
)
from exam_mem.lifecycle import LifecycleCandidateSnapshot, LifecyclePolicyInput, decide_lifecycle
from exam_mem.practice.corrections import (
    ConfirmedCorrectionRelationClassifier,
    CorrectionError,
    ExplicitCorrectionRequest,
    ExplicitCorrectionService,
    ResolvedCorrectionTarget,
    recognize_correction_intent,
)
from exam_mem.practice.memory import MemoryWriteResult
from exam_mem.practice.trace import PracticeSpanName, PracticeTraceRecorder
from exam_mem.storage.event_repository import AppendStatus

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, 10, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="correction_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)
SCOPE = MemoryScope(**CONTEXT.model_dump(), memory_namespace="error_pattern")


def _memory(*, state: LifecycleState = LifecycleState.ACTIVE) -> LearningMemory:
    return LearningMemory(
        memory_id="error_memory:v1",
        scope=SCOPE,
        slot_key="error_pattern:math1.probability.bayes:formula_misuse",
        value=ErrorPatternValue(
            error_type=ErrorType.FORMULA_MISUSE,
            summary="Always reverses the Bayes numerator.",
            details=["one observed answer"],
        ),
        confidence=0.9,
        evidence_count=1,
        lifecycle_state=state,
        version=1,
        valid_from=NOW,
        valid_to=(NOW if state is LifecycleState.INVALIDATED else None),
        superseded_by=None,
        provenance=["answer:event:1"],
    )


class FakeTargetReader:
    def __init__(self, target: LearningMemory | None) -> None:
        self.target = target
        self.calls = []

    async def get_target(self, scope, memory_id):  # noqa: ANN001, ANN201
        self.calls.append((scope, memory_id))
        if self.target is None or self.target.scope != scope:
            return None
        return ResolvedCorrectionTarget(
            memory=self.target,
            knowledge_point_ids=("math1.probability.bayes",),
        )


class PolicyCorrectionWriter:
    def __init__(self, target: LearningMemory, *, projection: bool = True) -> None:
        self.target = target
        self.projection = projection
        self.refresh_calls = []
        self.classifier = ConfirmedCorrectionRelationClassifier()

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        candidate = candidates[0]
        snapshot = LifecycleCandidateSnapshot(
            memory=self.target,
            row_version=1,
            policy_version="lifecycle_policy_v1",
        )
        relation = await self.classifier.classify(candidate, (snapshot,))
        decision = decide_lifecycle(
            LifecyclePolicyInput(
                event=event,
                candidate=candidate,
                candidate_snapshots=(snapshot,),
                relation=relation,
                evaluated_at=event.occurred_at,
            )
        ).decision
        return MemoryWriteResult(
            decisions=(decision,),
            projection_requests=((object(),) if self.projection else ()),  # type: ignore[arg-type]
        )

    async def refresh_after_commit(self, result):  # noqa: ANN001, ANN201
        self.refresh_calls.append(result)


class RejectingCorrectionWriter:
    def __init__(self) -> None:
        self.write_calls = 0

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        del event, candidates
        self.write_calls += 1
        raise CorrectionTargetNotCurrent(
            "historical terminal versions cannot receive a new correction"
        )

    async def refresh_after_commit(self, result):  # noqa: ANN001, ANN201
        raise AssertionError(f"rejected correction cannot refresh projection: {result!r}")


class FakeRecommendationRefresher:
    def __init__(self, source_ids: tuple[str, ...] = ()) -> None:
        self.source_ids = source_ids
        self.contexts = []

    async def refresh(self, context):  # noqa: ANN001, ANN201
        self.contexts.append(context)
        return self.source_ids


class FakeTraceRepository:
    def __init__(self) -> None:
        self.spans = []

    async def next_step_id(self, trace_id: str) -> int:
        return len(self.spans) + 1

    async def append(self, span):  # noqa: ANN001, ANN201
        self.spans.append(span)
        return SimpleNamespace(status=AppendStatus.CREATED)


def _request(**updates: object) -> ExplicitCorrectionRequest:
    payload = {
        "context": CONTEXT,
        "memory_namespace": "error_pattern",
        "target_memory_id": "error_memory:v1",
        "session_id": "practice:correction:1",
        "idempotency_key": "correction:1",
        "statement": "This diagnosis is not accurate.",
        "occurred_at": NOW,
        "trace_id": "trace:correction:1",
        "confirmed": True,
    }
    payload.update(updates)
    return ExplicitCorrectionRequest.model_validate(payload)


def _service(target: LearningMemory | None, writer):  # noqa: ANN001, ANN202
    traces = FakeTraceRepository()
    recommendations = FakeRecommendationRefresher(("other:active",))
    service = ExplicitCorrectionService(
        target_reader=FakeTargetReader(target),
        memory_writer=writer,
        recommendation_refresher=recommendations,
        trace=PracticeTraceRecorder(traces, trace_id="trace:correction:1"),
    )
    return service, traces, recommendations


@pytest.mark.parametrize(
    ("updates", "operation"),
    [
        ({}, LifecycleOperation.INVALIDATE),
        (
            {
                "replacement_value": ErrorPatternValue(
                    error_type=ErrorType.FORMULA_MISUSE,
                    summary="Sometimes omits the denominator.",
                    details=["confirmed by the learner"],
                )
            },
            LifecycleOperation.SUPERSEDE,
        ),
        ({"uncertain": True}, LifecycleOperation.CONTESTED),
    ],
)
async def test_confirmed_corrections_use_one_event_and_deterministic_policy(
    updates: dict,
    operation: LifecycleOperation,
) -> None:
    target = _memory()
    writer = PolicyCorrectionWriter(target)
    service, traces, recommendations = _service(target, writer)

    result = await service.apply(_request(**updates))

    assert result.event.event_type.value == "explicit_correction"
    assert result.event.correction is not None
    assert result.event.correction.target_memory_ids == [target.memory_id]
    assert result.event.knowledge_point_ids == ["math1.probability.bayes"]
    assert result.memory_result.decisions[0].operation is operation
    assert recommendations.contexts == [CONTEXT]
    assert result.recommendation_source_memory_ids == ("other:active",)
    assert [span.name for span in traces.spans] == [
        PracticeSpanName.CORRECTION_TARGET_RESOLVED,
        PracticeSpanName.CORRECTION_EVENT_APPENDED,
        PracticeSpanName.CORRECTION_LIFECYCLE_APPLIED,
        PracticeSpanName.STUDENT_MODEL_PROJECTED,
        PracticeSpanName.RECOMMENDATION_REFRESHED,
    ]


async def test_historical_target_is_transactionally_rejected_and_cross_scope_is_hidden() -> None:
    writer = RejectingCorrectionWriter()
    historical, _, _ = _service(_memory(state=LifecycleState.INVALIDATED), writer)
    with pytest.raises(CorrectionError, match="historical terminal"):
        await historical.apply(_request())
    assert writer.write_calls == 1

    missing, traces, _ = _service(None, PolicyCorrectionWriter(_memory()))
    with pytest.raises(CorrectionError, match="authenticated Scope"):
        await missing.apply(_request())
    assert traces.spans[-1].error_code == "correction_target_not_found"


def test_unconfirmed_request_and_namespace_mismatch_are_rejected() -> None:
    with pytest.raises(ValueError, match="requires user confirmation"):
        _request(confirmed=False)
    with pytest.raises(ValueError, match="type must match"):
        _request(
            replacement_value={
                "type": "mastery",
                "level": "low",
                "score": 0.1,
            }
        )


def test_chat_intent_recognition_is_read_only_and_preserves_query() -> None:
    assert recognize_correction_intent("你记错了：贝叶斯公式") == "贝叶斯公式"
    assert recognize_correction_intent("Your memory is wrong: Bayes") == "Bayes"
    assert recognize_correction_intent("继续下一题") is None


async def test_correction_refuses_missing_or_mismatched_provenance_points() -> None:
    class InvalidProvenanceReader(FakeTargetReader):
        def __init__(self, knowledge_point_ids: tuple[str, ...]) -> None:
            super().__init__(_memory())
            self.knowledge_point_ids = knowledge_point_ids

        async def get_target(self, scope, memory_id):  # noqa: ANN001, ANN201
            self.calls.append((scope, memory_id))
            assert self.target is not None
            return ResolvedCorrectionTarget(
                memory=self.target,
                knowledge_point_ids=self.knowledge_point_ids,
            )

    for points, code in (
        ((), "correction_target_provenance_invalid"),
        (("math1.linear_algebra.matrix_rank",), "correction_target_provenance_mismatch"),
    ):
        traces = FakeTraceRepository()
        service = ExplicitCorrectionService(
            target_reader=InvalidProvenanceReader(points),
            memory_writer=PolicyCorrectionWriter(_memory()),
            recommendation_refresher=FakeRecommendationRefresher(),
            trace=PracticeTraceRecorder(traces, trace_id="trace:correction:1"),
        )
        with pytest.raises(CorrectionError) as caught:
            await service.apply(_request())
        assert caught.value.error_code == code
