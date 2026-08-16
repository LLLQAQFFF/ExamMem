"""DeepTutor Capability adapter for the recoverable ExamMem practice workflow."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import ValidationError

from deeptutor.plugins.host_services import (
    BaseCapability,
    CapabilityManifest,
    StreamBus,
    UnifiedContext,
    current_user_id,
    emit_capability_result,
)
from exam_mem.contracts import LearningContext, LifecycleState, MemoryNamespace

from .contracts import PracticeContext, PracticeState, Question
from .corrections import recognize_correction_intent
from .memory_workbench import LearningMemoryListRequest
from .plan_transitions import recognize_plan_cancellation_intent
from .trace import PracticeSpanName
from .workflow import ExamPracticeWorkflow, PracticeWorkflowError, PracticeWorkflowResult

PRACTICE_CONTEXT_METADATA_KEY = "exam_practice_context"
PRACTICE_QUESTIONS_CONFIG_KEY = "exam_practice_questions"


class PracticeCapabilityInputError(ValueError):
    """Raised when the real entry has no valid structured practice context."""

    error_code = "exam_practice_context_invalid"


class PracticeRuntimeHandle(Protocol):
    workflow: ExamPracticeWorkflow


class PracticeRuntimeContextManager(Protocol):
    async def __aenter__(self) -> PracticeRuntimeHandle: ...

    async def __aexit__(self, exc_type, exc, traceback) -> None: ...  # noqa: ANN001


class LearningMemoryRuntimeHandle(Protocol):
    queries: Any
    trace: Any


class LearningMemoryRuntimeContextManager(Protocol):
    async def __aenter__(self) -> LearningMemoryRuntimeHandle: ...

    async def __aexit__(self, exc_type, exc, traceback) -> None: ...  # noqa: ANN001


class PracticeRuntimeFactory(Protocol):
    def open(
        self,
        unified_context: UnifiedContext,
        practice_context: PracticeContext,
    ) -> PracticeRuntimeContextManager: ...

    def open_learning_memories(
        self,
        *,
        trace_id: str,
    ) -> LearningMemoryRuntimeContextManager: ...


class ExamPracticeCapability(BaseCapability):
    """Own one DeepTutor turn while delegating single-purpose structured tools."""

    manifest = CapabilityManifest(
        name="exam_practice",
        description="Recoverable ExamMem question-to-recommendation workflow",
        stages=[state.value for state in PracticeState],
        tools_used=[
            "question_retriever",
            "answer_grader",
            "knowledge_mapper",
            "error_analyzer",
            "memory_reader",
            "memory_writer",
            "recommendation",
        ],
        request_schema={
            "type": "object",
            "required": [PRACTICE_CONTEXT_METADATA_KEY],
            "properties": {
                PRACTICE_CONTEXT_METADATA_KEY: PracticeContext.model_json_schema(),
                PRACTICE_QUESTIONS_CONFIG_KEY: {
                    "type": "array",
                    "minItems": 1,
                    "items": Question.model_json_schema(),
                },
            },
            "additionalProperties": False,
        },
        session_surface="exam_practice",
    )

    def __init__(
        self,
        workflow: ExamPracticeWorkflow | None = None,
        *,
        runtime_factory: PracticeRuntimeFactory | None = None,
    ) -> None:
        if workflow is not None and runtime_factory is not None:
            raise ValueError("configure either workflow or runtime_factory, not both")
        self._workflow = workflow
        self._runtime_factory = runtime_factory

    async def run(self, context: UnifiedContext, stream: StreamBus) -> None:
        try:
            practice_context = _practice_context(context)
        except PracticeCapabilityInputError as exc:
            await stream.error(
                "ExamMem practice context is invalid.",
                source=self.manifest.name,
                metadata={"error_code": exc.error_code, "retryable": False},
            )
            raise
        correction_query = recognize_correction_intent(context.user_message)
        if correction_query is not None:
            await self._run_correction_intent(
                practice_context,
                correction_query,
                stream,
            )
            return
        plan_intent = recognize_plan_cancellation_intent(context.user_message)
        if plan_intent is not None:
            await self._run_plan_cancellation_intent(
                practice_context,
                plan_intent.query,
                confirmed=plan_intent.confirmed,
                stream=stream,
            )
            return
        try:
            result = await self._run_workflow(context, practice_context, stream)
        except PracticeWorkflowError as exc:
            await stream.error(
                "ExamMem practice workflow could not complete.",
                source=self.manifest.name,
                stage=exc.step_state.value,
                metadata={
                    "error_code": exc.error_code,
                    "retryable": exc.retryable,
                    "trace_id": practice_context.trace_id,
                    "practice": (
                        None
                        if exc.checkpoint is None
                        else _public_practice_checkpoint(exc.checkpoint)
                    ),
                },
            )
            raise
        except Exception as exc:
            error_code = getattr(exc, "error_code", None)
            if not isinstance(error_code, str):
                raise
            await stream.error(
                "ExamMem practice runtime is not configured for this turn.",
                source=self.manifest.name,
                stage=practice_context.step_state.value,
                metadata={
                    "error_code": error_code,
                    "retryable": False,
                    "trace_id": practice_context.trace_id,
                },
            )
            raise

        await emit_capability_result(
            stream,
            _result_payload(result),
            source=self.manifest.name,
        )

    async def _run_correction_intent(
        self,
        practice_context: PracticeContext,
        query: str,
        stream: StreamBus,
    ) -> None:
        runtime_factory = self._runtime_factory or _default_runtime_factory()
        learning_context = LearningContext(
            user_id=current_user_id(),
            exam_id=practice_context.scope.exam_id,
            subject_id=practice_context.scope.subject_id,
        )
        candidates: list[dict[str, Any]] = []
        async with runtime_factory.open_learning_memories(
            trace_id=practice_context.trace_id,
        ) as runtime:
            resolve_started = runtime.trace.start()
            for namespace in (
                MemoryNamespace.MASTERY,
                MemoryNamespace.ERROR_PATTERN,
                MemoryNamespace.PLAN,
            ):
                summaries = await runtime.queries.list_memories(
                    LearningMemoryListRequest(
                        context=learning_context,
                        memory_namespace=namespace,
                        lifecycle_states=(
                            LifecycleState.ACTIVE,
                            LifecycleState.CONTESTED,
                        ),
                        query=query or None,
                    )
                )
                candidates.extend(
                    {
                        "memory_id": item.memory.memory_id,
                        "memory_namespace": namespace.value,
                        "slot_key": item.memory.slot_key,
                        "lifecycle_state": item.memory.lifecycle_state.value,
                        "version": item.memory.version,
                        "value": item.memory.value.model_dump(mode="json"),
                    }
                    for item in summaries
                )
            await runtime.trace.completed(
                name=PracticeSpanName.CORRECTION_TARGET_RESOLVED,
                started=resolve_started,
                input_summary={
                    "query_present": bool(query),
                    "memory_namespaces": [
                        MemoryNamespace.MASTERY.value,
                        MemoryNamespace.ERROR_PATTERN.value,
                        MemoryNamespace.PLAN.value,
                    ],
                },
                output_summary={
                    "candidate_count": len(candidates),
                    "requires_confirmation": True,
                },
                related_record_ids=tuple(candidate["memory_id"] for candidate in candidates),
            )
        await emit_capability_result(
            stream,
            {
                "response": (
                    "Please confirm which Learning Memory should be corrected."
                    if candidates
                    else "No current Learning Memory matched this correction request."
                ),
                "correction_intent": {
                    "query": query,
                    "requires_confirmation": True,
                    "candidates": candidates,
                    "scope": {
                        "exam_id": learning_context.exam_id,
                        "subject_id": learning_context.subject_id,
                    },
                },
            },
            source=self.manifest.name,
        )

    async def _run_plan_cancellation_intent(
        self,
        practice_context: PracticeContext,
        query: str,
        *,
        confirmed: bool,
        stream: StreamBus,
    ) -> None:
        runtime_factory = self._runtime_factory or _default_runtime_factory()
        learning_context = LearningContext(
            user_id=current_user_id(),
            exam_id=practice_context.scope.exam_id,
            subject_id=practice_context.scope.subject_id,
        )
        async with runtime_factory.open_learning_memories(
            trace_id=practice_context.trace_id,
        ) as runtime:
            resolve_started = runtime.trace.start()
            summaries = await runtime.queries.list_memories(
                LearningMemoryListRequest(
                    context=learning_context,
                    memory_namespace=MemoryNamespace.PLAN,
                    lifecycle_states=(
                        LifecycleState.ACTIVE,
                        LifecycleState.CONTESTED,
                    ),
                    query=query or None,
                )
            )
            candidates = [
                {
                    "memory_id": item.memory.memory_id,
                    "memory_namespace": MemoryNamespace.PLAN.value,
                    "slot_key": item.memory.slot_key,
                    "lifecycle_state": item.memory.lifecycle_state.value,
                    "version": item.memory.version,
                    "value": item.memory.value.model_dump(mode="json"),
                }
                for item in summaries
            ]
            await runtime.trace.completed(
                name=PracticeSpanName.REQUEST_RECEIVED,
                started=resolve_started,
                input_summary={
                    "query_present": bool(query),
                    "operation": "resolve_plan_cancellation_target",
                },
                output_summary={
                    "candidate_count": len(candidates),
                    "requires_confirmation": True,
                },
                related_record_ids=tuple(candidate["memory_id"] for candidate in candidates),
            )
        await emit_capability_result(
            stream,
            {
                "response": (
                    "Please confirm which Plan should be cancelled."
                    if candidates
                    else "No current Plan matched this cancellation request."
                ),
                "plan_transition_intent": {
                    "kind": "user_cancellation",
                    "query": query,
                    "confirmed": confirmed,
                    "requires_confirmation": True,
                    "candidates": candidates,
                    "scope": {
                        "exam_id": learning_context.exam_id,
                        "subject_id": learning_context.subject_id,
                    },
                },
            },
            source=self.manifest.name,
        )

    async def _run_workflow(
        self,
        unified_context: UnifiedContext,
        practice_context: PracticeContext,
        stream: StreamBus,
    ) -> PracticeWorkflowResult:
        async with stream.stage(
            practice_context.step_state.value,
            source=self.manifest.name,
            metadata={"trace_id": practice_context.trace_id},
        ):
            if self._workflow is not None:
                return await self._workflow.run(practice_context, stream=stream)
            runtime_factory = self._runtime_factory or _default_runtime_factory()
            async with runtime_factory.open(unified_context, practice_context) as runtime:
                return await runtime.workflow.run(practice_context, stream=stream)


def _practice_context(context: UnifiedContext) -> PracticeContext:
    payload = _request_value(context, PRACTICE_CONTEXT_METADATA_KEY)
    if payload is None:
        raise PracticeCapabilityInputError(f"config.{PRACTICE_CONTEXT_METADATA_KEY} is required")
    try:
        practice_context = PracticeContext.model_validate(payload)
    except ValidationError as exc:
        raise PracticeCapabilityInputError(
            f"config.{PRACTICE_CONTEXT_METADATA_KEY} is invalid"
        ) from exc
    authenticated_scope = practice_context.scope.model_copy(update={"user_id": current_user_id()})
    return practice_context.model_copy(update={"scope": authenticated_scope})


def _request_value(context: UnifiedContext, key: str) -> Any:
    """Read only the public DeepTutor request-config channel."""
    return context.config_overrides.get(key)


def _default_runtime_factory() -> PracticeRuntimeFactory:
    raise PracticeCapabilityInputError(
        "exam_practice runtime_factory must be supplied by the ExamMem plugin"
    )


def _result_payload(result: PracticeWorkflowResult) -> dict[str, Any]:
    checkpoint = result.checkpoint
    question = (
        None
        if checkpoint.context.catalog_completed
        else checkpoint.recommended_question or checkpoint.context.current_question
    )
    payload: dict[str, Any] = {
        "response": _response_text(checkpoint.context.step_state, question),
        "practice": {
            "practice_session_id": checkpoint.context.practice_session_id,
            "trace_id": checkpoint.context.trace_id,
            "scope": checkpoint.context.scope.model_dump(
                mode="json",
                exclude={"user_id"},
            ),
            "step_state": checkpoint.context.step_state.value,
            "answered_question_count": len(checkpoint.context.answered_question_ids),
            "question_count": len(checkpoint.context.question_catalog),
            "completed": checkpoint.context.catalog_completed,
            "runtime": (
                None
                if checkpoint.runtime_snapshot is None
                else checkpoint.runtime_snapshot.model_dump(mode="json")
            ),
            "question": None if question is None else _public_question(question),
            "grade_result": (
                None
                if checkpoint.grade_result is None
                else checkpoint.grade_result.model_dump(mode="json")
            ),
            "grade_artifact": (
                None
                if checkpoint.grade_artifact_identity is None
                else {
                    "identity": checkpoint.grade_artifact_identity.model_dump(mode="json"),
                    "reused": checkpoint.grade_reused_from_checkpoint is not None,
                    "source_checkpoint": checkpoint.grade_reused_from_checkpoint,
                }
            ),
            "diagnosis_result": (
                None
                if checkpoint.diagnosis_result is None
                else checkpoint.diagnosis_result.model_dump(mode="json")
            ),
            "recommendation": (
                None
                if checkpoint.recommendation is None
                else checkpoint.recommendation.model_dump(mode="json")
            ),
            "resumed_from_state": result.resumed_from_state.value,
            "replayed": result.replayed,
        },
    }
    return payload


def _public_practice_checkpoint(checkpoint) -> dict[str, Any]:  # noqa: ANN001
    question = (
        None
        if checkpoint.context.catalog_completed
        else checkpoint.recommended_question or checkpoint.context.current_question
    )
    return {
        "practice_session_id": checkpoint.context.practice_session_id,
        "trace_id": checkpoint.context.trace_id,
        "scope": checkpoint.context.scope.model_dump(mode="json", exclude={"user_id"}),
        "step_state": checkpoint.context.step_state.value,
        "answered_question_count": len(checkpoint.context.answered_question_ids),
        "question_count": len(checkpoint.context.question_catalog),
        "completed": checkpoint.context.catalog_completed,
        "runtime": (
            None
            if checkpoint.runtime_snapshot is None
            else checkpoint.runtime_snapshot.model_dump(mode="json")
        ),
        "question": None if question is None else _public_question(question),
        "grade_result": (
            None
            if checkpoint.grade_result is None
            else checkpoint.grade_result.model_dump(mode="json")
        ),
        "grade_artifact": (
            None
            if checkpoint.grade_artifact_identity is None
            else {
                "identity": checkpoint.grade_artifact_identity.model_dump(mode="json"),
                "reused": checkpoint.grade_reused_from_checkpoint is not None,
                "source_checkpoint": checkpoint.grade_reused_from_checkpoint,
            }
        ),
        "diagnosis_result": (
            None
            if checkpoint.diagnosis_result is None
            else checkpoint.diagnosis_result.model_dump(mode="json")
        ),
        "recommendation": (
            None
            if checkpoint.recommendation is None
            else checkpoint.recommendation.model_dump(mode="json")
        ),
        "resumed_from_state": checkpoint.context.step_state.value,
        "replayed": False,
    }


def _public_question(question: Question) -> dict[str, Any]:
    return {
        "question_id": question.question_id,
        "stem": question.stem,
        "knowledge_point_ids": list(question.knowledge_point_ids),
        "difficulty": question.difficulty,
    }


def _response_text(state: PracticeState, question: Question | None) -> str:
    if question is None:
        return f"ExamMem practice reached {state.value}."
    return f"ExamMem practice reached {state.value}; next question: {question.question_id}."


__all__ = [
    "ExamPracticeCapability",
    "PRACTICE_CONTEXT_METADATA_KEY",
    "PRACTICE_QUESTIONS_CONFIG_KEY",
    "PracticeCapabilityInputError",
    "PracticeRuntimeFactory",
]
