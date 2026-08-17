"""Concrete adapters for the five frozen Stage 08 backend modes."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import timedelta
import hashlib
import json
import math
from pathlib import Path
import re
from time import monotonic
from typing import Any

from pydantic import JsonValue
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from deeptutor.plugins.host_services import complete
from deeptutor.services.config import resolve_llm_runtime_config
from deeptutor.services.memory import MemoryStore, memory_path_service_override
from deeptutor.services.memory.paths import L3_SLOTS
from deeptutor.services.memory.trace import TraceEvent, iter_by_ids, iter_since
from deeptutor.services.path_service import PathService
from evaluation.contracts.case import ActionType, EvaluationCase, EvaluationQuery, VersionRelation
from evaluation.contracts.trace import (
    LLMCallTrace,
    MemoryStateTrace,
    RecommendationTrace,
    TokenUsage,
    TraceError,
    TraceStage,
)
from evaluation.materializer import MaterializedStep
from exam_mem.backends import BackendMode, NoMemoryBackend
from exam_mem.backends.baseline import (
    AppendOnlyMemoryBackend,
    VectorMemoryBackend,
    _baseline_memory_view,
)
from exam_mem.backends.lifecycle import LifecycleMemoryBackend
from exam_mem.backends.native import NativeMemoryBackend, NativeMemoryClient, NativeMemoryEvent
from exam_mem.backends.protocol import MemoryBackend
from exam_mem.contracts import (
    ErrorPatternValue,
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
from exam_mem.domain import KnowledgePointStatus, load_taxonomy
from exam_mem.lifecycle import (
    DeepTutorRelationClassifierAdapter,
    LifecycleApplier,
    MemoryRelation,
    PostCommitProjectionRefresher,
    RelationClassifier,
    RelationClassifierOutput,
    resolve_validated_relation_output,
)
from exam_mem.practice.corrections import ConfirmedCorrectionRelationClassifier
from exam_mem.practice.provider import (
    _plan_sources_by_knowledge_point,
    _recommendation_candidate,
)
from exam_mem.practice.recommendation import RecommendationPolicyV1
from exam_mem.storage import (
    PostgresBaselineFactRepository,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    PostgresLifecycleAuditRepository,
    PostgresStudentModelRepository,
    StudentModelRebuildService,
)
from exam_mem.storage.baseline_fact_repository import BaselineFactRecord

_EMBEDDING_DIMENSION = 1024
_PRODUCING_OPERATIONS = {
    LifecycleOperation.ADD,
    LifecycleOperation.MERGE,
    LifecycleOperation.SUPERSEDE,
    LifecycleOperation.CONTESTED,
}


class EvaluationRecommendationPolicy:
    """Observe the production recommendation policy without question retrieval."""

    def __init__(self) -> None:
        taxonomy = load_taxonomy("math1_v1")
        self._policy = RecommendationPolicyV1(taxonomy=taxonomy)
        self._knowledge_point_ids = tuple(
            node.id
            for node in taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
        )

    def recommend(
        self,
        *,
        context: LearningContext,
        model: StudentModel | None = None,
        memories: Sequence[LearningMemory] = (),
        plan_memories: Sequence[LearningMemory] = (),
        plan_events: Sequence[LearningEvent] = (),
    ) -> RecommendationTrace:
        plan_sources = _plan_sources_by_knowledge_point(plan_memories, plan_events)
        candidates = tuple(
            _recommendation_candidate(
                knowledge_point_id=knowledge_point_id,
                model=model,
                memories=memories,
                plan_memories=plan_sources.get(knowledge_point_id, ()),
            )
            for knowledge_point_id in self._knowledge_point_ids
        )
        score = self._policy.rank(context=context, candidates=candidates)[0]
        features = score.candidate.features
        review_signal = max(
            features.weakness,
            features.stable_error,
            features.forgetting_risk,
            features.active_plan_priority,
        )
        if review_signal > 0:
            action_type = ActionType.RECOMMEND_REVIEW
        elif (
            model is not None and score.candidate.target_knowledge_point_id in model.mastered_points
        ):
            action_type = ActionType.AVOID_OVER_REVIEW
        else:
            action_type = ActionType.RECOMMEND_KNOWLEDGE_POINT
        return RecommendationTrace(
            action_type=action_type,
            knowledge_point_ids=[score.candidate.target_knowledge_point_id],
            difficulty=score.candidate.target_difficulty,
            reason_code=",".join(score.reason_codes),
        )


class EvaluationBackendError(RuntimeError):
    """Raised when a baseline cannot preserve the evaluation contract."""


class DeepTutorNativeEvaluationClient:
    """Case-isolated adapter over DeepTutor's actual L1/L2/L3 MemoryStore."""

    def __init__(self, root: Path, *, call_prefix: str) -> None:
        self._paths = PathService(workspace_root=root)
        self._store = MemoryStore()
        resolved = resolve_llm_runtime_config()
        self._provider = resolved.provider_name
        self._model = resolved.model
        self._call_prefix = call_prefix
        self._calls: list[LLMCallTrace] = []
        self._started: dict[tuple[Any, ...], float] = {}
        self._counter = 0

    async def append_once(self, event: NativeMemoryEvent) -> bool:
        native_event = TraceEvent(**asdict(event))
        with memory_path_service_override(self._paths):
            existing = next(iter_by_ids([native_event.id]), None)
            if existing is not None:
                if asdict(existing) != asdict(native_event):
                    raise EvaluationBackendError(
                        "Native Memory event identity conflicts with stored trace"
                    )
                return False
            await self._store.emit(native_event)
        return True

    async def consolidate_quiz(self) -> None:
        with memory_path_service_override(self._paths):
            await self._store.update_l2("quiz", language="zh", on_event=self._on_event)
            await self._store.update_l3("recent", language="zh", on_event=self._on_event)
            await self._store.update_l3("scope", language="zh", on_event=self._on_event)

    async def _on_event(self, event: dict[str, Any]) -> None:
        stage = event.get("stage")
        key = (event.get("label"), event.get("turn"), event.get("chunk_index"))
        if stage == "llm_io_start":
            self._started[key] = monotonic()
            return
        if stage != "llm_io_end":
            return
        self._counter += 1
        started = self._started.pop(key, monotonic())
        error_text = event.get("error")
        error = None
        if error_text:
            error = TraceError(
                stage=TraceStage.PROJECT,
                error_type="NativeMemoryConsolidationError",
                message=str(error_text),
                retryable=True,
                attempt=1,
            )
        self._calls.append(
            LLMCallTrace(
                call_id=f"{self._call_prefix}:native:{self._counter:04d}",
                purpose=f"native_memory_{event.get('label') or 'consolidation'}",
                provider=self._provider,
                model=str(event.get("model") or self._model),
                token_usage=TokenUsage(
                    prompt_tokens=0,
                    completion_tokens=0,
                    total_tokens=0,
                ),
                latency_ms=(monotonic() - started) * 1000,
                succeeded=error is None,
                error=error,
            )
        )

    def snapshot(self) -> dict[str, JsonValue]:
        with memory_path_service_override(self._paths):
            l1 = [asdict(event) for event in iter_since("quiz")]
            l2 = self._store.read_raw("L2", "quiz")
            l3 = {slot: self._store.read_raw("L3", slot) for slot in L3_SLOTS}
        payload: dict[str, JsonValue] = {
            "backend_mode": "native",
            "l1": l1,
            "l2": l2,
            "l3": l3,
            "record_count": len(l1),
            "native_typed_lifecycle_available": False,
        }
        payload["canonical_byte_size"] = _canonical_bytes(payload)
        return payload

    def take_calls(self) -> list[LLMCallTrace]:
        calls = self._calls
        self._calls = []
        return calls


class NativeEvaluationSession:
    """Run one case through DeepTutor Native Memory without typed-state claims."""

    mode = BackendMode.NATIVE
    policy_version = "deeptutor_native_memory_v1"

    def __init__(
        self,
        *,
        root: Path,
        run_id: str,
        case: EvaluationCase,
        client: NativeMemoryClient | None = None,
    ) -> None:
        self.case = case
        self._client = client or DeepTutorNativeEvaluationClient(
            root / run_id / case.case_id,
            call_prefix=f"{run_id}:native:{case.case_id}",
        )
        self._backend = NativeMemoryBackend(
            self._client,
            trace_id=f"{run_id}:native:{case.case_id}",
        )
        self._recommendation = EvaluationRecommendationPolicy()

    async def seed(self, case: EvaluationCase) -> dict[str, JsonValue]:
        if case.case_id != self.case.case_id:
            raise EvaluationBackendError("backend session is bound to another case")
        created = False
        for memory in case.initial_memory:
            event = NativeMemoryEvent(
                id=f"quiz:exam_mem:evaluation_seed:{memory.memory_id}",
                ts=memory.valid_from.isoformat(),
                surface="quiz",
                kind="exam_mem_initial_memory",
                payload={"initial_memory": memory.model_dump(mode="json")},
                session_id=f"evaluation_seed:{case.case_id}",
                turn_id=f"evaluation_seed:{memory.memory_id}",
            )
            created = await self._client.append_once(event) or created
        if created:
            await self._client.consolidate_quiz()
        return self._client.snapshot()

    async def process(
        self,
        step: MaterializedStep,
    ) -> tuple[list[LifecycleDecision], dict[str, JsonValue]]:
        await self._backend.record_event(step.event)
        decisions = await self._backend.update(step.event, list(step.candidates))
        return decisions, self._client.snapshot()

    async def retrieve(self, query: EvaluationQuery) -> list[LearningMemory]:
        return await self._backend.retrieve(query.scope, query.text, query.top_k)

    async def recommend(self, step: MaterializedStep) -> RecommendationTrace | None:
        return self._recommendation.recommend(context=step.event.context)

    def state_trace(self, snapshot: dict[str, JsonValue]) -> MemoryStateTrace:
        return MemoryStateTrace(
            active_memory_ids=[],
            archived_memory_ids=[],
            invalidated_memory_ids=[],
            contested_memory_ids=[],
            version_relations=[],
        )

    def candidate_ids(
        self,
        snapshot: dict[str, JsonValue],
        candidate: MemoryUpdateCandidate,
    ) -> list[str]:
        return []

    def take_llm_calls(self) -> list[LLMCallTrace]:
        take_calls = getattr(self._client, "take_calls", None)
        return take_calls() if callable(take_calls) else []


class DeterministicHashEmbeddingClient:
    """Frozen, local 1024-d feature hashing used only by the vector baseline."""

    version = "feature_hash_embedding_v1"

    def __init__(self) -> None:
        self.call_count = 0

    async def embed(
        self,
        texts: list[str],
        *,
        input_type: str | None = None,
    ) -> list[list[float]]:
        self.call_count += 1
        return [self._embed_one(text) for text in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        normalized = " ".join(text.casefold().split())
        words = re.findall(r"[a-z0-9_:.]+|[\u4e00-\u9fff]", normalized)
        features = words + [normalized[index : index + 2] for index in range(len(normalized) - 1)]
        vector = [0.0] * _EMBEDDING_DIMENSION
        for feature in features or ["<empty>"]:
            digest = hashlib.sha256(feature.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % _EMBEDDING_DIMENSION
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]


class TrackedRelationCompletion:
    """Observe real relation-classifier calls without reading credentials."""

    def __init__(self, call_prefix: str) -> None:
        resolved = resolve_llm_runtime_config()
        self.provider = resolved.provider_name
        self.model = resolved.model
        self._call_prefix = call_prefix
        self._calls: list[LLMCallTrace] = []
        self._counter = 0

    async def __call__(
        self,
        *,
        prompt: str,
        system_prompt: str,
        response_format: dict[str, object],
        temperature: float,
    ) -> str:
        self._counter += 1
        started = monotonic()
        error: TraceError | None = None
        try:
            result = await complete(
                prompt=prompt,
                system_prompt=system_prompt,
                response_format=response_format,
                temperature=temperature,
            )
            succeeded = True
            return result
        except Exception as exc:
            succeeded = False
            error = TraceError(
                stage=TraceStage.DECIDE,
                error_type=type(exc).__name__,
                message=str(exc) or type(exc).__name__,
                retryable=True,
                attempt=self._counter,
            )
            raise
        finally:
            self._calls.append(
                LLMCallTrace(
                    call_id=f"{self._call_prefix}:relation:{self._counter:04d}",
                    purpose="memory_relation_classification",
                    provider=self.provider,
                    model=self.model,
                    token_usage=TokenUsage(
                        prompt_tokens=0,
                        completion_tokens=0,
                        total_tokens=0,
                    ),
                    latency_ms=(monotonic() - started) * 1000,
                    succeeded=succeeded,
                    error=error,
                )
            )

    def take_calls(self) -> list[LLMCallTrace]:
        calls = self._calls
        self._calls = []
        return calls


class EvaluationRelationClassifier:
    """Use deterministic correction binding and the real semantic classifier."""

    def __init__(self, completion: TrackedRelationCompletion) -> None:
        self._semantic = DeepTutorRelationClassifierAdapter(completion=completion)
        self._correction = ConfirmedCorrectionRelationClassifier()

    async def classify(self, candidate, candidate_snapshots):  # noqa: ANN001, ANN201
        if "correction_source" in candidate.evidence:
            return await self._correction.classify(candidate, candidate_snapshots)
        return await self._semantic.classify(candidate, candidate_snapshots)


class SmokeOnlyRelationClassifier:
    """Deterministic wiring probe; its output is forbidden in formal reports."""

    def __init__(self) -> None:
        self._correction = ConfirmedCorrectionRelationClassifier()

    async def classify(self, candidate, candidate_snapshots):  # noqa: ANN001, ANN201
        if "correction_source" in candidate.evidence:
            return await self._correction.classify(candidate, candidate_snapshots)
        return resolve_validated_relation_output(
            candidate,
            candidate_snapshots,
            RelationClassifierOutput(
                candidate_display_number=1,
                relation=MemoryRelation.COMPLEMENTARY,
                confidence=1.0,
                reason="smoke_only_database_wiring_probe",
            ),
        )


def _canonical_bytes(value: dict[str, JsonValue]) -> int:
    return len(
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    )


class PostgresEvaluationSession:
    """Run append/vector/lifecycle against actual PostgreSQL repositories."""

    def __init__(
        self,
        *,
        engine: AsyncEngine,
        mode: BackendMode,
        run_id: str,
        case: EvaluationCase,
        relation_classifier: RelationClassifier | None = None,
    ) -> None:
        if mode not in {BackendMode.APPEND_ONLY, BackendMode.VECTOR, BackendMode.LIFECYCLE}:
            raise ValueError("PostgresEvaluationSession requires a PostgreSQL backend mode")
        self.engine = engine
        self.mode = mode
        self.policy_version = (
            "lifecycle_policy_v1" if mode is BackendMode.LIFECYCLE else f"{mode.value}_v1"
        )
        self.case = case
        self._prefix = f"eval:{run_id}:{mode.value}:"
        self._logical_ids: dict[str, str] = {}
        self._event_target_ids: dict[str, str] = {}
        self._embedding = DeterministicHashEmbeddingClient()
        self._completion: TrackedRelationCompletion | None = None
        self._relation_mode = "not_used"
        self._relation: RelationClassifier | None = relation_classifier
        if mode is BackendMode.LIFECYCLE and relation_classifier is None:
            self._completion = TrackedRelationCompletion(f"{run_id}:{mode.value}:{case.case_id}")
            self._relation = EvaluationRelationClassifier(self._completion)
            self._relation_mode = "configured_llm"
        elif mode is BackendMode.LIFECYCLE:
            self._relation_mode = "injected_smoke_only"
        self._contexts = self._runtime_contexts(case)
        self._recommendation = EvaluationRecommendationPolicy()

    def _runtime_scalar_id(self, value: str) -> str:
        return f"{self._prefix}{value}"

    def _runtime_context(self, context: LearningContext) -> LearningContext:
        return LearningContext(
            user_id=self._runtime_scalar_id(context.user_id),
            exam_id=self._runtime_scalar_id(context.exam_id),
            subject_id=context.subject_id,
        )

    def _runtime_scope(self, scope: MemoryScope) -> MemoryScope:
        return MemoryScope(
            **self._runtime_context(
                LearningContext.model_validate(scope.model_dump(exclude={"memory_namespace"}))
            ).model_dump(),
            memory_namespace=scope.memory_namespace,
        )

    def _runtime_contexts(self, case: EvaluationCase) -> tuple[LearningContext, ...]:
        original: dict[tuple[str, str, str], LearningContext] = {}
        for memory in case.initial_memory:
            context = LearningContext.model_validate(
                memory.scope.model_dump(exclude={"memory_namespace"})
            )
            original[(context.user_id, context.exam_id, context.subject_id)] = context
        for event in case.events:
            original[(event.context.user_id, event.context.exam_id, event.context.subject_id)] = (
                event.context
            )
        return tuple(self._runtime_context(context) for context in original.values())

    def _runtime_event(self, event: LearningEvent) -> LearningEvent:
        payload = event.model_dump(mode="json")
        payload["event_id"] = self._runtime_scalar_id(event.event_id)
        payload["idempotency_key"] = self._runtime_scalar_id(event.idempotency_key)
        payload["session_id"] = self._runtime_scalar_id(event.session_id)
        payload["context"] = self._runtime_context(event.context).model_dump(mode="json")
        if payload.get("correction") is not None:
            payload["correction"]["target_memory_ids"] = [
                self._actual_memory_id(memory_id)
                for memory_id in event.correction.target_memory_ids
            ]
        if payload.get("plan_transition") is not None:
            payload["plan_transition"]["target_memory_id"] = self._actual_memory_id(
                event.plan_transition.target_memory_id
            )
        return LearningEvent.model_validate(payload)

    def _runtime_candidate(
        self,
        candidate: MemoryUpdateCandidate,
        runtime_event: LearningEvent,
    ) -> MemoryUpdateCandidate:
        evidence = dict(candidate.evidence)
        target_id = evidence.get("target_memory_id")
        if isinstance(target_id, str):
            evidence["target_memory_id"] = self._actual_memory_id(target_id)
        return candidate.model_copy(
            update={
                "event_id": runtime_event.event_id,
                "scope": self._runtime_scope(candidate.scope),
                "evidence": evidence,
            }
        )

    def _actual_memory_id(self, logical_id: str) -> str:
        actual_id = self._event_target_ids.get(logical_id)
        if actual_id is None:
            raise EvaluationBackendError(f"logical memory target does not resolve: {logical_id}")
        return actual_id

    def _runtime_memory(self, memory: LearningMemory) -> LearningMemory:
        actual_id = self._runtime_scalar_id(memory.memory_id)
        self._event_target_ids[memory.memory_id] = actual_id
        if self.mode is BackendMode.LIFECYCLE:
            self._logical_ids[actual_id] = memory.memory_id
        return memory.model_copy(
            update={
                "memory_id": actual_id,
                "scope": self._runtime_scope(memory.scope),
                "superseded_by": (
                    None
                    if memory.superseded_by is None
                    else self._runtime_scalar_id(memory.superseded_by)
                ),
                "provenance": [self._runtime_scalar_id(event_id) for event_id in memory.provenance],
            }
        )

    def _seed_event(
        self,
        *,
        memory: LearningMemory,
        event_id: str,
        offset: int,
    ) -> LearningEvent:
        replayed = next(
            (
                event
                for event in self.case.events
                if self._runtime_scalar_id(event.event_id) == event_id
            ),
            None,
        )
        if replayed is not None:
            return self._runtime_event(replayed)
        slot_parts = memory.slot_key.split(":")
        knowledge_point_id = (
            slot_parts[1]
            if memory.scope.memory_namespace
            in {MemoryNamespace.MASTERY, MemoryNamespace.ERROR_PATTERN}
            else self.case.gold_operations[0].canonical_knowledge_point_ids[0]
        )
        correct = not isinstance(memory.value, ErrorPatternValue)
        if isinstance(memory.value, MasteryValue):
            correct = memory.value.score >= 0.5
        error_type = (
            memory.value.error_type if isinstance(memory.value, ErrorPatternValue) else None
        )
        return LearningEvent(
            event_id=event_id,
            idempotency_key=f"{event_id}:idempotency",
            event_type=LearningEventType.ANSWER_ATTEMPT,
            context=LearningContext.model_validate(
                memory.scope.model_dump(exclude={"memory_namespace"})
            ),
            session_id=f"{event_id}:session",
            question_id=f"{event_id}:question",
            knowledge_point_ids=[knowledge_point_id],
            difficulty=0.5,
            answer_correct=correct,
            error_type=error_type,
            error_detail=(
                memory.value.summary if isinstance(memory.value, ErrorPatternValue) else None
            ),
            occurred_at=memory.valid_from - timedelta(seconds=offset + 1),
        )

    async def _backend(self, connection: AsyncConnection) -> MemoryBackend:
        events = PostgresLearningEventRepository(connection)
        if self.mode is BackendMode.APPEND_ONLY:
            return AppendOnlyMemoryBackend(
                event_repository=events,
                fact_repository=PostgresBaselineFactRepository(connection),
                trace_id=f"{self._prefix}trace",
            )
        if self.mode is BackendMode.VECTOR:
            return VectorMemoryBackend(
                event_repository=events,
                fact_repository=PostgresBaselineFactRepository(connection),
                embedding_client=self._embedding,
                trace_id=f"{self._prefix}trace",
            )
        memories = PostgresLearningMemoryRepository(connection)
        assert self._relation is not None
        return LifecycleMemoryBackend(
            event_repository=events,
            memory_repository=memories,
            student_model_repository=PostgresStudentModelRepository(connection),
            relation_classifier=self._relation,
            applier=LifecycleApplier(
                connection,
                memory_repository=memories,
                audit_repository=PostgresLifecycleAuditRepository(connection),
                event_repository=events,
            ),
            trace_id=f"{self._prefix}trace",
            embedding_client=self._embedding,
        )

    async def seed(self, case: EvaluationCase) -> dict[str, JsonValue]:
        if case.case_id != self.case.case_id:
            raise EvaluationBackendError("backend session is bound to another case")
        async with self.engine.begin() as connection:
            events = PostgresLearningEventRepository(connection)
            runtime_memories = [self._runtime_memory(memory) for memory in case.initial_memory]
            seed_events: dict[str, LearningEvent] = {}
            for memory in runtime_memories:
                for offset, event_id in enumerate(memory.provenance):
                    seed_events.setdefault(
                        event_id,
                        self._seed_event(memory=memory, event_id=event_id, offset=offset),
                    )
            for event in seed_events.values():
                replayed_event_ids = {
                    self._runtime_scalar_id(case_event.event_id) for case_event in case.events
                }
                trace_suffix = "trace" if event.event_id in replayed_event_ids else "seed"
                await events.append(event, trace_id=f"{self._prefix}{trace_suffix}")

            if self.mode is BackendMode.LIFECYCLE:
                repository = PostgresLearningMemoryRepository(connection)
                for memory in sorted(
                    runtime_memories, key=lambda item: (item.version, item.memory_id)
                ):
                    await repository.insert_version(
                        memory,
                        policy_version="evaluation_seed_v1",
                        content_embedding=(await self._embedding.embed([memory.slot_key]))[0],
                    )
            else:
                shadow_repository = PostgresLearningMemoryRepository(connection)
                for memory in runtime_memories:
                    await shadow_repository.insert_version(
                        memory,
                        policy_version="evaluation_reference_shadow_v1",
                    )
                repository = PostgresBaselineFactRepository(connection)
                for logical_memory, runtime_memory in zip(
                    case.initial_memory, runtime_memories, strict=True
                ):
                    candidate = MemoryUpdateCandidate(
                        event_id=runtime_memory.provenance[0],
                        scope=runtime_memory.scope,
                        slot_key=runtime_memory.slot_key,
                        proposed_value=runtime_memory.value,
                        evidence={"evaluation_seed": True},
                    )
                    embedding = (
                        (await self._embedding.embed([runtime_memory.slot_key]))[0]
                        if self.mode is BackendMode.VECTOR
                        else None
                    )
                    record = BaselineFactRecord(
                        backend_mode=self.mode,
                        candidate=candidate,
                        created_at=runtime_memory.valid_from,
                        content_embedding=embedding,
                    )
                    await repository.append(record)
                    actual_id = _baseline_memory_view(record).memory_id
                    self._logical_ids[actual_id] = logical_memory.memory_id
        return await self._snapshot()

    async def process(
        self,
        step: MaterializedStep,
    ) -> tuple[list[LifecycleDecision], dict[str, JsonValue]]:
        before = await self._snapshot()
        before_ids = set(self._snapshot_memories(before))
        runtime_event = self._runtime_event(step.event)
        runtime_candidates = [
            self._runtime_candidate(candidate, runtime_event) for candidate in step.candidates
        ]
        projection_requests = ()
        async with self.engine.begin() as connection:
            backend = await self._backend(connection)
            await backend.record_event(runtime_event)
            decisions = await backend.update(runtime_event, runtime_candidates)
            take_requests = getattr(backend, "take_projection_requests", None)
            if callable(take_requests):
                projection_requests = take_requests()
        await self._refresh_projections(projection_requests)
        snapshot = await self._snapshot()
        self._map_result_ids(
            step=step,
            runtime_event=runtime_event,
            decisions=decisions,
            before_ids=before_ids,
            snapshot=snapshot,
        )
        snapshot["logical_id_map"] = dict(sorted(self._logical_ids.items()))
        return decisions, snapshot

    async def _refresh_projections(self, requests: Sequence[Any]) -> None:
        for request in requests:
            async with self.engine.begin() as connection:
                refresher = PostCommitProjectionRefresher(
                    StudentModelRebuildService(
                        event_repository=PostgresLearningEventRepository(connection),
                        memory_repository=PostgresLearningMemoryRepository(connection),
                        student_model_repository=PostgresStudentModelRepository(connection),
                    )
                )
                await refresher.refresh(request)

    def _map_result_ids(
        self,
        *,
        step: MaterializedStep,
        runtime_event: LearningEvent,
        decisions: Sequence[LifecycleDecision],
        before_ids: set[str],
        snapshot: dict[str, JsonValue],
    ) -> None:
        memories = self._snapshot_memories(snapshot)
        for index, operation in enumerate(step.gold_operations):
            if index >= len(decisions) or decisions[index].operation not in _PRODUCING_OPERATIONS:
                continue
            if operation.result_memory_id is None:
                continue
            matches = [
                memory
                for memory in memories.values()
                if memory.memory_id not in before_ids
                and memory.slot_key == operation.slot_key
                and runtime_event.event_id in memory.provenance
            ]
            if len(matches) == 1:
                self._logical_ids[matches[0].memory_id] = operation.result_memory_id

    async def _snapshot(self) -> dict[str, JsonValue]:
        contexts: list[dict[str, JsonValue]] = []
        async with self.engine.connect() as connection:
            backend = await self._backend(connection)
            for context in self._contexts:
                contexts.append(
                    {
                        "context": context.model_dump(mode="json"),
                        "snapshot": await backend.snapshot(context),
                    }
                )
        payload: dict[str, JsonValue] = {
            "backend_mode": self.mode.value,
            "contexts": contexts,
            "logical_id_map": dict(sorted(self._logical_ids.items())),
            "embedding_model": self._embedding.version,
            "embedding_call_count": self._embedding.call_count,
            "relation_classifier_mode": self._relation_mode,
        }
        payload["canonical_byte_size"] = _canonical_bytes(payload)
        return payload

    def _snapshot_memories(
        self,
        snapshot: dict[str, JsonValue],
    ) -> dict[str, LearningMemory]:
        memories: dict[str, LearningMemory] = {}
        contexts = snapshot.get("contexts", [])
        if not isinstance(contexts, list):
            return memories
        for item in contexts:
            if not isinstance(item, dict) or not isinstance(item.get("snapshot"), dict):
                continue
            payload = item["snapshot"]
            if self.mode is BackendMode.LIFECYCLE:
                records = payload.get("memories", [])
                if isinstance(records, list):
                    for record in records:
                        memory = LearningMemory.model_validate(record)
                        memories[memory.memory_id] = memory
            else:
                records = payload.get("facts", [])
                if isinstance(records, list):
                    for record in records:
                        memory = _baseline_memory_view(BaselineFactRecord.model_validate(record))
                        memories[memory.memory_id] = memory
        return memories

    def _logical_id(self, actual_id: str) -> str:
        return self._logical_ids.get(actual_id, actual_id)

    def state_trace(self, snapshot: dict[str, JsonValue]) -> MemoryStateTrace:
        groups = {state: [] for state in LifecycleState}
        relations: list[VersionRelation] = []
        for memory in self._snapshot_memories(snapshot).values():
            groups[memory.lifecycle_state].append(self._logical_id(memory.memory_id))
            if memory.superseded_by is not None:
                relations.append(
                    VersionRelation(
                        predecessor_memory_id=self._logical_id(memory.memory_id),
                        successor_memory_id=self._logical_id(memory.superseded_by),
                        relation="superseded_by",
                    )
                )
        return MemoryStateTrace(
            active_memory_ids=sorted(groups[LifecycleState.ACTIVE]),
            archived_memory_ids=sorted(groups[LifecycleState.ARCHIVED]),
            invalidated_memory_ids=sorted(groups[LifecycleState.INVALIDATED]),
            contested_memory_ids=sorted(groups[LifecycleState.CONTESTED]),
            version_relations=sorted(
                relations,
                key=lambda relation: (
                    relation.predecessor_memory_id,
                    relation.successor_memory_id,
                ),
            ),
        )

    def candidate_ids(
        self,
        snapshot: dict[str, JsonValue],
        candidate: MemoryUpdateCandidate,
    ) -> list[str]:
        runtime_scope = self._runtime_scope(candidate.scope)
        return sorted(
            self._logical_id(memory.memory_id)
            for memory in self._snapshot_memories(snapshot).values()
            if memory.scope == runtime_scope
            and memory.slot_key == candidate.slot_key
            and memory.lifecycle_state not in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
        )

    async def retrieve(self, query: EvaluationQuery) -> list[LearningMemory]:
        scope = self._runtime_scope(query.scope)
        target_ids = next(
            action.knowledge_point_ids
            for action in self.case.gold_actions
            if action.step_id == query.after_step_id
        )
        knowledge_point_id = target_ids[0] if target_ids else ""
        if scope.memory_namespace is MemoryNamespace.MASTERY:
            layer_query = f"mastery:{knowledge_point_id}"
        elif scope.memory_namespace is MemoryNamespace.ERROR_PATTERN:
            matching_slots = [
                operation.slot_key
                for operation in self.case.gold_operations
                if operation.step_id == query.after_step_id
                and operation.slot_key.startswith(f"error_pattern:{knowledge_point_id}:")
            ]
            layer_query = matching_slots[0] if matching_slots else query.text
        else:
            layer_query = query.text
        async with self.engine.connect() as connection:
            backend = await self._backend(connection)
            memories = await backend.retrieve(scope, layer_query, query.top_k)
        return [
            memory.model_copy(update={"memory_id": self._logical_id(memory.memory_id)})
            for memory in memories
        ]

    async def recommend(self, step: MaterializedStep) -> RecommendationTrace | None:
        runtime_context = self._runtime_context(step.event.context)
        model = None
        usable_evidence: tuple[LearningMemory, ...] = ()
        usable_plans: tuple[LearningMemory, ...] = ()
        plan_events: Sequence[LearningEvent] = ()
        if self.mode is BackendMode.LIFECYCLE:
            async with self.engine.connect() as connection:
                students = PostgresStudentModelRepository(connection)
                student_snapshot = await students.get_latest(runtime_context)
                model = None if student_snapshot is None else student_snapshot.model
                repository = PostgresLearningMemoryRepository(connection)
                by_namespace: dict[MemoryNamespace, list[LearningMemory]] = {}
                for namespace in MemoryNamespace:
                    by_namespace[namespace] = await repository.snapshot(
                        MemoryScope(
                            **runtime_context.model_dump(),
                            memory_namespace=namespace,
                        )
                    )
                usable_evidence = tuple(
                    memory
                    for namespace in (MemoryNamespace.MASTERY, MemoryNamespace.ERROR_PATTERN)
                    for memory in by_namespace[namespace]
                    if memory.lifecycle_state
                    not in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
                )
                usable_plans = tuple(
                    memory
                    for memory in by_namespace[MemoryNamespace.PLAN]
                    if memory.lifecycle_state
                    not in {LifecycleState.ARCHIVED, LifecycleState.INVALIDATED}
                )
                plan_event_ids = sorted(
                    {event_id for memory in usable_plans for event_id in memory.provenance}
                )
                plan_events = await PostgresLearningEventRepository(connection).get_by_ids(
                    runtime_context,
                    plan_event_ids,
                )
        return self._recommendation.recommend(
            context=runtime_context,
            model=model,
            memories=usable_evidence,
            plan_memories=usable_plans,
            plan_events=plan_events,
        )

    def take_llm_calls(self) -> list[LLMCallTrace]:
        return [] if self._completion is None else self._completion.take_calls()


class NoMemoryEvaluationSession:
    """No-memory arm with the same orchestration surface and no persistence."""

    mode = BackendMode.NONE
    policy_version = "none_v1"

    def __init__(self) -> None:
        self._backend = NoMemoryBackend()
        self._recommendation = EvaluationRecommendationPolicy()

    async def seed(self, case: EvaluationCase) -> dict[str, JsonValue]:
        return {"backend_mode": "none", "discarded_initial_memory_count": len(case.initial_memory)}

    async def process(
        self,
        step: MaterializedStep,
    ) -> tuple[list[LifecycleDecision], dict[str, JsonValue]]:
        await self._backend.record_event(step.event)
        decisions = await self._backend.update(step.event, list(step.candidates))
        return decisions, {"backend_mode": "none"}

    async def retrieve(self, query: EvaluationQuery) -> list[LearningMemory]:
        return await self._backend.retrieve(query.scope, query.text, query.top_k)

    async def recommend(self, step: MaterializedStep) -> RecommendationTrace | None:
        return self._recommendation.recommend(context=step.event.context)

    def state_trace(self, snapshot: dict[str, JsonValue]) -> MemoryStateTrace:
        return MemoryStateTrace(
            active_memory_ids=[],
            archived_memory_ids=[],
            invalidated_memory_ids=[],
            contested_memory_ids=[],
            version_relations=[],
        )

    def candidate_ids(
        self,
        snapshot: dict[str, JsonValue],
        candidate: MemoryUpdateCandidate,
    ) -> list[str]:
        return []

    def take_llm_calls(self) -> list[LLMCallTrace]:
        return []


__all__ = [
    "DeepTutorNativeEvaluationClient",
    "DeterministicHashEmbeddingClient",
    "EvaluationBackendError",
    "NativeEvaluationSession",
    "NoMemoryEvaluationSession",
    "PostgresEvaluationSession",
    "SmokeOnlyRelationClassifier",
    "TrackedRelationCompletion",
]
