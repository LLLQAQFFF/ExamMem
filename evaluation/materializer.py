"""Materialize layer-isolated backend inputs from controlled cases."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from evaluation.contracts.case import EvaluationCase, GoldOperation
from exam_mem.contracts import (
    ErrorPatternValue,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
    MemoryUpdateCandidate,
    PlanStatus,
    PlanValue,
)
from exam_mem.domain.slot_key import validate_slot_key


class CaseMaterializationError(ValueError):
    """Raised when controlled upstream truth cannot form a backend input."""


@dataclass(frozen=True, slots=True)
class MaterializedStep:
    step_id: str
    event: LearningEvent
    candidates: tuple[MemoryUpdateCandidate, ...]
    gold_operations: tuple[GoldOperation, ...]


def _operations_by_event(case: EvaluationCase) -> dict[str, tuple[GoldOperation, ...]]:
    grouped: dict[str, list[GoldOperation]] = {}
    for operation in case.gold_operations:
        grouped.setdefault(operation.event_id, []).append(operation)
    return {event_id: tuple(operations) for event_id, operations in grouped.items()}


def _target_memory(
    event: LearningEvent,
    memories: Sequence[LearningMemory],
) -> LearningMemory:
    target_id: str | None = None
    if event.event_type is LearningEventType.EXPLICIT_CORRECTION:
        assert event.correction is not None
        if len(event.correction.target_memory_ids) != 1:
            raise CaseMaterializationError("controlled correction requires one target")
        target_id = event.correction.target_memory_ids[0]
    elif event.event_type is LearningEventType.PLAN_TRANSITION:
        assert event.plan_transition is not None
        target_id = event.plan_transition.target_memory_id
    if target_id is None:
        raise CaseMaterializationError("event does not carry a memory target")
    matches = [memory for memory in memories if memory.memory_id == target_id]
    if len(matches) != 1:
        raise CaseMaterializationError(
            f"event target must resolve to exactly one current memory: {target_id}"
        )
    return matches[0]


def _candidate_value(
    event: LearningEvent,
    operation: GoldOperation,
    memories: Sequence[LearningMemory],
):
    namespace = MemoryNamespace(operation.slot_key.partition(":")[0])
    if event.event_type is LearningEventType.EXPLICIT_CORRECTION:
        return _target_memory(event, memories).value
    if event.event_type is LearningEventType.PLAN_TRANSITION:
        target = _target_memory(event, memories)
        if not isinstance(target.value, PlanValue) or event.plan_transition is None:
            raise CaseMaterializationError("plan transition target must contain PlanValue")
        status = event.plan_transition.to_status
        progress = 1.0 if status is PlanStatus.COMPLETED else target.value.progress
        return PlanValue(
            goal=target.value.goal,
            status=status,
            progress=progress,
            due_at=target.value.due_at,
        )
    if namespace is MemoryNamespace.MASTERY:
        if event.answer_correct is None:
            raise CaseMaterializationError("mastery candidate requires answer correctness")
        return MasteryValue(
            level=MasteryLevel.HIGH if event.answer_correct else MasteryLevel.LOW,
            score=1.0 if event.answer_correct else 0.0,
        )
    if namespace is MemoryNamespace.ERROR_PATTERN:
        if event.error_type is None:
            raise CaseMaterializationError("error-pattern candidate requires error_type")
        return ErrorPatternValue(
            error_type=event.error_type,
            summary=event.error_detail or "受控作答中识别出的错误模式",
            details=[event.error_detail or "受控作答错误证据"],
        )
    raise CaseMaterializationError(f"unsupported controlled candidate namespace: {namespace.value}")


def _candidate_evidence(event: LearningEvent) -> dict[str, object]:
    evidence: dict[str, object] = {
        "evaluation_upstream": "gold_normalized_slot",
        "evidence_confidence": event.evidence_quality.confidence,
        "temporary_exception": event.evidence_quality.is_temporary_exception,
    }
    if event.correction is not None:
        evidence.update(
            {
                "target_memory_id": event.correction.target_memory_ids[0],
                "correction_source": event.correction.source.value,
                "correction_confidence": event.evidence_quality.confidence,
                "replacement_supplied": False,
            }
        )
    if event.plan_transition is not None:
        evidence.update(
            {
                "target_memory_id": event.plan_transition.target_memory_id,
                "plan_transition_source": event.plan_transition.source.value,
                "plan_transition_status": event.plan_transition.to_status.value,
            }
        )
    return evidence


def materialize_case(
    case: EvaluationCase,
    *,
    current_memories: Sequence[LearningMemory] | None = None,
) -> tuple[MaterializedStep, ...]:
    """Build one candidate per registered Gold slot without using Gold decisions."""
    operations_by_event = _operations_by_event(case)
    memories = tuple(current_memories if current_memories is not None else case.initial_memory)
    steps: list[MaterializedStep] = []
    for event in case.events:
        operations = operations_by_event[event.event_id]
        step_ids = {operation.step_id for operation in operations}
        if len(step_ids) != 1:
            raise CaseMaterializationError("one event must map to exactly one step")
        candidates: list[MemoryUpdateCandidate] = []
        for operation in operations:
            slot_key = str(validate_slot_key(operation.slot_key))
            namespace = MemoryNamespace(slot_key.partition(":")[0])
            scope = MemoryScope(
                **event.context.model_dump(),
                memory_namespace=namespace,
            )
            candidates.append(
                MemoryUpdateCandidate(
                    event_id=event.event_id,
                    scope=scope,
                    slot_key=slot_key,
                    proposed_value=_candidate_value(event, operation, memories),
                    evidence=_candidate_evidence(event),
                )
            )
        steps.append(
            MaterializedStep(
                step_id=next(iter(step_ids)),
                event=event,
                candidates=tuple(candidates),
                gold_operations=operations,
            )
        )
    return tuple(steps)


__all__ = [
    "CaseMaterializationError",
    "MaterializedStep",
    "materialize_case",
]
