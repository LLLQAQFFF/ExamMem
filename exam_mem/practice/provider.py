"""Production dependency assembly for one ExamMem practice turn."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import AsyncIterator, Callable, Sequence

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from deeptutor.plugins.host_services import UnifiedContext, get_embedding_client
from exam_mem.backends import (
    BackendMode,
    MemoryBackend,
    build_memory_backend,
    validate_runtime_backend_mode,
)
from exam_mem.backends.baseline import AppendOnlyMemoryBackend, VectorMemoryBackend
from exam_mem.backends.lifecycle import LifecycleMemoryBackend
from exam_mem.backends.native import NativeMemoryBackend, NativeMemoryClient
from exam_mem.config import ExamMemSettings, backend_side_effects, settings_revision
from exam_mem.contracts import (
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MemoryNamespace,
    MemoryScope,
)
from exam_mem.domain import KnowledgePointStatus, Taxonomy, load_taxonomy
from exam_mem.lifecycle import (
    DeepTutorRelationClassifierAdapter,
    LifecycleApplier,
    PostCommitProjectionRefresher,
    RelationClassifier,
)
from exam_mem.storage import (
    CommittedPostgresPracticeCheckpointRepository,
    CommittedPostgresPracticeTraceRepository,
    PostgresAssessmentRepository,
    PostgresBaselineFactRepository,
    PostgresExamProductRepository,
    PostgresGradeReviewRepository,
    PostgresLearningArchiveRepository,
    PostgresLearningEventRepository,
    PostgresLearningMemoryRepository,
    PostgresLearningObservationRepository,
    PostgresLifecycleAuditRepository,
    PostgresPracticeCheckpointRepository,
    PostgresStudentModelRepository,
    PostgresStudyPlanRepository,
    StudentModelRebuildService,
    load_database_settings,
)

from .checkpoint import PracticeRuntimeSnapshot
from .contracts import PracticeContext, Question, Recommendation
from .corrections import (
    ConfirmedCorrectionRelationClassifier,
    ExplicitCorrectionService,
    QueryServiceRecommendationRefresher,
    ResolvedCorrectionTarget,
)
from .grading import GRADER_CONTRACT_VERSION
from .learning_profile_service import LearningProfileQueryService
from .memory import MemoryWriter, MemoryWriteResult, PracticeMemoryCandidateBuilder
from .memory_workbench import LearningMemoryQueryService
from .plan_transitions import PlanTransitionService, ResolvedPlanTarget
from .question_retriever import QuestionCatalog, QuestionRetriever
from .recommendation import (
    RecommendationCandidate,
    RecommendationFeatures,
    RecommendationPolicyV1,
)
from .tools import (
    AnswerGraderTool,
    ErrorAnalyzerTool,
    KnowledgeMapperTool,
    MemoryWriterTool,
    QuestionRetrieverTool,
    RecommendationTool,
)
from .trace import PracticeTraceRecorder
from .workflow import ExamPracticeWorkflow

PRACTICE_QUESTIONS_METADATA_KEY = "exam_practice_questions"


class PracticeRuntimeConfigurationError(RuntimeError):
    """Raised instead of silently changing the selected runtime arm."""

    error_code = "practice_runtime_configuration_error"


class BoundQuestionCatalog(QuestionCatalog):
    """Expose validated runtime questions only inside one exact four-D Scope."""

    def __init__(self, scope: MemoryScope, questions: Sequence[Question]) -> None:
        self._scope = scope
        self._questions = tuple(questions)

    async def list_questions(self, scope: MemoryScope) -> Sequence[Question]:
        if scope != self._scope:
            raise ValueError("question catalog request is outside the bound Scope")
        return self._questions


class TransactionalPracticeMemoryWriter:
    """Commit L1/L2/audit atomically and rebuild disposable L3 afterwards."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        mode: BackendMode,
        trace_id: str,
        relation_classifier: RelationClassifier | None = None,
        native_memory_client_factory: Callable[[], NativeMemoryClient] | None = None,
    ) -> None:
        self._engine = engine
        self._mode = mode
        self._trace_id = trace_id
        self._relation_classifier = relation_classifier
        self._native_memory_client_factory = native_memory_client_factory
        self._native_backend: NativeMemoryBackend | None = None

    async def write(self, event, candidates):  # noqa: ANN001, ANN201
        if self._mode is BackendMode.NATIVE:
            if self._native_backend is None:
                if self._native_memory_client_factory is None:
                    raise PracticeRuntimeConfigurationError(
                        "native backend requires an explicit Host memory adapter"
                    )
                self._native_backend = NativeMemoryBackend(
                    self._native_memory_client_factory(),
                    trace_id=self._trace_id,
                )
            return await MemoryWriter(self._native_backend).write(event, candidates)
        if self._mode is BackendMode.NONE:
            return await MemoryWriter(build_memory_backend(self._mode)).write(event, candidates)

        async with self._engine.begin() as connection:
            backend = _postgres_backend(
                connection,
                mode=self._mode,
                trace_id=self._trace_id,
                relation_classifier=self._relation_classifier,
            )
            return await MemoryWriter(backend).write(event, candidates)

    async def refresh_after_commit(self, result: MemoryWriteResult) -> None:
        if not result.projection_requests:
            return
        if self._mode is not BackendMode.LIFECYCLE:
            raise PracticeRuntimeConfigurationError(
                "only lifecycle backend may request Student Model projection"
            )
        for request in result.projection_requests:
            async with self._engine.begin() as connection:
                events = PostgresLearningEventRepository(connection)
                memories = PostgresLearningMemoryRepository(connection)
                students = PostgresStudentModelRepository(connection)
                refresher = PostCommitProjectionRefresher(
                    StudentModelRebuildService(
                        event_repository=events,
                        memory_repository=memories,
                        student_model_repository=students,
                    )
                )
                await refresher.refresh(request)


class RuntimeRecommendationTool:
    """Build deterministic policy inputs from current L2/L3 and retrieve a question."""

    def __init__(
        self,
        engine: AsyncEngine,
        *,
        mode: BackendMode,
        retriever: QuestionRetrieverTool,
        taxonomy_version: str = "math1_v1",
        taxonomy: Taxonomy | None = None,
    ) -> None:
        self._engine = engine
        self._mode = mode
        self._retriever = retriever
        resolved_taxonomy = taxonomy or load_taxonomy(taxonomy_version)
        self._policy = RecommendationPolicyV1(taxonomy=resolved_taxonomy)
        self._knowledge_point_ids = tuple(
            node.id
            for node in resolved_taxonomy.nodes
            if node.status is KnowledgePointStatus.ACTIVE
            and not resolved_taxonomy.children_of(node.id)
        )

    async def recommend(
        self,
        context: PracticeContext,
        *,
        exclude_question_ids: Sequence[str] = (),
    ) -> tuple[Recommendation, Question]:
        candidates = await self._candidates(context)
        learning_context = _learning_context(context)
        ranked = self._policy.rank(context=learning_context, candidates=candidates)
        for score in ranked:
            try:
                question = await self._retriever.retrieve(
                    scope=context.scope,
                    target_knowledge_point_id=score.candidate.target_knowledge_point_id,
                    target_difficulty=score.candidate.target_difficulty,
                    exclude_question_ids=exclude_question_ids,
                )
            except Exception as exc:
                if getattr(exc, "error_code", None) == "question_bank_no_candidate":
                    continue
                raise
            return self._policy.build_recommendation(score, question), question

        question = await self._retriever.retrieve_syllabus_fallback(
            scope=context.scope,
            exclude_question_ids=exclude_question_ids,
        )
        target_id = self._retriever.fallback_target_knowledge_point_id(question)
        return (
            self._policy.build_fallback_recommendation(
                question,
                target_knowledge_point_id=target_id,
            ),
            question,
        )

    async def _candidates(
        self,
        context: PracticeContext,
    ) -> tuple[RecommendationCandidate, ...]:
        model = None
        usable_evidence: tuple[LearningMemory, ...] = ()
        usable_plans: tuple[LearningMemory, ...] = ()
        plan_events: Sequence[LearningEvent] = ()
        if self._mode is BackendMode.LIFECYCLE:
            learning_context = _learning_context(context)
            async with self._engine.connect() as connection:
                events = PostgresLearningEventRepository(connection)
                memories = PostgresLearningMemoryRepository(connection)
                students = PostgresStudentModelRepository(connection)
                snapshot = await students.get_latest(learning_context)
                model = None if snapshot is None else snapshot.model
                mastery = await memories.snapshot(
                    context.scope.model_copy(update={"memory_namespace": MemoryNamespace.MASTERY})
                )
                errors = await memories.snapshot(
                    context.scope.model_copy(
                        update={"memory_namespace": MemoryNamespace.ERROR_PATTERN}
                    )
                )
                plans = await memories.snapshot(
                    context.scope.model_copy(update={"memory_namespace": MemoryNamespace.PLAN})
                )

                usable_evidence = tuple(
                    memory
                    for memory in (*mastery, *errors)
                    if memory.lifecycle_state
                    not in {
                        LifecycleState.ARCHIVED,
                        LifecycleState.INVALIDATED,
                    }
                )
                usable_plans = tuple(
                    memory
                    for memory in plans
                    if memory.lifecycle_state
                    not in {
                        LifecycleState.ARCHIVED,
                        LifecycleState.INVALIDATED,
                    }
                )
                plan_event_ids = sorted(
                    {event_id for memory in usable_plans for event_id in memory.provenance}
                )
                plan_events = await events.get_by_ids(
                    learning_context,
                    plan_event_ids,
                )

        plan_sources = _plan_sources_by_knowledge_point(usable_plans, plan_events)
        return tuple(
            _recommendation_candidate(
                knowledge_point_id=knowledge_point_id,
                model=model,
                memories=usable_evidence,
                plan_memories=plan_sources.get(knowledge_point_id, ()),
            )
            for knowledge_point_id in self._knowledge_point_ids
        )


class PostgresPlanTargetReader:
    """Resolve one Plan ID only inside an authenticated three-D context."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_plan_target(
        self,
        context: LearningContext,
        target_memory_id: str,
    ) -> ResolvedPlanTarget | None:
        scope = MemoryScope(
            **context.model_dump(),
            memory_namespace=MemoryNamespace.PLAN,
        )
        async with self._engine.connect() as connection:
            snapshot = await PostgresLearningMemoryRepository(connection).get_lifecycle_snapshot(
                scope, target_memory_id
            )
            if snapshot is None:
                return None
            events = await PostgresLearningEventRepository(connection).get_by_ids(
                context,
                snapshot.memory.provenance,
            )
        return ResolvedPlanTarget(
            memory=snapshot.memory,
            knowledge_point_ids=tuple(
                dict.fromkeys(
                    knowledge_point_id
                    for event in events
                    for knowledge_point_id in event.knowledge_point_ids
                )
            ),
        )


class PostgresCorrectionTargetReader:
    """Resolve one correction target only inside the supplied full Scope."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def get_target(
        self,
        scope: MemoryScope,
        memory_id: str,
    ) -> ResolvedCorrectionTarget | None:
        async with self._engine.connect() as connection:
            snapshot = await PostgresLearningMemoryRepository(connection).get_lifecycle_snapshot(
                scope,
                memory_id,
            )
            if snapshot is None:
                return None
            events = await PostgresLearningEventRepository(connection).get_by_ids(
                LearningContext.model_validate(scope.model_dump(exclude={"memory_namespace"})),
                snapshot.memory.provenance,
            )
        return ResolvedCorrectionTarget(
            memory=snapshot.memory,
            knowledge_point_ids=tuple(
                dict.fromkeys(
                    knowledge_point_id
                    for event in events
                    for knowledge_point_id in event.knowledge_point_ids
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class PracticeRuntime:
    workflow: ExamPracticeWorkflow
    engine: AsyncEngine


@dataclass(frozen=True, slots=True)
class PlanTransitionRuntime:
    service: PlanTransitionService
    targets: PostgresPlanTargetReader
    engine: AsyncEngine


@dataclass(frozen=True, slots=True)
class LearningMemoryRuntime:
    queries: LearningMemoryQueryService
    corrections: ExplicitCorrectionService
    trace: PracticeTraceRecorder
    engine: AsyncEngine


@dataclass(frozen=True, slots=True)
class ExamProductRuntime:
    assessments: PostgresAssessmentRepository
    products: PostgresExamProductRepository
    reviews: PostgresGradeReviewRepository
    checkpoints: PostgresPracticeCheckpointRepository
    study_plans: PostgresStudyPlanRepository
    observations: PostgresLearningObservationRepository
    learning_archive: PostgresLearningArchiveRepository
    learning_profiles: LearningProfileQueryService
    connection: AsyncConnection
    engine: AsyncEngine


class PracticeRuntimeProvider:
    """Resolve settings and open one isolated runtime per Capability turn."""

    def __init__(
        self,
        *,
        settings: ExamMemSettings,
        engine_factory: Callable[[str], AsyncEngine] = create_async_engine,
        native_memory_client_factory: Callable[[], NativeMemoryClient] | None = None,
    ) -> None:
        self._settings = settings
        self._engine_factory = engine_factory
        self._native_memory_client_factory = native_memory_client_factory

    @asynccontextmanager
    async def open(
        self,
        unified_context: UnifiedContext,
        practice_context: PracticeContext,
    ) -> AsyncIterator[PracticeRuntime]:
        settings = self._settings
        if not settings.enabled or not settings.capabilities.exam_practice:
            raise PracticeRuntimeConfigurationError("exam_practice is disabled")
        questions = practice_context.question_catalog or _runtime_questions(unified_context)
        engine = self._engine_factory(load_database_settings().sqlalchemy_url())
        try:
            taxonomy = await _runtime_taxonomy(engine, practice_context)
            checkpoints = CommittedPostgresPracticeCheckpointRepository(engine)
            current_mode = validate_runtime_backend_mode(settings.memory_backend)
            current_snapshot = PracticeRuntimeSnapshot(
                config_revision=settings_revision(settings),
                backend_mode=current_mode,
                side_effects=backend_side_effects(current_mode),
            )
            pinned_snapshot = await checkpoints.get_runtime_snapshot(
                _learning_context(practice_context),
                practice_context.practice_session_id,
            )
            runtime_snapshot = pinned_snapshot or current_snapshot
            mode = runtime_snapshot.backend_mode
            workflow = ExamPracticeWorkflow(
                checkpoint_repository=checkpoints,
                trace_repository=CommittedPostgresPracticeTraceRepository(engine),
                answer_grader=AnswerGraderTool(),
                knowledge_mapper=KnowledgeMapperTool(taxonomy=taxonomy),
                error_analyzer=ErrorAnalyzerTool(),
                memory_candidate_builder=PracticeMemoryCandidateBuilder(taxonomy),
                memory_writer=MemoryWriterTool(
                    TransactionalPracticeMemoryWriter(
                        engine,
                        mode=mode,
                        trace_id=practice_context.trace_id,
                        native_memory_client_factory=self._native_memory_client_factory,
                    )
                ),
                recommendation_tool=RecommendationTool(
                    RuntimeRecommendationTool(
                        engine,
                        mode=mode,
                        retriever=QuestionRetrieverTool(
                            QuestionRetriever(
                                BoundQuestionCatalog(practice_context.scope, questions),
                                taxonomy=taxonomy,
                            )
                        ),
                        taxonomy=taxonomy,
                    )
                ),
                taxonomy_version=taxonomy.taxonomy_version,
                grader_contract_version=GRADER_CONTRACT_VERSION,
                config_revision=runtime_snapshot.config_revision,
                runtime_snapshot=runtime_snapshot,
            )
            yield PracticeRuntime(workflow=workflow, engine=engine)
        finally:
            await engine.dispose()

    @asynccontextmanager
    async def open_plan_transitions(
        self,
        *,
        trace_id: str,
    ) -> AsyncIterator[PlanTransitionRuntime]:
        settings = self._settings
        if not settings.enabled or not settings.capabilities.exam_practice:
            raise PracticeRuntimeConfigurationError("exam_practice is disabled")
        mode = validate_runtime_backend_mode(settings.memory_backend)
        if mode is not BackendMode.LIFECYCLE:
            raise PracticeRuntimeConfigurationError(
                "plan lifecycle transitions require memory_backend='lifecycle'"
            )
        engine = self._engine_factory(load_database_settings().sqlalchemy_url())
        try:
            targets = PostgresPlanTargetReader(engine)
            service = PlanTransitionService(
                target_reader=targets,
                memory_writer=MemoryWriterTool(
                    TransactionalPracticeMemoryWriter(
                        engine,
                        mode=mode,
                        trace_id=trace_id,
                        native_memory_client_factory=self._native_memory_client_factory,
                    )
                ),
                trace=PracticeTraceRecorder(
                    CommittedPostgresPracticeTraceRepository(engine),
                    trace_id=trace_id,
                ),
            )
            yield PlanTransitionRuntime(
                service=service,
                targets=targets,
                engine=engine,
            )
        finally:
            await engine.dispose()

    @asynccontextmanager
    async def open_learning_memories(
        self,
        *,
        trace_id: str,
    ) -> AsyncIterator[LearningMemoryRuntime]:
        settings = self._settings
        if not settings.enabled or not settings.capabilities.exam_practice:
            raise PracticeRuntimeConfigurationError("exam_practice is disabled")
        mode = validate_runtime_backend_mode(settings.memory_backend)
        if mode is not BackendMode.LIFECYCLE:
            raise PracticeRuntimeConfigurationError(
                "Learning Memory queries and corrections require memory_backend='lifecycle'"
            )
        engine = self._engine_factory(load_database_settings().sqlalchemy_url())
        try:
            async with engine.connect() as connection:
                trace = PracticeTraceRecorder(
                    CommittedPostgresPracticeTraceRepository(engine),
                    trace_id=trace_id,
                )
                queries = LearningMemoryQueryService(
                    memory_repository=PostgresLearningMemoryRepository(connection),
                    event_repository=PostgresLearningEventRepository(connection),
                )
                corrections = ExplicitCorrectionService(
                    target_reader=PostgresCorrectionTargetReader(engine),
                    memory_writer=MemoryWriterTool(
                        TransactionalPracticeMemoryWriter(
                            engine,
                            mode=mode,
                            trace_id=trace_id,
                            relation_classifier=ConfirmedCorrectionRelationClassifier(),
                            native_memory_client_factory=self._native_memory_client_factory,
                        )
                    ),
                    recommendation_refresher=QueryServiceRecommendationRefresher(queries),
                    trace=trace,
                )
                yield LearningMemoryRuntime(
                    queries=queries,
                    corrections=corrections,
                    trace=trace,
                    engine=engine,
                )
        finally:
            await engine.dispose()

    @asynccontextmanager
    async def open_product(self) -> AsyncIterator[ExamProductRuntime]:
        settings = self._settings
        if not settings.enabled or not settings.capabilities.exam_practice:
            raise PracticeRuntimeConfigurationError("exam_practice is disabled")
        engine = self._engine_factory(load_database_settings().sqlalchemy_url())
        try:
            async with engine.connect() as connection:
                yield ExamProductRuntime(
                    assessments=PostgresAssessmentRepository(connection),
                    products=PostgresExamProductRepository(connection),
                    reviews=PostgresGradeReviewRepository(connection),
                    checkpoints=PostgresPracticeCheckpointRepository(connection),
                    study_plans=PostgresStudyPlanRepository(connection),
                    observations=PostgresLearningObservationRepository(connection),
                    learning_archive=PostgresLearningArchiveRepository(connection),
                    learning_profiles=LearningProfileQueryService(
                        event_repository=PostgresLearningEventRepository(connection),
                        memory_repository=PostgresLearningMemoryRepository(connection),
                        model_repository=PostgresStudentModelRepository(connection),
                    ),
                    connection=connection,
                    engine=engine,
                )
        finally:
            await engine.dispose()


def _postgres_backend(  # noqa: ANN001
    connection,
    *,
    mode: BackendMode,
    trace_id: str,
    relation_classifier: RelationClassifier | None = None,
) -> MemoryBackend:
    events = PostgresLearningEventRepository(connection)
    facts = PostgresBaselineFactRepository(connection)
    memories = PostgresLearningMemoryRepository(connection)
    students = PostgresStudentModelRepository(connection)
    audit = PostgresLifecycleAuditRepository(connection)

    def lifecycle_backend() -> LifecycleMemoryBackend:
        embedding_client = get_embedding_client()
        return LifecycleMemoryBackend(
            event_repository=events,
            memory_repository=memories,
            student_model_repository=students,
            relation_classifier=(relation_classifier or DeepTutorRelationClassifierAdapter()),
            applier=LifecycleApplier(
                connection,
                memory_repository=memories,
                audit_repository=audit,
                event_repository=events,
                embedding_client=embedding_client,
            ),
            trace_id=trace_id,
            embedding_client=embedding_client,
        )

    providers = {
        BackendMode.APPEND_ONLY: lambda: AppendOnlyMemoryBackend(
            event_repository=events,
            fact_repository=facts,
            trace_id=trace_id,
        ),
        BackendMode.VECTOR: lambda: VectorMemoryBackend(
            event_repository=events,
            fact_repository=facts,
            embedding_client=get_embedding_client(),
            trace_id=trace_id,
        ),
        BackendMode.LIFECYCLE: lifecycle_backend,
    }
    return build_memory_backend(mode, providers)


def _runtime_questions(context: UnifiedContext) -> tuple[Question, ...]:
    payload = context.config_overrides.get(PRACTICE_QUESTIONS_METADATA_KEY)
    if not isinstance(payload, list) or not payload:
        raise PracticeRuntimeConfigurationError(
            f"config.{PRACTICE_QUESTIONS_METADATA_KEY} must contain structured questions"
        )
    try:
        questions = tuple(Question.model_validate(item) for item in payload)
    except Exception as exc:
        raise PracticeRuntimeConfigurationError(
            f"config.{PRACTICE_QUESTIONS_METADATA_KEY} is invalid"
        ) from exc
    return questions


async def _runtime_taxonomy(engine: AsyncEngine, practice_context: PracticeContext) -> Taxonomy:
    learning_context = _learning_context(practice_context)
    if (
        learning_context.exam_id == "postgraduate_entrance_exam"
        and learning_context.subject_id == "math_1"
        and practice_context.taxonomy_version == "math1_v1"
    ):
        return load_taxonomy("math1_v1")
    async with engine.connect() as connection:
        return await PostgresStudyPlanRepository(connection).taxonomy(
            user_id=learning_context.user_id,
            exam_id=learning_context.exam_id,
            subject_id=learning_context.subject_id,
            taxonomy_version=practice_context.taxonomy_version,
        )


def _learning_context(context: PracticeContext) -> LearningContext:
    return LearningContext.model_validate(context.scope.model_dump(exclude={"memory_namespace"}))


def _recommendation_candidate(
    *,
    knowledge_point_id: str,
    model,
    memories: Sequence[LearningMemory],
    plan_memories: Sequence[LearningMemory] = (),
) -> RecommendationCandidate:
    evidence_sources = tuple(
        memory for memory in memories if _memory_targets(memory, knowledge_point_id)
    )
    sources = (*evidence_sources, *plan_memories)
    contested = any(memory.lifecycle_state is LifecycleState.CONTESTED for memory in sources)
    weak = model is not None and knowledge_point_id in model.weak_points
    mastered = model is not None and knowledge_point_id in model.mastered_points
    stable_error = any(
        memory.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN
        and memory.lifecycle_state is LifecycleState.ACTIVE
        for memory in sources
    )
    return RecommendationCandidate(
        target_knowledge_point_id=knowledge_point_id,
        target_difficulty=0.35 if weak else 0.7 if mastered else 0.5,
        features=RecommendationFeatures(
            weakness=1.0 if weak else 0.0,
            stable_error=1.0 if stable_error else 0.0,
            forgetting_risk=_forgetting_risk(evidence_sources),
            active_plan_priority=(
                1.0
                if any(memory.lifecycle_state is LifecycleState.ACTIVE for memory in plan_memories)
                else 0.0
            ),
            coverage_gap=0.0 if weak or mastered else 1.0,
        ),
        source_memories=sources,
        source_evidence_weight=0.5 if contested else 1.0,
    )


def _plan_sources_by_knowledge_point(
    memories: Sequence[LearningMemory],
    events: Sequence[LearningEvent],
) -> dict[str, tuple[LearningMemory, ...]]:
    event_ids_by_knowledge_point: dict[str, set[str]] = {}
    for event in events:
        for knowledge_point_id in event.knowledge_point_ids:
            event_ids_by_knowledge_point.setdefault(knowledge_point_id, set()).add(event.event_id)
    return {
        knowledge_point_id: tuple(
            memory for memory in memories if set(memory.provenance) & event_ids
        )
        for knowledge_point_id, event_ids in event_ids_by_knowledge_point.items()
    }


def _memory_targets(memory: LearningMemory, knowledge_point_id: str) -> bool:
    if memory.scope.memory_namespace is MemoryNamespace.MASTERY:
        return memory.slot_key == f"mastery:{knowledge_point_id}"
    if memory.scope.memory_namespace is MemoryNamespace.ERROR_PATTERN:
        return memory.slot_key.startswith(f"error_pattern:{knowledge_point_id}:")
    return False


def _forgetting_risk(memories: Sequence[LearningMemory]) -> float:
    active = [memory for memory in memories if memory.lifecycle_state is LifecycleState.ACTIVE]
    if not active:
        return 0.0
    latest = max(memory.valid_from for memory in active)
    elapsed_days = max(0.0, (datetime.now(timezone.utc) - latest).total_seconds() / 86400)
    return min(1.0, elapsed_days / 30.0)


__all__ = [
    "BoundQuestionCatalog",
    "PRACTICE_QUESTIONS_METADATA_KEY",
    "PlanTransitionRuntime",
    "PostgresPlanTargetReader",
    "PracticeRuntime",
    "PracticeRuntimeConfigurationError",
    "PracticeRuntimeProvider",
    "RuntimeRecommendationTool",
    "TransactionalPracticeMemoryWriter",
]
