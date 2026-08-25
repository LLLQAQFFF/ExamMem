"""Dense error-pattern shard for the semantic-retrieval-v2 benchmark.

The shard intentionally makes retrieval difficult inside one four-dimensional
scope: every active taxonomy leaf has three current error-pattern memories,
plus semantically close historical and cross-user records used as safety gold.
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
)
from exam_mem.domain.slot_key import build_error_pattern_slot_key
from exam_mem.domain.taxonomy import Taxonomy, load_taxonomy

DATASET_VERSION = "exam_mem_semantic_retrieval_v2"
ERRORS_SEED = 20260825
_BASE_TIME = datetime(2026, 8, 25, tzinfo=timezone.utc)
_ERROR_TYPES = (
    ErrorType.CONCEPT_CONFUSION,
    ErrorType.FORMULA_MISUSE,
    ErrorType.CONDITION_OMISSION,
)


@dataclass(frozen=True)
class _TopicSemantics:
    concept_memory: str
    concept_query: str
    formula_memory: str
    formula_query: str
    condition_memory: str
    condition_query: str


# Memory and query wording are deliberately different. The paired descriptions
# express the same misconception without copying a diagnostic sentence verbatim.
_SEMANTICS: dict[str, _TopicSemantics] = {
    "math1.linear_algebra.matrix_multiplication": _TopicSemantics(
        "把矩阵乘积错误地当作可交换运算，认为交换左右因子不影响结果",
        "我总觉得两个矩阵前后换个位置乘，答案应该还是一样",
        "把矩阵乘法写成对应位置元素逐项相乘",
        "算 AB 时我直接把同位置的数相乘了",
        "没有检查前一矩阵列数必须等于后一矩阵行数",
        "动笔前没核对左边的列和右边的行能不能接上",
    ),
    "math1.linear_algebra.inverse_matrix": _TopicSemantics(
        "把逆矩阵理解成对每个矩阵元素分别取倒数",
        "求逆时我把每个非零元素都翻成了分之一",
        "伴随矩阵公式中遗漏行列式倒数或伴随转置关系",
        "我写了 A 的逆等于伴随矩阵乘 det(A)，分母没了",
        "没有先确认矩阵为方阵且行列式非零",
        "没判断 det(A) 会不会等于零就开始求逆",
    ),
    "math1.linear_algebra.determinant": _TopicSemantics(
        "混淆行列式与矩阵元素和，忽略排列项的符号",
        "我算行列式时把所有对角方向的乘积都直接加起来了",
        "二阶行列式把副对角乘积写成加号而不是减号",
        "二阶式子被我写成 ad+bc 了",
        "把行列式用于非方阵而未检查阶数条件",
        "这个矩阵行列数不一样，我还是直接写了 det",
    ),
    "math1.linear_algebra.elementary_transformations": _TopicSemantics(
        "混淆初等行变换与初等列变换对方程组含义的影响",
        "解方程时我随手把两列交换了，还以为未知量含义不变",
        "一行乘常数后未同步处理行列式的倍数变化",
        "把某行放大三倍后，我仍说行列式完全没变",
        "行倍加操作中没有限定倍加到另一行而误改同一行",
        "我把一行的倍数加回它自己，没检查这是不是合法初等操作",
    ),
    "math1.linear_algebra.matrix_rank": _TopicSemantics(
        "把矩阵的总行数或非零元素个数直接当成秩",
        "三行矩阵我就顺手说 rank 是三，没看行之间是否重复",
        "求秩时用行列式值代替最高阶非零子式的阶数",
        "我算出 det=5，就写矩阵的秩等于 5",
        "没有检查秩不能超过行数与列数的较小者",
        "结果 rank 比矩阵较短的那个维度还大，我没觉得有问题",
    ),
    "math1.linear_algebra.linear_equation_solution_structure": _TopicSemantics(
        "把非齐次方程组的通解误认为仅由齐次方程通解组成",
        "我解 Ax=b 时只写了零空间那一部分，漏了一个特解",
        "判断有解时混用系数矩阵秩与增广矩阵秩",
        "我只看 rank(A)，没比较增广矩阵的秩就宣布有解",
        "讨论唯一解或无穷多解时遗漏未知量个数条件",
        "两个秩相等后我直接说唯一解，完全没看未知数有几个",
    ),
    "math1.linear_algebra.vector_linear_representation": _TopicSemantics(
        "认为向量只要能被向量组表示，其表示系数就必然唯一",
        "找到一组系数后，我就认定不可能还有另一组写法",
        "将线性表示方程的系数矩阵与增广列位置颠倒",
        "我解系数时把目标向量放到了矩阵左边去乘",
        "未检查目标向量是否属于给定向量组张成的空间",
        "没验证目标向量在不在 span 里，就硬解出一组系数",
    ),
    "math1.linear_algebra.linear_independence": _TopicSemantics(
        "把存在非零系数使线性组合为零当成线性无关判据",
        "我找到一组不全为零的系数组合成零，反而说它们无关",
        "对非方阵向量组直接用行列式非零判定线性无关",
        "向量个数和维数对不上，我仍然只算了一个 determinant",
        "忽略向量组含零向量时必然线性相关",
        "向量组里明明有零向量，我却没先排除无关性",
    ),
    "math1.linear_algebra.vector_group_rank": _TopicSemantics(
        "把向量组中向量的总个数直接等同于向量组的秩",
        "有五个向量我就写秩为五，没有看其中是否重复",
        "用某个子式的数值而不是非零子式的最高阶数表示秩",
        "我找到一个值为三的子式，就说向量组秩等于三",
        "删除向量时没有确认被删向量可由其余向量线性表示",
        "我随便去掉一个向量，还声称删前删后秩一定相同",
    ),
    "math1.linear_algebra.vector_space": _TopicSemantics(
        "认为任意非空向量集合都自动构成向量空间",
        "集合里有几个向量，我就直接把它叫线性空间了",
        "检验封闭性时只检查加法而遗漏数乘",
        "我只试了两个向量相加还在集合里，没试任意倍数",
        "没有检查集合包含零向量以及对加法和数乘同时封闭",
        "这个集合连零向量都没有，我还是说它是子空间",
    ),
    "math1.linear_algebra.eigenvalue": _TopicSemantics(
        "把矩阵的迹误认为唯一的特征值而非全部特征值之和",
        "看到主对角线加起来是五，我就说特征值只有五",
        "特征方程把 det(A-λI) 错写成 det(A)-λ",
        "我把特征多项式写成矩阵行列式最后再减 lambda",
        "没有确认对象是方阵就讨论其特征值",
        "行列数不同的矩阵我也直接开始求 eigenvalue",
    ),
    "math1.linear_algebra.eigenvector": _TopicSemantics(
        "允许零向量充当特征向量，忽略非零定义",
        "零向量也满足 Av=lambda v，所以我把它算进去了",
        "求特征向量时把齐次方程写成 (A+λI)x=0",
        "我代入 lambda 后算的是 A 加 lambda I 的零空间",
        "未先确认 λ 是特征值就求对应的非零特征向量",
        "这个 lambda 根本不让行列式为零，我仍硬找对应向量",
    ),
    "math1.linear_algebra.similarity_diagonalization": _TopicSemantics(
        "认为每个方阵都一定能相似对角化",
        "只要是方阵，我就默认总能找到可逆 P 把它变成对角阵",
        "相似变换次序写反，把 P⁻¹AP 错写成 PAP⁻¹且列向量未对应",
        "我用特征向量作列后却把变换写成 P A P 的逆",
        "没有检查存在足够多个线性无关特征向量",
        "我只找到一个特征方向，却说三阶矩阵已经可对角化",
    ),
    "math1.linear_algebra.quadratic_form": _TopicSemantics(
        "把交叉项系数全部放入一个矩阵元而未在对称位置平分",
        "二次型里有 2xy，我就在两个对称位置都填了 2",
        "将二次型矩阵表达式 xᵀAx 的左右向量次序或转置写错",
        "我把列向量直接写成 xAx，没有左侧转置",
        "没有使用对称矩阵表示，导致同一交叉项系数不唯一",
        "矩阵两侧对应位置不对称，我仍把它当标准二次型矩阵",
    ),
    "math1.linear_algebra.positive_definite_quadratic_form": _TopicSemantics(
        "认为对角元全为正就足以推出整个二次型正定",
        "矩阵主对角线都大于零，我就没看别的直接判正定",
        "Sylvester 判据误检查任意子式而非顺序主子式",
        "我挑了几个不连续的子式都为正，就套了正定判据",
        "应用正定判据前遗漏实对称矩阵条件",
        "矩阵不对称，我仍直接检查顺序主子式判正定",
    ),
    "math1.probability.random_event": _TopicSemantics(
        "混淆随机事件与事件发生的概率数值",
        "我把事件 A 本身直接写成了 0.4，好像集合就是概率",
        "事件的并交补运算被当作普通数字加减乘除",
        "我计算 A 并 B 时直接写成 P(A)+P(B)，没管重叠",
        "描述事件时没有先限定同一个随机试验的样本空间",
        "两个来自不同试验的结果，我没统一样本空间就做交集",
    ),
    "math1.probability.probability_properties": _TopicSemantics(
        "认为任意两个事件的并概率都等于各自概率之和",
        "A 和 B 有重叠，我还是直接把两个概率加起来",
        "补事件公式误写成 P(Aᶜ)=P(A)-1",
        "我算反事件时拿原概率减一，符号方向反了",
        "使用概率可加性时遗漏事件互斥条件或交集修正项",
        "没证明两件事互斥，也没减交集，就套了加法",
    ),
    "math1.probability.conditional_probability": _TopicSemantics(
        "把 P(A|B) 与 P(B|A) 当成同一个概率",
        "看到竖线两边换了位置，我觉得数值不会变",
        "条件概率公式把交集概率与条件事件概率相乘而不是相除",
        "我写成 P(A 交 B) 乘 P(B)，没有除以条件那项",
        "没有检查条件事件 B 的概率必须大于零",
        "分母那个事件概率是零，我仍然继续算条件概率",
    ),
    "math1.probability.independence": _TopicSemantics(
        "把互斥事件误认为一定相互独立",
        "两件事不能同时发生，我反而说它们互不影响",
        "独立性判据把 P(AB)=P(A)P(B) 中乘法写成加法",
        "我用 P(A交B)=P(A)+P(B) 来证明独立",
        "多个事件只验证两两独立就宣称相互独立",
        "三件事我只两两检查，没检验三者交集就下结论",
    ),
    "math1.probability.total_probability": _TopicSemantics(
        "使用全概率公式时遗漏各分支的先验权重",
        "我只把每条分支下的条件概率相加，没有乘分支概率",
        "把全概率展开写成 P(A|Bᵢ) 的无权重简单求和",
        "我的式子只有条件概率之和，看不到 P(B_i)",
        "没有检查 Bᵢ 构成互斥且完备的样本空间划分",
        "这些分支既有重叠又没覆盖全部情况，我仍套全概率",
    ),
    "math1.probability.bayes": _TopicSemantics(
        "混淆先验概率、似然与后验概率的角色",
        "我把原因发生的原始概率和看到证据后的概率当成一回事",
        "贝叶斯公式中分子与证据概率分母位置颠倒",
        "我写成 P(A|B) 乘 P(A) 再除 P(B)，先验放错了对象",
        "没有检查证据事件概率为正且分母需按全概率展开",
        "证据概率没算也可能是零，我就直接做了后验更新",
    ),
    "math1.probability.random_variable_distribution": _TopicSemantics(
        "把随机变量的分布与随机变量可能取值列表混为一谈",
        "我只列了 X 能取哪些数，就说已经给出了概率分布",
        "离散概率质量相加未归一到 1，或连续密度积分未归一",
        "各取值概率加起来超过一，我还是当成合法分布",
        "没有检查概率质量非负及总质量等于一",
        "出现负概率后我没排除，所有概率的和也没验算",
    ),
    "math1.probability.distribution_function": _TopicSemantics(
        "把累积分布函数 F(x) 误写成点概率 P(X=x)",
        "我以为 CDF 只表示正好取到 x 的概率",
        "离散情形把 F 的跳跃大小直接当成 F 本身而未累加",
        "每个点的概率我单独写出来了，却没有从左往右累积",
        "遗漏分布函数单调不减、右连续及两端极限条件",
        "画出的曲线会下降而且跳点取左值，我没检查基本性质",
    ),
    "math1.probability.joint_distribution": _TopicSemantics(
        "认为任意两个随机变量的联合分布都等于边缘分布乘积",
        "不管 X 和 Y 有没有关联，我都把联合概率拆成两个相乘",
        "求边缘分布时没有对另一变量求和或积分",
        "我直接把联合表的一格当成 X 的边缘概率，没有按行累加",
        "使用乘积分解前遗漏 X 与 Y 独立条件",
        "题目没说独立，我却把 f(x,y) 写成 f_X(x)f_Y(y)",
    ),
    "math1.probability.expectation": _TopicSemantics(
        "把所有可能取值的普通算术平均当成数学期望",
        "各结果概率不同，我还是把取值直接加起来除以个数",
        "离散期望求和时遗漏概率权重，连续期望遗漏密度",
        "我算 E(X) 只加 x，没有乘 p(x)",
        "使用期望公式时没有检查绝对可积或期望存在性",
        "尾部很重可能发散，我仍直接写出一个有限均值",
    ),
    "math1.probability.variance": _TopicSemantics(
        "混淆方差与标准差，计算后又错误开方或漏开方",
        "我算出离均差平方的均值后，把它叫标准差但没开根号",
        "方差公式误写成 E(X²)-E(X) 而漏掉均值平方",
        "我的式子第二项只有 E(X)，没有整体再平方",
        "没有检查二阶矩存在就使用有限方差公式",
        "E(X²) 可能发散，我仍说方差是有限数",
    ),
    "math1.probability.covariance_correlation": _TopicSemantics(
        "把协方差为零直接解释成两个变量必然独立",
        "算出 cov=0，我就断言 X 和 Y 完全独立",
        "相关系数公式遗漏两个标准差的乘积作为分母",
        "我把 rho 直接写成 covariance，没有除 sigma_X sigma_Y",
        "方差为零时仍计算相关系数而忽略分母不可用",
        "其中一个变量是常数，我还是给出了相关系数数值",
    ),
    "math1.probability.law_large_numbers": _TopicSemantics(
        "把大数定律理解为有限样本均值必然精确等于期望",
        "只抽了几十次，我就说平均数肯定和理论均值一模一样",
        "把依概率收敛误写成每条样本路径从某项起完全相等",
        "我说样本均值到某个 n 后就永远等于 mu，不再有波动",
        "应用定律时遗漏独立同分布或有限期望等版本条件",
        "样本彼此强依赖且均值可能不存在，我仍直接套大数定律",
    ),
    "math1.probability.central_limit_theorem": _TopicSemantics(
        "认为中心极限定理要求原始随机变量本身服从正态分布",
        "看到原数据不是正态，我就说不能用 CLT",
        "标准化样本和时遗漏减去 nμ 或除以 σ√n",
        "我只拿总和减 mu 再除 sigma，n 和根号 n 都没出现",
        "使用经典中心极限定理时遗漏独立同分布及有限方差条件",
        "变量方差都不存在而且互相依赖，我仍套标准正态极限",
    ),
    "math1.probability.parameter_estimation": _TopicSemantics(
        "把估计量这个随机变量与未知参数的真实固定值混为一谈",
        "样本均值这次算出 3，我就说参数本身定义上等于 3",
        "极大似然中直接对似然求和或遗漏取对数后的等价优化",
        "我把各样本的似然项相加来最大化，没有做乘积或对数和",
        "求估计值时遗漏参数空间、边界与可辨识性条件",
        "导数给出区间外的点，我没检查边界就当作最终估计",
    ),
}


def _scope(split: RetrievalSplit, *, cross_scope: bool = False) -> MemoryScope:
    user_suffix = "other_user" if cross_scope else "primary_user"
    return MemoryScope(
        user_id=f"errors_{split.value}_{user_suffix}",
        exam_id="errors_exam",
        subject_id="errors_math1",
        memory_namespace=MemoryNamespace.ERROR_PATTERN,
    )


def _event(
    *,
    split: RetrievalSplit,
    scope: MemoryScope,
    knowledge_point_id: str,
    error_type: ErrorType,
    suffix: str,
    occurred_at: datetime,
    error_detail: str,
) -> LearningEvent:
    event_id = f"errors_{split.value}_event_{knowledge_point_id.replace('.', '_')}_{error_type.value}_{suffix}"
    return LearningEvent(
        event_id=event_id,
        idempotency_key=f"errors_{split.value}_idem_{knowledge_point_id.replace('.', '_')}_{error_type.value}_{suffix}",
        context=LearningContext(
            user_id=scope.user_id,
            exam_id=scope.exam_id,
            subject_id=scope.subject_id,
        ),
        session_id=f"errors_{split.value}_session_{suffix}",
        question_id=f"errors_question_{knowledge_point_id.replace('.', '_')}_{error_type.value}",
        knowledge_point_ids=[knowledge_point_id],
        difficulty=0.72,
        answer_correct=False,
        error_type=error_type,
        error_detail=error_detail,
        occurred_at=occurred_at,
    )


def _record(
    *,
    write_index: int,
    split: RetrievalSplit,
    taxonomy: Taxonomy,
    scope: MemoryScope,
    knowledge_point_id: str,
    error_type: ErrorType,
    summary: str,
    details: list[str],
    suffix: str,
    lifecycle_state: LifecycleState,
    version: int,
    valid_from: datetime,
    valid_to: datetime | None = None,
    superseded_by: str | None = None,
    contested_group_id: str | None = None,
) -> CorpusMemoryRecord:
    memory_id = f"errors_{split.value}_memory_{knowledge_point_id.replace('.', '_')}_{error_type.value}_{suffix}"
    event = _event(
        split=split,
        scope=scope,
        knowledge_point_id=knowledge_point_id,
        error_type=error_type,
        suffix=suffix,
        occurred_at=valid_from,
        error_detail=summary,
    )
    memory = LearningMemory(
        memory_id=memory_id,
        scope=scope,
        slot_key=str(build_error_pattern_slot_key(taxonomy, knowledge_point_id, error_type)),
        value=ErrorPatternValue(
            error_type=error_type,
            summary=summary,
            details=details,
        ),
        confidence=0.91,
        evidence_count=1,
        lifecycle_state=lifecycle_state,
        version=version,
        valid_from=valid_from,
        valid_to=valid_to,
        superseded_by=superseded_by,
        provenance=[event.event_id],
    )
    return CorpusMemoryRecord(
        write_index=write_index,
        memory=memory,
        provenance_events=[event],
        embedding_text=canonical_embedding_text(memory),
        contested_group_id=contested_group_id,
        provenance_relations={event.event_id: "created_by"},
    )


def _error_texts(topic: _TopicSemantics, error_type: ErrorType) -> tuple[str, str]:
    if error_type is ErrorType.CONCEPT_CONFUSION:
        return topic.concept_memory, topic.concept_query
    if error_type is ErrorType.FORMULA_MISUSE:
        return topic.formula_memory, topic.formula_query
    return topic.condition_memory, topic.condition_query


def _build_corpus(
    split: RetrievalSplit,
    taxonomy: Taxonomy,
    knowledge_point_ids: tuple[str, ...],
) -> tuple[
    list[CorpusMemoryRecord],
    dict[tuple[str, ErrorType], str],
    dict[tuple[str, ErrorType], str],
    dict[tuple[str, ErrorType], str],
]:
    records: list[CorpusMemoryRecord] = []
    current_ids: dict[tuple[str, ErrorType], str] = {}
    terminal_ids: dict[tuple[str, ErrorType], str] = {}
    cross_scope_ids: dict[tuple[str, ErrorType], str] = {}
    primary_scope = _scope(split)

    for topic_index, knowledge_point_id in enumerate(knowledge_point_ids):
        topic = _SEMANTICS[knowledge_point_id]
        topic_time = _BASE_TIME + timedelta(days=topic_index)
        for error_index, error_type in enumerate(_ERROR_TYPES):
            summary, query_wording = _error_texts(topic, error_type)
            slot_suffix = knowledge_point_id.replace(".", "_")
            current_suffix = "current"
            current_id = (
                f"errors_{split.value}_memory_{slot_suffix}_{error_type.value}_{current_suffix}"
            )

            if error_type is ErrorType.CONCEPT_CONFUSION:
                terminal_state = LifecycleState.ARCHIVED
                terminal_suffix = "archived"
                terminal_summary = f"早期记录：{summary}"
                superseded_by = current_id
            elif error_type is ErrorType.FORMULA_MISUSE:
                terminal_state = LifecycleState.INVALIDATED
                terminal_suffix = "invalidated"
                terminal_summary = f"经复核无效的旧记录：{summary}"
                superseded_by = None
            else:
                terminal_state = None
                terminal_suffix = ""
                terminal_summary = ""
                superseded_by = None

            if terminal_state is not None:
                terminal = _record(
                    write_index=len(records),
                    split=split,
                    taxonomy=taxonomy,
                    scope=primary_scope,
                    knowledge_point_id=knowledge_point_id,
                    error_type=error_type,
                    summary=terminal_summary,
                    details=["用于验证终态 Memory 永远不进入可检索候选"],
                    suffix=terminal_suffix,
                    lifecycle_state=terminal_state,
                    version=1,
                    valid_from=topic_time + timedelta(minutes=error_index * 10),
                    valid_to=topic_time + timedelta(minutes=error_index * 10 + 5),
                    superseded_by=superseded_by,
                )
                records.append(terminal)
                terminal_ids[(knowledge_point_id, error_type)] = terminal.memory.memory_id

            current_version = 2 if terminal_state is not None else 1
            current = _record(
                write_index=len(records),
                split=split,
                taxonomy=taxonomy,
                scope=primary_scope,
                knowledge_point_id=knowledge_point_id,
                error_type=error_type,
                summary=summary,
                details=[
                    "该结论来自多步作答中的稳定、可复核错误",
                    "该错因与同知识点其他错误类型分别建槽，不做静默合并",
                ],
                suffix=current_suffix,
                lifecycle_state=LifecycleState.ACTIVE,
                version=current_version,
                valid_from=topic_time + timedelta(minutes=error_index * 10 + 5),
            )
            records.append(current)
            current_ids[(knowledge_point_id, error_type)] = current.memory.memory_id

    cross_scope = _scope(split, cross_scope=True)
    for topic_index, knowledge_point_id in enumerate(knowledge_point_ids):
        topic = _SEMANTICS[knowledge_point_id]
        for error_index, error_type in enumerate(_ERROR_TYPES):
            summary, query_wording = _error_texts(topic, error_type)
            cross = _record(
                write_index=len(records),
                split=split,
                taxonomy=taxonomy,
                scope=cross_scope,
                knowledge_point_id=knowledge_point_id,
                error_type=error_type,
                summary=summary,
                details=[f"跨用户近重复：{query_wording}"],
                suffix="cross_scope",
                lifecycle_state=LifecycleState.ACTIVE,
                version=1,
                valid_from=_BASE_TIME + timedelta(days=topic_index, hours=2, minutes=error_index),
            )
            records.append(cross)
            cross_scope_ids[(knowledge_point_id, error_type)] = cross.memory.memory_id

    return records, current_ids, terminal_ids, cross_scope_ids


def _related_topic_ids(
    taxonomy: Taxonomy,
    knowledge_point_id: str,
    knowledge_point_ids: tuple[str, ...],
) -> list[str]:
    node = taxonomy.get(knowledge_point_id)
    if node is None:
        raise ValueError(f"unknown taxonomy node: {knowledge_point_id}")
    reverse_prerequisites = [
        other.id for other in taxonomy.nodes if knowledge_point_id in other.prerequisites
    ]
    same_area = [
        candidate
        for candidate in knowledge_point_ids
        if candidate != knowledge_point_id
        and candidate.rsplit(".", 1)[0] == knowledge_point_id.rsplit(".", 1)[0]
    ]
    other_area = [
        candidate
        for candidate in knowledge_point_ids
        if candidate != knowledge_point_id and candidate not in same_area
    ]
    ordered = list(node.prerequisites) + reverse_prerequisites + same_area + other_area
    return list(dict.fromkeys(candidate for candidate in ordered if candidate in _SEMANTICS))


def _hard_negatives(
    *,
    taxonomy: Taxonomy,
    knowledge_point_id: str,
    error_type: ErrorType,
    knowledge_point_ids: tuple[str, ...],
    current_ids: dict[tuple[str, ErrorType], str],
) -> list[str]:
    candidates: list[str] = [
        current_ids[(knowledge_point_id, other_type)]
        for other_type in _ERROR_TYPES
        if other_type is not error_type
    ]
    neighbors = _related_topic_ids(taxonomy, knowledge_point_id, knowledge_point_ids)
    candidates.extend(current_ids[(neighbor, error_type)] for neighbor in neighbors[:4])
    for neighbor in neighbors:
        for other_type in _ERROR_TYPES:
            if other_type is not error_type:
                candidates.append(current_ids[(neighbor, other_type)])
            if len(dict.fromkeys(candidates)) >= 12:
                return list(dict.fromkeys(candidates))[:12]
    raise ValueError("taxonomy did not provide enough hard-negative candidates")


def _forbidden_ids(
    *,
    knowledge_point_id: str,
    error_type: ErrorType,
    terminal_ids: dict[tuple[str, ErrorType], str],
    cross_scope_ids: dict[tuple[str, ErrorType], str],
) -> list[str]:
    terminal_key = (
        (knowledge_point_id, error_type)
        if (knowledge_point_id, error_type) in terminal_ids
        else (knowledge_point_id, ErrorType.CONCEPT_CONFUSION)
    )
    return [terminal_ids[terminal_key], cross_scope_ids[(knowledge_point_id, error_type)]]


def _answerable_query(
    *,
    split: RetrievalSplit,
    family: str,
    ordinal: int,
    text: str,
    knowledge_point_id: str,
    error_type: ErrorType,
    taxonomy: Taxonomy,
    knowledge_point_ids: tuple[str, ...],
    current_ids: dict[tuple[str, ErrorType], str],
    terminal_ids: dict[tuple[str, ErrorType], str],
    cross_scope_ids: dict[tuple[str, ErrorType], str],
) -> RetrievalQuery:
    relevant_id = current_ids[(knowledge_point_id, error_type)]
    return RetrievalQuery(
        query_id=f"errors_{split.value}_query_{ordinal:03d}",
        query_family=family,
        text=text,
        scope=_scope(split),
        top_k=5,
        relevant_memory_ids=[relevant_id],
        relevance_grades={relevant_id: 3},
        hard_negative_memory_ids=_hard_negatives(
            taxonomy=taxonomy,
            knowledge_point_id=knowledge_point_id,
            error_type=error_type,
            knowledge_point_ids=knowledge_point_ids,
            current_ids=current_ids,
        ),
        must_not_return_memory_ids=_forbidden_ids(
            knowledge_point_id=knowledge_point_id,
            error_type=error_type,
            terminal_ids=terminal_ids,
            cross_scope_ids=cross_scope_ids,
        ),
    )


_OUT_OF_DOMAIN_QUERIES = (
    "我背英语单词时总忘记重音位置，查一下对应错因记录",
    "历史课的朝代顺序记混了，找出以前的错误模式",
    "写 Python 时把列表和元组搞混，看看我是否犯过同类错误",
    "化学方程式配平经常漏掉氧元素，检索旧记录",
    "物理电路中电流方向画反了，找最相关的学习记忆",
    "作文总是漏写标点符号，查查有没有稳定问题",
    "我在地理题里分不清季风和洋流，检索历史弱点",
    "篮球投篮动作不稳定，看看之前记录了什么错误",
    "数据库 SQL 的 JOIN 条件老写错，找相关错因",
    "经济学里名义 GDP 和实际 GDP 总弄反，查询旧记忆",
)


def _no_answer_queries(
    *,
    split: RetrievalSplit,
    taxonomy: Taxonomy,
    start_ordinal: int,
    count: int,
    knowledge_point_ids: tuple[str, ...],
    current_ids: dict[tuple[str, ErrorType], str],
    terminal_ids: dict[tuple[str, ErrorType], str],
    cross_scope_ids: dict[tuple[str, ErrorType], str],
) -> list[RetrievalQuery]:
    queries: list[RetrievalQuery] = []
    for index in range(count):
        knowledge_point_id = knowledge_point_ids[index % len(knowledge_point_ids)]
        if index % 2 == 0:
            family = f"errors_{split.value}_absent_domain"
            text = _OUT_OF_DOMAIN_QUERIES[(index // 2) % len(_OUT_OF_DOMAIN_QUERIES)]
        else:
            family = f"errors_{split.value}_absent_reading_error"
            topic_node = taxonomy.get(knowledge_point_id)
            if topic_node is None:
                raise ValueError(f"unknown taxonomy node: {knowledge_point_id}")
            topic_name = topic_node.name_zh
            text = (
                f"只查 {topic_name} 因为看错题干造成的 reading error；"
                "如果没有这种记录就不要拿概念或公式错误代替"
            )
        all_current = [
            current_ids[(candidate, error_type)]
            for candidate in knowledge_point_ids
            for error_type in _ERROR_TYPES
        ]
        hard_ids = (
            [current_ids[(knowledge_point_id, error_type)] for error_type in _ERROR_TYPES]
            if index % 2
            else []
        )
        hard_ids.extend(memory_id for memory_id in all_current[index:] if memory_id not in hard_ids)
        if len(hard_ids) < 12:
            hard_ids.extend(memory_id for memory_id in all_current if memory_id not in hard_ids)
        queries.append(
            RetrievalQuery(
                query_id=f"errors_{split.value}_query_{start_ordinal + index:03d}",
                query_family=family,
                text=text,
                scope=_scope(split),
                top_k=5,
                relevant_memory_ids=[],
                relevance_grades={},
                hard_negative_memory_ids=list(dict.fromkeys(hard_ids))[:12],
                must_not_return_memory_ids=[
                    terminal_ids[(knowledge_point_id, ErrorType.CONCEPT_CONFUSION)],
                    cross_scope_ids[(knowledge_point_id, ErrorType.CONCEPT_CONFUSION)],
                ],
                expected_no_answer=True,
            )
        )
    return queries


def _build(split: RetrievalSplit) -> RetrievalDataset:
    taxonomy = load_taxonomy("math1_v1")
    knowledge_point_ids = tuple(
        node.id
        for node in taxonomy.nodes
        if node.status.value == "active" and not taxonomy.children_of(node.id)
    )
    if set(knowledge_point_ids) != set(_SEMANTICS):
        missing = set(knowledge_point_ids) - set(_SEMANTICS)
        stale = set(_SEMANTICS) - set(knowledge_point_ids)
        raise ValueError(
            f"semantic coverage drift; missing={sorted(missing)}, stale={sorted(stale)}"
        )

    corpus, current_ids, terminal_ids, cross_scope_ids = _build_corpus(
        split,
        taxonomy,
        knowledge_point_ids,
    )
    queries: list[RetrievalQuery] = []
    if split is RetrievalSplit.DEV:
        query_specs = (
            ("errors_dev_misconception_symptom", ErrorType.CONCEPT_CONFUSION),
            ("errors_dev_formula_trace", ErrorType.FORMULA_MISUSE),
        )
        no_answer_count = 10
    else:
        query_specs = (
            ("errors_test_colloquial_self_report", ErrorType.CONCEPT_CONFUSION),
            ("errors_test_symbolic_derivation", ErrorType.FORMULA_MISUSE),
            ("errors_test_missing_assumption", ErrorType.CONDITION_OMISSION),
        )
        no_answer_count = 20

    for knowledge_point_id in knowledge_point_ids:
        topic = _SEMANTICS[knowledge_point_id]
        for family, error_type in query_specs:
            _, query_wording = _error_texts(topic, error_type)
            if split is RetrievalSplit.DEV:
                text = f"诊断这段独立作答症状并查最匹配的历史错因：“{query_wording}”"
            elif error_type is ErrorType.CONCEPT_CONFUSION:
                text = f"学生口头复盘说：“{query_wording}”。过去哪条误区记录最吻合？"
            elif error_type is ErrorType.FORMULA_MISUSE:
                text = f"检查这段推导：“{query_wording}”。召回最相关的公式使用错误。"
            else:
                text = f"结论看似完整，但学生承认：“{query_wording}”。匹配遗漏前提的记录。"
            queries.append(
                _answerable_query(
                    split=split,
                    family=family,
                    ordinal=len(queries),
                    text=text,
                    knowledge_point_id=knowledge_point_id,
                    error_type=error_type,
                    taxonomy=taxonomy,
                    knowledge_point_ids=knowledge_point_ids,
                    current_ids=current_ids,
                    terminal_ids=terminal_ids,
                    cross_scope_ids=cross_scope_ids,
                )
            )

    queries.extend(
        _no_answer_queries(
            split=split,
            taxonomy=taxonomy,
            start_ordinal=len(queries),
            count=no_answer_count,
            knowledge_point_ids=knowledge_point_ids,
            current_ids=current_ids,
            terminal_ids=terminal_ids,
            cross_scope_ids=cross_scope_ids,
        )
    )
    return RetrievalDataset(
        protocol_version=RETRIEVAL_PROTOCOL_VERSION,
        dataset_version=DATASET_VERSION,
        split=split,
        seed=ERRORS_SEED,
        policy_version="errors_error_pattern_fixture_v1",
        embedding_dimension=RETRIEVAL_EMBEDDING_DIMENSION,
        document_input_type="search_document",
        query_input_type="search_query",
        corpus=corpus,
        queries=queries,
    )


def build_dev() -> RetrievalDataset:
    """Build the deterministic development error-pattern shard."""
    return _build(RetrievalSplit.DEV)


def build_test() -> RetrievalDataset:
    """Build the deterministic held-out-family error-pattern shard."""
    return _build(RetrievalSplit.TEST)


__all__ = ["DATASET_VERSION", "ERRORS_SEED", "build_dev", "build_test"]
