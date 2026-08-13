"""Practice workflow Trace spans, separate from Stage 08 rollout traces."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import time
from typing import Annotated

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    StringConstraints,
    model_validator,
)

NonEmptyString = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class PracticeSpanName(str, Enum):
    REQUEST_RECEIVED = "request_received"
    QUESTION_SELECTED = "question_selected"
    ANSWER_GRADED = "answer_graded"
    KNOWLEDGE_MAPPED = "knowledge_mapped"
    ERROR_DIAGNOSED = "error_diagnosed"
    EVENT_APPENDED = "event_appended"
    LIFECYCLE_DECIDED = "lifecycle_decided"
    LIFECYCLE_APPLIED = "lifecycle_applied"
    STUDENT_MODEL_PROJECTED = "student_model_projected"
    QUESTION_RECOMMENDED = "question_recommended"
    PLAN_TRANSITION_APPENDED = "plan_transition_appended"
    PLAN_TRANSITION_APPLIED = "plan_transition_applied"
    CORRECTION_TARGET_RESOLVED = "correction_target_resolved"
    CORRECTION_EVENT_APPENDED = "correction_event_appended"
    CORRECTION_LIFECYCLE_APPLIED = "correction_lifecycle_applied"
    RECOMMENDATION_REFRESHED = "recommendation_refreshed"
    RESPONSE_SENT = "response_sent"


class PracticeSpanStatus(str, Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class PracticeTraceSpan(BaseModel):
    """One JSON-safe, privacy-reduced workflow observation."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    trace_id: NonEmptyString
    step_id: Annotated[int, Field(ge=1)]
    name: PracticeSpanName
    status: PracticeSpanStatus
    input_summary: dict[str, JsonValue]
    output_summary: dict[str, JsonValue]
    versions: dict[str, JsonValue]
    started_at: AwareDatetime
    completed_at: AwareDatetime
    duration_ms: Annotated[float, Field(ge=0.0)]
    retry_count: Annotated[int, Field(ge=0)] = 0
    llm_calls: Annotated[int, Field(ge=0)] = 0
    input_tokens: Annotated[int, Field(ge=0)] = 0
    output_tokens: Annotated[int, Field(ge=0)] = 0
    error_code: NonEmptyString | None = None
    related_record_ids: tuple[NonEmptyString, ...] = ()

    @model_validator(mode="after")
    def validate_outcome(self) -> PracticeTraceSpan:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.status is PracticeSpanStatus.FAILED and self.error_code is None:
            raise ValueError("failed span requires error_code")
        if self.status is PracticeSpanStatus.COMPLETED and self.error_code is not None:
            raise ValueError("completed span must not contain error_code")
        if len(self.related_record_ids) != len(set(self.related_record_ids)):
            raise ValueError("related_record_ids must be unique")
        return self


class PracticeTracePersistenceError(RuntimeError):
    """Raised when an append-only span cannot be persisted exactly once."""


class PracticeTraceRecorder:
    """Create monotonic, privacy-reduced spans through a repository port."""

    def __init__(self, repository: object, *, trace_id: str) -> None:
        if not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        self._repository = repository
        self._trace_id = trace_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    def start(self) -> tuple[datetime, float]:
        return datetime.now(timezone.utc), time.perf_counter()

    async def completed(
        self,
        *,
        name: PracticeSpanName,
        started: tuple[datetime, float],
        input_summary: dict[str, JsonValue],
        output_summary: dict[str, JsonValue],
        versions: dict[str, JsonValue] | None = None,
        retry_count: int = 0,
        llm_calls: int = 0,
        related_record_ids: tuple[str, ...] = (),
    ) -> PracticeTraceSpan:
        return await self._append(
            name=name,
            status=PracticeSpanStatus.COMPLETED,
            started=started,
            input_summary=input_summary,
            output_summary=output_summary,
            versions=versions or {},
            retry_count=retry_count,
            llm_calls=llm_calls,
            error_code=None,
            related_record_ids=related_record_ids,
        )

    async def failed(
        self,
        *,
        name: PracticeSpanName,
        started: tuple[datetime, float],
        input_summary: dict[str, JsonValue],
        error_code: str,
        versions: dict[str, JsonValue] | None = None,
        retry_count: int = 0,
        llm_calls: int = 0,
    ) -> PracticeTraceSpan:
        return await self._append(
            name=name,
            status=PracticeSpanStatus.FAILED,
            started=started,
            input_summary=input_summary,
            output_summary={},
            versions=versions or {},
            retry_count=retry_count,
            llm_calls=llm_calls,
            error_code=error_code,
            related_record_ids=(),
        )

    async def _append(
        self,
        *,
        name: PracticeSpanName,
        status: PracticeSpanStatus,
        started: tuple[datetime, float],
        input_summary: dict[str, JsonValue],
        output_summary: dict[str, JsonValue],
        versions: dict[str, JsonValue],
        retry_count: int,
        llm_calls: int,
        error_code: str | None,
        related_record_ids: tuple[str, ...],
    ) -> PracticeTraceSpan:
        # Wall clocks can move backwards under NTP/VM adjustment while the
        # monotonic clock still advances. Preserve the span invariant without
        # discarding that call from the append-only audit trail.
        completed_at = max(started[0], datetime.now(timezone.utc))
        span = PracticeTraceSpan(
            trace_id=self._trace_id,
            step_id=await self._repository.next_step_id(self._trace_id),
            name=name,
            status=status,
            input_summary=input_summary,
            output_summary=output_summary,
            versions=versions,
            started_at=started[0],
            completed_at=completed_at,
            duration_ms=max(0.0, (time.perf_counter() - started[1]) * 1000),
            retry_count=retry_count,
            llm_calls=llm_calls,
            error_code=error_code,
            related_record_ids=related_record_ids,
        )
        result = await self._repository.append(span)
        status = getattr(result.status, "value", result.status)
        if status not in {"created", "existing"}:
            raise PracticeTracePersistenceError("practice trace span identity conflict")
        return span


__all__ = [
    "PracticeSpanName",
    "PracticeSpanStatus",
    "PracticeTraceSpan",
    "PracticeTracePersistenceError",
    "PracticeTraceRecorder",
]
