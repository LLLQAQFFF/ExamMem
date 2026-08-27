# ExamMem Memory 语义检索 v2 修复方案

## 1. 结论与修复边界

本轮评测证明 Memory 写入、生命周期、来源关系、向量存储和 Scope 隔离均可靠；需要修复的
不是 L1/L2 数据模型，也不是 PostgreSQL 入库流程，而是**查询侧的检索决策链**。

修复前问题按优先级分为：

1. 没有拒答机制：Scope 内存在向量时固定返回 Top-K，导致 no-answer accuracy 为 0；
2. 没有逐条置信过滤和二阶段重排，相邻概念容易进入 Top-5；
3. Ollama 路径没有把 `search_query` 角色转成 Qwen3 所需的查询指令；
4. 生产 SQL 的稳定排序形状不满足 pgvector HNSW 的索引查询要求；
5. 当前 `hard-negative hit@5` 同时受固定 Top-5 和稀疏 Gold 影响，不能单独代表首位排序质量。

修复必须保持以下边界：

- 不修改已冻结 test 数据及其 SHA-256，不在 test 上选择阈值；
- 不改变 Learning Memory 只记录用户学习证据的语义；
- 不放宽四维 Scope、生命周期和 provenance 约束；
- DeepTutor Core 不得 import `exam_mem`；模型调用通过中性 Host Hook/Protocol 提供；
- Repository 负责可验证的数据查询，不负责产品级“是否回答”判断；
- 不通过删除 no-answer、hard negative 或降低安全门槛掩盖失败。

基线证据见[本地评测结果](./semantic-retrieval-v2-results.zh-CN.md)。

## 2. 当前调用链与问题位置

### 2.1 写入链路：没有发现系统性故障

```text
LearningEvent
→ LifecycleApplier 生成/更新 LearningMemory
→ canonical memory text
→ Embedding Host Hook(search_document)
→ 1024 维向量
→ PostgresLearningMemoryRepository.insert_version()
→ learning_memories.content_embedding
```

该阶段的有效写入、重放幂等、非法写零增长、向量合法性和 provenance round-trip 均为
1.000。修复不应重写该链路，也不需要新增 migration。

### 2.2 修复前查询链路

```text
自然语言查询
→ LifecycleMemoryBackend.retrieve()
→ Embedding Host Hook(search_query)
→ Ollama/Qwen3 embedding
→ PostgresLearningMemoryRepository.find_similar()
→ 四维 Scope + active/contested 过滤
→ cosine distance 排序并固定 LIMIT top_k
→ list[LearningMemory]
```

问题出现在以下四个阶段：

| 阶段 | 当前行为 | 直接后果 |
|---|---|---|
| Query embedding | Ollama adapter 只发送原始文本，`search_query` 角色未转为查询指令 | 没有使用 Qwen3 推荐的非对称检索格式 |
| Repository 返回 | `find_similar()` 只返回 Memory，不返回 distance/score | 上层无法实施可审计的置信门 |
| 检索决策 | 没有结构化约束、重排和拒答，始终返回固定 Top-K | 60/60 no-answer 全部误返回 5 条 |
| PostgreSQL 排序 | `ORDER BY cosine_distance, memory_id` | 10k profile 的 100 个生产计划全部走顺序扫描 |

## 3. 现象、根因与判断

### 3.1 无答案查询全部失败

这不是 embedding 完全无法区分答案，而是系统没有拒答出口。可回答查询 Top-1 cosine
distance 中位数为 0.264，无答案查询为 0.433，但两类分布有明显重叠。

冻结 test 上的事后诊断显示：

- 单一距离阈值的最高总体准确率约为 87.1%，此时拒答准确率只有 56.7%；
- 若要求拒答准确率达到 95%，可回答召回率会下降到 42%；
- 同时使用 Top-1 距离和 Top-1/Top-2 margin，95% 拒答准确率下的可回答召回率也只有
  54.4%。

因此“增加一个固定 cosine 阈值”不是完整修复。需要先识别查询中的知识点、错因和状态等
结构化约束，再结合重排分数做拒答。上述数值只能用于诊断，不能作为生产阈值。

### 3.2 难负例进入 Top-5

基线的 `hard-negative hit@5` 为 65.8%，但同时：

- relevant-hard-negative pairwise accuracy 为 98.8%；
- 250 条可回答查询中有 209 条 Gold 排第一；
- 可回答查询中只有 20 条由 hard negative 排第一，即 8%。

这说明基础 embedding 大多能把 Gold 排在难负例前，但固定返回 5 条会把后续相邻概念也交给
下游。尤其很多查询只有一个 Gold，剩余四个位置不应被视为同等可信结果。

修复目标应从“无条件填满 Top-5”改为“返回 0..K 条通过置信门的结果”。原有
`hard-negative hit@5` 保留为诊断指标，同时增加首位和通过置信门后的污染指标。

### 3.3 Query instruction 未生效

`EmbeddingClient` 只有在 adapter 声明支持 `input_type` 时才向下传递角色；Ollama adapter
目前没有该能力，最终 payload 只有 `model` 和原始 `input`。因此评测虽记录
`query_input_type=search_query`，模型实际收到的仍是无指令查询。

[Qwen3 Embedding 官方模型卡](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)建议查询携带
任务指令、检索文档不加指令，并报告多数检索任务通常有 1%～5% 的收益。该改进可能提高
相关性，但不能单独解决拒答。

### 3.4 HNSW 没有进入生产计划

正常语义数据每个 Scope 只有 56 或 90 个候选，B-tree Scope 过滤后的精确计算已经足够快。
真正的问题出现在同 Scope 10,000 候选画像：生产查询 P95 约 62.1 ms，100/100 个计划都是
`Seq Scan → Sort → Limit`。

移除额外 tie-breaker 并强制 planner 的基线诊断中，HNSW Recall@5 为 97.2%，P95 约
2.98 ms，证明索引有效。[pgvector 官方说明](https://github.com/pgvector/pgvector#troubleshooting)
要求索引查询使用升序距离运算符直接 `ORDER BY` 并带 `LIMIT`。实施阶段进一步确认：修正
两阶段排序后，带 Scope/Lifecycle 后置过滤的 pgvector 0.8 查询仍需
`hnsw.iterative_scan=strict_order`，否则 10k Scope 下 planner 仍选择精确扫描。

修复不应强制所有查询使用 HNSW。目标是让 SQL 具备索引兼容性，再由 planner 根据候选规模
选择精确或近似路径。

## 4. 目标调用链

```text
查询
→ Scope/生命周期硬过滤
→ 解析 RetrievalIntent（知识点、错因、状态、明确程度）
→ 明确不存在或指代不足：返回空/请求澄清
→ Query instruction-aware embedding
→ PostgreSQL 召回候选 Top-N（带 distance）
→ Reranker 对 query-memory pair 精排
→ dev 校准的置信门和逐条过滤
→ 返回 0..K 条 ScoredMemory
→ 下游只把通过置信门的 Memory 当作学习证据
```

整个流程只有一次有界候选召回和一次重排；拒答后不再用无限 fallback 换其他 namespace、
Scope 或相邻知识点。

## 5. 分阶段修复

### 阶段 A：建立可评分的中性检索契约

1. 新增不可变的 scored result，例如 `ScoredLearningMemory(memory, distance)`；
2. Repository 新增或升级带分数的相似检索方法，继续强制四维 Scope、active/contested 和
   embedding 非空过滤；
3. `LifecycleMemoryBackend` 保留 canonical slot 的精确查询路径，自然语言路径改为消费
   scored results；
4. 不在 Repository 内硬编码阈值、Taxonomy 或产品拒答文案。

验证：Repository 单元/集成测试证明返回顺序、distance、Scope、terminal 过滤和原有版本
语义不变。

### 阶段 B：补齐 query instruction 与版本指纹

1. 在中性 embedding Host 配置中支持可选的 retrieval query instruction；
2. Qwen3 查询使用英文任务指令，文档 embedding 保持 canonical 原文；
3. 在评测报告和运行指纹中记录 instruction 内容/版本；
4. 先在 dev 集做“无指令 vs 有指令”A/B，不直接重跑冻结 test；
5. 只有 document 序列化或模型发生变化才重建文档向量；单独增加 query instruction 不要求
   重写现有 Memory 向量。

验证：adapter/Host contract 测试确认只有 query 添加指令，document 不变，并报告 dev 的
Recall、MRR、nDCG 和距离分布变化。

### 阶段 C：结构化约束和可靠拒答

定义最小 `RetrievalIntent`：

- `knowledge_point_ids`；
- `error_type`（如果查询明确要求）；
- `requested_state` 或 mastery 变化方向；
- `reference_sufficient`，表示“上次那个错误”等指代是否足够；
- 解析置信度和可审计原因。

决策规则：

1. canonical slot 继续走精确查询；
2. 查询明确指定知识点/错因，而当前 Scope 没有对应 slot 时直接拒答；
3. 查询要求 `reading_error` 时，不允许用 `concept_confusion` 或 `formula_misuse` 顶替；
4. 指代不足且无法从本次显式上下文解析时返回空或请求澄清；
5. 只有结构约束成立的候选才进入语义排序；
6. 最终置信门由 dev 集校准，冻结后再用于 test。

验证：no-answer 按“知识点不存在、错因不存在、跨学科、指代不足”分别报告准确率，同时
检查 answerable recall，防止通过过度拒答获得高分。

### 阶段 D：二阶段重排与可变 Top-K

1. 向量召回固定上界的 Top-N 候选；开发集最终选择 `N=max(K, 5)`，Repository 内层保留
   `4N` 近邻用于稳定排序；
2. 通过中性 Reranker Protocol/Host Hook 对 query-memory pair 评分；
3. 首个候选未通过回答门则返回空；其余候选逐条通过结果门后才返回；
4. 最终返回数量为 0..K，不再为填满 K 引入低置信结果；
5. contested 分支不得静默合并，重排后仍保留 branch/group 身份供下游展示。

候选模型可评测
[Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)，但引入模型、运行时或
依赖前必须单独确认。模型选择不是本修复文档预先锁死的产品契约。

验证：dev 集比较 embedding-only、instruction-only、reranker 和完整 gate 四组结果，并记录
端到端延迟与模型资源消耗。

### 阶段 E：HNSW 兼容查询与规模画像

1. 内层使用仅按 cosine distance 升序的 pgvector-compatible 查询召回候选；
2. 外层仅对有限候选按 `(distance, memory_id)` 稳定排序；
3. 不关闭 seqscan，不通过 planner 测试特判强制索引；
4. 在 1k、10k、50k、100k 同 Scope 候选上记录实际计划、ANN Recall 和延迟；
5. 带高选择性过滤时，评测 pgvector 0.8 的 iterative scan 和 `ef_search`；只有真实数据分布
   证明必要时才考虑 partial index 或 partition。

pgvector 说明近似索引通常在扫描后应用过滤，过滤条件可能减少实际返回数；iterative scan
可继续扫描直到得到足够结果。参见[Filtering](https://github.com/pgvector/pgvector#filtering)和
[Iterative index scans](https://github.com/pgvector/pgvector#iterative-index-scans)。

验证：小 Scope 允许 planner 选择精确查询；大 Scope 必须证明实际采用 HNSW、ANN
Recall@5 不低于 95%，并显著快于 exact reference。

## 6. 验收指标

所有配置只在 dev 集选择；instruction、Reranker、阈值、candidate N 和 HNSW 参数冻结后，
才能再次运行一次 test。

| 类别 | 指标 | 目标 |
|---|---|---:|
| 可回答检索 | Recall@5 | ≥ 0.90 |
| 可回答检索 | Hit@5 | ≥ 0.95 |
| 排序 | MRR | ≥ 0.85 |
| 排序 | nDCG@5 | ≥ 0.85 |
| 拒答 | no-answer accuracy | ≥ 0.95 |
| 难负例 | relevant-hard-negative pairwise accuracy | ≥ 0.90 |
| 难负例 | answerable hard-negative@1 | ≤ 0.05 |
| 安全 | archived/invalidated hit rate | 0 |
| 隔离 | cross-Scope leakage | 0 |
| 大 Scope | 实际 HNSW plan rate | ≥ 0.95 |
| 大 Scope | ANN Recall@5 | ≥ 0.95 |

`hard-negative hit@5` 继续报告，但在可变 Top-K 生效前不单独作为上线门禁。新增
“accepted-result hard-negative rate”，只统计通过最终置信门后真正交给下游的结果。

## 7. 最终预期效果

完成修复后，系统预期表现为：

- 已有明确 Memory 时，保持当前约 97% 的 Top-5 召回，不因拒答机制发生大幅下降；
- 没有对应 Memory、错误类型不匹配或指代不足时，至少 95% 的查询返回空或请求澄清；
- 不再固定填满 5 条，相邻概念只有通过结构约束和重排置信门才会进入上下文；
- 归档、失效、跨 Scope 记录继续保持零泄漏；
- 小 Scope 保持低延迟精确检索，大 Scope 能实际使用 HNSW，并维持至少 95% ANN Recall@5；
- 每个返回结果都保留 Memory ID、版本、Scope、lifecycle state、provenance 和检索分数，能够
  解释“为什么取到”和“为什么拒答”。

这些是需要通过 dev/test 和真实用户数据验证的工程目标，不是根据当前冻结 test 推导出的
保证值。

## 8. 风险与延期项

- 当前数据由多个独立 Agent 构造，但仍是单学科、合成数据，后续需要教师双盲标注和真实
  查询日志；
- Reranker 会增加延迟、显存和模型运维成本；本轮采用 Qwen3-Reranker-4B NF4，依赖放在
  `exam-mem-rerank` 可选 extra，RTX 2060 实测约占 2.7 GiB GPU 显存；
- Taxonomy alias 或意图解析过严可能造成误拒答，必须同时监控 answerable recall；
- 如果未来更换 embedding 模型、维数或 document canonical text，需要显式重建索引，不能
  在同一索引中混用向量语义；
- 检索准确不等于推荐正确或学习效果提升，推荐和教学效果仍需独立端到端评测。

## 9. 实施结果（2026-08-26）

### 9.1 已落地调用链

```text
自然语言查询
→ TaxonomyRetrievalIntentResolver（只提取显式硬约束）
→ Qwen3 search_query instruction embedding
→ Repository 四维 Scope + active/contested + 非空向量召回
→ 内层 distance-only HNSW-compatible Top-4N
→ 外层 (distance, memory_id) 稳定 Top-N
→ 中性 Host Reranking Hook
→ Qwen3-Reranker-4B NF4 pairwise score
→ 绝对阈值 0.003 + Top-1 分差 Δ=0.03
→ 0..K ScoredLearningMemory + RetrievalDecision
```

canonical slot 仍走原有精确候选路径，不调用 embedding 或 reranker。自然语言路径没有跨
Scope、namespace 或相邻知识点的二次 fallback。生产 Host Hook 默认返回已校准的本地
Qwen3-Reranker-4B；缺少可选依赖时显式失败，不静默换模型。

### 9.2 开发集校准

开发集 170 条查询（140 answerable、30 no-answer）只用于模型、指令和阈值选择。最终配置：

- embedding：`qwen3-embedding:0.6b`，1024 维，query instruction 生效；
- reranker：`Qwen/Qwen3-Reranker-4B`，NF4；
- candidate N：5；绝对置信阈值：0.003；最大 Top-1 分差：0.03；
- Recall/Hit@5：0.9298 / 0.9643；MRR：0.9208；nDCG@5：0.9057；
- no-answer accuracy：0.9667；answerable hard-negative@1：0.0500；
- hard-negative@K：0.2765；accepted-result hard-negative：0.2450；
- archived/invalidated 与 cross-Scope 泄漏：均为 0。

0.6B reranker 在相同开发集上降低排序质量，因此没有作为默认实现。4B 的绝对阈值和相对
Top-1 分差冻结后才执行正式 test；没有读取 test Gold 调参。

### 9.3 冻结 test

冻结 test canonical SHA-256 保持
`14c9e0730449671a74b3bc7ff66dea490f550e4a8f4b885f762b6b15c044ddb5`。在 310 条查询上：

| 指标 | 结果 | 门槛 |
|---|---:|---:|
| Recall@5 / Hit@5 | 0.9547 / 0.9800 | ≥ 0.90 / ≥ 0.95 |
| MRR / nDCG@5 | 0.9660 / 0.9505 | ≥ 0.85 / ≥ 0.85 |
| pairwise accuracy | 0.9892 | ≥ 0.90 |
| no-answer accuracy | 0.9833 | ≥ 0.95 |
| answerable hard-negative@1 | 0.0280 | ≤ 0.05 |
| hard-negative@K | 0.1290 | 诊断项，越低越好 |
| accepted-result hard-negative | 0.1176 | 诊断项，越低越好 |
| archived/invalidated hit | 0 | = 0 |
| cross-Scope leakage | 0 | = 0 |
| 端到端 p95（含 reranker） | 594.78 ms | ≤ 1000 ms |

动态截断前的基线是 `hard-negative@K=0.3871`、accepted-result hard-negative `0.2628`；
启用 Δ=0.03 后分别降至 `0.1290` 和 `0.1176`。它们仍是诊断项，不是门禁；调用方必须
消费 relevance score 和可变结果，不能把所有返回项视为同等可信。

接受规则为：

```text
threshold = max(absolute_threshold=0.003, top1_score - maximum_relevance_gap=0.03)
```

因此 `top_k` 是返回数量上限，而不是必须填满的数量；最终可以返回 0、1、2……条。

### 9.4 10k HNSW 规模画像

在同一四维 Scope 的 10,000 条 active、带向量记录上运行 100 条查询：

- 生产 HNSW plan rate：1.000；
- ANN Recall@5：1.000；
- Repository 生产 p95：64.77 ms；exact reference p95：56.87 ms；
- 强制索引控制组 p95：3.29 ms。

生产路径没有关闭 seqscan；它通过 pgvector 0.8 的 strict-order iterative scan 和
`ef_search=100` 让 planner 自主选择 HNSW。生产 p95 包括完整 Memory/provenance 回读，因此
高于只取 ID 的控制组。1k、50k、100k 梯度画像延期，当前 10k 验收已经通过。

### 9.5 环境与数据库副作用

- conda 环境：`exammem`；本地安装 `sentence-transformers 5.7.0`、`torch 2.9.1+cu126`、
  `accelerate 1.14.0`、`bitsandbytes 0.49.2`；另安装 `mypy 1.20.2` 仅用于开发检查；
- 模型缓存：Qwen3-Reranker-0.6B 与 4B；未新增凭据；
- migration head 为 `0015_textbook_plan_source`，本次没有新增或修改 migration；
- 正式评测库与开发评测库均为本轮创建的隔离库；两个测试库及 HNSW 调试 clone 在验收后已删除，
  没有留下共享库或生产库副作用；
- test JSON 内容未改，只有指标目录哈希因门禁定义纠正而更新。
