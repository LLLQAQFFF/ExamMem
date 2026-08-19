"""Authenticated HTTP boundary owned by the ExamMem plugin."""

from __future__ import annotations

import asyncio
import base64
import binascii
from contextlib import AbstractAsyncContextManager
from datetime import datetime, timezone
import hashlib
import json
import logging
from typing import Annotated, Any, Literal, Protocol
import uuid

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
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
    PluginLearningHost,
    PluginLearningObjective,
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
    Taxonomy,
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
from exam_mem.practice.learning_observation import (
    LEARNING_OBSERVATION_AGENT_VERSION,
    KnowledgePointOption,
    LearningObservationAgent,
    LearningObservationDraft,
)
from exam_mem.storage import (
    AppendStatus,
    AssessmentConflict,
    AssessmentNotFound,
    LearningObservationConflict,
    StudyPlanConflict,
    StudyPlanNotFound,
)
from exam_mem.study import StudyPlanTree

from .study_plan import StudyPlanOutlineImporter

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
_EXAM_ID = "postgraduate_entrance_exam"
_SUBJECT_ID = "math_1"
logger = logging.getLogger(__name__)


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
    taxonomy_version: NonEmptyString = "math1_v1"
    num_questions: Annotated[int, Field(ge=2, le=10)] = 4
    difficulty: Literal["auto", "easy", "medium", "hard"] = "auto"
    language: Literal["zh", "en"] = "zh"
    attachments: tuple[PracticeSourceAttachment, ...] = ()
    assessment_id: NonEmptyString | None = None
    assessment_title: Annotated[
        str | None,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    ] = None


class AssessmentAttemptBody(StrictApiModel):
    practice_session_id: NonEmptyString
    trace_id: NonEmptyString


class StudyPlanImportBody(StrictApiModel):
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
    source_kind: Literal["file", "url", "generated"]
    filename: NonEmptyString | None = None
    mime_type: NonEmptyString | None = None
    base64: NonEmptyString | None = None
    url: NonEmptyString | None = None
    request: NonEmptyString | None = None

    @model_validator(mode="after")
    def validate_source(self) -> StudyPlanImportBody:
        fields = {
            "file": (self.filename, self.mime_type, self.base64),
            "url": (self.url,),
            "generated": (self.request,),
        }
        if not all(fields[self.source_kind]):
            raise ValueError(f"{self.source_kind} study-plan source is incomplete")
        if self.source_kind != "file" and any((self.filename, self.mime_type, self.base64)):
            raise ValueError("file fields are only valid for source_kind='file'")
        if self.source_kind != "url" and self.url is not None:
            raise ValueError("url is only valid for source_kind='url'")
        if self.source_kind != "generated" and self.request is not None:
            raise ValueError("request is only valid for source_kind='generated'")
        if self.source_kind == "file":
            suffix = str(self.filename).lower().rsplit(".", maxsplit=1)[-1]
            allowed = {
                "pdf": {"application/pdf"},
                "txt": {"text/plain"},
                "md": {"text/markdown", "text/plain"},
            }
            if suffix not in allowed or str(self.mime_type).lower() not in allowed[suffix]:
                raise ValueError("study-plan sources currently support only PDF, TXT and Markdown")
        return self


class StudyPlanDraftBody(StrictApiModel):
    tree: StudyPlanTree


class OpenStudyObjectiveBody(StrictApiModel):
    version: Annotated[int, Field(ge=1)] | None = None
    language: Literal["zh", "en"] = "zh"


class AnalyzeConversationBody(StrictApiModel):
    session_id: NonEmptyString
    exam_id: NonEmptyString
    subject_id: NonEmptyString
    taxonomy_version: NonEmptyString
    language: Literal["zh", "en"] = "zh"


class SummarizeLearningPathBody(StrictApiModel):
    version: Annotated[int, Field(ge=1)] | None = None
    language: Literal["zh", "en"] = "zh"


class LearningObservationActionBody(StrictApiModel):
    action: Literal["confirm", "dismiss"]
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


class GradeDisputeBody(StrictApiModel):
    practice_session_id: NonEmptyString
    checkpoint_key: NonEmptyString
    idempotency_key: NonEmptyString
    reason: NonEmptyString
    exam_id: NonEmptyString = _EXAM_ID
    subject_id: NonEmptyString = _SUBJECT_ID


class GradeDispositionBody(StrictApiModel):
    action: Literal["uphold", "overturn"]
    practice_session_id: NonEmptyString
    checkpoint_key: NonEmptyString
    idempotency_key: NonEmptyString
    reason: NonEmptyString
    replacement_grade: GradeResult | None = None
    exam_id: NonEmptyString = _EXAM_ID
    subject_id: NonEmptyString = _SUBJECT_ID

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

    def open_plan_transitions(self, *, trace_id: str) -> AbstractAsyncContextManager[Any]: ...

    def open_product(self) -> AbstractAsyncContextManager[Any]: ...


class PracticeGenerationProgressSink(Protocol):
    async def __call__(self, event: dict[str, Any]) -> None: ...


async def _complete_attempt_if_finished(
    runtime_provider: RuntimeProvider,
    *,
    completed: bool,
    user_id: str,
    practice_session_id: str,
) -> None:
    if not completed:
        return
    async with runtime_provider.open_product() as runtime:
        await runtime.assessments.complete_attempt(
            user_id=user_id,
            practice_session_id=practice_session_id,
        )
        await runtime.connection.commit()


def build_router(
    runtime_provider: RuntimeProvider,
    *,
    turn_host: PluginTurnHost | None = None,
    settings_contribution: SettingsContribution | None = None,
    effective_settings: ExamMemSettings | None = None,
    outline_importer: StudyPlanOutlineImporter | None = None,
    learning_host: PluginLearningHost | None = None,
    observation_agent: LearningObservationAgent | None = None,
) -> APIRouter:
    """Build one router wired to the same Provider as the plugin Capability."""

    router = APIRouter()
    host = turn_host
    effective = effective_settings or ExamMemSettings()
    importer = outline_importer or StudyPlanOutlineImporter()
    learning = learning_host or PluginLearningHost()
    observer = observation_agent or LearningObservationAgent()

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

    async def generate_practice_result(
        body: GeneratedPracticeStartBody,
        progress: PracticeGenerationProgressSink | None = None,
    ) -> dict[str, Any]:
        if body.exam_id.startswith("plan:"):
            try:
                async with runtime_provider.open_product() as runtime:
                    await runtime.study_plans.require_active(
                        user_id=current_user_id(),
                        plan_id=body.exam_id.removeprefix("plan:"),
                    )
            except StudyPlanConflict as exc:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await _report_generation_progress(
            progress,
            stage="scope",
            completed_questions=0,
            total_questions=body.num_questions,
        )
        taxonomy = await _taxonomy_for_scope(
            runtime_provider,
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            taxonomy_version=body.taxonomy_version,
        )
        canonical_id = _canonical_knowledge_point(
            taxonomy,
            body.knowledge_point_id,
            body.knowledge_point_name,
        )
        questions = await _generate_practice_questions(
            runtime_host(),
            body=body,
            canonical_knowledge_point_id=canonical_id,
            progress=progress,
        )
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            taxonomy_version=body.taxonomy_version,
            questions=questions,
        )
        assessment_id = body.assessment_id or uuid.uuid4().hex
        assessment_title = body.assessment_title or f"{body.knowledge_point_name} 专项检测"
        user_id = current_user_id()
        await _report_generation_progress(
            progress,
            stage="persisting",
            completed_questions=len(questions),
            total_questions=len(questions),
        )
        try:
            async with runtime_provider.open_product() as runtime:
                assessment_version = await runtime.assessments.create_version(
                    assessment_id=assessment_id,
                    user_id=user_id,
                    exam_id=body.exam_id,
                    subject_id=body.subject_id,
                    taxonomy_version=body.taxonomy_version,
                    title=assessment_title,
                    knowledge_point_ids=[canonical_id],
                    questions=questions,
                    generation={
                        "learning_path_id": body.learning_path_id,
                        "difficulty": body.difficulty,
                        "language": body.language,
                        "source_files": [item.filename for item in body.attachments],
                    },
                )
                attempt = await runtime.assessments.start_attempt(
                    attempt_id=f"attempt:{uuid.uuid4().hex}",
                    user_id=user_id,
                    assessment_id=assessment_id,
                    version=assessment_version["version"],
                    practice_session_id=body.practice_session_id,
                    trace_id=body.trace_id,
                )
                await runtime.connection.commit()
        except AssessmentConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        await _report_generation_progress(
            progress,
            stage="starting",
            completed_questions=len(questions),
            total_questions=len(questions),
        )
        try:
            result = await _run_practice_turn(
                runtime_host(),
                content=(
                    f"Start the {body.knowledge_point_name} assessment"
                    if body.language == "en"
                    else f"开始 {body.knowledge_point_name} 专项练习"
                ),
                session_id=body.session_id,
                context=context,
            )
        except HTTPException:
            async with runtime_provider.open_product() as runtime:
                await runtime.assessments.fail_attempt(
                    user_id=user_id,
                    practice_session_id=body.practice_session_id,
                )
                await runtime.connection.commit()
            raise
        result["generation"] = {
            "learning_path_id": body.learning_path_id,
            "knowledge_point_id": canonical_id,
            "knowledge_point_name": body.knowledge_point_name,
            "question_count": len(questions),
            "language": body.language,
            "source_files": [item.filename for item in body.attachments],
        }
        result["assessment"] = {
            "assessment_id": assessment_id,
            "version": assessment_version["version"],
            "attempt_id": attempt["attempt_id"],
        }
        return result

    @router.post("/practice/generate")
    async def generate_practice(body: GeneratedPracticeStartBody) -> dict[str, Any]:
        return await generate_practice_result(body)

    @router.post("/practice/generate/stream")
    async def stream_generated_practice(body: GeneratedPracticeStartBody) -> StreamingResponse:
        async def event_stream():
            queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

            async def progress(event: dict[str, Any]) -> None:
                await queue.put({"type": "progress", **event})

            async def run() -> None:
                try:
                    result = await generate_practice_result(body, progress)
                    await queue.put({"type": "complete", "result": result})
                except HTTPException as exc:
                    await queue.put(
                        {
                            "type": "error",
                            "status": exc.status_code,
                            "detail": exc.detail,
                        }
                    )
                except Exception:
                    logger.exception("Streamed ExamMem practice generation failed")
                    await queue.put(
                        {
                            "type": "error",
                            "status": status.HTTP_500_INTERNAL_SERVER_ERROR,
                            "detail": "Practice generation failed.",
                        }
                    )

            task = asyncio.create_task(run())
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15)
                    except TimeoutError:
                        yield "\n"
                        continue
                    yield json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n"
                    if event["type"] in {"complete", "error"}:
                        break
            finally:
                if not task.done():
                    await task

        return StreamingResponse(
            event_stream(),
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.get("/catalog")
    async def get_catalog() -> dict[str, Any]:
        taxonomy = load_taxonomy("math1_v1")
        leaves = [
            node
            for node in taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
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

    @router.get("/study-plans")
    async def list_study_plans(
        archival: Literal["active", "archived", "all"] = "active",
    ) -> dict[str, Any]:
        user_id = current_user_id()
        archived = {"active": False, "archived": True, "all": None}[archival]
        async with runtime_provider.open_product() as runtime:
            plans = await runtime.study_plans.list(user_id=user_id, archived=archived)
        return {"plans": plans}

    @router.post("/study-plans/{plan_id}/archive")
    async def archive_study_plan(plan_id: NonEmptyString) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                plan = await runtime.study_plans.archive(user_id=current_user_id(), plan_id=plan_id)
                await runtime.connection.commit()
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"plan": plan}

    @router.post("/study-plans/{plan_id}/restore")
    async def restore_study_plan(plan_id: NonEmptyString) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                plan = await runtime.study_plans.restore(user_id=current_user_id(), plan_id=plan_id)
                await runtime.connection.commit()
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"plan": plan}

    @router.get("/assessments")
    async def list_assessments(
        archival: Literal["active", "archived", "all"] = "active",
    ) -> dict[str, Any]:
        archived = {"active": False, "archived": True, "all": None}[archival]
        async with runtime_provider.open_product() as runtime:
            items = await runtime.assessments.list(
                user_id=current_user_id(),
                archived=archived,
            )
        return {"assessments": items}

    @router.post("/assessments/{assessment_id}/archive")
    async def archive_assessment(assessment_id: NonEmptyString) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                assessment = await runtime.assessments.archive(
                    user_id=current_user_id(),
                    assessment_id=assessment_id,
                )
                await runtime.connection.commit()
        except AssessmentNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"assessment": assessment}

    @router.post("/assessments/{assessment_id}/restore")
    async def restore_assessment(assessment_id: NonEmptyString) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                assessment = await runtime.assessments.restore(
                    user_id=current_user_id(),
                    assessment_id=assessment_id,
                )
                await runtime.connection.commit()
        except AssessmentNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return {"assessment": assessment}

    @router.post("/assessments/{assessment_id}/versions/{version}/attempts")
    async def repeat_assessment(
        assessment_id: NonEmptyString,
        version: Annotated[int, Field(ge=1)],
        body: AssessmentAttemptBody,
    ) -> dict[str, Any]:
        user_id = current_user_id()
        try:
            async with runtime_provider.open_product() as runtime:
                stored = await runtime.assessments.get_version(
                    user_id=user_id,
                    assessment_id=assessment_id,
                    version=version,
                )
                assessment = stored["assessment"]
                attempt = await runtime.assessments.start_attempt(
                    attempt_id=f"attempt:{uuid.uuid4().hex}",
                    user_id=user_id,
                    assessment_id=assessment_id,
                    version=version,
                    practice_session_id=body.practice_session_id,
                    trace_id=body.trace_id,
                )
                await runtime.connection.commit()
        except AssessmentNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except AssessmentConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        context = _practice_context_payload(
            practice_session_id=body.practice_session_id,
            trace_id=body.trace_id,
            exam_id=assessment["exam_id"],
            subject_id=assessment["subject_id"],
            taxonomy_version=assessment["taxonomy_version"],
            questions=stored["questions"],
        )
        try:
            response_language = stored["questions"][0].response_language
            result = await _run_practice_turn(
                runtime_host(),
                content=(
                    f"Repeat assessment {assessment['title']}"
                    if response_language == "en"
                    else f"重新开始检测 {assessment['title']}"
                ),
                session_id=None,
                context=context,
            )
        except HTTPException:
            async with runtime_provider.open_product() as runtime:
                await runtime.assessments.fail_attempt(
                    user_id=user_id,
                    practice_session_id=body.practice_session_id,
                )
                await runtime.connection.commit()
            raise
        result["assessment"] = {
            "assessment_id": assessment_id,
            "version": version,
            "attempt_id": attempt["attempt_id"],
        }
        return result

    @router.get("/study-plans/{plan_id}")
    async def get_study_plan(plan_id: NonEmptyString) -> dict[str, Any]:
        user_id = current_user_id()
        try:
            async with runtime_provider.open_product() as runtime:
                plan = await runtime.study_plans.get(user_id=user_id, plan_id=plan_id)
                plan = await _study_plan_with_progress(
                    runtime.study_plans,
                    learning,
                    user_id=user_id,
                    plan=plan,
                )
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        return plan

    @router.post("/study-plans/import")
    async def import_study_plan(body: StudyPlanImportBody) -> dict[str, Any]:
        plan_id = uuid.uuid4().hex
        try:
            if body.source_kind == "file":
                imported = await importer.from_file(
                    plan_id=plan_id,
                    plan_name=body.name,
                    filename=str(body.filename),
                    mime_type=str(body.mime_type),
                    encoded=str(body.base64),
                )
            elif body.source_kind == "url":
                imported = await importer.from_url(
                    plan_id=plan_id,
                    plan_name=body.name,
                    url=str(body.url),
                )
            else:
                imported = await importer.generated(
                    plan_id=plan_id,
                    plan_name=body.name,
                    request=str(body.request),
                )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error_code": "study_plan_source_invalid", "message": str(exc)},
            ) from exc
        except RuntimeError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error_code": "study_plan_generation_failed", "message": str(exc)},
            ) from exc
        async with runtime_provider.open_product() as runtime:
            plan = await runtime.study_plans.create_draft(
                user_id=current_user_id(),
                plan_id=plan_id,
                tree=imported.tree,
                source_kind=imported.source_kind,
                source_metadata=imported.source_metadata,
            )
            await runtime.connection.commit()
        return plan

    @router.put("/study-plans/{plan_id}/draft")
    async def replace_study_plan_draft(
        plan_id: NonEmptyString, body: StudyPlanDraftBody
    ) -> dict[str, Any]:
        user_id = current_user_id()
        try:
            async with runtime_provider.open_product() as runtime:
                current = await runtime.study_plans.get(user_id=user_id, plan_id=plan_id)
                source = current["draft"] or current["published"]
                if source is None:
                    raise StudyPlanConflict("study plan has no source provenance")
                plan = await runtime.study_plans.replace_draft(
                    user_id=user_id,
                    plan_id=plan_id,
                    tree=body.tree,
                    source_kind=source["source_kind"],
                    source_metadata=source["source_metadata"],
                )
                await runtime.connection.commit()
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StudyPlanConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return plan

    @router.post("/study-plans/{plan_id}/publish")
    async def publish_study_plan(plan_id: NonEmptyString) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                plan = await runtime.study_plans.publish(user_id=current_user_id(), plan_id=plan_id)
                await runtime.connection.commit()
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except (StudyPlanConflict, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return plan

    @router.post("/study-plans/{plan_id}/objectives/{objective_id}/open")
    async def open_study_objective(
        plan_id: NonEmptyString,
        objective_id: NonEmptyString,
        body: OpenStudyObjectiveBody,
    ) -> dict[str, Any]:
        user_id = current_user_id()
        created_session_id: str | None = None
        try:
            async with runtime_provider.open_product() as runtime:
                await runtime.study_plans.require_active(user_id=user_id, plan_id=plan_id)
                version = await runtime.study_plans.get_version(
                    user_id=user_id,
                    plan_id=plan_id,
                    version=body.version,
                )
                plan_version = int(version["version"])
                tree = StudyPlanTree.model_validate(version["tree"])
                resolved = tree.objective(objective_id)
                if resolved is None:
                    raise StudyPlanNotFound("knowledge point not found in this plan version")
                subject, module, objective = resolved
                await runtime.study_plans.lock_objective_session(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    objective_id=objective_id,
                )
                existing = await runtime.study_plans.find_objective_session(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=plan_version,
                    objective_id=objective_id,
                )
                if existing is not None and await runtime_host().session_exists(
                    existing["host_session_id"]
                ):
                    if not await runtime_host().bind_session_context_sources(
                        existing["host_session_id"], ("exam_mem_learning",)
                    ):
                        raise RuntimeError("Host learning session could not be rebound")
                    await runtime.connection.commit()
                    return _objective_session_payload(
                        existing,
                        learning.objective_progress(
                            path_id=existing["host_path_id"],
                            objective_id=objective_id,
                        ),
                        created=False,
                    )

                host_path_id = _host_objective_path_id(user_id, plan_id, plan_version, objective_id)
                learning.ensure_single_objective_path(
                    path_id=host_path_id,
                    objective=PluginLearningObjective(
                        id=objective.id,
                        name=objective.name,
                        type=objective.type.value,
                        module_id=module.id,
                        module_name=f"{subject.name} / {module.name}",
                    ),
                )
                session, turn = await runtime_host().start_turn(
                    PluginTurnRequest(
                        content=_objective_start_prompt(
                            language=body.language,
                            plan_name=tree.name,
                            subject_name=subject.name,
                            module_name=module.name,
                            objective_name=objective.name,
                        ),
                        capability="mastery_path",
                        language=body.language,
                        mastery_path_id=host_path_id,
                        context_sources=("exam_mem_learning",),
                    )
                )
                created_session_id = session["id"]
                if existing is None:
                    link, _ = await runtime.study_plans.bind_objective_session(
                        link_id=f"study_session:{uuid.uuid4().hex}",
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        objective_id=objective_id,
                        host_path_id=host_path_id,
                        host_session_id=session["id"],
                        initial_turn_id=turn["id"],
                    )
                else:
                    link = await runtime.study_plans.replace_objective_session(
                        user_id=user_id,
                        plan_id=plan_id,
                        plan_version=plan_version,
                        objective_id=objective_id,
                        host_path_id=host_path_id,
                        host_session_id=session["id"],
                        initial_turn_id=turn["id"],
                    )
                await runtime.connection.commit()
                return _objective_session_payload(
                    link,
                    learning.objective_progress(
                        path_id=host_path_id,
                        objective_id=objective_id,
                    ),
                    created=True,
                )
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StudyPlanConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except Exception:
            if created_session_id is not None:
                await runtime_host().delete_session(created_session_id)
            raise

    @router.get("/learning-observations/conversations")
    async def list_observation_conversations() -> dict[str, Any]:
        conversations = await runtime_host().list_conversations(limit=75)
        return {
            "conversations": [
                {
                    "session_id": item.session_id,
                    "title": item.title,
                    "message_count": item.message_count,
                    "updated_at": item.updated_at,
                }
                for item in conversations
            ]
        }

    @router.post("/learning-observations/analyze-conversation")
    async def analyze_conversation(body: AnalyzeConversationBody) -> dict[str, Any]:
        taxonomy = await _taxonomy_for_scope(
            runtime_provider,
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            taxonomy_version=body.taxonomy_version,
        )
        transcript = await runtime_host().read_conversation(body.session_id)
        if transcript is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        try:
            draft = await observer.analyze(
                channel="chat",
                transcript=transcript.messages,
                knowledge_points=_observation_knowledge_points(taxonomy, body.language),
                language=body.language,
            )
        except (RuntimeError, ValueError) as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error_code": "learning_observation_failed",
                    "message": str(exc),
                },
            ) from exc
        if not draft.related_to_study:
            return {"related_to_study": False, "observation": None}
        observation = await _append_observation(
            runtime_provider,
            channel="chat",
            exam_id=body.exam_id,
            subject_id=body.subject_id,
            taxonomy_version=body.taxonomy_version,
            session_id=transcript.session_id,
            messages=transcript.messages,
            draft=draft,
            auto_confirm=False,
        )
        return {"related_to_study": True, "observation": observation}

    @router.post("/study-plans/{plan_id}/objectives/{objective_id}/summarize")
    async def summarize_study_objective(
        plan_id: NonEmptyString,
        objective_id: NonEmptyString,
        body: SummarizeLearningPathBody,
    ) -> dict[str, Any]:
        user_id = current_user_id()
        try:
            async with runtime_provider.open_product() as runtime:
                await runtime.study_plans.require_active(user_id=user_id, plan_id=plan_id)
                version = await runtime.study_plans.get_version(
                    user_id=user_id,
                    plan_id=plan_id,
                    version=body.version,
                )
                tree = StudyPlanTree.model_validate(version["tree"])
                resolved = tree.objective(objective_id)
                if resolved is None:
                    raise StudyPlanNotFound("knowledge point not found in this plan version")
                subject, _, _ = resolved
                link = await runtime.study_plans.find_objective_session(
                    user_id=user_id,
                    plan_id=plan_id,
                    plan_version=int(version["version"]),
                    objective_id=objective_id,
                )
            if link is None:
                raise StudyPlanNotFound("learning path has no linked conversation")
            transcript = await runtime_host().read_conversation(link["host_session_id"])
            if transcript is None:
                raise StudyPlanNotFound("linked learning conversation was not found")
            taxonomy_version = str(version["taxonomy_versions"][subject.id])
            taxonomy = await _taxonomy_for_scope(
                runtime_provider,
                exam_id=f"plan:{plan_id}",
                subject_id=subject.id,
                taxonomy_version=taxonomy_version,
            )
            try:
                draft = await observer.analyze(
                    channel="learning_path",
                    transcript=transcript.messages,
                    knowledge_points=_observation_knowledge_points(taxonomy, body.language),
                    language=body.language,
                    fixed_knowledge_point_id=objective_id,
                )
            except (RuntimeError, ValueError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error_code": "learning_observation_failed",
                        "message": str(exc),
                    },
                ) from exc
            observation = await _append_observation(
                runtime_provider,
                channel="learning_path",
                exam_id=f"plan:{plan_id}",
                subject_id=subject.id,
                taxonomy_version=taxonomy_version,
                session_id=transcript.session_id,
                messages=transcript.messages,
                draft=draft,
                auto_confirm=True,
            )
        except StudyPlanNotFound as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except StudyPlanConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"observation": observation}

    @router.get("/learning-observations")
    async def list_learning_observations(
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        taxonomy_version: NonEmptyString | None = None,
        channel: Literal["chat", "learning_path"] | None = None,
        observation_status: Literal["pending", "confirmed", "dismissed"] | None = None,
        knowledge_point_id: Annotated[list[NonEmptyString] | None, Query()] = None,
    ) -> dict[str, Any]:
        async with runtime_provider.open_product() as runtime:
            observations = await runtime.observations.list(
                user_id=current_user_id(),
                exam_id=exam_id,
                subject_id=subject_id,
                taxonomy_version=taxonomy_version,
                channel=channel,
                knowledge_point_ids=tuple(knowledge_point_id or ()),
                status=observation_status,
            )
        return {"observations": observations}

    @router.post("/learning-observations/{observation_id}/actions")
    async def act_on_learning_observation(
        observation_id: NonEmptyString,
        body: LearningObservationActionBody,
    ) -> dict[str, Any]:
        try:
            async with runtime_provider.open_product() as runtime:
                observation = await runtime.observations.append_action(
                    action_id=f"observation_action:{uuid.uuid4().hex}",
                    observation_id=observation_id,
                    user_id=current_user_id(),
                    action=body.action,
                    idempotency_key=body.idempotency_key,
                )
                await runtime.connection.commit()
        except LookupError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except LearningObservationConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {"observation": observation}

    @router.get("/learning-archive")
    async def get_learning_archive(
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        taxonomy_version: NonEmptyString | None = None,
        knowledge_point_id: Annotated[list[NonEmptyString] | None, Query()] = None,
        memory_namespace: Annotated[list[MemoryNamespace] | None, Query()] = None,
        lifecycle_state: Annotated[list[LifecycleState] | None, Query()] = None,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            archive = await runtime.learning_archive.read(
                context,
                taxonomy_version=taxonomy_version,
                knowledge_point_ids=tuple(knowledge_point_id or ()),
                namespaces=tuple(memory_namespace or ()),
                lifecycle_states=tuple(lifecycle_state or ()),
            )
            observations = await runtime.observations.list(
                user_id=current_user_id(),
                exam_id=exam_id,
                subject_id=subject_id,
                taxonomy_version=taxonomy_version,
                channel="learning_path",
                knowledge_point_ids=tuple(knowledge_point_id or ()),
                status="confirmed",
            )
        return {**archive, "learning_path_observations": observations}

    @router.get("/learning-profile")
    async def get_learning_profile(
        exam_id: NonEmptyString,
        subject_id: NonEmptyString,
        taxonomy_version: NonEmptyString,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        taxonomy = await _taxonomy_for_scope(
            runtime_provider,
            exam_id=exam_id,
            subject_id=subject_id,
            taxonomy_version=taxonomy_version,
        )
        try:
            async with runtime_provider.open_product() as runtime:
                profile = await runtime.learning_profiles.get(
                    context=context,
                    taxonomy=taxonomy,
                    evaluated_at=datetime.now(timezone.utc),
                )
        except PracticeRuntimeConfigurationError as exc:
            raise _configuration_error(exc) from exc
        payload = profile.model_dump(mode="json")
        payload["context"].pop("user_id", None)
        return payload

    @router.post("/practice/answer")
    async def answer_practice(body: PracticeAnswerBody) -> dict[str, Any]:
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
            if replay is None:
                try:
                    await runtime.assessments.require_practice_active(
                        user_id=learning_context.user_id,
                        practice_session_id=body.practice_session_id,
                    )
                except AssessmentConflict as exc:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail=str(exc),
                    ) from exc
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
        result = await _run_practice_turn(
            runtime_host(),
            content=(
                "Submit answer" if _practice_response_language(context) == "en" else "提交答案"
            ),
            session_id=body.session_id,
            context=context,
        )
        await _complete_attempt_if_finished(
            runtime_provider,
            completed=result["practice"]["completed"],
            user_id=learning_context.user_id,
            practice_session_id=body.practice_session_id,
        )
        return result

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
            review = await runtime.products.get_practice_session(context, practice_session_id)
        if review is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
        return review

    @router.post("/practice/sessions/{practice_session_id}/resume")
    async def resume_practice(
        practice_session_id: NonEmptyString,
        exam_id: NonEmptyString = _EXAM_ID,
        subject_id: NonEmptyString = _SUBJECT_ID,
    ) -> dict[str, Any]:
        context = _authenticated_context(exam_id=exam_id, subject_id=subject_id)
        async with runtime_provider.open_product() as runtime:
            try:
                await runtime.assessments.require_practice_active(
                    user_id=context.user_id,
                    practice_session_id=practice_session_id,
                )
            except AssessmentConflict as exc:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=str(exc),
                ) from exc
            latest = await runtime.checkpoints.get_latest(context, practice_session_id)
        if latest is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Exam not found")
        result = await _run_practice_turn(
            runtime_host(),
            content=(
                "Resume assessment"
                if _practice_response_language(latest.checkpoint.context.model_dump(mode="json"))
                == "en"
                else "恢复练习"
            ),
            session_id=None,
            context=latest.checkpoint.context.model_dump(mode="json"),
        )
        await _complete_attempt_if_finished(
            runtime_provider,
            completed=result["practice"]["completed"],
            user_id=context.user_id,
            practice_session_id=practice_session_id,
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
        context = _authenticated_context(exam_id=body.exam_id, subject_id=body.subject_id)
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
        context = _authenticated_context(exam_id=body.exam_id, subject_id=body.subject_id)
        expected_chain = _review_chain_id(context, body.practice_session_id, body.checkpoint_key)
        if expected_chain != review_chain_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Review identity conflict"
            )
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
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Dispute not found"
                )
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
                decision.model_dump(mode="json") for decision in result.memory_result.decisions
            ],
            "recommendation_source_memory_ids": list(result.recommendation_source_memory_ids),
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
                decision.model_dump(mode="json") for decision in result.memory_result.decisions
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
    taxonomy_version: str = "math1_v1",
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
        "taxonomy_version": taxonomy_version,
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
                language=_practice_response_language(context),
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


def _practice_response_language(context: dict[str, Any]) -> Literal["zh", "en"]:
    catalog = context.get("question_catalog")
    if isinstance(catalog, list) and catalog:
        first = catalog[0]
        if isinstance(first, dict):
            rubric = first.get("grading_rubric")
            if isinstance(rubric, dict) and rubric.get("response_language") == "en":
                return "en"
    return "zh"


def _validate_controlled_scope(exam_id: str, subject_id: str) -> None:
    if exam_id != _EXAM_ID or subject_id != _SUBJECT_ID:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "practice_scope_not_supported",
                "message": "The selected exam scope is not available in the controlled catalog.",
            },
        )


def _canonical_knowledge_point(
    taxonomy: Taxonomy, knowledge_point_id: str, knowledge_point_name: str
) -> str:
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


async def _taxonomy_for_scope(
    runtime_provider: RuntimeProvider,
    *,
    exam_id: str,
    subject_id: str,
    taxonomy_version: str,
) -> Taxonomy:
    if exam_id == _EXAM_ID and subject_id == _SUBJECT_ID and taxonomy_version == "math1_v1":
        return load_taxonomy("math1_v1")
    try:
        async with runtime_provider.open_product() as runtime:
            return await runtime.study_plans.taxonomy(
                user_id=current_user_id(),
                exam_id=exam_id,
                subject_id=subject_id,
                taxonomy_version=taxonomy_version,
            )
    except StudyPlanNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error_code": "practice_scope_not_published",
                "message": "The selected knowledge point is not in a published study plan.",
            },
        ) from exc


def _observation_knowledge_points(
    taxonomy: Taxonomy,
    language: Literal["zh", "en"],
) -> tuple[KnowledgePointOption, ...]:
    del language  # Taxonomy v1 has one canonical display label.
    options = []
    for node in taxonomy.nodes:
        if node.status is not KnowledgePointStatus.ACTIVE or taxonomy.children_of(node.id):
            continue
        parent = taxonomy.get(node.parent_id) if node.parent_id is not None else None
        options.append(
            KnowledgePointOption(
                id=node.id,
                name=node.name_zh,
                module_name=parent.name_zh if parent is not None else node.name_zh,
            )
        )
    return tuple(options)


async def _append_observation(
    runtime_provider: RuntimeProvider,
    *,
    channel: Literal["chat", "learning_path"],
    exam_id: str,
    subject_id: str,
    taxonomy_version: str,
    session_id: str,
    messages: tuple[dict[str, str], ...],
    draft: LearningObservationDraft,
    auto_confirm: bool,
) -> dict[str, Any]:
    source_payload = {
        "channel": channel,
        "scope": [exam_id, subject_id, taxonomy_version],
        "session_id": session_id,
        "messages": list(messages),
        "agent_contract_version": LEARNING_OBSERVATION_AGENT_VERSION,
    }
    fingerprint = hashlib.sha256(
        json.dumps(
            source_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    try:
        async with runtime_provider.open_product() as runtime:
            observation = await runtime.observations.append(
                observation_id=f"observation:{uuid.uuid4().hex}",
                user_id=current_user_id(),
                exam_id=exam_id,
                subject_id=subject_id,
                taxonomy_version=taxonomy_version,
                channel=channel,
                source_session_id=session_id,
                source_turn_ids=tuple(item["id"] for item in messages if item.get("id")),
                knowledge_point_ids=draft.knowledge_point_ids,
                summary=draft.summary,
                rationale=draft.rationale,
                confidence=draft.confidence,
                agent_contract_version=LEARNING_OBSERVATION_AGENT_VERSION,
                source_fingerprint=fingerprint,
            )
            if auto_confirm:
                observation = await runtime.observations.append_action(
                    action_id=f"observation_action:{uuid.uuid4().hex}",
                    observation_id=observation["observation_id"],
                    user_id=current_user_id(),
                    action="confirm",
                    idempotency_key=f"auto-confirm:{observation['observation_id']}",
                )
            await runtime.connection.commit()
            return observation
    except LearningObservationConflict as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


async def _generate_practice_questions(
    host: PluginTurnHost,
    *,
    body: GeneratedPracticeStartBody,
    canonical_knowledge_point_id: str,
    progress: PracticeGenerationProgressSink | None = None,
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
    topic = _practice_generation_prompt(body)
    session: dict[str, Any] | None = None
    pairs: list[dict[str, Any]] = []
    generation_error: dict[str, Any] | None = None
    completed_questions = 0
    try:
        session, turn = await host.start_turn(
            PluginTurnRequest(
                content=topic,
                capability="deep_question",
                language=body.language,
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
            if event.get("type") == "stage_start":
                progress_stage = {
                    "exploring": "exploring",
                    "planning": "planning",
                    "quizzing": "generating",
                }.get(str(event.get("stage") or ""))
                if progress_stage is not None:
                    await _report_generation_progress(
                        progress,
                        stage=progress_stage,
                        completed_questions=completed_questions,
                        total_questions=body.num_questions,
                    )
            if event.get("type") == "error":
                generation_error = event
            if metadata.get("call_kind") == "quiz_question_emitted":
                pair = metadata.get("qa_pair")
                if isinstance(pair, dict):
                    pairs.append(pair)
                    completed_questions += 1
                    await _report_generation_progress(
                        progress,
                        stage="generating",
                        completed_questions=completed_questions,
                        total_questions=body.num_questions,
                    )
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
                    "language": body.language,
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
            option_text = "\n" + "\n".join(f"{key}. {value}" for key, value in options.items())
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
                    "response_language": body.language,
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


async def _report_generation_progress(
    sink: PracticeGenerationProgressSink | None,
    *,
    stage: Literal[
        "scope",
        "exploring",
        "planning",
        "generating",
        "persisting",
        "starting",
    ],
    completed_questions: int,
    total_questions: int,
) -> None:
    if sink is None:
        return
    await sink(
        {
            "stage": stage,
            "completed_questions": completed_questions,
            "total_questions": total_questions,
        }
    )


def _practice_generation_prompt(body: GeneratedPracticeStartBody) -> str:
    if body.language == "en":
        return (
            f"Generate {body.num_questions} progressively challenging practice questions "
            f'about the knowledge point "{body.knowledge_point_name}". '
            "Every question must be independently answerable. Write every question, answer, "
            "and explanation in English. You must respond in English, including all reasons and "
            "explanations. If attachments are present, use only their content as source material."
        )
    return (
        f"围绕知识点“{body.knowledge_point_name}”生成 {body.num_questions} 道递进练习题。"
        "每道题都必须能够独立作答。所有题目、答案、解析和理由必须使用简体中文；"
        "你必须全程用中文回答。如有附件，只能依据附件内容出题。"
    )


async def _study_plan_with_progress(
    repository,  # noqa: ANN001
    learning: PluginLearningHost,
    *,
    user_id: str,
    plan: dict[str, Any],
) -> dict[str, Any]:
    published = plan.get("published")
    if not isinstance(published, dict):
        return {**plan, "objective_sessions": {}}
    links = await repository.list_objective_sessions(
        user_id=user_id,
        plan_id=plan["plan_id"],
        plan_version=int(published["version"]),
    )
    sessions: dict[str, Any] = {}
    for link in links:
        try:
            progress = learning.objective_progress(
                path_id=link["host_path_id"],
                objective_id=link["objective_id"],
            )
        except RuntimeError:
            progress = {"status": "unavailable", "mastery": 0.0}
        sessions[link["objective_id"]] = _objective_session_payload(link, progress, created=False)
    return {**plan, "objective_sessions": sessions}


def _objective_session_payload(
    link: dict[str, Any], progress: dict[str, Any], *, created: bool
) -> dict[str, Any]:
    return {
        "objective_id": link["objective_id"],
        "session_id": link["host_session_id"],
        "initial_turn_id": link["initial_turn_id"],
        "chat_url": f"/home/{link['host_session_id']}",
        "created": created,
        "learning_status": progress["status"],
        "learning_mastery": progress["mastery"],
        "created_at": link["created_at"].isoformat(),
        "updated_at": link["updated_at"].isoformat(),
    }


def _host_objective_path_id(
    user_id: str, plan_id: str, plan_version: int, objective_id: str
) -> str:
    identity = "\x1f".join((user_id, plan_id, str(plan_version), objective_id)).encode()
    return f"em{hashlib.sha256(identity).hexdigest()[:28]}"


def _objective_start_prompt(
    *,
    language: str,
    plan_name: str,
    subject_name: str,
    module_name: str,
    objective_name: str,
) -> str:
    if language == "en":
        return (
            f'Start the learning unit "{objective_name}" in {plan_name} / '
            f"{subject_name} / {module_name}. First state the objective, required "
            "prerequisites, and a concise study plan, then begin tutoring. Focus only "
            "on this objective and use the mastery tools to record progress."
        )
    return (
        f"开始学习“{plan_name} / {subject_name} / {module_name} / {objective_name}”。"
        "请先说明本学习单元的学习目标、必要前置知识和简明学习安排，然后开始辅导。"
        "本次只聚焦这个知识点，并使用精通路径工具记录学习进度。"
    )


def _query_trace_id(context: LearningContext, operation: str) -> str:
    now = datetime.now().astimezone().isoformat()
    identity = "\x1f".join(
        (context.user_id, context.exam_id, context.subject_id, operation, now)
    ).encode()
    return f"exam_mem_query:{hashlib.sha256(identity).hexdigest()}"


def _operation_trace_id(context: LearningContext, operation: str, idempotency_key: str) -> str:
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
    "OpenStudyObjectiveBody",
    "PlanTransitionBody",
    "PracticeAnswerBody",
    "PracticeStartBody",
    "StudyPlanDraftBody",
    "StudyPlanImportBody",
    "build_router",
]
