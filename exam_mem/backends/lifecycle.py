"""Full Learning Memory backend built from the frozen Stage 05/06 services."""

from __future__ import annotations

import hashlib
from typing import Protocol

from pydantic import JsonValue

from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningEventType,
    LearningMemory,
    LifecycleDecision,
    LifecycleOperation,
    LifecycleState,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
    MemoryUpdateCandidate,
    StudentModel,
)
from exam_mem.domain.candidate_query import CandidateMatchReason, build_candidate_query
from exam_mem.domain.slot_key import validate_slot_key
from exam_mem.lifecycle import (
    LifecycleApplier,
    LifecycleCandidateSnapshot,
    LifecyclePolicyInput,
    LifecyclePolicyV1Config,
    MemoryRelation,
    ProjectionRefreshRequest,
    RelationClassifier,
    RelationClassifierOutput,
    ResolvedRelationClassification,
    build_projection_refresh_request,
    decide_lifecycle,
    resolve_validated_relation_output,
)
from exam_mem.lifecycle.state_machine import non_mutating_answer_reason
from exam_mem.storage.event_repository import AppendStatus, LearningEventRepository
from exam_mem.storage.memory_repository import LearningMemoryRepository
from exam_mem.storage.student_model_repository import StudentModelRepository

from .baseline import BackendWriteConflict


class CorrectionTargetNotCurrent(BackendWriteConflict):
    """Reject a new explicit correction whose target is no longer writable."""

    error_code = "correction_target_not_current"


class LifecycleEmbeddingClient(Protocol):
    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]: ...


class LifecycleMemoryBackend:
    """Append L1, decide deterministically, apply with CAS, and expose L3."""

    def __init__(
        self,
        *,
        event_repository: LearningEventRepository,
        memory_repository: LearningMemoryRepository,
        student_model_repository: StudentModelRepository,
        relation_classifier: RelationClassifier,
        applier: LifecycleApplier,
        trace_id: str | None = None,
        embedding_client: LifecycleEmbeddingClient | None = None,
        event_page_size: int = 1000,
    ) -> None:
        if trace_id is not None and not trace_id.strip():
            raise ValueError("trace_id must not be blank")
        if event_page_size < 1:
            raise ValueError("event_page_size must be greater than or equal to 1")
        self._event_repository = event_repository
        self._memory_repository = memory_repository
        self._student_model_repository = student_model_repository
        self._relation_classifier = relation_classifier
        self._applier = applier
        self._trace_id = trace_id
        self._embedding_client = embedding_client
        self._event_page_size = event_page_size
        self._projection_requests: list[ProjectionRefreshRequest] = []
        self._event_append_statuses: dict[str, AppendStatus] = {}

    async def record_event(self, event: LearningEvent) -> None:
        result = await self._event_repository.append(
            event,
            trace_id=self._trace_id or event.event_id,
        )
        if result.status is AppendStatus.CONFLICT:
            raise BackendWriteConflict("learning event identity conflicts with stored L1")
        self._event_append_statuses[event.event_id] = result.status

    async def update(
        self,
        event: LearningEvent,
        candidates: list[MemoryUpdateCandidate],
    ) -> list[LifecycleDecision]:
        _validate_candidates(event, candidates)
        append_status = self._event_append_statuses.pop(event.event_id, None)
        historical_events = await self._historical_events(event)
        decisions: list[LifecycleDecision] = []
        for candidate in candidates:
            if append_status is AppendStatus.EXISTING and await self._event_was_applied(
                event,
                candidate,
            ):
                decisions.append(await self._replay_decision(event, candidate))
                continue
            query = build_candidate_query(
                scope=candidate.scope,
                slot_key=candidate.slot_key,
                match_reason=CandidateMatchReason.EXACT_SLOT,
            )
            snapshots = tuple(await self._memory_repository.find_candidate_snapshots(query))
            _require_current_correction_target(event, candidate, snapshots)
            relation = await self._resolve_relation(event, candidate, snapshots)
            policy_input = LifecyclePolicyInput(
                event=event,
                candidate=candidate,
                candidate_snapshots=snapshots,
                relation=relation,
                historical_events=historical_events,
                evaluated_at=event.occurred_at,
            )
            policy_result = decide_lifecycle(policy_input)
            application = await self._applier.apply(
                policy_input,
                policy_result,
                decision_id=_decision_id(event, candidate),
                trace_id=self._trace_id or event.event_id,
                applied_at=event.occurred_at,
            )
            decisions.append(application.decision.policy_result.decision)
            refresh_request = build_projection_refresh_request(application)
            if refresh_request is not None:
                self._projection_requests.append(refresh_request)
        return decisions

    async def _resolve_relation(
        self,
        event: LearningEvent,
        candidate: MemoryUpdateCandidate,
        snapshots: tuple[LifecycleCandidateSnapshot, ...],
    ) -> ResolvedRelationClassification | None:
        if not snapshots:
            return None
        if (
            non_mutating_answer_reason(
                event,
                candidate,
                has_candidate_snapshots=True,
                minimum_confidence=LifecyclePolicyV1Config().minimum_candidate_confidence,
            )
            is not None
        ):
            return None
        if event.event_type is LearningEventType.EXPLICIT_CORRECTION:
            return await self._relation_classifier.classify(candidate, snapshots)
        if candidate.scope.memory_namespace is MemoryNamespace.MASTERY:
            return _resolve_mastery_relation(candidate, snapshots)
        if candidate.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN:
            return await self._relation_classifier.classify(candidate, snapshots)
        return None

    async def query_state(self, context: LearningContext) -> StudentModel | None:
        snapshot = await self._student_model_repository.get_latest(context)
        return None if snapshot is None else snapshot.model

    async def retrieve(
        self,
        scope: MemoryScope,
        query: str,
        top_k: int,
    ) -> list[LearningMemory]:
        if top_k < 1:
            raise ValueError("top_k must be greater than or equal to 1")
        try:
            slot_key = str(validate_slot_key(query))
        except ValueError:
            slot_key = None
        if slot_key is not None:
            if slot_key.partition(":")[0] != scope.memory_namespace.value:
                raise ValueError("retrieval slot_key namespace must match scope")
            candidates = await self._memory_repository.find_candidates(
                build_candidate_query(
                    scope=scope,
                    slot_key=slot_key,
                    match_reason=CandidateMatchReason.EXACT_SLOT,
                )
            )
            return candidates[:top_k]

        if not query.strip():
            raise ValueError("retrieval query must not be blank")
        if self._embedding_client is None:
            raise ValueError("semantic lifecycle retrieval requires an embedding client")
        embeddings = await self._embedding_client.embed(
            [query],
            input_type="search_query",
        )
        if len(embeddings) != 1:
            raise ValueError("embedding client must return exactly one query vector")
        return await self._memory_repository.find_similar(scope, embeddings[0], top_k)

    async def snapshot(self, context: LearningContext) -> dict[str, JsonValue]:
        memories: list[LearningMemory] = []
        for namespace in MemoryNamespace:
            memories.extend(
                await self._memory_repository.snapshot(
                    MemoryScope(
                        **context.model_dump(),
                        memory_namespace=namespace,
                    )
                )
            )
        return {
            "backend_mode": "lifecycle",
            "memories": [memory.model_dump(mode="json") for memory in memories],
        }

    def take_projection_requests(self) -> tuple[ProjectionRefreshRequest, ...]:
        requests = tuple(self._projection_requests)
        self._projection_requests.clear()
        return requests

    async def _event_was_applied(
        self,
        event: LearningEvent,
        candidate: MemoryUpdateCandidate,
    ) -> bool:
        return await self._memory_repository.event_was_applied(
            candidate.scope,
            candidate.slot_key,
            event.event_id,
        )

    async def _replay_decision(
        self,
        event: LearningEvent,
        candidate: MemoryUpdateCandidate,
    ) -> LifecycleDecision:
        snapshots = await self._memory_repository.list_slot_snapshots(
            candidate.scope,
            candidate.slot_key,
        )
        target_ids = [
            snapshot.memory.memory_id
            for snapshot in snapshots
            if event.event_id in snapshot.memory.provenance
        ]
        if not target_ids:
            raise BackendWriteConflict("applied learning event has no matching Memory provenance")
        return LifecycleDecision(
            operation=LifecycleOperation.NO_OP,
            target_memory_ids=target_ids,
            reason_code="already_applied_replay",
            confidence=1.0,
            policy_version="lifecycle_policy_v1",
        )

    async def _historical_events(
        self,
        current_event: LearningEvent,
    ) -> tuple[LearningEvent, ...]:
        events: list[LearningEvent] = []
        watermark: str | None = None
        while True:
            page = await self._event_repository.list_after(
                current_event.context,
                watermark,
                self._event_page_size,
            )
            events.extend(event for event in page if event.event_id != current_event.event_id)
            if len(page) < self._event_page_size:
                break
            watermark = page[-1].event_id
        return tuple(events)


def _validate_candidates(
    event: LearningEvent,
    candidates: list[MemoryUpdateCandidate],
) -> None:
    identities: set[tuple[str, str]] = set()
    for candidate in candidates:
        if candidate.event_id != event.event_id:
            raise ValueError("candidate event_id must match the current event")
        if candidate.scope.model_dump(exclude={"memory_namespace"}) != event.context.model_dump():
            raise ValueError("candidate scope must match the current event context")
        identity = (candidate.scope.memory_namespace.value, candidate.slot_key)
        if identity in identities:
            raise ValueError("candidate namespace and slot_key must be unique per update")
        identities.add(identity)


def _resolve_mastery_relation(
    candidate: MemoryUpdateCandidate,
    snapshots: tuple[LifecycleCandidateSnapshot, ...],
) -> ResolvedRelationClassification:
    proposed = candidate.proposed_value
    if not isinstance(proposed, MasteryValue):
        raise ValueError("mastery relation requires MasteryValue candidate")
    ordered = tuple(
        sorted(
            snapshots,
            key=lambda snapshot: (snapshot.memory.version, snapshot.memory.memory_id),
        )
    )
    active = [
        (index, snapshot)
        for index, snapshot in enumerate(ordered, start=1)
        if snapshot.memory.lifecycle_state is LifecycleState.ACTIVE
    ]
    if len(active) != 1:
        raise ValueError("mastery relation requires exactly one active snapshot")
    display_number, target = active[0]
    current = target.memory.value
    if not isinstance(current, MasteryValue):
        raise ValueError("mastery relation target must contain MasteryValue")

    same_single_value = len(ordered) == 1 and current.score == proposed.score
    relation = MemoryRelation.DUPLICATE if same_single_value else MemoryRelation.CONTRADICTORY
    canonical_id = candidate.slot_key.split(":", 1)[1]
    return resolve_validated_relation_output(
        candidate,
        ordered,
        RelationClassifierOutput(
            candidate_display_number=display_number,
            relation=relation,
            canonical_knowledge_point_id=canonical_id,
            error_type=None,
            error_summary=None,
            confidence=1.0,
            reason=(
                "Typed mastery scores are equal in one active slot."
                if relation is MemoryRelation.DUPLICATE
                else "Typed mastery scores represent opposing mastery evidence."
            ),
        ),
    )


def _require_current_correction_target(
    event: LearningEvent,
    candidate: MemoryUpdateCandidate,
    snapshots: tuple,
) -> None:
    if event.event_type is not LearningEventType.EXPLICIT_CORRECTION:
        return
    assert event.correction is not None
    target_id = candidate.evidence.get("target_memory_id")
    if not isinstance(target_id, str) or event.correction.target_memory_ids != [target_id]:
        raise ValueError("explicit correction candidate must match its single event target")
    if target_id not in {snapshot.memory.memory_id for snapshot in snapshots}:
        raise CorrectionTargetNotCurrent(
            "historical terminal versions cannot receive a new correction"
        )


def _decision_id(event: LearningEvent, candidate: MemoryUpdateCandidate) -> str:
    payload = "\x1f".join(
        (
            event.event_id,
            candidate.scope.user_id,
            candidate.scope.exam_id,
            candidate.scope.subject_id,
            candidate.scope.memory_namespace.value,
            candidate.slot_key,
        )
    ).encode("utf-8")
    return f"lifecycle_decision:{hashlib.sha256(payload).hexdigest()}"


__all__ = [
    "CorrectionTargetNotCurrent",
    "LifecycleEmbeddingClient",
    "LifecycleMemoryBackend",
]
