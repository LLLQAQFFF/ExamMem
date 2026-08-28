"""Build the cross-subject computer-science controlled holdout dataset."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from evaluation.contracts.dataset import ControlledQuestion, DatasetManifest
from evaluation.data_builder import _build_dataset
from evaluation.protocols.validation import DATASET_ROOT
from exam_mem.domain.taxonomy import load_taxonomy

DATASET_VERSION = "exam_mem_controlled_v2"
TAXONOMY_VERSION = "cs_v1"
_GENERATED_AT = datetime(2026, 8, 28, tzinfo=timezone.utc)

_QUESTION_SPECS: dict[str, tuple[str, str, str, str, str, float]] = {
    "cs.data_structures.array": (
        "在数组中按下标读取一个元素的典型时间复杂度是多少？",
        "典型时间复杂度是 O(1)，因为数组使用连续存储，可由首地址和下标直接定位。",
        "时间复杂度是 O(n)，因为必须从第一个元素依次扫描。",
        "混淆了按下标访问与按值查找。",
        "data_structures",
        0.2,
    ),
    "cs.data_structures.linked_list": (
        "已知单链表某节点的指针，在其后插入新节点的典型时间复杂度是多少？",
        "典型时间复杂度是 O(1)，只需修改常数个指针。",
        "时间复杂度是 O(n)，任何链表插入都必须遍历完整链表。",
        "忽略了题目已经给出目标节点指针。",
        "data_structures",
        0.3,
    ),
    "cs.data_structures.stack": (
        "函数调用栈最符合哪一种元素访问顺序？",
        "后进先出（LIFO）；最后进入的调用帧最先退出。",
        "先进先出（FIFO）；最早进入的调用帧最先退出。",
        "把栈的 LIFO 特性与队列的 FIFO 特性混淆。",
        "data_structures",
        0.2,
    ),
    "cs.data_structures.queue": (
        "普通队列的入队和出队遵循什么顺序？",
        "先进先出（FIFO）；队尾入队，队头出队。",
        "后进先出（LIFO）；队尾入队并从队尾出队。",
        "把普通队列误当成栈。",
        "data_structures",
        0.2,
    ),
    "cs.data_structures.tree_traversal": (
        "二叉树中序遍历访问根节点、左子树和右子树的顺序是什么？",
        "先遍历左子树，再访问根节点，最后遍历右子树。",
        "先访问根节点，再遍历左子树和右子树。",
        "把中序遍历与前序遍历混淆。",
        "data_structures",
        0.35,
    ),
    "cs.data_structures.hash_table": (
        "哈希表发生冲突是什么意思？",
        "两个或多个不同键经过哈希函数后映射到同一位置。",
        "同一个键在两次查询中得到不同的哈希值。",
        "没有理解哈希冲突描述的是不同键映射到同一位置。",
        "data_structures",
        0.35,
    ),
    "cs.algorithms.binary_search": (
        "二分查找能够直接应用于数组的关键前提是什么？",
        "数组必须按所用比较规则有序，才能根据中点比较排除一半区间。",
        "数组必须完全随机，才能保证每次排除一半元素。",
        "遗漏了二分查找依赖有序性。",
        "algorithms",
        0.3,
    ),
    "cs.algorithms.sorting_stability": (
        "排序算法的稳定性指什么？",
        "具有相等关键字的元素在排序后仍保持原有相对顺序。",
        "算法对任何输入都具有相同运行时间。",
        "把结果的相对顺序性质误解成运行时间稳定。",
        "algorithms",
        0.35,
    ),
    "cs.algorithms.bfs": (
        "在无权图中求单源最短路径通常使用什么遍历？",
        "通常使用广度优先搜索（BFS），它按距离层次访问节点。",
        "通常使用深度优先搜索（DFS），首次到达一定是最短路径。",
        "混淆了 BFS 的分层性质与 DFS 的深入探索顺序。",
        "algorithms",
        0.4,
    ),
    "cs.algorithms.dfs": (
        "深度优先搜索递归实现中的隐式辅助结构是什么？",
        "调用栈；它保存尚未完成的递归路径。",
        "队列；它保证按距离层次访问节点。",
        "把 DFS 所需的栈与 BFS 所需的队列混淆。",
        "algorithms",
        0.35,
    ),
    "cs.algorithms.dynamic_programming": (
        "动态规划适合处理的问题通常具有什么结构？",
        "通常具有重叠子问题和最优子结构，可保存子问题结果避免重复计算。",
        "子问题必须互不相关，且不能复用任何中间结果。",
        "颠倒了动态规划依赖重叠子问题的条件。",
        "algorithms",
        0.5,
    ),
    "cs.algorithms.greedy": (
        "贪心算法为什么不能仅凭每步局部最优就保证全局最优？",
        "还必须证明问题具有贪心选择性质和相应的最优子结构。",
        "因为任何局部最优选择都必然自动得到全局最优。",
        "把贪心策略当成无需证明即可成立的普遍结论。",
        "algorithms",
        0.5,
    ),
}
_TOPIC_ORDER = tuple(_QUESTION_SPECS)


def _question_bank() -> list[ControlledQuestion]:
    taxonomy = load_taxonomy(TAXONOMY_VERSION)
    questions: list[ControlledQuestion] = []
    for knowledge_point_id, (
        prompt,
        reference,
        wrong,
        error_detail,
        subject_area,
        difficulty,
    ) in sorted(_QUESTION_SPECS.items()):
        node = taxonomy.get(knowledge_point_id)
        if node is None or taxonomy.children_of(knowledge_point_id):
            raise ValueError(f"v2 question must reference a taxonomy leaf: {knowledge_point_id}")
        slug = knowledge_point_id.rsplit(".", 1)[-1]
        questions.append(
            ControlledQuestion(
                question_id=f"controlled2:{slug}:v1",
                knowledge_point_id=knowledge_point_id,
                subject_area=subject_area,
                difficulty=difficulty,
                prompt_zh=prompt,
                reference_answer_zh=reference,
                rubric_items=["结论正确", "关键概念或复杂度正确", "理由可复核"],
                answer_forms=[
                    {"answer_id": "correct", "text_zh": reference, "correct": True},
                    {
                        "answer_id": "wrong",
                        "text_zh": wrong,
                        "correct": False,
                        "error_type": "concept_confusion",
                        "error_detail": error_detail,
                    },
                ],
            )
        )
    return questions


def build_cross_subject_dataset(output_root: Path = DATASET_ROOT) -> DatasetManifest:
    """Materialize a deterministic 40/80 CS split without tuning production policy."""
    return _build_dataset(
        output_root=output_root,
        dataset_version=DATASET_VERSION,
        taxonomy_version=TAXONOMY_VERSION,
        questions=_question_bank(),
        topic_order=_TOPIC_ORDER,
        case_prefix="holdout2",
        case_root=Path(DATASET_VERSION),
        generated_at=_GENERATED_AT,
        learner_background_zh=(
            "我正在学习计算机基础中的数据结构与算法，正在通过练习检查长期掌握情况。"
        ),
        construction_notes=[
            "The production recommendation policy was frozen before this cross-subject dataset was scored.",
            "The 24 reviewed lifecycle cases provide trajectory shapes, not subject labels or answers.",
            "All questions and canonical IDs belong to the independent cs_v1 taxonomy.",
            "Every case has isolated identifiers, at least three events, and at least two sessions.",
            "Each scenario uses ten distinct knowledge-point tasks; trajectory_family is the leakage key.",
            "Dev contains 40 cases and the one-time frozen test contains 80 cases using seed 20260806.",
        ],
    )


__all__ = ["DATASET_VERSION", "TAXONOMY_VERSION", "build_cross_subject_dataset"]
