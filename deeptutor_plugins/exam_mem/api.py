"""Authenticated HTTP boundary owned by the ExamMem plugin."""

from __future__ import annotations

import base64
import binascii
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
import hashlib
import json
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

from deeptutor.plugins import (
    SettingsContribution,
    load_plugin_settings,
    save_plugin_settings,
)
from deeptutor.plugins.host_services import (
    PluginTurnHost,
    PluginTurnRequest,
    current_user_id,
    current_user_is_admin,
)
from exam_mem.config import ExamMemSettings, backend_side_effects, settings_revision
from exam_mem.contracts import LearningContext, LifecycleState, MemoryNamespace, MemoryValue
from exam_mem.domain import (
    KnowledgePointStatus,
    RuleBasedKnowledgePointNormalizer,
    load_taxonomy,
)
from exam_mem.practice import (
    AnswerSubmission,
    CorrectionError,
    ExplicitCorrectionRequest,
    GradeResult,
    GradeReviewAction,
    GradeReviewEvent,
    LearningMemoryListRequest,
    PlanTransitionError,
    PracticeProgressTransitionRequest,
    PracticeRuntimeConfigurationError,
    PracticeState,
    Question,
    SystemPlanExpirationRequest,
    UserPlanCancellationRequest,
    stage07_practice_questions,
)
from exam_mem.storage import AppendStatus

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_EXAM_ID = "postgraduate_entrance_exam"
_SUBJECT_ID = "math_1"


class StrictApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PracticeStartBody(StrictApiModel):
    practice_session_id: NonEmptyString
    trace_id: NonEmptyString
    session_id: NonEmptyString | None = None
    exam_id: NonEmptyString = _EXAM_ID
    subject_id: NonEmptyString = _SUBJECT_ID


class PracticeAnswerBody(StrictApiModel):
    practice_session_id: NonEmptyString
    trace_id: NonEmptyString
    session_id: NonEmptyString
    question_id: NonEmptyString
    answer: NonEmptyString
    submitted_at: AwareDatetime
    idempotency_key: NonEmptyString
    exam_id: NonEmptyString = _EXAM_ID
    subject_id: NonEmptyString = _SUBJECT_ID


class PracticeSourceAttachment(StrictApiModel):
    type: Literal["file", "pdf"] = "file"
    filename: NonEmptyString
    mime_type: NonEmptyString
    base64: NonEmptyString

    @model_validator(mode="after")
    def validate_practice_source_type(self) -> PracticeSourceAttachment:
        suffix = self.filename.lower().rsplit(".", maxsplit=1)[-1]
        allowed = {
            "pdf": {"application/pdf"},
            "txt": {"text/plain"},
            "md": {"text/markdown", "text/plain"},
        }
        if suffix not in allowed or self.mime_type.lower() not in allowed[suffix]:
            raise ValueError("practice sources currently support only PDF, TXT and Markdown")
        if (suffix == "pdf") != (self.type == "pdf"):
            raise ValueError("PDF sources must use type=pdf and other sources type=file")
        return self


class GeneratedPracticeStartBody(PracticeStartBody):
    learning_path_id: NonEmptyString
    knowledge_point_id: NonEmptyString
    knowledge_point_name: NonEmptyString
    num_questions: Annotated[int, Field(ge=2, le=10)] = 4
    difficulty: Literal["auto", "easy", "medium", "hard"] = "auto"
    attachments: tuple[PracticeSourceAttachment, ...] = ()


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


class GradeDisputeBody(StrictApiModel):
    practice_session_id: NonEmptyString
    checkpoint_key: NonEmptyString
    idempotency_key: NonEmptyString
    reason: NonEmptyString


class GradeDispositionBody(StrictApiModel):
    action: Literal["uphold", "overturn"]
    practice_session_id: NonEmptyString
    checkpoint_key: NonEmptyString
    idempotency_key: NonEmptyString
    reason: NonEmptyString
    replacement_grade: GradeResult | None = None

    @model_validator(mode="after")
    def validate_replacement(self) -> GradeDispositionBody:
        if self.action == "overturn" and self.replacement_grade is None:
            raise ValueError("overturn requires replacement_grade")
        if self.action == "uphold" and self.replacement_grade is not None:
            raise ValueError("uphold must not include replacement_grade")
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

    def open_product(self) -> AbstractAsyncContextManager[Any]: ...


def build_router(
    runtime_provider: RuntimeProvider,
    *,
    turn_host: PluginTurnHost | None = None,
    settings_contribution: SettingsContribution | None = None,
    effective_settings: ExamMemSettings | None = None,
) -> APIRouter:
    """Build one router wired to the same Provider as the plugin Capability."""

    router = APIRouter()
    host = turn_host
    effective = effective_settings or ExamMemSettings()

    def runtime_host() -> PluginTurnHost:
        nonlocal host
        if host is None:
            host = PluginTurnHost()
        return host

    @router.post("/practice/start")
    async def start_practice(body: PracticeStartBody) -> dict[str, Any]:
        _validate_controlled_scope(body.exam_id, body.subject_id)
        questions = tuple(stage07_practice_questions())
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            questions=questions,
        )
        return await _run_practice_turn(
            runtime_host(),
            content="开始数学一练习",
            session_id=body.session_id,
            context=context,
        )

    @router.post("/practice/generate")
    async def generate_practice(body: GeneratedPracticeStartBody) -> dict[str, Any]:
        _validate_controlled_scope(body.exam_id, body.subject_id)
        canonical_id = _canonical_knowledge_point(
            body.knowledge_point_id,
            body.knowledge_point_name,
        )
        questions = await _generate_practice_questions(
            runtime_host(),
            body=body,
            canonical_knowledge_point_id=canonical_id,
        )
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            questions=questions,
        )
        result = await _run_practice_turn(
            runtime_host(),
            content=f"开始 {body.knowledge_point_name} 专项练习",
            session_id=body.session_id,
            context=context,
        )
        result["generation"] = {
            "learning_path_id": body.learning_path_id,
            "knowledge_point_id": canonical_id,
            "knowledge_point_name": body.knowledge_point_name,
            "question_count": len(questions),
            "source_files": [item.filename for item in body.attachments],
        }
        return result

    @router.get("/catalog")
    async def get_catalog() -> dict[str, Any]:
        taxonomy = load_taxonomy("math1_v1")
        leaves = [
            node
            for node in taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE
            and not taxonomy.children_of(node.id)
        ]
        return {
            "scopes": [
                {
                    "exam_id": _EXAM_ID,
                    "exam_name": "全国硕士研究生招生考试",
                    "subject_id": _SUBJECT_ID,
                    "subject_name": "数学一",
                    "taxonomy_version": taxonomy.taxonomy_version,
                }
            ],
            "knowledge_points": [
                {"id": node.id, "name": node.name_zh, "aliases": list(node.aliases)}
                for node in leaves
            ],
        }

    @router.post("/practice/answer")
    async def answer_practice(body: PracticeAnswerBody) -> dict[str, Any]:
        _validate_controlled_scope(body.exam_id, body.subject_id)
        learning_context = _authenticated_context(
            exam_id=body.exam_id,
            subject_id=body.subject_id,
        )
        async with runtime_provider.open_product() as runtime:
            replay = await runtime.checkpoints.get(
                learning_context,
                body.practice_session_id,
                f"answer:{body.idempotency_key}",
            )
            latest = replay or await runtime.checkpoints.get_latest(
                learning_context,
                body.practice_session_id,
            )
        if latest is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error_code": "practice_session_not_found",
                    "message": "Practice session was not found.",
                },
            )
        if replay is not None:
            context = replay.checkpoint.context.model_dump(mode="json")
        else:
            checkpoint = latest.checkpoint
            question = checkpoint.recommended_question or checkpoint.context.current_question
            if question is None or question.question_id != body.question_id:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_code": "practice_question_not_issued",
                        "message": "Question does not match the latest issued question.",
                    },
                )
            submission = AnswerSubmission.model_validate(
                {
                "practice_session_id": body.practice_session_id,
                "question_id": question.question_id,
                "answer": body.answer,
                "submitted_at": body.submitted_at.isoformat(),
                "idempotency_key": body.idempotency_key,
                }
            )
            context = checkpoint.context.model_copy(
                update={
                    "current_question": question,
                    "submitted_answer": submission,
                    "step_state": PracticeState.ANSWER_RECEIVED,
                }
            ).model_dump(mode="json")
        return await _run_practice_turn(
            runtime_host(),
            content="提交答案",
            session_id=body.session_id,
            context=context,
        )

    @router.get("/practice/sessions")
    async def list_practice_sessions(
        exam_id: NonEmptyString = _EXAM_ID,
        subject_id: NonEmptyString = _SUBJECT_ID,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            sessions = await runtime.products.list_practice_sessions(context)
        return {"scope": _public_scope(context), "sessions": sessions}

    @router.get("/practice/sessions/{practice_session_id}")
    async def get_practice_session(
        practice_session_id: NonEmptyString,
        exam_id: NonEmptyString = _EXAM_ID,
        subject_id: NonEmptyString = _SUBJECT_ID,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            review = await runtime.products.get_practice_session(
                context, practice_session_id
            )
        if review is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
        return review

    @router.post("/practice/sessions/{practice_session_id}/resume")
    async def resume_practice(
        practice_session_id: NonEmptyString,
        exam_id: NonEmptyString = _EXAM_ID,
        subject_id: NonEmptyString = _SUBJECT_ID,
    ) -> dict[str, Any]:
        _validate_controlled_scope(exam_id, subject_id)
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            latest = await runtime.checkpoints.get_latest(context, practice_session_id)
        if latest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
        result = await _run_practice_turn(
            runtime_host(),
            content="恢复数学一练习",
            session_id=None,
            context=latest.checkpoint.context.model_dump(mode="json"),
        )
        return result

    @router.get("/issues")
    async def list_issues(
        exam_id: NonEmptyString = _EXAM_ID,
        subject_id: NonEmptyString = _SUBJECT_ID,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            issues = await runtime.products.list_issues(context)
        return {"scope": _public_scope(context), "issues": issues}

    @router.get("/configuration")
    async def get_configuration(
        practice_session_id: NonEmptyString | None = None,
    ) -> dict[str, Any]:
        contribution = settings_contribution
        if contribution is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Settings contribution is unavailable",
            )
        saved = ExamMemSettings.model_validate(load_plugin_settings(contribution))
        pinned = None
        if practice_session_id is not None:
            context = _authenticated_context(exam_id=_EXAM_ID, subject_id=_SUBJECT_ID)
            async with runtime_provider.open_product() as runtime:
                pinned = await runtime.checkpoints.get_runtime_snapshot(
                    context, practice_session_id
                )
        return _configuration_payload(saved=saved, effective=effective, pinned=pinned)

    @router.put("/configuration")
    async def update_configuration(body: ExamMemSettings) -> dict[str, Any]:
        if not current_user_is_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="ExamMem configuration requires an administrator",
            )
        contribution = settings_contribution
        if contribution is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Settings contribution is unavailable",
            )
        saved = ExamMemSettings.model_validate(
            save_plugin_settings(contribution, body.model_dump(mode="json"))
        )
        return {
            **_configuration_payload(saved=saved, effective=effective, pinned=None),
            "restart_required": saved != effective,
        }

    @router.post("/grade-reviews/disputes")
    async def dispute_grade(body: GradeDisputeBody) -> dict[str, Any]:
        context = _authenticated_context(exam_id=_EXAM_ID, subject_id=_SUBJECT_ID)
        chain_id = _review_chain_id(context, body.practice_session_id, body.checkpoint_key)
        event = _review_event(
            context=context,
            chain_id=chain_id,
            action=GradeReviewAction.DISPUTE,
            body=body,
        )
        async with runtime_provider.open_product() as runtime:
            checkpoint = await runtime.checkpoints.get(
                context, body.practice_session_id, body.checkpoint_key
            )
            if checkpoint is None or checkpoint.checkpoint.grade_result is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Graded checkpoint not found",
                )
            result = await runtime.reviews.append(event)
            await runtime.connection.commit()
        if result.status is AppendStatus.CONFLICT:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Review conflict")
        return {
            "status": result.status.value,
            "review": result.event.model_dump(mode="json") if result.event else None,
        }

    @router.post("/grade-reviews/{review_chain_id}/dispositions")
    async def dispose_grade_review(
        review_chain_id: NonEmptyString,
        body: GradeDispositionBody,
    ) -> dict[str, Any]:
        if not current_user_is_admin():
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Grade Review disposition requires an administrator",
            )
        context = _authenticated_context(exam_id=_EXAM_ID, subject_id=_SUBJECT_ID)
        expected_chain = _review_chain_id(
            context, body.practice_session_id, body.checkpoint_key
        )
        if expected_chain != review_chain_id:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Review identity conflict")
        event = _review_event(
            context=context,
            chain_id=review_chain_id,
            action=GradeReviewAction(body.action),
            body=body,
            replacement_grade=body.replacement_grade,
        )
        async with runtime_provider.open_product() as runtime:
            chain = await runtime.reviews.list_chain(context, review_chain_id)
            if not chain or chain[0].action is not GradeReviewAction.DISPUTE:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dispute not found")
            if len(chain) > 1 and not any(
                item.idempotency_key == body.idempotency_key for item in chain
            ):
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Review is closed")
            result = await runtime.reviews.append(event)
            await runtime.connection.commit()
        if result.status is AppendStatus.CONFLICT:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Review conflict")
        return {
            "status": result.status.value,
            "review": result.event.model_dump(mode="json") if result.event else None,
        }

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


def _public_scope(context: LearningContext) -> dict[str, str]:
    return {"exam_id": context.exam_id, "subject_id": context.subject_id}


def _review_chain_id(
    context: LearningContext,
    practice_session_id: str,
    checkpoint_key: str,
) -> str:
    identity = "\x1f".join(
        (
            context.user_id,
            context.exam_id,
            context.subject_id,
            practice_session_id,
            checkpoint_key,
        )
    ).encode()
    return f"grade_review:{hashlib.sha256(identity).hexdigest()}"


def _review_event(
    *,
    context: LearningContext,
    chain_id: str,
    action: GradeReviewAction,
    body: GradeDisputeBody | GradeDispositionBody,
    replacement_grade: GradeResult | None = None,
) -> GradeReviewEvent:
    identity = f"{context.user_id}\x1f{body.idempotency_key}".encode()
    return GradeReviewEvent(
        review_event_id=f"grade_review_event:{hashlib.sha256(identity).hexdigest()}",
        review_chain_id=chain_id,
        idempotency_key=body.idempotency_key,
        action=action,
        user_id=context.user_id,
        exam_id=context.exam_id,
        subject_id=context.subject_id,
        practice_session_id=body.practice_session_id,
        checkpoint_key=body.checkpoint_key,
        reason=body.reason,
        replacement_grade=replacement_grade,
        created_at=datetime.now(timezone.utc),
    )


def _configuration_payload(
    *,
    saved: ExamMemSettings,
    effective: ExamMemSettings,
    pinned,
) -> dict[str, Any]:  # noqa: ANN001
    return {
        "saved": {
            "revision": settings_revision(saved),
            "settings": saved.model_dump(mode="json"),
            "side_effects": list(backend_side_effects(saved.memory_backend)),
        },
        "effective": {
            "revision": settings_revision(effective),
            "settings": effective.model_dump(mode="json"),
            "side_effects": list(backend_side_effects(effective.memory_backend)),
        },
        "pinned": None if pinned is None else pinned.model_dump(mode="json"),
    }


def _practice_context_payload(
    *,
    practice_session_id: str,
    trace_id: str,
    exam_id: str = _EXAM_ID,
    subject_id: str = _SUBJECT_ID,
    questions=(),  # noqa: ANN001
    question: dict[str, Any] | None = None,
    submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "practice_session_id": practice_session_id,
        "scope": {
            "user_id": current_user_id(),
            "exam_id": exam_id,
            "subject_id": subject_id,
            "memory_namespace": MemoryNamespace.MASTERY.value,
        },
        "step_state": "IDLE" if submission is None else "ANSWER_RECEIVED",
        "trace_id": trace_id,
        "question_catalog": [item.model_dump(mode="json") for item in questions],
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
    questions = list(context.get("question_catalog") or ())
    if not questions:
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


def _validate_controlled_scope(exam_id: str, subject_id: str) -> None:
    if exam_id != _EXAM_ID or subject_id != _SUBJECT_ID:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "practice_scope_not_supported",
                "message": "The selected exam scope is not available in the controlled catalog.",
            },
        )


def _canonical_knowledge_point(knowledge_point_id: str, knowledge_point_name: str) -> str:
    taxonomy = load_taxonomy("math1_v1")
    candidate = taxonomy.get(knowledge_point_id)
    if (
        candidate is not None
        and candidate.status is KnowledgePointStatus.ACTIVE
        and not taxonomy.children_of(candidate.id)
    ):
        return candidate.id
    normalized = RuleBasedKnowledgePointNormalizer(taxonomy).normalize(
        knowledge_point_name,
        1.0,
    )
    if normalized.knowledge_point_id == "unknown":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "learning_path_knowledge_point_unmapped",
                "message": "This learning-path knowledge point is not in the selected exam taxonomy.",
            },
        )
    return normalized.knowledge_point_id


async def _generate_practice_questions(
    host: PluginTurnHost,
    *,
    body: GeneratedPracticeStartBody,
    canonical_knowledge_point_id: str,
) -> tuple[Question, ...]:
    source_artifacts = []
    for attachment in body.attachments:
        try:
            content = base64.b64decode(attachment.base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error_code": "invalid_practice_attachment",
                    "message": f"Attachment {attachment.filename!r} is not valid base64.",
                },
            ) from exc
        source_artifacts.append(
            {
                "filename": attachment.filename,
                "mime_type": attachment.mime_type,
                "sha256": hashlib.sha256(content).hexdigest(),
            }
        )
    topic = (
        f"围绕知识点“{body.knowledge_point_name}”生成 {body.num_questions} 道递进练习题。"
        "题目必须能够独立作答，答案与解释必须明确；如有附件，只能依据附件内容出题。"
    )
    session: dict[str, Any] | None = None
    pairs: list[dict[str, Any]] = []
    generation_error: dict[str, Any] | None = None
    try:
        session, turn = await host.start_turn(
            PluginTurnRequest(
                content=topic,
                capability="deep_question",
                language="zh",
                config={
                    "mode": "custom",
                    "topic": topic,
                    "num_questions": body.num_questions,
                    "difficulty": body.difficulty,
                    "question_types": ["concept", "short_answer", "written"],
                    "per_type_counts": {},
                    "_persist_user_message": False,
                },
                attachments=tuple(item.model_dump(mode="json") for item in body.attachments),
            )
        )
        async for event in host.stream_turn(turn["id"]):
            metadata = event.get("metadata") or {}
            if event.get("type") == "error":
                generation_error = event
            if metadata.get("call_kind") == "quiz_question_emitted":
                pair = metadata.get("qa_pair")
                if isinstance(pair, dict):
                    pairs.append(pair)
            if event.get("type") == "result" and event.get("source") == "deep_question":
                summary = metadata.get("summary") or {}
                for item in summary.get("results") or []:
                    pair = item.get("qa_pair") if isinstance(item, dict) else None
                    if isinstance(pair, dict):
                        pairs.append(pair)
    except RuntimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error_code": "question_generation_failed", "message": str(exc)},
        ) from exc
    finally:
        if session is not None:
            await host.delete_session(session["id"])

    if generation_error is not None:
        metadata = generation_error.get("metadata") or {}
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": metadata.get("error_code", "question_generation_failed"),
                "message": generation_error.get("content", "Question generation failed."),
            },
        )

    unique_pairs: dict[str, dict[str, Any]] = {}
    for pair in pairs:
        stem = str(pair.get("question") or "").strip()
        answer = str(pair.get("correct_answer") or "").strip()
        if not stem or not answer:
            continue
        identity = hashlib.sha256(
            json.dumps(
                {
                    "scope": [body.exam_id, body.subject_id],
                    "path": body.learning_path_id,
                    "knowledge_point": canonical_knowledge_point_id,
                    "source_artifacts": source_artifacts,
                    "stem": stem,
                    "answer": answer,
                },
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()[:24]
        unique_pairs.setdefault(identity, pair)
    if len(unique_pairs) < 2:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error_code": "question_generation_incomplete",
                "message": "The native Quiz pipeline did not produce enough validated questions.",
            },
        )

    source = {
        "kind": "deeptutor_native_quiz",
        "learning_path_id": body.learning_path_id,
        "knowledge_point_id": canonical_knowledge_point_id,
        "source_artifacts": source_artifacts,
    }
    questions = []
    for identity, pair in list(unique_pairs.items())[: body.num_questions]:
        options = pair.get("options")
        option_text = ""
        if isinstance(options, dict) and options:
            option_text = "\n" + "\n".join(
                f"{key}. {value}" for key, value in options.items()
            )
        difficulty = {"easy": 0.3, "medium": 0.55, "hard": 0.8}.get(
            str(pair.get("difficulty") or body.difficulty).lower(),
            0.5,
        )
        questions.append(
            {
                "question_id": f"generated:{identity}",
                "stem": f"{str(pair['question']).strip()}{option_text}",
                "knowledge_point_ids": [canonical_knowledge_point_id],
                "difficulty": difficulty,
                "reference_answer": str(pair["correct_answer"]).strip(),
                "grading_rubric": {
                    "required_steps": [
                        {
                            "id": "expected_answer",
                            "description": str(
                                pair.get("explanation") or pair["correct_answer"]
                            ).strip(),
                        }
                    ],
                    "source": source,
                },
            }
        )
    return tuple(Question.model_validate(item) for item in questions)


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
