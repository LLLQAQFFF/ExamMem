# ExamMem Memory 与语义检索评测 v2：本地结果

## 1. 评测快照

- 日期：2026-08-25
- 数据协议：`semantic_retrieval_v2`
- 冻结 test SHA-256：`14c9e0730449671a74b3bc7ff66dea490f550e4a8f4b885f762b6b15c044ddb5`
- 指标目录 SHA-256：`76a80f3fb4128753fae4ce44521ee705f4d8117831ec72bed560e8ebb23e0ef1`
- PostgreSQL：pgvector `0.8.2` / PostgreSQL 16，migration head
  `0014_textbook_grounding`
- Embedding：中性 Host Hook → `ollama:qwen3-embedding:0.6b:1024`
- 正式语义 test：396 条 L2 Memory，250 条可回答查询、60 条拒答查询
- HNSW profile：同一四维 `PROFILE` Scope 内 10,000 条 active Memory、100 条查询

原始本地报告：

- `/tmp/exammem-retrieval-v2/storage-report-final.json`
- `/tmp/exammem-retrieval-v2/retrieval-report-final.json`

原始报告包含逐写入 observation、逐查询排名、距离和 `EXPLAIN (ANALYZE, BUFFERS,
FORMAT JSON)`；它们是本机运行产物，不作为可跨机器比较的固定基准。

## 2. 存储结果

正式库 `exammem_retrieval_v2_final_a2` 最终有 10,396 条 `learning_events` 和
10,396 条 `learning_memories`，每条 Memory 都有 1024 维非零有限向量。语义数据使用一个
caller-owned transaction 保证 deferred `superseded_by` 版本链原子提交；规模数据每 240
条一个 transaction，但每条仍依次调用 L1/L2 Repository。

| 指标 | 结果 | 样本 | 结论 |
|---|---:|---:|---|
| valid write acceptance | 1.000 | 396 | 通过 |
| importer/backend replay idempotency | 1.000 | 396 | 通过 |
| invariant rejection with zero growth | 1.000 | 32 | 通过 |
| vector validity | 1.000 | 396 | 通过 |
| provenance round-trip integrity | 1.000 | 396 | 通过 |

32 个非法写覆盖缺失/跨 Scope provenance、错误 version、重复 active、非法终态区间、
错误维数、NaN 和全零向量。每次试验位于独立回滚事务，指标同时要求抛错和 Memory 行数
零增长。

真实 embedding 加串行写入耗时：396 条语义数据约 29.7 秒，10,000 条规模数据约
645.8 秒。`learning_memories` 含索引总大小约 156.7 MB，其中 HNSW 索引约 85.2 MB。

## 3. 语义检索结果

每条查询只生成一次 `search_query` vector；exact reference 禁用 index/bitmap scan，生产
结果调用 `PostgresLearningMemoryRepository.find_similar()`，二者使用完全相同的 Scope、
状态和非空向量过滤条件。

| 指标 | 结果 | 样本 | 门槛结论 |
|---|---:|---:|---|
| Recall@5 | 0.976 | 250 | 通过 |
| Precision@5 | 0.267 | 250 | 通过预注册诊断门槛 |
| Hit@5 | 0.976 | 250 | 通过 |
| MRR | 0.895 | 250 | 通过 |
| nDCG@5 | 0.916 | 250 | 通过 |
| relevant-hard-negative pairwise accuracy | 0.988 | 3,900 对 | 通过 |
| no-answer accuracy | 0.000 | 60 | **失败** |
| hard-negative hit rate | 0.658 | 310 | **失败** |
| archived/invalidated hit rate | 0.000 | 1,550 个返回项 | 通过 |
| cross-Scope leakage | 0.000 | 1,550 个返回项 | 通过 |

250 条可回答查询中有 6 条 Top-5 完全未命中 Gold：3 条是特征向量相关的 mastery
improving 表述，另 3 条分别来自错因的公式推导、口语复盘和缺失前提 family。显式难负例
进入 Top-5 的比例按 shard 分别为 mastery 61%、errors 88.2%、language 46%。

拒答失败不是数据缺失：生产语义检索目前没有相似度阈值或拒答判定，只要 Scope 内有向量
就固定返回 Top-K，因此 60 条 no-answer 全部返回了 5 条。这项失败不能通过删掉拒答样本
隐藏。

310 条语义查询的生产 P95 为约 7.83 ms，exact P95 为约 4.68 ms；这两个数字只描述本机
热缓存环境。

## 4. HNSW 证据

在同一 Scope 的 10,000 候选上，100/100 个生产 `EXPLAIN` 都是：

```text
Limit → top-N Sort → Seq Scan(10,000 candidates)
```

因此：

- 生产 HNSW 计划命中率为 0%；
- 生产结果与 exact 的集合一致率虽然是 1.000，但它来自精确顺序扫描；
- 生产 `ANN Recall@5` 必须标记为**未测量**，不能报告成 1.000；
- 生产 10k Scope P95 约 62.1 ms。

为排除“索引未建好”，另做了明确标注的强制 planner 诊断：移除额外 `memory_id`
tie-breaker，并在局部事务关闭 seq/bitmap/sort。100/100 个计划实际使用
`ix_learning_memories_content_embedding_hnsw`，相对 exact 的 Recall@5 为 0.972，P95 约
2.98 ms。该结果只证明索引可用、近似误差满足 0.95 诊断门槛，不代表当前生产查询已经
使用 HNSW。

生产代码按 `(cosine_distance, memory_id)` 排序，且当前 10k/过滤条件下 planner 的成本
选择均会影响索引采用。若后续优化，应在独立变更中验证两阶段稳定排序或其他 pgvector
兼容查询形状，并重新用本协议测量，不能在本次评测 Runner 中测试特判。

## 5. 结论与限制

当前 Memory 存储、版本链、provenance、向量合法性、终态过滤和四维 Scope 隔离均通过；
语义相关性总体较好，但系统还不具备可靠拒答，Top-5 中难负例偏多，生产查询没有实际使用
HNSW。这三项是当前最重要的改进方向。

限制：数据由三个独立 Agent 基于数学一 taxonomy 构造，虽经契约、反泄漏和主线程语义
审计，但尚未经过教师双盲标注；只评测一个 embedding 模型、单机热缓存和一个学科；检索
命中不等于推荐正确，更不等于真实学习提升。
