from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from deeptutor.core.context import UnifiedContext
from deeptutor.core.stream import StreamEventType
from deeptutor.core.stream_bus import StreamBus
from deeptutor.runtime.orchestrator import ChatOrchestrator
from deeptutor.runtime.registry.capability_registry import CapabilityRegistry
from deeptutor.runtime.registry.tool_registry import ToolRegistry
from exam_mem.contracts import (
    ErrorPatternValue,
    LearningMemory,
    LifecycleState,
    MemoryScope,
    PlanValue,
)
from exam_mem.practice import (
    PRACTICE_CONTEXT_METADATA_KEY,
    ExamPracticeCapability,
    PracticeCapabilityInputError,
    PracticeContext,
    PracticeState,
    PracticeWorkflowCheckpoint,
    PracticeWorkflowResult,
    Question,
    Recommendation,
)
from exam_mem.practice.memory_workbench import LearningMemorySummary

pytestmark = pytest.mark.asyncio


def _question() -> Question:
    return Question(
        question_id="question:capability:001",
        stem="Visible question stem.",
        knowledge_point_ids=["math1.probability.bayes"],
        difficulty=0.4,
        reference_answer="SECRET REFERENCE ANSWER",
        grading_rubric={"secret": "SECRET RUBRIC"},
    )


def _workflow_result() -> PracticeWorkflowResult:
    question = _question()
    context = PracticeContext(
        practice_session_id="practice:capability:001",
        scope={
            "user_id": "capability_user",
            "exam_id": "postgraduate_entrance_exam",
            "subject_id": "math_1",
            "memory_namespace": "mastery",
        },
        current_question=question,
        step_state=PracticeState.QUESTION_READY,
        trace_id="trace:capability:001",
    )
    checkpoint = PracticeWorkflowCheckpoint(
        checkpoint_key="start",
        context=context,
        recommendation=Recommendation(
            question_id=question.question_id,
            target_knowledge_point_id="math1.probability.bayes",
            target_difficulty=question.difficulty,
            reason_codes=["syllabus_fallback"],
            source_memory_ids=[],
            policy_version="recommendation_policy_v1",
        ),
        recommended_question=question,
    )
    return PracticeWorkflowResult(
        checkpoint=checkpoint,
        resumed_from_state=PracticeState.IDLE,
        replayed=False,
    )


def test_manifest_exposes_public_config_without_requiring_questions_for_corrections() -> None:
    schema = ExamPracticeCapability.manifest.request_schema

    assert schema["required"] == [PRACTICE_CONTEXT_METADATA_KEY]
    assert "exam_practice_questions" in schema["properties"]
    assert schema["additionalProperties"] is False


async def test_capability_uses_deeptutor_result_envelope_without_leaking_answers() -> None:
    workflow = AsyncMock()
    workflow.run.return_value = _workflow_result()
    capability = ExamPracticeCapability(workflow)
    bus = StreamBus()
    context = UnifiedContext(
        config_overrides={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:capability:001",
                "scope": {
                    "user_id": "capability_user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:capability:001",
            }
        }
    )

    await capability.run(context, bus)
    await bus.close()
    events = [event async for event in bus.subscribe()]

    result = next(event for event in events if event.type is StreamEventType.RESULT)
    serialized = str(result.metadata)
    assert result.source == "exam_practice"
    assert result.metadata["practice"]["step_state"] == "QUESTION_READY"
    assert "SECRET REFERENCE ANSWER" not in serialized
    assert "SECRET RUBRIC" not in serialized


async def test_capability_rejects_missing_structured_context() -> None:
    capability = ExamPracticeCapability(AsyncMock())

    with pytest.raises(PracticeCapabilityInputError):
        await capability.run(UnifiedContext(), StreamBus())


async def test_orchestrator_resolves_exam_practice_through_capability_registry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workflow = AsyncMock()
    workflow.run.return_value = _workflow_result()
    registry = CapabilityRegistry()
    registry.register(ExamPracticeCapability(workflow))
    orchestrator = ChatOrchestrator.__new__(ChatOrchestrator)
    orchestrator._cap_registry = registry
    orchestrator._tool_registry = ToolRegistry()
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    monkeypatch.setattr(
        "deeptutor.runtime.orchestrator.get_event_bus",
        lambda: event_bus,
    )

    context = UnifiedContext(
        active_capability="exam_practice",
        config_overrides={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:capability:001",
                "scope": {
                    "user_id": "untrusted-client-user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:capability:001",
            }
        },
    )

    events = [event async for event in orchestrator.handle(context)]

    workflow.run.assert_awaited_once()
    called_context = workflow.run.await_args.args[0]
    assert called_context.scope.user_id == "local-admin"
    assert any(event.type is StreamEventType.RESULT for event in events)
    assert events[-1].type is StreamEventType.DONE
    assert events[-1].source == "exam_practice"


async def test_chat_correction_intent_returns_scoped_candidates_without_writing() -> None:
    memory = LearningMemory(
        memory_id="capability:correction:v1",
        scope=MemoryScope(
            user_id="local-admin",
            exam_id="postgraduate_entrance_exam",
            subject_id="math_1",
            memory_namespace="error_pattern",
        ),
        slot_key="error_pattern:math1.probability.bayes:formula_misuse",
        value=ErrorPatternValue(
            error_type="formula_misuse",
            summary="Bayes formula was misused.",
        ),
        confidence=0.9,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from="2026-08-12T00:00:00Z",
        valid_to=None,
        superseded_by=None,
        provenance=["event:capability:correction"],
    )
    queries = AsyncMock()
    queries.list_memories.side_effect = [
        (),
        (LearningMemorySummary(memory=memory, correction_allowed=True),),
        (),
    ]
    trace = MagicMock()
    trace.start.return_value = MagicMock()
    trace.completed = AsyncMock()

    class CorrectionIntentRuntimeFactory:
        @asynccontextmanager
        async def open_learning_memories(self, *, trace_id):  # noqa: ANN001, ANN202
            assert trace_id == "trace:capability:001"
            yield SimpleNamespace(queries=queries, trace=trace)

    capability = ExamPracticeCapability(
        runtime_factory=CorrectionIntentRuntimeFactory()  # type: ignore[arg-type]
    )
    bus = StreamBus()
    context = UnifiedContext(
        user_message="你记错了：Bayes formula",
        config_overrides={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:capability:001",
                "scope": {
                    "user_id": "untrusted-client-user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:capability:001",
            }
        },
    )

    await capability.run(context, bus)
    await bus.close()
    events = [event async for event in bus.subscribe()]

    result = next(event for event in events if event.type is StreamEventType.RESULT)
    intent = result.metadata["correction_intent"]
    assert intent["requires_confirmation"] is True
    assert [item["memory_id"] for item in intent["candidates"]] == [memory.memory_id]
    assert all(
        call.args[0].context.user_id == "local-admin"
        for call in queries.list_memories.await_args_list
    )
    trace.completed.assert_awaited_once()


@pytest.mark.parametrize(
    ("message", "confirmed"),
    [
        ("取消计划：概率复习", True),
        ("可能要取消计划：概率复习", False),
    ],
)
async def test_chat_plan_cancellation_returns_scoped_candidates_without_writing(
    message: str,
    confirmed: bool,
) -> None:
    memory = LearningMemory(
        memory_id="capability:plan:v1",
        scope=MemoryScope(
            user_id="local-admin",
            exam_id="postgraduate_entrance_exam",
            subject_id="math_1",
            memory_namespace="plan",
        ),
        slot_key="plan:postgraduate_entrance_exam:math_1",
        value=PlanValue(
            goal="概率复习",
            status="in_progress",
            progress=0.4,
        ),
        confidence=1.0,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from="2026-08-12T00:00:00Z",
        valid_to=None,
        superseded_by=None,
        provenance=["event:capability:plan"],
    )
    queries = AsyncMock()
    queries.list_memories.return_value = (
        LearningMemorySummary(memory=memory, correction_allowed=True),
    )
    trace = MagicMock()
    trace.start.return_value = MagicMock()
    trace.completed = AsyncMock()

    class PlanIntentRuntimeFactory:
        @asynccontextmanager
        async def open_learning_memories(self, *, trace_id):  # noqa: ANN001, ANN202
            assert trace_id == "trace:capability:001"
            yield SimpleNamespace(queries=queries, trace=trace)

    capability = ExamPracticeCapability(
        runtime_factory=PlanIntentRuntimeFactory()  # type: ignore[arg-type]
    )
    bus = StreamBus()
    context = UnifiedContext(
        user_message=message,
        config_overrides={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:capability:001",
                "scope": {
                    "user_id": "untrusted-client-user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:capability:001",
            }
        },
    )

    await capability.run(context, bus)
    await bus.close()
    events = [event async for event in bus.subscribe()]

    result = next(event for event in events if event.type is StreamEventType.RESULT)
    intent = result.metadata["plan_transition_intent"]
    assert intent["confirmed"] is confirmed
    assert intent["requires_confirmation"] is True
    assert [item["memory_id"] for item in intent["candidates"]] == [memory.memory_id]
    request = queries.list_memories.await_args.args[0]
    assert request.context.user_id == "local-admin"
    assert request.memory_namespace.value == "plan"
    trace.completed.assert_awaited_once()


async def test_public_config_is_the_only_practice_input_channel() -> None:
    workflow = AsyncMock()
    workflow.run.return_value = _workflow_result()
    capability = ExamPracticeCapability(workflow)
    context = UnifiedContext(
        config_overrides={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:config:001",
                "scope": {
                    "user_id": "untrusted-config-user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:config:001",
            }
        },
        metadata={
            PRACTICE_CONTEXT_METADATA_KEY: {
                "practice_session_id": "practice:metadata:must-not-win",
                "scope": {
                    "user_id": "metadata-user",
                    "exam_id": "postgraduate_entrance_exam",
                    "subject_id": "math_1",
                    "memory_namespace": "mastery",
                },
                "step_state": "IDLE",
                "trace_id": "trace:metadata:must-not-win",
            }
        },
    )

    await capability.run(context, StreamBus())

    called_context = workflow.run.await_args.args[0]
    assert called_context.practice_session_id == "practice:config:001"
    assert called_context.trace_id == "trace:config:001"
    assert called_context.scope.user_id == "local-admin"
