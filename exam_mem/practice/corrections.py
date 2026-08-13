"""Confirmed, event-driven Learning Memory corrections for Stage 07."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Annotated, Protocol

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    JsonValue,
    StringConstraints,
    model_validator,
)

from exam_mem.backends.baseline import BackendWriteConflict
from exam_mem.contracts import (
    CorrectionSource,
    EvidenceQuality,
    EvidenceQualityReason,
    ExplicitCorrection,
    LearningContext,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    MemoryNamespace,
    MemoryScope,
    MemoryUpdateCandidate,
    MemoryValue,
)
from exam_mem.domain.slot_key import validate_slot_key
from exam_mem.domain.taxonomy import KnowledgePointStatus, load_taxonomy
from exam_mem.lifecycle import (
    LifecycleCandidateSnapshot,
    MemoryRelation,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    resolve_relation_output,
    validate_relation_candidate_pool,
)

from .memory import MemoryWriteResult
from .memory_workbench import LearningMemoryQueryService
from .trace import PracticeSpanName, PracticeTraceRecorder

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class StrictCorrectionModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ExplicitCorrectionRequest(StrictCorrectionModel):
    """Server-owned Scope plus one user-confirmed correction command."""

    context: LearningContext
    memory_namespace: MemoryNamespace
    target_memory_id: NonEmptyString
    session_id: NonEmptyString
    idempotency_key: NonEmptyString
    statement: NonEmptyString
    occurred_at: AwareDatetime
    trace_id: NonEmptyString
    source: CorrectionSource = CorrectionSource.USER
    replacement_value: MemoryValue | None = None
    uncertain: bool = False
    confirmed: bool

    @model_validator(mode="after")
    def validate_confirmation_and_replacement(self) -> ExplicitCorrectionRequest:
        if not self.confirmed:
            raise ValueError("explicit correction requires user confirmation")
        if (
            self.replacement_value is not None
            and self.replacement_value.type is not self.memory_namespace
        ):
            raise ValueError("replacement value type must match memory_namespace")
        return self


class CorrectionError(RuntimeError):
    """Structured refusal that occurs before a correction write."""

    def __init__(self, error_code: str, message: str) -> None:
        self.error_code = error_code
        super().__init__(message)


class CorrectionTargetReader(Protocol):
    async def get_target(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> ResolvedCorrectionTarget | None: ...


class CorrectionMemoryWriter(Protocol):
    async def write(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> MemoryWriteResult: ...

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None: ...


class RecommendationInputRefresher(Protocol):
    async def refresh(self, context: LearningContext) -> tuple[str, ...]: ...


class QueryServiceRecommendationRefresher:
    """Refresh the exact source rows consumed by the next deterministic rank."""

    def __init__(self, query_service: LearningMemoryQueryService) -> None:
        self._query_service = query_service

    async def refresh(self, context: LearningContext) -> tuple[str, ...]:
        return await self._query_service.recommendation_inputs(context)


@dataclass(frozen=True, slots=True)
class ExplicitCorrectionResult:
    event: LearningEvent
    candidate: MemoryUpdateCandidate
    memory_result: MemoryWriteResult
    recommendation_source_memory_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ResolvedCorrectionTarget:
    """One scoped target plus canonical knowledge points from its L1 provenance."""

    memory: LearningMemory
    knowledge_point_ids: tuple[str, ...]


class ConfirmedCorrectionRelationClassifier:
    """Bind a confirmed correction to its server-selected target, without an LLM."""

    async def classify(
        self,
        candidate: MemoryUpdateCandidate,
        candidate_snapshots: tuple[LifecycleCandidateSnapshot, ...]
        | list[LifecycleCandidateSnapshot],
    ) -> ResolvedRelationClassification:
        ordered = validate_relation_candidate_pool(candidate, candidate_snapshots)
        target_id = candidate.evidence.get("target_memory_id")
        confidence = candidate.evidence.get("correction_confidence")
        if not isinstance(target_id, str) or not isinstance(confidence, (float, int)):
            raise ValueError("confirmed correction evidence is incomplete")
        matching_indexes = [
            index
            for index, snapshot in enumerate(ordered, start=1)
            if snapshot.memory.memory_id == target_id
        ]
        if len(matching_indexes) != 1:
            raise ValueError("confirmed correction target is not authoritative")
        return resolve_relation_output(
            RelationClassifierOutput(
                candidate_display_number=matching_indexes[0],
                relation=MemoryRelation.CONTRADICTORY,
                confidence=float(confidence),
                reason="user_confirmed_explicit_correction",
            ),
            ordered,
        )


class ExplicitCorrectionService:
    """Resolve one current target and reuse the frozen Lifecycle write path."""

    def __init__(
        self,
        *,
        target_reader: CorrectionTargetReader,
        memory_writer: CorrectionMemoryWriter,
        recommendation_refresher: RecommendationInputRefresher,
        trace: PracticeTraceRecorder,
    ) -> None:
        self._target_reader = target_reader
        self._memory_writer = memory_writer
        self._recommendations = recommendation_refresher
        self._trace = trace
        self._taxonomy = load_taxonomy("math1_v1")

    async def apply(
        self,
        request: ExplicitCorrectionRequest,
    ) -> ExplicitCorrectionResult:
        if request.trace_id != self._trace.trace_id:
            raise CorrectionError(
                "correction_trace_mismatch",
                "request trace_id does not match the bound runtime",
            )
        resolve_started = self._trace.start()
        scope = MemoryScope(
            **request.context.model_dump(),
            memory_namespace=request.memory_namespace,
        )
        resolved_target = await self._target_reader.get_target(
            scope,
            request.target_memory_id,
        )
        if resolved_target is None:
            await self._trace.failed(
                name=PracticeSpanName.CORRECTION_TARGET_RESOLVED,
                started=resolve_started,
                input_summary={"target_memory_id": request.target_memory_id},
                error_code="correction_target_not_found",
            )
            raise CorrectionError(
                "correction_target_not_found",
                "no correction target exists in the authenticated Scope",
            )
        target = resolved_target.memory
        knowledge_point_ids = self._validated_knowledge_points(
            target,
            resolved_target.knowledge_point_ids,
        )
        await self._trace.completed(
            name=PracticeSpanName.CORRECTION_TARGET_RESOLVED,
            started=resolve_started,
            input_summary={"target_memory_id": request.target_memory_id},
            output_summary={
                "memory_namespace": target.scope.memory_namespace.value,
                "slot_key": target.slot_key,
            },
            related_record_ids=(target.memory_id,),
        )

        event, candidate = _build_event_and_candidate(
            request,
            target,
            knowledge_point_ids=knowledge_point_ids,
        )
        write_started = self._trace.start()
        apply_started = self._trace.start()
        try:
            memory_result = await self._memory_writer.write(event, [candidate])
        except Exception as exc:
            error_code = _error_code(exc, "correction_write_failed")
            await self._trace.failed(
                name=PracticeSpanName.CORRECTION_EVENT_APPENDED,
                started=write_started,
                input_summary=_trace_input(event),
                error_code=error_code,
                versions={"schema_version": 1},
            )
            if isinstance(exc, BackendWriteConflict):
                raise CorrectionError(error_code, str(exc)) from exc
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
            name=PracticeSpanName.CORRECTION_EVENT_APPENDED,
            started=write_started,
            input_summary=_trace_input(event),
            output_summary={"event_id": event.event_id},
            versions={"schema_version": 1},
            related_record_ids=(event.event_id, target.memory_id),
        )
        await self._trace.completed(
            name=PracticeSpanName.CORRECTION_LIFECYCLE_APPLIED,
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

        recommendation_started = self._trace.start()
        source_ids = await self._recommendations.refresh(request.context)
        await self._trace.completed(
            name=PracticeSpanName.RECOMMENDATION_REFRESHED,
            started=recommendation_started,
            input_summary={"event_id": event.event_id},
            output_summary={"eligible_source_count": len(source_ids)},
            versions={"recommendation_policy": "recommendation_policy_v1"},
            related_record_ids=tuple(dict.fromkeys((event.event_id, *source_ids))),
        )
        return ExplicitCorrectionResult(
            event=event,
            candidate=candidate,
            memory_result=memory_result,
            recommendation_source_memory_ids=source_ids,
        )

    def _validated_knowledge_points(
        self,
        target: LearningMemory,
        knowledge_point_ids: tuple[str, ...],
    ) -> tuple[str, ...]:
        ordered = tuple(dict.fromkeys(knowledge_point_ids))
        if not ordered:
            raise CorrectionError(
                "correction_target_provenance_invalid",
                "correction target has no canonical knowledge-point provenance",
            )
        for knowledge_point_id in ordered:
            node = self._taxonomy.get(knowledge_point_id)
            if (
                node is None
                or node.status is not KnowledgePointStatus.ACTIVE
                or self._taxonomy.children_of(knowledge_point_id)
            ):
                raise CorrectionError(
                    "correction_target_provenance_invalid",
                    "correction provenance must contain active taxonomy leaves",
                )
        if target.scope.memory_namespace in {
            MemoryNamespace.MASTERY,
            MemoryNamespace.ERROR_PATTERN,
        }:
            slot_knowledge_point_id = target.slot_key.split(":")[1]
            if slot_knowledge_point_id not in ordered:
                raise CorrectionError(
                    "correction_target_provenance_mismatch",
                    "correction provenance does not support the target slot",
                )
        return ordered


def recognize_correction_intent(message: str) -> str | None:
    """Recognize an explicit correction phrase and return its optional query text."""
    normalized = " ".join(message.strip().split())
    folded = normalized.casefold()
    markers = (
        "你记错了",
        "记忆不准确",
        "这条记忆不准确",
        "记忆错了",
        "remembered wrong",
        "memory is inaccurate",
        "memory is wrong",
    )
    for marker in markers:
        index = folded.find(marker.casefold())
        if index >= 0:
            remainder = normalized[index + len(marker) :].strip(" ：:，,。.!！?")
            return remainder
    return None


def _build_event_and_candidate(
    request: ExplicitCorrectionRequest,
    target: LearningMemory,
    *,
    knowledge_point_ids: tuple[str, ...],
) -> tuple[LearningEvent, MemoryUpdateCandidate]:
    if target.scope.model_dump(exclude={"memory_namespace"}) != request.context.model_dump():
        raise CorrectionError(
            "correction_target_scope_mismatch",
            "correction target is outside the authenticated Scope",
        )
    slot_key = str(validate_slot_key(target.slot_key))
    if slot_key.partition(":")[0] != request.memory_namespace.value:
        raise CorrectionError(
            "correction_target_invalid",
            "correction target namespace does not match its slot",
        )
    replacement = request.replacement_value or target.value
    if replacement.type is not request.memory_namespace:
        raise CorrectionError(
            "correction_replacement_namespace_mismatch",
            "replacement value type must match the target namespace",
        )
    confidence = 0.5 if request.uncertain else 1.0
    evidence_quality = (
        EvidenceQuality(
            confidence=confidence,
            reasons=[EvidenceQualityReason.USER_REPORTED_EXCEPTION],
        )
        if request.uncertain
        else EvidenceQuality()
    )
    event = LearningEvent(
        event_id=_event_id(request),
        idempotency_key=request.idempotency_key,
        event_type=LearningEventType.EXPLICIT_CORRECTION,
        context=request.context,
        session_id=request.session_id,
        knowledge_point_ids=list(knowledge_point_ids),
        evidence_quality=evidence_quality,
        correction=ExplicitCorrection(
            target_memory_ids=[target.memory_id],
            source=request.source,
            statement=request.statement,
        ),
        occurred_at=request.occurred_at,
    )
    candidate = MemoryUpdateCandidate(
        event_id=event.event_id,
        scope=target.scope,
        slot_key=target.slot_key,
        proposed_value=replacement,
        evidence={
            "target_memory_id": target.memory_id,
            "correction_source": request.source.value,
            "correction_confidence": confidence,
            "replacement_supplied": request.replacement_value is not None,
        },
    )
    return event, candidate


def _event_id(request: ExplicitCorrectionRequest) -> str:
    identity = "\x1f".join((request.context.user_id, request.idempotency_key)).encode()
    return f"learning_event:{hashlib.sha256(identity).hexdigest()}"


def _trace_input(event: LearningEvent) -> dict[str, JsonValue]:
    assert event.correction is not None
    return {
        "event_id": event.event_id,
        "target_memory_id": event.correction.target_memory_ids[0],
        "source": event.correction.source.value,
    }


def _error_code(error: Exception, fallback: str) -> str:
    code = getattr(error, "error_code", None)
    return code if isinstance(code, str) and code.strip() else fallback


__all__ = [
    "ConfirmedCorrectionRelationClassifier",
    "CorrectionError",
    "CorrectionMemoryWriter",
    "CorrectionTargetReader",
    "ExplicitCorrectionRequest",
    "ExplicitCorrectionResult",
    "ExplicitCorrectionService",
    "QueryServiceRecommendationRefresher",
    "RecommendationInputRefresher",
    "ResolvedCorrectionTarget",
    "recognize_correction_intent",
]
