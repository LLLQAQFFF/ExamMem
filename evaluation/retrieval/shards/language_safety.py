"""Language-variation, no-answer, and isolation shard for retrieval-v2.

The benchmark corpus is deliberately dense inside one retrieval scope.  Safety
records (terminal state, another user, another exam, and the plan
namespace) live in the same dataset but are never relevant Gold.  The scale
corpus is exposed separately so synthetic load cannot improve semantic scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from evaluation.retrieval.contracts import (
    RETRIEVAL_EMBEDDING_DIMENSION,
    RETRIEVAL_PROTOCOL_VERSION,
    CorpusMemoryRecord,
    RetrievalDataset,
    RetrievalQuery,
    RetrievalSplit,
    canonical_embedding_text,
)
from exam_mem.contracts import (
    ErrorPatternValue,
    ErrorType,
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MemoryNamespace,
    MemoryScope,
    PlanStatus,
    PlanValue,
    ProfileValue,
)
from exam_mem.domain.taxonomy import KnowledgePointStatus, TaxonomyNode, load_taxonomy

_DATASET_VERSION = "exam_mem_semantic_retrieval_v2"
_POLICY_VERSION = "semantic_retrieval_v2_language_safety"
_BASE_TIME = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)
_CURRENT_ERROR_TYPES = (
    ErrorType.CONCEPT_CONFUSION,
    ErrorType.FORMULA_MISUSE,
    ErrorType.CONDITION_OMISSION,
)


@dataclass(frozen=True, slots=True)
class _Candidate:
    memory_id: str
    knowledge_point_id: str
    name: str
    alias: str
    subject_area: str
    error_type: ErrorType
    summary: str


def build_dev() -> RetrievalDataset:
    """Build 40 answerable and 10 no-answer development queries."""
    return _build_split(RetrievalSplit.DEV, answerable_count=40, no_answer_count=10)


def build_test() -> RetrievalDataset:
    """Build 80 answerable and 20 no-answer held-out queries."""
    return _build_split(RetrievalSplit.TEST, answerable_count=80, no_answer_count=20)


def build_scale_corpus(target_size: int = 10_000) -> list[CorpusMemoryRecord]:
    """Build deterministic non-Gold records for PostgreSQL/HNSW load tests.

    The return type is intentionally only a corpus, not ``RetrievalDataset``:
    these synthetic records may measure ingestion, index build, and latency but
    cannot be included in relevance metric denominators.
    """
    if target_size < 1:
        raise ValueError("target_size must be greater than or equal to 1")

    points = _leaf_points()
    scope = MemoryScope(
        user_id="hnsw_scale_user_main",
        exam_id="hnsw_scale_exam_main",
        subject_id="hnsw_scale_math_1",
        memory_namespace=MemoryNamespace.PROFILE,
    )
    records: list[CorpusMemoryRecord] = []
    for index in range(target_size):
        point = points[index % len(points)]
        attribute = f"retrieval_pressure_{index:05d}"
        summary = (
            f"压力语料（不参与相关性 Gold）：学习画像属性 {attribute}，"
            f"关联主题 {point.name_zh}，样本 {index:05d}"
        )
        event_time = _BASE_TIME + timedelta(seconds=index)
        event = LearningEvent(
            event_id=f"hnsw_scale_event_{index:05d}",
            idempotency_key=f"hnsw_scale_idempotency_{index:05d}",
            context=LearningContext(
                user_id=scope.user_id,
                exam_id=scope.exam_id,
                subject_id=scope.subject_id,
            ),
            session_id=f"hnsw_scale_session_{index // 100:05d}",
            question_id=f"hnsw_scale_question_{index:05d}",
            knowledge_point_ids=[point.id],
            difficulty=0.5,
            answer_correct=True,
            error_type=None,
            error_detail=None,
            occurred_at=event_time,
        )
        memory = LearningMemory(
            memory_id=f"hnsw_scale_memory_{index:05d}",
            scope=scope,
            slot_key=f"profile:{attribute}",
            value=ProfileValue(attribute=attribute, content=summary),
            confidence=0.8,
            evidence_count=1,
            lifecycle_state=LifecycleState.ACTIVE,
            version=1,
            valid_from=event_time,
            valid_to=None,
            superseded_by=None,
            provenance=[event.event_id],
        )
        records.append(_record(index, memory, event))
    return records


def _build_split(
    split: RetrievalSplit,
    *,
    answerable_count: int,
    no_answer_count: int,
) -> RetrievalDataset:
    scope = _main_scope(split)
    points = _leaf_points()
    records: list[CorpusMemoryRecord] = []
    candidates: list[_Candidate] = []

    # Version one is terminal and version two is current for the first slot.
    first_point = points[0]
    first_error = _CURRENT_ERROR_TYPES[0]
    successor_id = _memory_id(split, first_point.id, first_error, suffix="current_v2")
    archived_id = _memory_id(split, first_point.id, first_error, suffix="archived_v1")
    archived_event = _answer_event(
        token=f"{split.value}_archived",
        scope=scope,
        knowledge_point_id=first_point.id,
        error_type=first_error,
        summary=f"旧证据：把{first_point.name_zh}与相邻概念混淆",
        occurred_at=_BASE_TIME,
    )
    archived_memory = LearningMemory(
        memory_id=archived_id,
        scope=scope,
        slot_key=f"error_pattern:{first_point.id}:{first_error.value}",
        value=ErrorPatternValue(
            error_type=first_error,
            summary=f"旧证据：把{first_point.name_zh}与相邻概念混淆",
            details=["已由较新的学习证据取代"],
        ),
        confidence=0.62,
        evidence_count=1,
        lifecycle_state=LifecycleState.ARCHIVED,
        version=1,
        valid_from=_BASE_TIME,
        valid_to=_BASE_TIME + timedelta(minutes=1),
        superseded_by=successor_id,
        provenance=[archived_event.event_id],
    )
    records.append(_record(len(records), archived_memory, archived_event))

    for point_index, point in enumerate(points):
        aliases = point.aliases or (point.name_zh,)
        for error_index, error_type in enumerate(_CURRENT_ERROR_TYPES):
            summary = _summary(point.name_zh, error_type)
            is_successor = point_index == 0 and error_index == 0
            suffix = "current_v2" if is_successor else "current_v1"
            memory_id = _memory_id(split, point.id, error_type, suffix=suffix)
            event_time = _BASE_TIME + timedelta(minutes=2 + len(records))
            event = _answer_event(
                token=f"{split.value}_current_{point_index:02d}_{error_index}",
                scope=scope,
                knowledge_point_id=point.id,
                error_type=error_type,
                summary=summary,
                occurred_at=event_time,
            )
            memory = LearningMemory(
                memory_id=memory_id,
                scope=scope,
                slot_key=f"error_pattern:{point.id}:{error_type.value}",
                value=ErrorPatternValue(
                    error_type=error_type,
                    summary=summary,
                    details=[
                        f"正式表述：{summary}",
                        f"口语表述：{aliases[0]}这块经常弄错",
                    ],
                ),
                confidence=0.76 + 0.02 * error_index,
                evidence_count=1,
                lifecycle_state=LifecycleState.ACTIVE,
                version=2 if is_successor else 1,
                valid_from=event_time,
                valid_to=None,
                superseded_by=None,
                provenance=[event.event_id],
            )
            records.append(_record(len(records), memory, event))
            candidates.append(
                _Candidate(
                    memory_id=memory_id,
                    knowledge_point_id=point.id,
                    name=point.name_zh,
                    alias=aliases[0],
                    subject_area=point.id.rpartition(".")[0],
                    error_type=error_type,
                    summary=summary,
                )
            )

    safety_ids = _append_safety_records(records, split=split, scope=scope, points=points)
    queries = _queries(
        split,
        candidates,
        scope=scope,
        safety_ids=safety_ids,
        answerable_count=answerable_count,
        no_answer_count=no_answer_count,
    )
    return RetrievalDataset(
        protocol_version=RETRIEVAL_PROTOCOL_VERSION,
        dataset_version=_DATASET_VERSION,
        split=split,
        seed=20_260_825 if split is RetrievalSplit.DEV else 20_260_826,
        policy_version=_POLICY_VERSION,
        embedding_dimension=RETRIEVAL_EMBEDDING_DIMENSION,
        document_input_type="search_document",
        query_input_type="search_query",
        corpus=records,
        queries=queries,
    )


def _append_safety_records(
    records: list[CorpusMemoryRecord],
    *,
    split: RetrievalSplit,
    scope: MemoryScope,
    points: tuple[TaxonomyNode, ...],
) -> tuple[str, ...]:
    invalidated_id = f"language_{split.value}_memory_invalidated"
    invalidated_event = _answer_event(
        token=f"{split.value}_invalidated",
        scope=scope,
        knowledge_point_id=points[1].id,
        error_type=ErrorType.CALCULATION_ERROR,
        summary=f"已撤销的{points[1].name_zh}计算错误",
        occurred_at=_BASE_TIME + timedelta(hours=3),
    )
    invalidated = LearningMemory(
        memory_id=invalidated_id,
        scope=scope,
        slot_key=f"error_pattern:{points[1].id}:calculation_error",
        value=ErrorPatternValue(
            error_type=ErrorType.CALCULATION_ERROR,
            summary=f"已撤销的{points[1].name_zh}计算错误",
            details=["教师复核后确认不是用户错误"],
        ),
        confidence=0.4,
        evidence_count=1,
        lifecycle_state=LifecycleState.INVALIDATED,
        version=1,
        valid_from=_BASE_TIME + timedelta(hours=2),
        valid_to=_BASE_TIME + timedelta(hours=3),
        superseded_by=None,
        provenance=[invalidated_event.event_id],
    )
    records.append(_record(len(records), invalidated, invalidated_event))

    cross_scope_ids: list[str] = []
    for label, foreign_scope in (
        (
            "other_user",
            scope.model_copy(update={"user_id": f"language_{split.value}_user_other"}),
        ),
        (
            "other_exam",
            scope.model_copy(update={"exam_id": f"language_{split.value}_exam_other"}),
        ),
    ):
        memory_id = f"language_{split.value}_memory_{label}"
        event = _answer_event(
            token=f"{split.value}_{label}",
            scope=foreign_scope,
            knowledge_point_id=points[0].id,
            error_type=ErrorType.CONCEPT_CONFUSION,
            summary=f"隔离样本：{points[0].name_zh}概念混淆",
            occurred_at=_BASE_TIME + timedelta(hours=4 + len(cross_scope_ids)),
        )
        memory = LearningMemory(
            memory_id=memory_id,
            scope=foreign_scope,
            slot_key=f"error_pattern:{points[0].id}:concept_confusion",
            value=ErrorPatternValue(
                error_type=ErrorType.CONCEPT_CONFUSION,
                summary=f"隔离样本：{points[0].name_zh}概念混淆",
                details=[label],
            ),
            confidence=0.9,
            evidence_count=1,
            lifecycle_state=LifecycleState.ACTIVE,
            version=1,
            valid_from=event.occurred_at,
            valid_to=None,
            superseded_by=None,
            provenance=[event.event_id],
        )
        records.append(_record(len(records), memory, event))
        cross_scope_ids.append(memory_id)

    plan_scope = MemoryScope(
        user_id=scope.user_id,
        exam_id=scope.exam_id,
        subject_id=scope.subject_id,
        memory_namespace=MemoryNamespace.PLAN,
    )
    plan_id = f"language_{split.value}_memory_plan_namespace"
    plan_event = _answer_event(
        token=f"{split.value}_plan_namespace",
        scope=plan_scope,
        knowledge_point_id=points[0].id,
        error_type=ErrorType.CONCEPT_CONFUSION,
        summary="计划命名空间隔离证据",
        occurred_at=_BASE_TIME + timedelta(hours=6),
    )
    plan_memory = LearningMemory(
        memory_id=plan_id,
        scope=plan_scope,
        slot_key=f"plan:{scope.exam_id}:{scope.subject_id}",
        value=PlanValue(
            goal="复习数学一易错知识点",
            status=PlanStatus.IN_PROGRESS,
            progress=0.25,
        ),
        confidence=0.85,
        evidence_count=1,
        lifecycle_state=LifecycleState.ACTIVE,
        version=1,
        valid_from=plan_event.occurred_at,
        valid_to=None,
        superseded_by=None,
        provenance=[plan_event.event_id],
    )
    records.append(_record(len(records), plan_memory, plan_event))
    return (
        records[0].memory.memory_id,
        invalidated_id,
        *cross_scope_ids,
        plan_id,
    )


def _queries(
    split: RetrievalSplit,
    candidates: list[_Candidate],
    *,
    scope: MemoryScope,
    safety_ids: tuple[str, ...],
    answerable_count: int,
    no_answer_count: int,
) -> list[RetrievalQuery]:
    variants = _answerable_variants(split)
    answerable: list[RetrievalQuery] = []
    for index in range(answerable_count):
        candidate = candidates[(index * 7) % len(candidates)]
        family, render = variants[index % len(variants)]
        relevant_candidates = (
            [item for item in candidates if item.knowledge_point_id == candidate.knowledge_point_id]
            if family in {"colloquial", "short_name", "spoken_followup", "alias_lookup"}
            else [candidate]
        )
        relevant_ids = [item.memory_id for item in relevant_candidates]
        answerable.append(
            RetrievalQuery(
                query_id=f"language_{split.value}_query_answerable_{index:03d}",
                query_family=f"language_{split.value}_{family}",
                text=render(candidate),
                scope=scope,
                top_k=5,
                relevant_memory_ids=relevant_ids,
                relevance_grades={memory_id: 3 for memory_id in relevant_ids},
                hard_negative_memory_ids=_hard_negatives(
                    candidates,
                    relevant_candidates,
                    count=12,
                ),
                must_not_return_memory_ids=list(safety_ids),
                expected_no_answer=False,
            )
        )

    outside_topics = (
        (
            "傅里叶变换的频谱泄漏",
            "常微分方程的通解",
            "复变函数的留数定理",
            "离散数学的图着色",
            "抽象代数的群同态",
        )
        if split is RetrievalSplit.DEV
        else (
            "拉普拉斯变换的收敛域",
            "曲面积分的方向选择",
            "数值分析的截断误差",
            "线性规划的单纯形表",
            "偏微分方程的边界条件",
        )
    )
    no_answer: list[RetrievalQuery] = []
    for index in range(no_answer_count):
        ambiguous = index % 2 == 1
        topic = outside_topics[(index // 2) % len(outside_topics)]
        no_answer.append(
            RetrievalQuery(
                query_id=f"language_{split.value}_query_no_answer_{index:03d}",
                query_family=(
                    f"language_{split.value}_ambiguous_reference"
                    if ambiguous
                    else f"language_{split.value}_no_answer"
                ),
                text=(
                    f"我上次说的那个错误到底是什么？只记得是第 {index + 1} 次提到的。"
                    if ambiguous
                    else f"我有没有关于{topic}的错误记忆？（查询变体 {index + 1}）"
                ),
                scope=scope,
                top_k=5,
                relevant_memory_ids=[],
                relevance_grades={},
                hard_negative_memory_ids=[item.memory_id for item in candidates[:12]],
                must_not_return_memory_ids=list(safety_ids),
                expected_no_answer=True,
            )
        )
    return answerable + no_answer


def _hard_negatives(
    candidates: list[_Candidate],
    relevant: list[_Candidate],
    *,
    count: int,
) -> list[str]:
    relevant_ids = {item.memory_id for item in relevant}
    relevant_areas = {item.subject_area for item in relevant}
    same_area = [
        item
        for item in candidates
        if item.memory_id not in relevant_ids and item.subject_area in relevant_areas
    ]
    other_area = [
        item
        for item in candidates
        if item.memory_id not in relevant_ids and item.subject_area not in relevant_areas
    ]
    return [item.memory_id for item in (same_area + other_area)[:count]]


def _record(
    write_index: int,
    memory: LearningMemory,
    event: LearningEvent,
) -> CorpusMemoryRecord:
    return CorpusMemoryRecord(
        write_index=write_index,
        memory=memory,
        provenance_events=[event],
        embedding_text=canonical_embedding_text(memory),
        contested_group_id=None,
        provenance_relations={event.event_id: "created_by"},
    )


def _answer_event(
    *,
    token: str,
    scope: MemoryScope,
    knowledge_point_id: str,
    error_type: ErrorType,
    summary: str,
    occurred_at: datetime,
) -> LearningEvent:
    return LearningEvent(
        event_id=f"language_event_{token}",
        idempotency_key=f"language_idempotency_{token}",
        context=LearningContext(
            user_id=scope.user_id,
            exam_id=scope.exam_id,
            subject_id=scope.subject_id,
        ),
        session_id=f"language_session_{token}",
        question_id=f"language_question_{token}",
        knowledge_point_ids=[knowledge_point_id],
        difficulty=0.65,
        answer_correct=False,
        error_type=error_type,
        error_detail=summary,
        occurred_at=occurred_at,
    )


def _main_scope(split: RetrievalSplit) -> MemoryScope:
    return MemoryScope(
        user_id=f"language_{split.value}_user_main",
        exam_id=f"language_{split.value}_exam_main",
        subject_id="language_math_1",
        memory_namespace=MemoryNamespace.ERROR_PATTERN,
    )


def _answerable_variants(split: RetrievalSplit):
    if split is RetrievalSplit.DEV:
        return (
            (
                "formal",
                lambda item: f"请查找我在{item.name}上的{_error_label(item.error_type)}记录。",
            ),
            ("colloquial", lambda item: f"{item.alias}这块我老出错，之前具体卡在哪？"),
            ("short_name", lambda item: f"搜一下“{item.alias}”的薄弱记忆。"),
            ("implicit", lambda item: f"帮我找找这类记录：{_paraphrased_issue(item)}"),
        )
    return (
        (
            "technical_expression",
            lambda item: f"检索与{item.name}的{_error_label(item.error_type)}有关的历史证据。",
        ),
        ("spoken_followup", lambda item: f"{item.alias}我怎么又栽了，历史上是哪种毛病？"),
        ("alias_lookup", lambda item: f"定位“{item.alias}”对应的易错项。"),
        (
            "indirect_reference",
            lambda item: f"哪条历史记忆对应这个情况：{_paraphrased_issue(item)}",
        ),
    )


def _leaf_points() -> tuple[TaxonomyNode, ...]:
    taxonomy = load_taxonomy("math1_v1")
    return tuple(
        node
        for node in taxonomy.nodes
        if node.status is KnowledgePointStatus.ACTIVE and not taxonomy.children_of(node.id)
    )


def _memory_id(
    split: RetrievalSplit,
    knowledge_point_id: str,
    error_type: ErrorType,
    *,
    suffix: str,
) -> str:
    point_token = knowledge_point_id.replace(".", "_")
    return f"language_{split.value}_memory_{point_token}_{error_type.value}_{suffix}"


def _summary(name: str, error_type: ErrorType) -> str:
    if error_type is ErrorType.CONCEPT_CONFUSION:
        return f"把{name}的定义与相邻概念混淆"
    if error_type is ErrorType.FORMULA_MISUSE:
        return f"在{name}计算中套用了不适用的公式"
    return f"使用{name}时遗漏公式或定理的成立条件"


def _paraphrased_issue(item: _Candidate) -> str:
    if item.error_type is ErrorType.CONCEPT_CONFUSION:
        return f"学{item.alias}时我似乎总分不清它和附近的概念。"
    if item.error_type is ErrorType.FORMULA_MISUSE:
        return f"做{item.alias}题时，我可能没判断公式适用的场景。"
    return f"处理{item.alias}时，我好像忘记核对必要前提。"


def _error_label(error_type: ErrorType) -> str:
    return {
        ErrorType.CONCEPT_CONFUSION: "概念混淆",
        ErrorType.FORMULA_MISUSE: "公式误用",
        ErrorType.CONDITION_OMISSION: "条件遗漏",
        ErrorType.CALCULATION_ERROR: "计算错误",
        ErrorType.REASONING_GAP: "推理缺口",
        ErrorType.READING_ERROR: "审题错误",
        ErrorType.CARELESS_ERROR: "粗心错误",
        ErrorType.UNKNOWN: "未分类错误",
    }[error_type]


__all__ = ["build_dev", "build_scale_corpus", "build_test"]
