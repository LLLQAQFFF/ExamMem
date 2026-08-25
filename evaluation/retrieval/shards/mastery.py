"""Deterministic mastery-state shard for semantic-retrieval-v2."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from evaluation.retrieval.contracts import (
    RETRIEVAL_PROTOCOL_VERSION,
    CorpusMemoryRecord,
    RetrievalDataset,
    RetrievalQuery,
    RetrievalSplit,
    canonical_embedding_text,
)
from exam_mem.contracts import (
    ErrorType,
    LearningContext,
    LearningEvent,
    LearningMemory,
    LifecycleState,
    MasteryLevel,
    MasteryValue,
    MemoryNamespace,
    MemoryScope,
)
from exam_mem.domain import load_taxonomy
from exam_mem.domain.taxonomy import TaxonomyNode

_SEED = 20260825
_ANSWERABLE_COUNTS = {RetrievalSplit.DEV: 40, RetrievalSplit.TEST: 80}
_NO_ANSWER_COUNTS = {RetrievalSplit.DEV: 10, RetrievalSplit.TEST: 20}
_BASE_TIME = datetime(2026, 8, 25, 8, 0, tzinfo=timezone.utc)


@dataclass(frozen=True, slots=True)
class _Target:
    memory_id: str
    trajectory: str
    node: TaxonomyNode
    conflicting_memory_id: str | None = None
    contested_group_id: str | None = None


def _leaves() -> tuple[TaxonomyNode, ...]:
    taxonomy = load_taxonomy("math1_v1")
    return tuple(node for node in taxonomy.nodes if not taxonomy.children_of(node.id))


def _slug(knowledge_point_id: str) -> str:
    return knowledge_point_id.replace(".", "_")


def _context(split: RetrievalSplit, *, cross_scope: bool = False) -> LearningContext:
    user_suffix = "cross_user" if cross_scope else "user"
    return LearningContext(
        user_id=f"mastery_{user_suffix}_{split.value}",
        exam_id="mastery_postgraduate_exam",
        subject_id="mastery_math_1",
    )


def _scope(split: RetrievalSplit, *, cross_scope: bool = False) -> MemoryScope:
    context = _context(split, cross_scope=cross_scope)
    return MemoryScope(**context.model_dump(), memory_namespace=MemoryNamespace.MASTERY)


def _event(
    *,
    split: RetrievalSplit,
    node: TaxonomyNode,
    sequence: int,
    level: MasteryLevel,
    cross_scope: bool = False,
) -> LearningEvent:
    correct = level in {MasteryLevel.IMPROVING, MasteryLevel.HIGH, MasteryLevel.MASTERED}
    identifier = f"mastery_{split.value}_{_slug(node.id)}_{sequence:03d}"
    return LearningEvent(
        event_id=f"{identifier}_event",
        idempotency_key=f"{identifier}_idempotency",
        context=_context(split, cross_scope=cross_scope),
        session_id=f"mastery_{split.value}_session",
        question_id=f"{identifier}_question",
        knowledge_point_ids=[node.id],
        difficulty=0.55,
        answer_correct=correct,
        error_type=None if correct else ErrorType.CONCEPT_CONFUSION,
        error_detail=None if correct else f"在{node.name_zh}的核心条件上存在稳定混淆。",
        occurred_at=_BASE_TIME + timedelta(seconds=sequence),
    )


def _memory(
    *,
    split: RetrievalSplit,
    node: TaxonomyNode,
    version: int,
    level: MasteryLevel,
    score: float,
    state: LifecycleState,
    valid_from: datetime,
    provenance: list[str],
    suffix: str,
    valid_to: datetime | None = None,
    superseded_by: str | None = None,
    cross_scope: bool = False,
) -> LearningMemory:
    return LearningMemory(
        memory_id=f"mastery_{split.value}_{_slug(node.id)}_{suffix}_memory",
        scope=_scope(split, cross_scope=cross_scope),
        slot_key=f"mastery:{node.id}",
        value=MasteryValue(level=level, score=score),
        confidence=0.92,
        evidence_count=len(provenance),
        lifecycle_state=state,
        version=version,
        valid_from=valid_from,
        valid_to=valid_to,
        superseded_by=superseded_by,
        provenance=provenance,
    )


def _record(
    records: list[CorpusMemoryRecord],
    memory: LearningMemory,
    events: list[LearningEvent],
    *,
    contested_group_id: str | None = None,
    merged_event_ids: frozenset[str] = frozenset(),
) -> CorpusMemoryRecord:
    record = CorpusMemoryRecord(
        write_index=len(records),
        memory=memory,
        provenance_events=events,
        embedding_text=canonical_embedding_text(memory),
        contested_group_id=contested_group_id,
        provenance_relations={
            event.event_id: ("merged_from" if event.event_id in merged_event_ids else "created_by")
            for event in events
        },
    )
    records.append(record)
    return record


def _history_records(
    split: RetrievalSplit,
    node: TaxonomyNode,
    node_index: int,
    records: list[CorpusMemoryRecord],
    *,
    improving: bool,
) -> tuple[_Target, str]:
    old_level = MasteryLevel.LOW if improving else MasteryLevel.MASTERED
    new_level = MasteryLevel.IMPROVING if improving else MasteryLevel.LOW
    old_score = 0.20 if improving else 0.94
    new_score = 0.68 if improving else 0.24
    old_event = _event(
        split=split,
        node=node,
        sequence=node_index * 10,
        level=old_level,
    )
    new_event = _event(
        split=split,
        node=node,
        sequence=node_index * 10 + 1,
        level=new_level,
    )
    new_memory_id = f"mastery_{split.value}_{_slug(node.id)}_current_v2_memory"
    transition_time = new_event.occurred_at
    predecessor = _memory(
        split=split,
        node=node,
        version=1,
        level=old_level,
        score=old_score,
        state=LifecycleState.ARCHIVED,
        valid_from=old_event.occurred_at,
        valid_to=transition_time,
        superseded_by=new_memory_id,
        provenance=[old_event.event_id],
        suffix="archived_v1",
    )
    _record(records, predecessor, [old_event])
    successor = _memory(
        split=split,
        node=node,
        version=2,
        level=new_level,
        score=new_score,
        state=LifecycleState.ACTIVE,
        valid_from=transition_time,
        provenance=[old_event.event_id, new_event.event_id],
        suffix="current_v2",
    )
    _record(
        records,
        successor,
        [old_event, new_event],
        merged_event_ids=frozenset({old_event.event_id}),
    )
    trajectory = "improving" if improving else "declining"
    return _Target(successor.memory_id, trajectory, node), predecessor.memory_id


def _contested_records(
    split: RetrievalSplit,
    node: TaxonomyNode,
    node_index: int,
    records: list[CorpusMemoryRecord],
) -> _Target:
    trajectory_cycle = (
        ("mastered", MasteryLevel.MASTERED, 0.95, MasteryLevel.LOW, 0.18),
        ("weak", MasteryLevel.LOW, 0.19, MasteryLevel.MASTERED, 0.93),
        ("improving", MasteryLevel.IMPROVING, 0.66, MasteryLevel.LOW, 0.25),
    )
    trajectory, gold_level, gold_score, contrast_level, contrast_score = trajectory_cycle[
        node_index % len(trajectory_cycle)
    ]
    group_id = f"mastery_{split.value}_{_slug(node.id)}_contested_group"
    gold_event = _event(
        split=split,
        node=node,
        sequence=node_index * 10,
        level=gold_level,
    )
    contrast_event = _event(
        split=split,
        node=node,
        sequence=node_index * 10 + 1,
        level=contrast_level,
    )
    gold = _memory(
        split=split,
        node=node,
        version=1,
        level=gold_level,
        score=gold_score,
        state=LifecycleState.CONTESTED,
        valid_from=gold_event.occurred_at,
        provenance=[gold_event.event_id],
        suffix="gold_v1",
    )
    _record(records, gold, [gold_event], contested_group_id=group_id)
    contrast = _memory(
        split=split,
        node=node,
        version=2,
        level=contrast_level,
        score=contrast_score,
        state=LifecycleState.CONTESTED,
        valid_from=contrast_event.occurred_at,
        provenance=[contrast_event.event_id],
        suffix="contrast_v2",
    )
    _record(records, contrast, [contrast_event], contested_group_id=group_id)
    return _Target(
        gold.memory_id,
        trajectory,
        node,
        conflicting_memory_id=contrast.memory_id,
        contested_group_id=group_id,
    )


def _corpus(
    split: RetrievalSplit,
) -> tuple[list[CorpusMemoryRecord], dict[str, _Target], list[str], str]:
    records: list[CorpusMemoryRecord] = []
    targets: dict[str, _Target] = {}
    archived_ids: list[str] = []
    leaves = _leaves()
    for index, node in enumerate(leaves):
        if index < 4:
            target, archived_id = _history_records(
                split,
                node,
                index,
                records,
                improving=index % 2 == 0,
            )
            archived_ids.append(archived_id)
        else:
            target = _contested_records(split, node, index, records)
        targets[node.id] = target

    cross_node = leaves[0]
    cross_event = _event(
        split=split,
        node=cross_node,
        sequence=999,
        level=MasteryLevel.MASTERED,
        cross_scope=True,
    )
    cross_memory = _memory(
        split=split,
        node=cross_node,
        version=1,
        level=MasteryLevel.MASTERED,
        score=0.99,
        state=LifecycleState.ACTIVE,
        valid_from=cross_event.occurred_at,
        provenance=[cross_event.event_id],
        suffix="cross_scope_v1",
        cross_scope=True,
    )
    _record(records, cross_memory, [cross_event])
    return records, targets, archived_ids, cross_memory.memory_id


def _hard_negatives(
    target: _Target,
    records: list[CorpusMemoryRecord],
    *,
    scope: MemoryScope,
    offset: int,
    excluded_memory_ids: frozenset[str] = frozenset(),
) -> list[str]:
    current = [
        record.memory
        for record in records
        if record.memory.scope == scope
        and record.memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
    ]
    target_parent = target.node.id.rpartition(".")[0]
    excluded = {target.memory_id, *excluded_memory_ids}
    candidates = [memory for memory in current if memory.memory_id not in excluded]
    candidates.sort(
        key=lambda memory: (
            0 if memory.slot_key == f"mastery:{target.node.id}" else 1,
            0
            if memory.slot_key.removeprefix("mastery:").rpartition(".")[0] == target_parent
            else 1,
            memory.memory_id,
        )
    )
    if candidates:
        offset %= len(candidates)
        candidates = candidates[offset:] + candidates[:offset]
    return [memory.memory_id for memory in candidates[:10]]


def _query_text(target: _Target, variant: int, *, expose_conflict: bool) -> str:
    alias = target.node.aliases[0] if target.node.aliases else target.node.name_zh
    if expose_conflict:
        return (
            f"关于{target.node.name_zh}存在尚未解决的冲突；请同时检索互相矛盾的"
            "两个当前分支，不要替我静默裁决。"
        )
    if target.contested_group_id is None:
        history_templates = {
            "improving": (
                "完成最近一轮练习后，{name}的最新学习判断是什么？",
                "请忽略已经归档的旧判断，只检索{alias}的当前版本。",
            ),
            "declining": (
                "请只看最新证据，我在{name}上现在应当怎样评估？",
                "忽略旧结论后，{alias}对应的当前学习记录是哪一条？",
            ),
        }
        return history_templates[target.trajectory][variant % 2].format(
            name=target.node.name_zh,
            alias=alias,
        )
    templates = {
        "mastered": (
            "{name}存在未解决的状态冲突；请检索其中声称已经形成稳定掌握的分支。",
            "面对{alias}尚未裁决的冲突记录，请找出声称掌握稳固的那个分支。",
        ),
        "weak": (
            "{name}存在未解决的状态冲突；请检索其中声称仍需重点补强的分支。",
            "面对{alias}尚未裁决的冲突记录，请找出声称基础仍不牢固的分支。",
        ),
        "improving": (
            "{name}存在未解决的状态冲突；请检索其中声称仍处在改善阶段的分支。",
            "面对{alias}尚未裁决的冲突记录，请找出声称正在进步的那个分支。",
        ),
        "declining": (
            "请只看最新证据，我在{name}上现在应当怎样评估？",
            "忽略旧结论后，{alias}对应的当前学习记录是哪一条？",
        ),
    }
    template = templates[target.trajectory][variant % 2]
    return template.format(name=target.node.name_zh, alias=alias)


def _queries(
    split: RetrievalSplit,
    records: list[CorpusMemoryRecord],
    targets: dict[str, _Target],
    archived_ids: list[str],
    cross_scope_id: str,
) -> list[RetrievalQuery]:
    leaves = _leaves()
    must_not = [*archived_ids[:2], cross_scope_id]
    queries: list[RetrievalQuery] = []
    for index in range(_ANSWERABLE_COUNTS[split]):
        target = targets[leaves[index % len(leaves)].id]
        expose_conflict = target.conflicting_memory_id is not None and index % 7 == 0
        relevant_memory_ids = [target.memory_id]
        query_trajectory = target.trajectory
        if expose_conflict:
            relevant_memory_ids.append(target.conflicting_memory_id)
            query_trajectory = "conflict_exposure"
        queries.append(
            RetrievalQuery(
                query_id=f"mastery_{split.value}_answerable_{index:03d}",
                query_family=(
                    f"mastery_{split.value}_{query_trajectory}_{_slug(target.node.id)}_"
                    f"{index // len(leaves):02d}"
                ),
                text=_query_text(
                    target,
                    index // len(leaves),
                    expose_conflict=expose_conflict,
                ),
                scope=_scope(split),
                top_k=5,
                relevant_memory_ids=relevant_memory_ids,
                relevance_grades={memory_id: 3 for memory_id in relevant_memory_ids},
                hard_negative_memory_ids=_hard_negatives(
                    target,
                    records,
                    scope=_scope(split),
                    offset=index,
                    excluded_memory_ids=frozenset(relevant_memory_ids),
                ),
                must_not_return_memory_ids=must_not,
                expected_no_answer=False,
            )
        )

    absent_topics = (
        "曲线积分与格林公式",
        "傅里叶级数的收敛判别",
        "多元函数的拉格朗日乘数法",
        "常微分方程的通解结构",
        "无穷级数的一致收敛性",
    )
    current_ids = [
        record.memory.memory_id
        for record in records
        if record.memory.scope == _scope(split)
        and record.memory.lifecycle_state in {LifecycleState.ACTIVE, LifecycleState.CONTESTED}
    ]
    for index in range(_NO_ANSWER_COUNTS[split]):
        rotated = current_ids[index:] + current_ids[:index]
        queries.append(
            RetrievalQuery(
                query_id=f"mastery_{split.value}_no_answer_{index:03d}",
                query_family=f"mastery_{split.value}_no_answer_family_{index:03d}",
                text=f"请检查现有学习记忆是否记录过我对{absent_topics[index % len(absent_topics)]}的掌握情况。",
                scope=_scope(split),
                top_k=5,
                relevant_memory_ids=[],
                relevance_grades={},
                hard_negative_memory_ids=rotated[:10],
                must_not_return_memory_ids=must_not,
                expected_no_answer=True,
            )
        )
    return queries


def _build(split: RetrievalSplit) -> RetrievalDataset:
    records, targets, archived_ids, cross_scope_id = _corpus(split)
    return RetrievalDataset(
        protocol_version=RETRIEVAL_PROTOCOL_VERSION,
        dataset_version="exam_mem_semantic_retrieval_v2",
        split=split,
        seed=_SEED,
        policy_version="mastery_retrieval_policy_v2",
        embedding_dimension=1024,
        document_input_type="search_document",
        query_input_type="search_query",
        corpus=records,
        queries=_queries(split, records, targets, archived_ids, cross_scope_id),
    )


def build_dev() -> RetrievalDataset:
    return _build(RetrievalSplit.DEV)


def build_test() -> RetrievalDataset:
    return _build(RetrievalSplit.TEST)


__all__ = ["build_dev", "build_test"]
