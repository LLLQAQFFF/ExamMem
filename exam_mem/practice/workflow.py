"""Recoverable seven-state orchestration for one ExamMem practice turn."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
import hashlib
import json
import logging
from typing import Any, Protocol, TypeVar

from pydantic import JsonValue

from exam_mem.contracts import LearningContext, LearningEvent
from exam_mem.domain import KnowledgePointNormalizationResult
from exam_mem.storage.event_repository import AppendStatus
from exam_mem.storage.practice_checkpoint_repository import (
    PracticeCheckpointRecord,
    PracticeCheckpointRepository,
)
from exam_mem.storage.practice_trace_repository import PracticeTraceRepository

from .checkpoint import PracticeWorkflowCheckpoint, checkpoint_key_for_context
from .contracts import (
    AnswerSubmission,
    DiagnosisResult,
    GradeResult,
    PracticeContext,
    PracticeState,
    Question,
    Recommendation,
)
from .memory import MemoryWriteResult, PracticeMemoryCandidateBuilder
from .trace import PracticeSpanName, PracticeTraceRecorder

T = TypeVar("T")
logger = logging.getLogger(__name__)

_STATE_ORDER = {state: index for index, state in enumerate(PracticeState)}


class AnswerGrader(Protocol):
    async def grade(self, question: Question, submission: AnswerSubmission) -> GradeResult: ...


class KnowledgeMapper(Protocol):
    async def map(self, question: Question) -> KnowledgePointNormalizationResult: ...


class ErrorAnalyzer(Protocol):
    async def analyze(
        self,
        question: Question,
        submission: AnswerSubmission,
        grade_result: GradeResult,
        knowledge_point_ids: Sequence[str],
    ) -> DiagnosisResult: ...


class PracticeMemoryWriter(Protocol):
    async def write(self, event: LearningEvent, candidates: list) -> MemoryWriteResult: ...

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None: ...


class PracticeRecommendationTool(Protocol):
    async def recommend(
        self,
        context: PracticeContext,
        *,
        exclude_question_ids: Sequence[str] = (),
    ) -> tuple[Recommendation, Question]: ...


class WorkflowEventSink(Protocol):
    async def tool_call(
        self,
        tool_name: str,
        args: dict[str, Any],
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None: ...

    async def tool_result(
        self,
        tool_name: str,
        result: str,
        source: str = "",
        stage: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None: ...


class PracticeWorkflowError(RuntimeError):
    """Structured terminal or retryable workflow failure."""

    def __init__(
        self,
        error_code: str,
        *,
        retryable: bool,
        step_state: PracticeState,
        checkpoint: PracticeWorkflowCheckpoint | None = None,
    ) -> None:
        self.error_code = error_code
        self.retryable = retryable
        self.step_state = step_state
        self.checkpoint = checkpoint
        super().__init__(error_code)


@dataclass(frozen=True, slots=True)
class PracticeWorkflowResult:
    checkpoint: PracticeWorkflowCheckpoint
    resumed_from_state: PracticeState
    replayed: bool


class ExamPracticeWorkflow:
    """Run only missing steps, checkpointing each validated result with CAS."""

    def __init__(
        self,
        *,
        checkpoint_repository: PracticeCheckpointRepository,
        trace_repository: PracticeTraceRepository,
        answer_grader: AnswerGrader,
        knowledge_mapper: KnowledgeMapper,
        error_analyzer: ErrorAnalyzer,
        memory_candidate_builder: PracticeMemoryCandidateBuilder,
        memory_writer: PracticeMemoryWriter,
        recommendation_tool: PracticeRecommendationTool,
        taxonomy_version: str = "math1_v1",
    ) -> None:
        if not taxonomy_version.strip():
            raise ValueError("taxonomy_version must not be blank")
        self._checkpoints = checkpoint_repository
        self._traces = trace_repository
        self._answer_grader = answer_grader
        self._knowledge_mapper = knowledge_mapper
        self._error_analyzer = error_analyzer
        self._candidate_builder = memory_candidate_builder
        self._memory_writer = memory_writer
        self._recommendation_tool = recommendation_tool
        self._taxonomy_version = taxonomy_version

    async def run(
        self,
        context: PracticeContext,
        *,
        stream: WorkflowEventSink | None = None,
    ) -> PracticeWorkflowResult:
        key = checkpoint_key_for_context(context)
        learning_context = _learning_context(context)
        if context.submitted_answer is not None:
            assert context.current_question is not None
            issued_question = await self._checkpoints.find_issued_question(
                learning_context,
                context.practice_session_id,
                context.current_question.question_id,
            )
            if issued_question != context.current_question:
                raise PracticeWorkflowError(
                    "practice_question_not_issued",
                    retryable=False,
                    step_state=context.step_state,
                )
        record = await self._checkpoints.get(
            learning_context,
            context.practice_session_id,
            key,
        )
        replayed = record is not None
        if record is None:
            created = await self._checkpoints.create(
                PracticeWorkflowCheckpoint(checkpoint_key=key, context=context)
            )
            if created.status is not AppendStatus.CREATED or created.record is None:
                raise PracticeWorkflowError(
                    "practice_checkpoint_create_conflict",
                    retryable=True,
                    step_state=context.step_state,
                )
            record = created.record
        else:
            _validate_replay_request(context, record.checkpoint)

        resumed_from = record.checkpoint.context.step_state
        trace = PracticeTraceRecorder(self._traces, trace_id=context.trace_id)
        retry_count = 1 if replayed else 0
        started = trace.start()
        await trace.completed(
            name=PracticeSpanName.REQUEST_RECEIVED,
            started=started,
            input_summary={
                "practice_session_id": context.practice_session_id,
                "checkpoint_key": key,
                "step_state": context.step_state.value,
            },
            output_summary={"resumed_from_state": resumed_from.value},
            retry_count=retry_count,
        )

        if context.submitted_answer is None:
            record = await self._start_practice(record, trace, stream, retry_count)
        else:
            record = await self._submit_answer(record, trace, stream, retry_count)

        response_started = trace.start()
        await trace.completed(
            name=PracticeSpanName.RESPONSE_SENT,
            started=response_started,
            input_summary={"step_state": record.checkpoint.context.step_state.value},
            output_summary={"question_id": _response_question_id(record.checkpoint)},
            retry_count=retry_count,
        )
        return PracticeWorkflowResult(
            checkpoint=record.checkpoint,
            resumed_from_state=resumed_from,
            replayed=replayed,
        )

    async def _start_practice(
        self,
        record: PracticeCheckpointRecord,
        trace: PracticeTraceRecorder,
        stream: WorkflowEventSink | None,
        retry_count: int,
    ) -> PracticeCheckpointRecord:
        if _at_least(record.checkpoint, PracticeState.QUESTION_READY):
            return record
        recommendation, question = await self._call_tool(
            name="recommendation",
            span_name=PracticeSpanName.QUESTION_SELECTED,
            state=PracticeState.IDLE,
            trace=trace,
            stream=stream,
            retry_count=retry_count,
            input_summary={"scope": _scope_summary(record.checkpoint.context)},
            operation=lambda: self._recommendation_tool.recommend(record.checkpoint.context),
            output_summary=lambda value: {
                "question_id": value[1].question_id,
                "reason_codes": list(value[0].reason_codes),
            },
            versions=lambda value: {"policy_version": value[0].policy_version},
            related_ids=lambda value: tuple(value[0].source_memory_ids),
        )
        next_context = _update_context(
            record.checkpoint.context,
            current_question=question,
            step_state=PracticeState.QUESTION_READY,
        )
        return await self._advance(
            record,
            _update_checkpoint(
                record.checkpoint,
                context=next_context,
                recommendation=recommendation,
                recommended_question=question,
            ),
        )

    async def _submit_answer(
        self,
        record: PracticeCheckpointRecord,
        trace: PracticeTraceRecorder,
        stream: WorkflowEventSink | None,
        retry_count: int,
    ) -> PracticeCheckpointRecord:
        checkpoint = record.checkpoint
        question = checkpoint.context.current_question
        submission = checkpoint.context.submitted_answer
        assert question is not None and submission is not None

        if not _at_least(checkpoint, PracticeState.GRADED):
            grade = await self._call_tool(
                name="answer_grader",
                span_name=PracticeSpanName.ANSWER_GRADED,
                state=PracticeState.ANSWER_RECEIVED,
                trace=trace,
                stream=stream,
                retry_count=retry_count,
                input_summary={"question_id": question.question_id},
                operation=lambda: self._answer_grader.grade(question, submission),
                output_summary=lambda value: {"correct": value.correct, "score": value.score},
                versions=lambda value: {"grader_version": value.grader_version},
                llm_calls=1,
            )
            record = await self._advance(
                record,
                _update_checkpoint(
                    checkpoint,
                    context=_update_context(
                        checkpoint.context,
                        step_state=PracticeState.GRADED,
                    ),
                    grade_result=grade,
                ),
            )
            checkpoint = record.checkpoint

        if not checkpoint.mapped_knowledge_point_ids:
            mapping = await self._call_tool(
                name="knowledge_mapper",
                span_name=PracticeSpanName.KNOWLEDGE_MAPPED,
                state=PracticeState.GRADED,
                trace=trace,
                stream=stream,
                retry_count=retry_count,
                input_summary={"question_id": question.question_id},
                operation=lambda: self._knowledge_mapper.map(question),
                output_summary=lambda value: {"knowledge_point_ids": list(_mapped_ids(value))},
                versions=lambda _: {"taxonomy_version": self._taxonomy_version},
                llm_calls=1,
            )
            checkpoint = _update_checkpoint(
                checkpoint,
                mapped_knowledge_point_ids=_mapped_ids(mapping),
            )
            record = await self._advance(record, checkpoint)

        if not _at_least(checkpoint, PracticeState.DIAGNOSED):
            assert checkpoint.grade_result is not None
            if checkpoint.grade_result.correct:
                diagnosis = DiagnosisResult(
                    knowledge_point_ids=list(checkpoint.mapped_knowledge_point_ids),
                    error_type=None,
                    explanation="No supported error classification is required.",
                    confidence=1.0,
                    analyzer_version="deterministic_correct_v1",
                )
                diagnosis_started = trace.start()
                await trace.completed(
                    name=PracticeSpanName.ERROR_DIAGNOSED,
                    started=diagnosis_started,
                    input_summary={"grade_correct": True},
                    output_summary={"error_type": None},
                    versions={"analyzer_version": diagnosis.analyzer_version},
                    retry_count=retry_count,
                )
            else:
                diagnosis = await self._call_tool(
                    name="error_analyzer",
                    span_name=PracticeSpanName.ERROR_DIAGNOSED,
                    state=PracticeState.GRADED,
                    trace=trace,
                    stream=stream,
                    retry_count=retry_count,
                    input_summary={
                        "question_id": question.question_id,
                        "grade_correct": False,
                        "knowledge_point_ids": list(checkpoint.mapped_knowledge_point_ids),
                    },
                    operation=lambda: self._error_analyzer.analyze(
                        question,
                        submission,
                        checkpoint.grade_result,
                        checkpoint.mapped_knowledge_point_ids,
                    ),
                    output_summary=lambda value: {
                        "error_type": value.error_type.value if value.error_type else None,
                        "confidence": value.confidence,
                    },
                    versions=lambda value: {"analyzer_version": value.analyzer_version},
                    llm_calls=1,
                )
            event = _learning_event(checkpoint.context, checkpoint.grade_result, diagnosis)
            candidates = tuple(
                self._candidate_builder.build(
                    event=event,
                    grade=checkpoint.grade_result,
                    diagnosis=diagnosis,
                )
            )
            checkpoint = _update_checkpoint(
                checkpoint,
                context=_update_context(
                    checkpoint.context,
                    step_state=PracticeState.DIAGNOSED,
                ),
                diagnosis_result=diagnosis,
                learning_event=event,
                memory_candidates=candidates,
            )
            record = await self._advance(record, checkpoint)

        if not _at_least(checkpoint, PracticeState.MEMORY_UPDATED):
            assert checkpoint.learning_event is not None
            memory_result = await self._call_tool(
                name="memory_writer",
                span_name=PracticeSpanName.EVENT_APPENDED,
                state=PracticeState.DIAGNOSED,
                trace=trace,
                stream=stream,
                retry_count=retry_count,
                input_summary={
                    "event_id": checkpoint.learning_event.event_id,
                    "candidate_count": len(checkpoint.memory_candidates),
                },
                operation=lambda: self._memory_writer.write(
                    checkpoint.learning_event,
                    list(checkpoint.memory_candidates),
                ),
                output_summary=lambda value: {
                    "decision_count": len(value.decisions),
                    "projection_request_count": len(value.projection_requests),
                },
                versions=lambda _: {"lifecycle_policy": "lifecycle_policy_v1"},
                related_ids=lambda _: (checkpoint.learning_event.event_id,),
                failure_checkpoint=checkpoint,
            )
            checkpoint = _update_checkpoint(
                checkpoint,
                context=_update_context(
                    checkpoint.context,
                    step_state=PracticeState.MEMORY_UPDATED,
                ),
                lifecycle_decisions=memory_result.decisions,
                projection_requests=memory_result.projection_requests,
                projection_refreshed=not memory_result.projection_requests,
                memory_write_completed=True,
            )
            record = await self._advance(record, checkpoint)
            if memory_result.decisions:
                related_ids = tuple(
                    dict.fromkeys(
                        (
                            checkpoint.learning_event.event_id,
                            *(
                                memory_id
                                for decision in memory_result.decisions
                                for memory_id in decision.target_memory_ids
                            ),
                        )
                    )
                )
                decided_started = trace.start()
                await trace.completed(
                    name=PracticeSpanName.LIFECYCLE_DECIDED,
                    started=decided_started,
                    input_summary={"event_id": checkpoint.learning_event.event_id},
                    output_summary={
                        "operations": [item.operation.value for item in memory_result.decisions]
                    },
                    versions={"lifecycle_policy": "lifecycle_policy_v1"},
                    retry_count=retry_count,
                    related_record_ids=related_ids,
                )
                lifecycle_started = trace.start()
                await trace.completed(
                    name=PracticeSpanName.LIFECYCLE_APPLIED,
                    started=lifecycle_started,
                    input_summary={"event_id": checkpoint.learning_event.event_id},
                    output_summary={
                        "operations": [item.operation.value for item in memory_result.decisions]
                    },
                    versions={"lifecycle_policy": "lifecycle_policy_v1"},
                    retry_count=retry_count,
                    related_record_ids=related_ids,
                )

        if checkpoint.projection_requests:
            projection_result = MemoryWriteResult(
                decisions=checkpoint.lifecycle_decisions,
                projection_requests=checkpoint.projection_requests,
            )
            await self._call_tool(
                name="memory_writer",
                span_name=PracticeSpanName.STUDENT_MODEL_PROJECTED,
                state=PracticeState.MEMORY_UPDATED,
                trace=trace,
                stream=stream,
                retry_count=retry_count,
                input_summary={"projection_request_count": len(checkpoint.projection_requests)},
                operation=lambda: self._memory_writer.refresh_after_commit(projection_result),
                output_summary=lambda _: {"projection_refreshed": True},
                versions=lambda _: {"projection_version": 1},
            )
            checkpoint = _update_checkpoint(
                checkpoint,
                projection_requests=(),
                projection_refreshed=True,
            )
            record = await self._advance(record, checkpoint)

        if not _at_least(checkpoint, PracticeState.RECOMMENDED):
            recommendation, next_question = await self._call_tool(
                name="recommendation",
                span_name=PracticeSpanName.QUESTION_RECOMMENDED,
                state=PracticeState.MEMORY_UPDATED,
                trace=trace,
                stream=stream,
                retry_count=retry_count,
                input_summary={
                    "scope": _scope_summary(checkpoint.context),
                    "exclude_question_ids": [question.question_id],
                },
                operation=lambda: self._recommendation_tool.recommend(
                    checkpoint.context,
                    exclude_question_ids=(question.question_id,),
                ),
                output_summary=lambda value: {
                    "question_id": value[1].question_id,
                    "reason_codes": list(value[0].reason_codes),
                },
                versions=lambda value: {"policy_version": value[0].policy_version},
                related_ids=lambda value: tuple(value[0].source_memory_ids),
            )
            checkpoint = _update_checkpoint(
                checkpoint,
                context=_update_context(
                    checkpoint.context,
                    step_state=PracticeState.RECOMMENDED,
                ),
                recommendation=recommendation,
                recommended_question=next_question,
            )
            record = await self._advance(record, checkpoint)
        return record

    async def _advance(
        self,
        record: PracticeCheckpointRecord,
        checkpoint: PracticeWorkflowCheckpoint,
    ) -> PracticeCheckpointRecord:
        advanced = await self._checkpoints.advance(
            checkpoint,
            expected_row_version=record.row_version,
        )
        if advanced is None:
            raise PracticeWorkflowError(
                "practice_checkpoint_cas_stale",
                retryable=True,
                step_state=record.checkpoint.context.step_state,
            )
        return advanced

    async def _call_tool(
        self,
        *,
        name: str,
        span_name: PracticeSpanName,
        state: PracticeState,
        trace: PracticeTraceRecorder,
        stream: WorkflowEventSink | None,
        retry_count: int,
        input_summary: dict[str, JsonValue],
        operation: Callable[[], Awaitable[T]],
        output_summary: Callable[[T], dict[str, JsonValue]],
        versions: Callable[[T], dict[str, JsonValue]],
        related_ids: Callable[[T], tuple[str, ...]] = lambda _: (),
        llm_calls: int = 0,
        failure_checkpoint: PracticeWorkflowCheckpoint | None = None,
    ) -> T:
        started = trace.start()
        if stream is not None:
            await stream.tool_call(
                name,
                input_summary,
                source="exam_practice",
                stage=state.value,
                metadata={"trace_id": trace.trace_id},
            )
        try:
            result = await operation()
        except Exception as exc:
            error_code = _error_code(exc, name)
            logger.warning(
                "Practice tool %s failed (%s, error_code=%s)",
                name,
                type(exc).__name__,
                error_code,
            )
            await trace.failed(
                name=span_name,
                started=started,
                input_summary=input_summary,
                error_code=error_code,
                retry_count=retry_count,
                llm_calls=llm_calls,
            )
            raise PracticeWorkflowError(
                error_code,
                retryable=True,
                step_state=state,
                checkpoint=failure_checkpoint,
            ) from exc
        summary = output_summary(result)
        await trace.completed(
            name=span_name,
            started=started,
            input_summary=input_summary,
            output_summary=summary,
            versions=versions(result),
            retry_count=retry_count,
            llm_calls=llm_calls,
            related_record_ids=related_ids(result),
        )
        if stream is not None:
            await stream.tool_result(
                name,
                json.dumps(summary, ensure_ascii=False, sort_keys=True),
                source="exam_practice",
                stage=state.value,
                metadata={"trace_id": trace.trace_id},
            )
        return result


def _mapped_ids(result: KnowledgePointNormalizationResult) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            (
                result.primary_knowledge_point_id,
                *result.secondary_knowledge_point_ids,
            )
        )
    )


def _learning_event(
    context: PracticeContext,
    grade: GradeResult,
    diagnosis: DiagnosisResult,
) -> LearningEvent:
    question = context.current_question
    submission = context.submitted_answer
    assert question is not None and submission is not None
    identity = "\x1f".join((context.scope.user_id, submission.idempotency_key)).encode()
    return LearningEvent(
        event_id=f"learning_event:{hashlib.sha256(identity).hexdigest()}",
        idempotency_key=submission.idempotency_key,
        context=_learning_context(context),
        session_id=context.practice_session_id,
        question_id=question.question_id,
        knowledge_point_ids=list(diagnosis.knowledge_point_ids),
        difficulty=question.difficulty,
        answer_correct=grade.correct,
        error_type=diagnosis.error_type,
        error_detail=None if grade.correct else diagnosis.explanation,
        occurred_at=submission.submitted_at,
    )


def _learning_context(context: PracticeContext) -> LearningContext:
    return LearningContext.model_validate(context.scope.model_dump(exclude={"memory_namespace"}))


def _update_context(context: PracticeContext, **updates: object) -> PracticeContext:
    payload = context.model_dump(mode="python")
    payload.update(updates)
    return PracticeContext.model_validate(payload)


def _update_checkpoint(
    checkpoint: PracticeWorkflowCheckpoint,
    **updates: object,
) -> PracticeWorkflowCheckpoint:
    payload = checkpoint.model_dump(mode="python")
    payload.update(updates)
    return PracticeWorkflowCheckpoint.model_validate(payload)


def _validate_replay_request(
    requested: PracticeContext,
    stored: PracticeWorkflowCheckpoint,
) -> None:
    stored_context = stored.context
    if (
        requested.practice_session_id != stored_context.practice_session_id
        or requested.scope != stored_context.scope
        or requested.trace_id != stored_context.trace_id
    ):
        raise PracticeWorkflowError(
            "practice_checkpoint_identity_conflict",
            retryable=False,
            step_state=stored_context.step_state,
        )
    if requested.current_question is not None:
        if requested.current_question != stored_context.current_question:
            raise PracticeWorkflowError(
                "practice_checkpoint_question_conflict",
                retryable=False,
                step_state=stored_context.step_state,
            )
    if requested.submitted_answer is not None:
        if requested.submitted_answer != stored_context.submitted_answer:
            raise PracticeWorkflowError(
                "practice_checkpoint_submission_conflict",
                retryable=False,
                step_state=stored_context.step_state,
            )


def _scope_summary(context: PracticeContext) -> dict[str, JsonValue]:
    return context.scope.model_dump(mode="json")


def _at_least(checkpoint: PracticeWorkflowCheckpoint, state: PracticeState) -> bool:
    return _STATE_ORDER[checkpoint.context.step_state] >= _STATE_ORDER[state]


def _response_question_id(checkpoint: PracticeWorkflowCheckpoint) -> str | None:
    question = checkpoint.recommended_question or checkpoint.context.current_question
    return None if question is None else question.question_id


def _error_code(error: Exception, tool_name: str) -> str:
    code = getattr(error, "error_code", None)
    if isinstance(code, str) and code.strip():
        return code
    return f"{tool_name}_failed"


__all__ = [
    "AnswerGrader",
    "ErrorAnalyzer",
    "ExamPracticeWorkflow",
    "KnowledgeMapper",
    "PracticeMemoryWriter",
    "PracticeRecommendationTool",
    "PracticeWorkflowError",
    "PracticeWorkflowResult",
    "WorkflowEventSink",
]
