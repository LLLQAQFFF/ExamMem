# ExamMem Memory 与语义检索评测 v2

## 1. 目的与边界

本协议把 ExamMem 的测试拆成互相可归因的阶段：

```text
原始文本/作答
  → L1 提取与 Slot
  → L2 存储与 Lifecycle
  → L3 投影
  → 语义检索
  → 推荐与工程成本
```

`semantic_retrieval_v2` 专门补足旧版受控轨迹中“同 Scope 候选最多只有两条、实际
Top-K 为 3”的检索有效性缺口。它不修改或覆盖已经公开的
`exam_mem_controlled_v1`，也不把合成检索结果解释为真实学习增益。

向量不是 Memory 的权威内容。数据集保存生产 `LearningEvent`、`LearningMemory` 和
canonical embedding 输入；1024 维向量必须在运行时由被测 embedding provider 生成。

## 2. 指标目录

机器可读的指标目录位于 `evaluation/retrieval/metrics.py`。阈值、公式或最小样本数发生
变化时，必须产生新的 metric catalog hash，不能在同一数据 release 下静默移动门槛。

### 2.1 L1、Slot 与 Lifecycle

| 层 | 核心指标 | 目的 |
|---|---|---|
| Extraction | knowledge-point accuracy、error-type macro-F1 | 原始文本到结构化证据是否正确 |
| Slot | precision、recall、F1 | 是否映射到正确的四维 Scope 与 canonical slot |
| Lifecycle | operation accuracy、operation macro-F1 | ADD/MERGE/SUPERSEDE/CONTESTED/NO_OP/INVALIDATE 是否正确 |
| Pollution | false merge、false supersede | 是否错误污染已有 Memory |
| State | active-state exact、stale、duplicate | 当前版本集合是否与 Gold 完全一致 |

检索 v2 的数据从已结构化 L1/L2 开始，因此不能用它声称 Extraction 或 Lifecycle 已达到
门槛；这些指标继续由独立轨迹数据集负责。

### 2.2 存储能力

| 指标 | 计算口径 | 正式门槛 |
|---|---|---:|
| valid write acceptance | 成功有效写 / 有效主写试验 | 100% |
| idempotency | 无增长且内容不变的重放 / 重放试验 | 100% |
| invariant rejection | 无副作用拒绝的非法写 / 非法写试验 | 100% |
| vector validity | 1024 维、有限、非零向量 / 成功向量写 | 100% |
| provenance integrity | round-trip 后 L1 provenance 完全一致 / 成功写 | 100% |

Observation 只记录实际结果，不能在 Schema 中强制“正确结果”。否则失败无法落入报告，
指标会退化成自证的 100%。非法写至少覆盖：跨 Scope provenance、缺失 provenance、错误
version、重复 active slot、非法生命周期区间、错误维数、NaN/Inf 和全零向量。

### 2.3 L3 投影

| 指标 | 门槛 |
|---|---:|
| 相同 L1/L2 snapshot 重建结果确定性 | 100% |
| source watermark 与输入完整性 | 100% |

L3 在 L1/L2 提交后单独重建；L3 失败不得回滚或污染已提交的权威层。

### 2.4 语义排序与拒答

| 指标 | 说明 | 正式门槛 |
|---|---|---:|
| Recall@K | 相关 Memory 的覆盖率 | ≥ 0.90 |
| Precision@K | Top-K 中相关结果比例 | 诊断指标；稀疏 Gold 下门槛 ≤ 理论上限 |
| Hit@K | 至少命中一个相关 Memory 的 query 比例 | ≥ 0.95 |
| MRR | 首个相关结果倒数排名均值 | ≥ 0.85 |
| nDCG@K | 使用 1..3 relevance grade 的排序质量 | ≥ 0.85 |
| relevant-hard-negative pairwise accuracy | 相关项距离小于相邻难负例的 pair 比例 | ≥ 0.90 |
| no-answer accuracy | 应拒答 query 返回空结果的比例 | ≥ 0.95 |
| hard-negative hit rate | Top-K 出现显式难负例的 query 比例 | 诊断指标 |

当前生产检索没有相似度阈值，预计可能无法通过 no-answer 门槛。该失败必须如实报告，不能
通过删除 no-answer query、缩小候选集或测试特判隐藏。

### 2.5 安全、ANN 与工程指标

| 指标 | 正式门槛/条件 |
|---|---|
| archived/invalidated hit rate | 0 |
| cross-scope leakage | 0；分别改变 user/exam/subject/namespace |
| HNSW ANN Recall@K | ≥ 0.95，参照同 snapshot exact cosine Top-K |
| HNSW 是否实际使用 | 必须由 `EXPLAIN (ANALYZE, FORMAT JSON)` 证明 |
| production/exact P50/P95/P99 | 同硬件、同缓存条件分别报告 |
| 写入与查询吞吐、索引大小 | 记录环境和 corpus 规模后报告，不跨环境硬比较 |

若 PostgreSQL planner 未选择 HNSW，则 ANN Recall 标记为未测量，不能把普通顺序扫描的
结果冒充 HNSW 结果。

### 2.6 推荐与成本

推荐知识点准确率、难度匹配、over-review、LLM 调用、token、端到端延迟、Memory 行增长
和字节增长继续保留，但必须与检索相关性分开报告。检索命中不等于推荐正确，更不等于
用户已经掌握或取得学习提升。

## 3. 数据 Schema

生产契约定义在 `evaluation/retrieval/contracts.py`。

### 3.1 CorpusMemoryRecord

```json
{
  "write_index": 0,
  "memory": "完整的 exam_mem.contracts.LearningMemory",
  "provenance_events": ["完整的 exam_mem.contracts.LearningEvent"],
  "embedding_text": "canonical slot_key + value JSON",
  "contested_group_id": null,
  "provenance_relations": {"event_id": "created_by"}
}
```

约束：

- `memory.provenance` 与事件 ID 顺序完全相同；
- L1 三维 context 与 L2 Scope 完全一致；
- `evidence_count` 等于 provenance 数量；
- `embedding_text` 必须等于生产 lifecycle 的 canonical 序列化；
- version 必须从 1 连续增长并按 `write_index` 顺序出现；
- 同 Scope/slot 最多一个 active；
- archived 必须有 `valid_to` 和同 Scope/slot 的后继版本；
- contested 必须携带 group ID；
- provenance relation 只允许生产 Repository 支持的四种枚举。

### 3.2 RetrievalQuery

```json
{
  "query_id": "...",
  "query_family": "...",
  "text": "我总把矩阵的行数当成秩",
  "scope": {"user_id": "...", "exam_id": "...", "subject_id": "...", "memory_namespace": "error_pattern"},
  "top_k": 5,
  "relevant_memory_ids": ["..."],
  "relevance_grades": {"memory_id": 3},
  "hard_negative_memory_ids": ["同 Scope、可检索的相邻概念"],
  "must_not_return_memory_ids": ["归档、失效或跨 Scope 记录"],
  "expected_no_answer": false
}
```

三种 ID 不能混用：

- `relevant_memory_ids`：直接相关 Gold；
- `hard_negative_memory_ids`：同 Scope 且 active/contested，用于真实排序竞争；
- `must_not_return_memory_ids`：terminal 或跨 Scope，用于安全约束。

每个 query 的可检索候选必须不少于 `max(50, 10 × top_k)`，hard negative 不少于 10。
每个 split 必须同时包含 answerable、no-answer、terminal 禁返和跨 Scope 禁返样本。

### 3.3 Dataset 与 Manifest

每个 dev/test 数据文件固定：协议版本、数据版本、seed、policy version、embedding 维数、
document/query input type、顺序 corpus 和 query 集合。Manifest 固定：

- 每个文件的 canonical SHA-256；
- corpus/query 数量；
- query family 列表；
- metric catalog SHA-256；
- test aggregate hash。

dev/test 必须按 `query_family` 分割，同一模板的同义改写不能随机落入两个 split。

## 4. 数据生产与独立性

Schema 和指标验收后，三个 Agent 才能并行生成数据分片：

1. 掌握度、薄弱点、进步/退步；覆盖全部 active leaf 知识点；
2. 错误类型、公式误用、相邻概念 hard negative；覆盖全部 active leaf 知识点；
3. 口语/模糊/中英混合/no-answer/状态与 Scope 干扰；覆盖全部 active leaf 知识点。

每个 Agent 只能读取共同 Schema、taxonomy 和自己的输出目录，不读取其他 Agent 的数据。
主线程负责合并、重排 `write_index`、去重、覆盖统计和 hash，不以“补齐分数”为理由修改
Gold。程序生成的规模噪声与人工/独立 Agent 生成的语义 Gold 必须在 manifest 中区分。

正式 test 至少满足：

- answerable query ≥ 200；
- no-answer query ≥ 50；
- 每个 active leaf 知识点均有多种 query family；
- 每 query 同 Scope 候选 ≥ `max(50, 10K)`；
- 每 query hard negative ≥ 10；
- 全库提供同一四维 Scope 内至少 10,000 条合法唯一 slot 的 HNSW profile，并标明其中
  哪些只是规模干扰项；把 10,000 条记录分散到许多小 Scope 不算大候选池验证。

## 5. 串行执行协议

### 5.1 存储阶段

在 migration head 的随机隔离 PostgreSQL Schema 中，按 `write_index` 串行：

1. 用 `PostgresLearningEventRepository.append()` 写入/重放 provenance L1；
2. 用配置的 Host embedding client 对 `embedding_text` 执行 `search_document`；
3. 用 `PostgresLearningMemoryRepository.insert_version()` 写 L2；
4. 立即 round-trip 校验结构、provenance 和 vector；
5. 对同一条记录执行一次幂等重放；
6. 单独执行预注册非法写，并确认失败无行数增长。

所有调用串行，但同一版本链可放在一个 caller-owned transaction 中，以满足 deferred
`superseded_by` 外键；报告必须明确事务边界。存储阶段未通过前不得开始检索评分。

### 5.2 检索阶段

对每个 query 只生成一次 `search_query` vector，并在同一 committed snapshot 上依次执行：

1. exact cosine reference；
2. 生产 `find_similar()`；
3. `EXPLAIN ANALYZE` 证明实际计划；
4. 记录完整排名、距离、延迟、terminal/Scope 泄漏；
5. 使用 query-level Gold 计算指标。

exact 与 production 必须使用完全相同的四维 Scope、ACTIVE/CONTESTED 状态和非空向量
过滤条件。不得从 exact 结果回填 production，也不得在评测代码中按 Gold 重排。

## 6. 报告解释规则

- 不满足 `minimum_sample_count` 的指标标记为未充分测量；
- 没有 `EXPLAIN` 证据时不得声称 HNSW 性能或 ANN Recall；
- configured embedding 与确定性 hash embedding 必须分开报告；
- test 发布后不能继续作为未见 holdout 调参；
- 失败、超时、返回不足 K、provider 信息和模型版本必须进入原始 observation；
- 任何门槛失败都保留在最终报告，不能通过删除 bad case 获得“全通过”。
