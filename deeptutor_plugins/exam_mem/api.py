"""Authenticated HTTP boundary owned by the ExamMem plugin."""

from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
import hashlib
from typing import Annotated, Any, Literal, Protocol

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from deeptutor.plugins.host_services import (
    PluginTurnHost,
    PluginTurnRequest,
    current_user_id,
    current_user_is_admin,
)
from exam_mem.contracts import LearningContext, LifecycleState, MemoryNamespace, MemoryValue
from exam_mem.practice import (
    CorrectionError,
    ExplicitCorrectionRequest,
    LearningMemoryListRequest,
    PlanTransitionError,
    PracticeProgressTransitionRequest,
    PracticeRuntimeConfigurationError,
    SystemPlanExpirationRequest,
    UserPlanCancellationRequest,
    stage07_practice_questions,
    stage07_question,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_EXAM_ID = "postgraduate_entrance_exam"
_SUBJECT_ID = "math_1"


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PracticeStartBody(StrictApiModel):
    practice_session_id: NonEmptyString
    trace_id: NonEmptyString
    session_id: NonEmptyString | None = None


class PracticeAnswerBody(StrictApiModel):
    practice_session_id: NonEmptyString
    trace_id: NonEmptyString
    session_id: NonEmptyString
    question_id: NonEmptyString
    answer: NonEmptyString
    submitted_at: AwareDatetime
    idempotency_key: NonEmptyString


class CorrectionBody(StrictApiModel):
    session_id: NonEmptyString
    idempotency_key: NonEmptyString
    statement: NonEmptyString
    occurred_at: AwareDatetime
    replacement_value: MemoryValue | None = None
    uncertain: bool = False
    confirmed: bool


class PlanTransitionBody(StrictApiModel):
    kind: Literal["practice_progress", "user_cancellation", "system_expiration"]
    session_id: NonEmptyString
    idempotency_key: NonEmptyString
    reason: NonEmptyString
    occurred_at: AwareDatetime
    progress: Annotated[float, Field(ge=0.0, le=1.0)] | None = None
    confirmed: bool | None = None

    @model_validator(mode="after")
    def validate_kind_fields(self) -> PlanTransitionBody:
        if self.kind == "practice_progress":
            if self.progress is None or self.confirmed is not None:
                raise ValueError("practice_progress requires only progress")
        elif self.kind == "user_cancellation":
            if self.confirmed is None or self.progress is not None:
                raise ValueError("user_cancellation requires only confirmed")
        elif self.progress is not None or self.confirmed is not None:
            raise ValueError("system_expiration accepts neither progress nor confirmed")
        return self


class LearningMemoryRuntime(Protocol):
    queries: Any
    corrections: Any


class RuntimeProvider(Protocol):
    def open_learning_memories(
        self, *, trace_id: str
    ) -> AbstractAsyncContextManager[LearningMemoryRuntime]: ...

    def open_plan_transitions(
        self, *, trace_id: str
    ) -> AbstractAsyncContextManager[Any]: ...


def build_router(
    runtime_provider: RuntimeProvider,
    *,
    turn_host: PluginTurnHost | None = None,
) -> APIRouter:
    """Build one router wired to the same Provider as the plugin Capability."""

    router = APIRouter()
    host = turn_host

    def runtime_host() -> PluginTurnHost:
        nonlocal host
        if host is None:
            host = PluginTurnHost()
        return host

    @router.post("/practice/start")
    async def start_practice(body: PracticeStartBody) -> dict[str, Any]:
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
        )
        return await _run_practice_turn(
            runtime_host(),
            content="开始数学一练习",
            session_id=body.session_id,
            context=context,
        )

    @router.post("/practice/answer")
    async def answer_practice(body: PracticeAnswerBody) -> dict[str, Any]:
        question = stage07_question(body.question_id)
        if question is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "practice_question_not_found",
                    "message": "Question is not in the Stage 07 practice catalog.",
                },
            )
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
            question=question.model_dump(mode="json"),
            submission={
                "practice_session_id": body.practice_session_id,
                "question_id": question.question_id,
                "answer": body.answer,
                "submitted_at": body.submitted_at.isoformat(),
                "idempotency_key": body.idempotency_key,
            },
        )
        return await _run_practice_turn(
            runtime_host(),
            content="提交答案",
            session_id=body.session_id,
            context=context,
        )

    @router.get("/memories")
    async def list_memories(
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        memory_namespace: MemoryNamespace,
        lifecycle_state: Annotated[list[LifecycleState] | None, Query()] = None,
        query: NonEmptyString | None = None,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        try:
            async with runtime_provider.open_learning_memories(
                trace_id=_query_trace_id(context, "list")
            ) as runtime:
                memories = await runtime.queries.list_memories(
                    LearningMemoryListRequest(
                        context=context,
                        memory_namespace=memory_namespace,
                        lifecycle_states=tuple(lifecycle_state or ()),
                        query=query,
                    )
                )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        return {
            "scope": {
                "exam_id": context.exam_id,
                "subject_id": context.subject_id,
                "memory_namespace": memory_namespace.value,
            },
            "count": len(memories),
            "memories": [item.model_dump(mode="json") for item in memories],
        }

    @router.get("/memories/{memory_id}")
    async def get_memory(
        memory_id: NonEmptyString,
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        memory_namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        try:
            async with runtime_provider.open_learning_memories(
                trace_id=_query_trace_id(context, "detail")
            ) as runtime:
                detail = await runtime.queries.get_detail(
                    context=context,
                    memory_namespace=memory_namespace,
                    memory_id=memory_id,
                )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        if detail is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
        return detail.model_dump(mode="json")

    @router.get("/memories/{memory_id}/evidence")
    async def get_memory_evidence(
        memory_id: NonEmptyString,
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        memory_namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        try:
            async with runtime_provider.open_learning_memories(
                trace_id=_query_trace_id(context, "evidence")
            ) as runtime:
                evidence = await runtime.queries.get_evidence(
                    context=context,
                    memory_namespace=memory_namespace,
                    memory_id=memory_id,
                )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        if evidence is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Memory not found")
        return evidence.model_dump(mode="json")

    @router.post("/memories/{memory_id}/corrections")
    async def correct_memory(
        memory_id: NonEmptyString,
        body: CorrectionBody,
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        memory_namespace: MemoryNamespace,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        trace_id = _correction_trace_id(context, body.idempotency_key)
        try:
            async with runtime_provider.open_learning_memories(trace_id=trace_id) as runtime:
                result = await runtime.corrections.apply(
                    ExplicitCorrectionRequest(
                        context=context,
                        memory_namespace=memory_namespace,
                        target_memory_id=memory_id,
                        session_id=body.session_id,
                        idempotency_key=body.idempotency_key,
                        statement=body.statement,
                        occurred_at=body.occurred_at,
                        trace_id=trace_id,
                        replacement_value=body.replacement_value,
                        uncertain=body.uncertain,
                        confirmed=body.confirmed,
                    )
                )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        except CorrectionError as exc:
            http_status = (
                status.HTTP_404_NOT_FOUND
                if exc.error_code == "correction_target_not_found"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(
                status_code=http_status,
                detail={"error_code": exc.error_code, "message": str(exc)},
            ) from exc
        return {
            "trace_id": trace_id,
            "event": result.event.model_dump(mode="json"),
            "decisions": [
                decision.model_dump(mode="json")
                for decision in result.memory_result.decisions
            ],
            "recommendation_source_memory_ids": list(
                result.recommendation_source_memory_ids
            ),
        }

    @router.post("/plans/{memory_id}/transitions")
    async def transition_plan(
        memory_id: NonEmptyString,
        body: PlanTransitionBody,
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
    ) -> dict[str, Any]:
        if body.kind != "user_cancellation" and not current_user_is_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="practice and system Plan tasks require an administrator",
            )
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        trace_id = _operation_trace_id(context, "plan_transition", body.idempotency_key)
        try:
            async with runtime_provider.open_plan_transitions(trace_id=trace_id) as runtime:
                target = await runtime.targets.get_plan_target(context, memory_id)
                if target is None:
                    raise PlanTransitionError(
                        "plan_target_not_found",
                        "no plan target exists in the authenticated learning context",
                    )
                request_payload = {
                    "context": context,
                    "target_memory_id": memory_id,
                    "session_id": body.session_id,
                    "idempotency_key": body.idempotency_key,
                    "knowledge_point_ids": target.knowledge_point_ids,
                    "reason": body.reason,
                    "occurred_at": body.occurred_at,
                    "trace_id": trace_id,
                }
                if body.kind == "practice_progress":
                    result = await runtime.service.apply_practice_progress(
                        PracticeProgressTransitionRequest(
                            **request_payload,
                            progress=body.progress,
                        )
                    )
                elif body.kind == "user_cancellation":
                    result = await runtime.service.apply_user_cancellation(
                        UserPlanCancellationRequest(
                            **request_payload,
                            confirmed=body.confirmed,
                        )
                    )
                else:
                    result = await runtime.service.apply_system_expiration(
                        SystemPlanExpirationRequest(**request_payload)
                    )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        except PlanTransitionError as exc:
            http_status = (
                status.HTTP_404_NOT_FOUND
                if exc.error_code == "plan_target_not_found"
                else status.HTTP_409_CONFLICT
            )
            raise HTTPException(
                status_code=http_status,
                detail={"error_code": exc.error_code, "message": str(exc)},
            ) from exc
        return {
            "trace_id": trace_id,
            "event": result.event.model_dump(mode="json"),
            "decisions": [
                decision.model_dump(mode="json")
                for decision in result.memory_result.decisions
            ],
        }

    return router


def _authenticated_context(*, exam_id: str, subject_id: str) -> LearningContext:
    return LearningContext(
        user_id=current_user_id(),
        exam_id=exam_id,
        subject_id=subject_id,
    )


def _practice_context_payload(
    *,
    practice_session_id: str,
    trace_id: str,
    question: dict[str, Any] | None = None,
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "practice_session_id": practice_session_id,
        "scope": {
            "user_id": current_user_id(),
            "exam_id": _EXAM_ID,
            "subject_id": _SUBJECT_ID,
            "memory_namespace": MemoryNamespace.MASTERY.value,
        },
        "step_state": "IDLE" if submission is None else "ANSWER_RECEIVED",
        "trace_id": trace_id,
    }
    if question is not None:
        payload["current_question"] = question
    if submission is not None:
        payload["submitted_answer"] = submission
    return payload


async def _run_practice_turn(
    host: PluginTurnHost,
    *,
    content: str,
    session_id: str | None,
    context: dict[str, Any],
) -> dict[str, Any]:
    questions = [question.model_dump(mode="json") for question in stage07_practice_questions()]
    try:
        session, turn = await host.start_turn(
            PluginTurnRequest(
                content=content,
                capability="exam_practice",
                session_id=session_id,
                language="zh",
                config={
                    "exam_practice_context": context,
                    "exam_practice_questions": questions,
                    "_persist_user_message": False,
                },
            )
        )
        result: dict[str, Any] | None = None
        error: dict[str, Any] | None = None
        async for event in host.stream_turn(turn["id"]):
            if event.get("type") == "result" and event.get("source") == "exam_practice":
                result = event
            elif event.get("type") == "error":
                metadata = event.get("metadata") or {}
                current_metadata = (error or {}).get("metadata") or {}
                if error is None or (
                    isinstance(metadata.get("practice"), dict)
                    and not isinstance(current_metadata.get("practice"), dict)
                ):
                    error = event
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "practice_turn_rejected", "message": str(exc)},
        ) from exc

    if result is None:
        detail = (error or {}).get("metadata") or {}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": detail.get("error_code", "practice_turn_failed"),
                "message": (error or {}).get("content", "Practice turn failed."),
                "retryable": bool(detail.get("retryable", False)),
                "session_id": session["id"],
                "turn_id": turn["id"],
                "practice": detail.get("practice"),
            },
        )
    metadata = result.get("metadata") or {}
    practice = metadata.get("practice")
    if not isinstance(practice, dict):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="exam_practice returned no structured practice result",
        )
    return {
        "session_id": session["id"],
        "turn_id": turn["id"],
        "response": metadata.get("response", ""),
        "practice": practice,
    }


def _query_trace_id(context: LearningContext, operation: str) -> str:
    now = datetime.now().astimezone().isoformat()
    identity = "\x1f".join(
        (context.user_id, context.exam_id, context.subject_id, operation, now)
    ).encode()
    return f"exam_mem_query:{hashlib.sha256(identity).hexdigest()}"


def _operation_trace_id(
    context: LearningContext, operation: str, idempotency_key: str
) -> str:
    identity = "\x1f".join((context.user_id, operation, idempotency_key)).encode()
    return f"exam_mem_{operation}:{hashlib.sha256(identity).hexdigest()}"


def _correction_trace_id(context: LearningContext, idempotency_key: str) -> str:
    identity = "\x1f".join((context.user_id, idempotency_key)).encode()
    return f"exam_mem_correction:{hashlib.sha256(identity).hexdigest()}"


def _configuration_error(exc: PracticeRuntimeConfigurationError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={"error_code": exc.error_code, "message": str(exc)},
    )


__all__ = [
    "CorrectionBody",
    "PlanTransitionBody",
    "PracticeAnswerBody",
    "PracticeStartBody",
    "build_router",
]
