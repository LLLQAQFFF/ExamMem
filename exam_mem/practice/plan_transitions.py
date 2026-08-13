"""Deterministic, event-driven plan transitions for Stage 07 practice."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import re
from typing import Annotated, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from exam_mem.contracts import (
    EvidenceQuality,
    EvidenceQualityReason,
    LearningContext,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    LifecycleState,
    MemoryNamespace,
    MemoryScope,
    MemoryUpdateCandidate,
    PlanStatus,
    PlanTransition,
    PlanTransitionSource,
    PlanValue,
)
from exam_mem.domain import KnowledgePointStatus, load_taxonomy
from exam_mem.domain.slot_key import build_plan_slot_key

from .memory import MemoryWriteResult
from .trace import PracticeSpanName, PracticeTraceRecorder

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]


class PlanTransitionRequest(BaseModel):
    """Shared authenticated material for one exact Plan transition."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    context: LearningContext
    target_memory_id: NonEmptyString
    session_id: NonEmptyString
    idempotency_key: NonEmptyString
    knowledge_point_ids: tuple[NonEmptyString, ...] = Field(min_length=1)
    reason: NonEmptyString
    occurred_at: AwareDatetime
    trace_id: NonEmptyString

    @model_validator(mode="after")
    def validate_knowledge_points_are_unique(self) -> PlanTransitionRequest:
        if len(self.knowledge_point_ids) != len(set(self.knowledge_point_ids)):
            raise ValueError("knowledge_point_ids must be unique")
        return self


class PracticeProgressTransitionRequest(PlanTransitionRequest):
    """Progress computed by the deterministic practice-progress rule."""

    progress: Probability


class UserPlanCancellationRequest(PlanTransitionRequest):
    """A confirmed or explicitly ambiguous user cancellation intent."""

    confirmed: bool


class SystemPlanExpirationRequest(PlanTransitionRequest):
    """A replayable system observation evaluated against the stored due date."""


class PlanTransitionError(RuntimeError):
    """Structured refusal before any plan event is written."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResolvedPlanTarget:
    """One scoped Plan plus canonical knowledge points from L1 provenance."""

    memory: LearningMemory
    knowledge_point_ids: tuple[str, ...]


class PlanTargetReader(Protocol):
    async def get_plan_target(
        self,
        context: LearningContext,
        target_memory_id: str,
    ) -> ResolvedPlanTarget | None: ...


class PlanMemoryWriter(Protocol):
    async def write(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> MemoryWriteResult: ...

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None: ...


@dataclass(frozen=True, slots=True)
class PlanTransitionResult:
    event: LearningEvent
    candidate: MemoryUpdateCandidate
    memory_result: MemoryWriteResult


class PlanTransitionService:
    """Resolve one authoritative target and delegate the outcome to Lifecycle."""

    def __init__(
        self,
        *,
        target_reader: PlanTargetReader,
        memory_writer: PlanMemoryWriter,
        trace: PracticeTraceRecorder,
        taxonomy_version: str = "math1_v1",
    ) -> None:
        self._target_reader = target_reader
        self._memory_writer = memory_writer
        self._trace = trace
        self._taxonomy = load_taxonomy(taxonomy_version)

    async def apply_practice_progress(
        self,
        request: PracticeProgressTransitionRequest,
    ) -> PlanTransitionResult:
        target, knowledge_point_ids = await self._target(request)
        value = _plan_value(target)
        if request.progress < value.progress:
            raise PlanTransitionError(
                "plan_progress_regression",
                "practice progress cannot move backwards",
            )
        event_id = _event_id(request)
        if (
            request.progress == value.progress
            and event_id not in target.provenance
            and request.progress < 1.0
        ):
            raise PlanTransitionError(
                "plan_progress_not_advanced",
                "practice progress must advance the selected plan",
            )
        status = PlanStatus.COMPLETED if request.progress == 1.0 else PlanStatus.IN_PROGRESS
        return await self._apply(
            request,
            target=target,
            status=status,
            source=PlanTransitionSource.PRACTICE_PROGRESS,
            progress=request.progress,
            evidence_quality=EvidenceQuality(),
            knowledge_point_ids=knowledge_point_ids,
        )

    async def apply_user_cancellation(
        self,
        request: UserPlanCancellationRequest,
    ) -> PlanTransitionResult:
        target, knowledge_point_ids = await self._target(request)
        evidence_quality = (
            EvidenceQuality()
            if request.confirmed
            else EvidenceQuality(
                confidence=0.5,
                reasons=[EvidenceQualityReason.AMBIGUOUS_RESPONSE],
            )
        )
        return await self._apply(
            request,
            target=target,
            status=PlanStatus.CANCELLED,
            source=PlanTransitionSource.USER,
            progress=_plan_value(target).progress,
            evidence_quality=evidence_quality,
            knowledge_point_ids=knowledge_point_ids,
        )

    async def apply_system_expiration(
        self,
        request: SystemPlanExpirationRequest,
    ) -> PlanTransitionResult:
        target, knowledge_point_ids = await self._target(request)
        value = _plan_value(target)
        if value.due_at is None or request.occurred_at < value.due_at:
            raise PlanTransitionError(
                "plan_not_expired",
                "the selected plan has not reached a stored due date",
            )
        return await self._apply(
            request,
            target=target,
            status=PlanStatus.EXPIRED,
            source=PlanTransitionSource.SYSTEM,
            progress=value.progress,
            evidence_quality=EvidenceQuality(),
            knowledge_point_ids=knowledge_point_ids,
        )

    async def _target(
        self,
        request: PlanTransitionRequest,
    ) -> tuple[LearningMemory, tuple[str, ...]]:
        if request.trace_id != self._trace.trace_id:
            raise PlanTransitionError(
                "plan_trace_mismatch",
                "request trace_id does not match the bound runtime",
            )
        resolved = await self._target_reader.get_plan_target(
            request.context,
            request.target_memory_id,
        )
        if resolved is None:
            raise PlanTransitionError(
                "plan_target_not_found",
                "no plan target exists in the authenticated learning context",
            )
        target = resolved.memory
        knowledge_point_ids = resolved.knowledge_point_ids
        if request.knowledge_point_ids != knowledge_point_ids:
            raise PlanTransitionError(
                "plan_knowledge_point_mismatch",
                "plan transition knowledge points must match target provenance",
            )
        self._validate_knowledge_points(knowledge_point_ids)
        expected_scope = MemoryScope(
            **request.context.model_dump(),
            memory_namespace=MemoryNamespace.PLAN,
        )
        if target.scope != expected_scope:
            raise PlanTransitionError(
                "plan_target_scope_mismatch",
                "the selected plan target is outside the authenticated Scope",
            )
        expected_slot = str(
            build_plan_slot_key(request.context.exam_id, request.context.subject_id)
        )
        if target.slot_key != expected_slot or not isinstance(target.value, PlanValue):
            raise PlanTransitionError(
                "plan_target_invalid",
                "the selected target is not a valid Plan Memory",
            )
        event_id = _event_id(request)
        if (
            target.lifecycle_state is not LifecycleState.ACTIVE
            and event_id not in target.provenance
        ):
            raise PlanTransitionError(
                "plan_target_not_current",
                "a historical Plan version cannot receive a new transition",
            )
        return target, knowledge_point_ids

    def _validate_knowledge_points(self, knowledge_point_ids: tuple[str, ...]) -> None:
        for knowledge_point_id in knowledge_point_ids:
            node = self._taxonomy.get(knowledge_point_id)
            if (
                node is None
                or node.status is not KnowledgePointStatus.ACTIVE
                or self._taxonomy.children_of(knowledge_point_id)
            ):
                raise PlanTransitionError(
                    "plan_knowledge_point_invalid",
                    "plan transition knowledge points must be active taxonomy leaves",
                )

    async def _apply(
        self,
        request: PlanTransitionRequest,
        *,
        target: LearningMemory,
        status: PlanStatus,
        source: PlanTransitionSource,
        progress: float,
        evidence_quality: EvidenceQuality,
        knowledge_point_ids: tuple[str, ...],
    ) -> PlanTransitionResult:
        target_value = _plan_value(target)
        event = LearningEvent(
            event_id=_event_id(request),
            idempotency_key=request.idempotency_key,
            event_type=LearningEventType.PLAN_TRANSITION,
            context=request.context,
            session_id=request.session_id,
            knowledge_point_ids=list(knowledge_point_ids),
            evidence_quality=evidence_quality,
            plan_transition=PlanTransition(
                target_memory_id=target.memory_id,
                to_status=status,
                source=source,
                reason=request.reason,
            ),
            occurred_at=request.occurred_at,
        )
        candidate = MemoryUpdateCandidate(
            event_id=event.event_id,
            scope=target.scope,
            slot_key=target.slot_key,
            proposed_value=PlanValue(
                goal=target_value.goal,
                status=status,
                progress=progress,
                due_at=target_value.due_at,
            ),
            evidence={
                "plan_transition_source": source.value,
                "plan_transition_status": status.value,
                "target_memory_id": target.memory_id,
            },
        )
        write_started = self._trace.start()
        apply_started = self._trace.start()
        try:
            memory_result = await self._memory_writer.write(event, [candidate])
        except Exception as exc:
            await self._trace.failed(
                name=PracticeSpanName.PLAN_TRANSITION_APPENDED,
                started=write_started,
                input_summary=_trace_input(event),
                error_code=_error_code(exc, "plan_transition_write_failed"),
                versions={"schema_version": 1},
            )
            raise
        related_ids = tuple(
            dict.fromkeys(
                (
                    event.event_id,
                    target.memory_id,
                    *(
                        memory_id
                        for decision in memory_result.decisions
                        for memory_id in decision.target_memory_ids
                    ),
                )
            )
        )
        await self._trace.completed(
            name=PracticeSpanName.PLAN_TRANSITION_APPENDED,
            started=write_started,
            input_summary=_trace_input(event),
            output_summary={"event_id": event.event_id},
            versions={"schema_version": 1},
            related_record_ids=(event.event_id, target.memory_id),
        )
        await self._trace.completed(
            name=PracticeSpanName.PLAN_TRANSITION_APPLIED,
            started=apply_started,
            input_summary={"event_id": event.event_id},
            output_summary={
                "operations": [decision.operation.value for decision in memory_result.decisions]
            },
            versions={"lifecycle_policy": "lifecycle_policy_v1"},
            related_record_ids=related_ids,
        )
        if memory_result.projection_requests:
            projection_started = self._trace.start()
            try:
                await self._memory_writer.refresh_after_commit(memory_result)
            except Exception as exc:
                await self._trace.failed(
                    name=PracticeSpanName.STUDENT_MODEL_PROJECTED,
                    started=projection_started,
                    input_summary={"event_id": event.event_id},
                    error_code=_error_code(exc, "student_model_projection_failed"),
                    versions={"projection_version": 1},
                )
                raise
            await self._trace.completed(
                name=PracticeSpanName.STUDENT_MODEL_PROJECTED,
                started=projection_started,
                input_summary={"event_id": event.event_id},
                output_summary={"projection_refreshed": True},
                versions={"projection_version": 1},
                related_record_ids=(event.event_id,),
            )
        return PlanTransitionResult(
            event=event,
            candidate=candidate,
            memory_result=memory_result,
        )


_PLAN_CANCELLATION_PATTERNS = (
    (
        re.compile(
            r"(?:可能|也许|考虑)(?:要)?取消(?:这个|该|我的)?计划[：:\s]*(.*)",
            re.IGNORECASE,
        ),
        False,
    ),
    (
        re.compile(r"(?:取消|停止|不再执行)(?:这个|该|我的)?计划[：:\s]*(.*)", re.IGNORECASE),
        True,
    ),
    (
        re.compile(r"(?:maybe|might)\s+cancel\s+(?:this\s+|my\s+)?plan[\s:,-]*(.*)", re.IGNORECASE),
        False,
    ),
    (
        re.compile(r"(?:cancel|stop)\s+(?:this\s+|my\s+)?plan[\s:,-]*(.*)", re.IGNORECASE),
        True,
    ),
)


@dataclass(frozen=True, slots=True)
class PlanCancellationIntent:
    query: str
    confirmed: bool


def recognize_plan_cancellation_intent(message: str) -> PlanCancellationIntent | None:
    """Return an optional target hint; recognition never selects or writes a Plan."""

    text = message.strip()
    if not text:
        return None
    for pattern, confirmed in _PLAN_CANCELLATION_PATTERNS:
        match = pattern.search(text)
        if match is not None:
            return PlanCancellationIntent(
                query=match.group(1).strip(),
                confirmed=confirmed,
            )
    return None


def _event_id(request: PlanTransitionRequest) -> str:
    identity = "\x1f".join((request.context.user_id, request.idempotency_key)).encode()
    return f"learning_event:{hashlib.sha256(identity).hexdigest()}"


def _plan_value(memory: LearningMemory) -> PlanValue:
    if not isinstance(memory.value, PlanValue):
        raise PlanTransitionError(
            "plan_target_invalid",
            "the selected target is not a valid Plan Memory",
        )
    return memory.value


def _trace_input(event: LearningEvent) -> dict[str, str]:
    assert event.plan_transition is not None
    return {
        "event_id": event.event_id,
        "target_memory_id": event.plan_transition.target_memory_id,
        "to_status": event.plan_transition.to_status.value,
        "source": event.plan_transition.source.value,
    }


def _error_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "error_code", None)
    return code if isinstance(code, str) and code.strip() else fallback


__all__ = [
    "PlanMemoryWriter",
    "PlanCancellationIntent",
    "PlanTargetReader",
    "PlanTransitionError",
    "PlanTransitionRequest",
    "PlanTransitionResult",
    "PlanTransitionService",
    "PracticeProgressTransitionRequest",
    "SystemPlanExpirationRequest",
    "UserPlanCancellationRequest",
    "ResolvedPlanTarget",
    "recognize_plan_cancellation_intent",
]
