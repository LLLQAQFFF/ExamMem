from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from exam_mem.backends.lifecycle import LifecycleMemoryBackend
from exam_mem.config import ExamMemSettings
from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MemoryScope,
    PlanStatus,
    PlanTransitionSource,
    PlanValue,
)
from exam_mem.lifecycle import (
    LifecycleCandidateSnapshot,
    LifecycleMemorySnapshot,
    LifecyclePolicyInput,
    decide_lifecycle,
)
from exam_mem.practice.memory import MemoryWriter, MemoryWriteResult
from exam_mem.practice.plan_transitions import (
    PlanTransitionError,
    PlanTransitionService,
    PracticeProgressTransitionRequest,
    ResolvedPlanTarget,
    SystemPlanExpirationRequest,
    UserPlanCancellationRequest,
    recognize_plan_cancellation_intent,
)
from exam_mem.practice.provider import (
    PracticeRuntimeConfigurationError,
    PracticeRuntimeProvider,
    _plan_sources_by_knowledge_point,
    _recommendation_candidate,
)
from exam_mem.practice.trace import PracticeSpanName, PracticeTraceRecorder
from exam_mem.storage.event_repository import AppendResult, AppendStatus

pytestmark = pytest.mark.asyncio

NOW = datetime(2026, 8, 12, 8, 0, tzinfo=timezone.utc)
CONTEXT = LearningContext(
    user_id="plan_transition_user",
    exam_id="postgraduate_entrance_exam",
    subject_id="math_1",
)
PLAN_SCOPE = MemoryScope(**CONTEXT.model_dump(), memory_namespace="plan")


def _plan_memory(**updates: object) -> LearningMemory:
    payload = {
        "memory_id": "plan_memory:v1",
        "scope": PLAN_SCOPE,
        "slot_key": "plan:postgraduate_entrance_exam:math_1",
        "value": PlanValue(
            goal="Complete the probability review plan",
            status=PlanStatus.IN_PROGRESS,
            progress=0.4,
            due_at=NOW + timedelta(days=1),
        ),
        "confidence": 1.0,
        "evidence_count": 1,
        "lifecycle_state": LifecycleState.ACTIVE,
        "version": 1,
        "valid_from": NOW - timedelta(days=2),
        "valid_to": None,
        "superseded_by": None,
        "provenance": ["plan_created_event"],
    }
    payload.update(updates)
    return LearningMemory.model_validate(payload)


class FakePlanTargetReader:
    def __init__(
        self,
        target: LearningMemory | None,
        *,
        knowledge_point_ids: tuple[str, ...] = ("math1.probability.bayes",),
    ) -> None:
        self.target = target
        self.knowledge_point_ids = knowledge_point_ids
        self.calls: list[tuple[LearningContext, str]] = []

    async def get_plan_target(
        self,
        context: LearningContext,
        target_memory_id: str,
    ) -> ResolvedPlanTarget | None:
        self.calls.append((context, target_memory_id))
        if self.target is None:
            return None
        return ResolvedPlanTarget(
            memory=self.target,
            knowledge_point_ids=self.knowledge_point_ids,
        )


class PolicyMemoryWriter:
    def __init__(self, target: LearningMemory, *, projection: bool = False) -> None:
        self.target = target
        self.projection = projection
        self.write_calls: list[tuple[object, list[object]]] = []
        self.refresh_calls: list[MemoryWriteResult] = []

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        self.write_calls.append((event, candidates))
        candidate = candidates[0]
        snapshot = LifecycleCandidateSnapshot(
            memory=self.target,
            row_version=1,
            policy_version="lifecycle_policy_v1",
        )
        decision = decide_lifecycle(
            LifecyclePolicyInput(
                event=event,
                candidate=candidate,
                candidate_snapshots=(snapshot,),
                evaluated_at=event.occurred_at,
            )
        ).decision
        return MemoryWriteResult(
            decisions=(decision,),
            projection_requests=((object(),) if self.projection else ()),  # type: ignore[arg-type]
        )

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None:
        self.refresh_calls.append(result)


class FixedMemoryWriter:
    def __init__(self, decision: LifecycleDecision) -> None:
        self.decision = decision
        self.write_calls: list[tuple[object, list[object]]] = []

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        self.write_calls.append((event, candidates))
        return MemoryWriteResult(decisions=(self.decision,), projection_requests=())

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None:
        raise AssertionError("replay must not request a projection refresh")


class FakeTraceRepository:
    def __init__(self) -> None:
        self.spans = []

    async def next_step_id(self, trace_id: str) -> int:
        return len(self.spans) + 1

    async def append(self, span):  # noqa: ANN001, ANN201
        self.spans.append(span)
        return SimpleNamespace(status=AppendStatus.CREATED)


class ExistingEventRepository:
    async def append(self, event, *, trace_id=None):  # noqa: ANN001, ANN201
        del trace_id
        return AppendResult(status=AppendStatus.EXISTING, event=event)

    async def list_after(self, context, watermark, limit):  # noqa: ANN001, ANN201
        del context, watermark, limit
        return []


class AppliedMemoryRepository:
    def __init__(self, memory: LearningMemory) -> None:
        self.memory = memory

    async def event_was_applied(self, scope, slot_key, event_id):  # noqa: ANN001, ANN201
        del scope, slot_key
        return event_id in self.memory.provenance

    async def list_slot_snapshots(self, scope, slot_key):  # noqa: ANN001, ANN201
        del scope, slot_key
        return [
            LifecycleMemorySnapshot(
                memory=self.memory,
                row_version=2,
                policy_version="lifecycle_policy_v1",
            )
        ]


class UnusedDependency:
    def __getattr__(self, name: str):
        raise AssertionError(f"replay must not call {name}")


def _base_payload() -> dict:
    return {
        "context": CONTEXT,
        "target_memory_id": "plan_memory:v1",
        "session_id": "practice:plan:001",
        "idempotency_key": "idem:plan:001",
        "knowledge_point_ids": ["math1.probability.bayes"],
        "reason": "the deterministic practice goal was reached",
        "occurred_at": NOW,
        "trace_id": "trace:plan:001",
    }


def _service(  # noqa: ANN001, ANN202
    target: LearningMemory,
    writer,
    *,
    knowledge_point_ids: tuple[str, ...] = ("math1.probability.bayes",),
):
    traces = FakeTraceRepository()
    return (
        PlanTransitionService(
            target_reader=FakePlanTargetReader(
                target,
                knowledge_point_ids=knowledge_point_ids,
            ),
            memory_writer=writer,
            trace=PracticeTraceRecorder(traces, trace_id="trace:plan:001"),
        ),
        traces,
    )


async def test_practice_progress_completion_builds_controlled_invalidation() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, traces = _service(target, writer)
    request = PracticeProgressTransitionRequest(**_base_payload(), progress=1.0)

    result = await service.apply_practice_progress(request)

    transition = result.event.plan_transition
    assert transition is not None
    assert transition.to_status is PlanStatus.COMPLETED
    assert transition.source is PlanTransitionSource.PRACTICE_PROGRESS
    assert result.candidate.proposed_value == target.value.model_copy(
        update={"status": PlanStatus.COMPLETED, "progress": 1.0}
    )
    assert result.memory_result.decisions[0].operation is LifecycleOperation.INVALIDATE
    assert [span.name for span in traces.spans] == [
        PracticeSpanName.PLAN_TRANSITION_APPENDED,
        PracticeSpanName.PLAN_TRANSITION_APPLIED,
    ]


async def test_ambiguous_user_cancellation_remains_contested() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, _ = _service(target, writer)
    request = UserPlanCancellationRequest(**_base_payload(), confirmed=False)

    result = await service.apply_user_cancellation(request)

    assert result.event.evidence_quality.confidence == 0.5
    assert result.event.plan_transition is not None
    assert result.event.plan_transition.source is PlanTransitionSource.USER
    assert result.memory_result.decisions[0].operation is LifecycleOperation.CONTESTED


async def test_confirmed_user_cancellation_is_a_controlled_invalidation() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, _ = _service(target, writer)
    request = UserPlanCancellationRequest(**_base_payload(), confirmed=True)

    result = await service.apply_user_cancellation(request)

    assert result.event.evidence_quality.confidence == 1.0
    assert result.memory_result.decisions[0].operation is LifecycleOperation.INVALIDATE


async def test_system_expiration_uses_stored_due_date_and_refreshes_l3() -> None:
    target = _plan_memory(
        value=_plan_memory().value.model_copy(update={"due_at": NOW - timedelta(seconds=1)})
    )
    writer = PolicyMemoryWriter(target, projection=True)
    service, traces = _service(target, writer)
    request = SystemPlanExpirationRequest(**_base_payload())

    result = await service.apply_system_expiration(request)

    assert result.event.plan_transition is not None
    assert result.event.plan_transition.source is PlanTransitionSource.SYSTEM
    assert result.memory_result.decisions[0].operation is LifecycleOperation.INVALIDATE
    assert writer.refresh_calls == [result.memory_result]
    assert traces.spans[-1].name is PracticeSpanName.STUDENT_MODEL_PROJECTED


async def test_system_refuses_to_expire_a_plan_before_its_stored_due_date() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, traces = _service(target, writer)
    request = SystemPlanExpirationRequest(**_base_payload())

    with pytest.raises(PlanTransitionError) as raised:
        await service.apply_system_expiration(request)

    assert raised.value.error_code == "plan_not_expired"
    assert writer.write_calls == []
    assert traces.spans == []


async def test_cross_scope_target_is_rejected_before_memory_write() -> None:
    target = _plan_memory(scope=PLAN_SCOPE.model_copy(update={"user_id": "another_user"}))
    writer = PolicyMemoryWriter(target)
    service, _ = _service(target, writer)

    with pytest.raises(PlanTransitionError) as raised:
        await service.apply_user_cancellation(
            UserPlanCancellationRequest(**_base_payload(), confirmed=True)
        )

    assert raised.value.error_code == "plan_target_scope_mismatch"
    assert writer.write_calls == []


async def test_historical_target_is_accepted_only_for_same_event_replay() -> None:
    first_target = _plan_memory()
    first_writer = PolicyMemoryWriter(first_target)
    first_service, _ = _service(first_target, first_writer)
    request = PracticeProgressTransitionRequest(**_base_payload(), progress=1.0)
    first = await first_service.apply_practice_progress(request)
    historical = _plan_memory(
        lifecycle_state=LifecycleState.INVALIDATED,
        valid_to=NOW,
        provenance=["plan_created_event", first.event.event_id],
    )
    replay_writer = FixedMemoryWriter(
        LifecycleDecision(
            operation=LifecycleOperation.NO_OP,
            target_memory_ids=[historical.memory_id],
            reason_code="already_applied_replay",
            confidence=1.0,
            policy_version="lifecycle_policy_v1",
        )
    )
    replay_service, _ = _service(historical, replay_writer)

    replay = await replay_service.apply_practice_progress(request)

    assert replay.memory_result.decisions[0].operation is LifecycleOperation.NO_OP


async def test_lifecycle_backend_replay_short_circuits_before_candidate_lookup() -> None:
    target = _plan_memory()
    writer = FixedMemoryWriter(
        LifecycleDecision(
            operation=LifecycleOperation.INVALIDATE,
            target_memory_ids=[target.memory_id],
            reason_code="plan_completed",
            confidence=1.0,
            policy_version="lifecycle_policy_v1",
        )
    )
    service, _ = _service(target, writer)
    built = await service.apply_practice_progress(
        PracticeProgressTransitionRequest(**_base_payload(), progress=1.0)
    )
    historical = _plan_memory(
        lifecycle_state=LifecycleState.INVALIDATED,
        valid_to=NOW,
        provenance=["plan_created_event", built.event.event_id],
    )
    backend = LifecycleMemoryBackend(
        event_repository=ExistingEventRepository(),
        memory_repository=AppliedMemoryRepository(historical),  # type: ignore[arg-type]
        student_model_repository=UnusedDependency(),  # type: ignore[arg-type]
        relation_classifier=UnusedDependency(),  # type: ignore[arg-type]
        applier=UnusedDependency(),  # type: ignore[arg-type]
        trace_id=built.event.event_id,
    )

    replay = await MemoryWriter(backend).write(built.event, [built.candidate])

    assert replay.decisions[0].operation is LifecycleOperation.NO_OP
    assert replay.decisions[0].target_memory_ids == [historical.memory_id]
    assert replay.projection_requests == ()


async def test_invalid_taxonomy_leaf_is_rejected_before_target_content_is_used() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, _ = _service(
        target,
        writer,
        knowledge_point_ids=("free_form.created.by.llm",),
    )
    payload = _base_payload()
    payload["knowledge_point_ids"] = ["free_form.created.by.llm"]

    with pytest.raises(PlanTransitionError) as raised:
        await service.apply_user_cancellation(
            UserPlanCancellationRequest(**payload, confirmed=True)
        )

    assert raised.value.error_code == "plan_knowledge_point_invalid"
    assert writer.write_calls == []


async def test_request_cannot_replace_authoritative_plan_knowledge_points() -> None:
    target = _plan_memory()
    writer = PolicyMemoryWriter(target)
    service, _ = _service(target, writer)
    payload = _base_payload()
    payload["knowledge_point_ids"] = ["math1.linear_algebra.determinant"]

    with pytest.raises(PlanTransitionError) as raised:
        await service.apply_user_cancellation(
            UserPlanCancellationRequest(**payload, confirmed=True)
        )

    assert raised.value.error_code == "plan_knowledge_point_mismatch"
    assert writer.write_calls == []


def test_plan_cancellation_intent_distinguishes_confirmation_without_selecting_target() -> None:
    confirmed = recognize_plan_cancellation_intent("取消计划：概率复习")
    uncertain = recognize_plan_cancellation_intent("可能要取消计划：概率复习")

    assert confirmed is not None
    assert confirmed.query == "概率复习"
    assert confirmed.confirmed is True
    assert uncertain is not None
    assert uncertain.query == "概率复习"
    assert uncertain.confirmed is False
    assert recognize_plan_cancellation_intent("继续下一题") is None


async def test_provider_refuses_plan_transition_instead_of_switching_backend() -> None:
    provider = PracticeRuntimeProvider(
        settings=ExamMemSettings.model_validate({"memory_backend": "append_only"})
    )

    with pytest.raises(PracticeRuntimeConfigurationError, match="require.*lifecycle"):
        async with provider.open_plan_transitions(trace_id="trace:plan:001"):
            pass


async def test_active_plan_priority_comes_from_scoped_provenance_not_goal_text() -> None:
    plan = _plan_memory(
        value=_plan_memory().value.model_copy(
            update={"goal": "Free text deliberately contains no taxonomy identifier"}
        )
    )
    source_event = LearningEvent(
        event_id="plan_created_event",
        idempotency_key="idem:plan-created-event",
        context=CONTEXT,
        session_id="practice:plan:001",
        question_id="question:plan-created-event",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.4,
        answer_correct=True,
        occurred_at=NOW - timedelta(days=2),
    )

    sources = _plan_sources_by_knowledge_point([plan], [source_event])
    candidate = _recommendation_candidate(
        knowledge_point_id="math1.probability.bayes",
        model=None,
        memories=(),
        plan_memories=sources["math1.probability.bayes"],
    )

    assert candidate.features.active_plan_priority == 1.0
    assert candidate.source_memories == (plan,)
