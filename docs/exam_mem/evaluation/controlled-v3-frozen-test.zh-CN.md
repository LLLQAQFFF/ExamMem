# ExamMem 跨学科 Memory 冻结评测（Controlled v3）

## 结论

`exam_mem_controlled_v3` 使用计算机基础的数据结构与算法内容，在推荐策略冻结后完成了
一次性 80-case、五 Backend 测试。Lifecycle 推荐知识点准确率为 **80.83%（194/240）**，
高于 none、native、append-only 和 vector 的 **55.00%（132/240）**；操作准确率为
**94.47%（376/398）**，当前状态完全一致率为 **89.17%（214/240）**，跨 Scope 泄漏为
**0**。

该结果支持“同一套 typed lifecycle 和推荐策略可以迁移到另一学科”的有限结论。它不评估
原始聊天抽取、出题、判题、真实学习增益，也不是独立设计生命周期形状的第二套 benchmark。

## 数据与冻结身份

| 项目 | 内容 |
| --- | --- |
| 数据集 | `exam_mem_controlled_v3`，计算机基础数据结构与算法 |
| Taxonomy | `cs_v1`，12 个叶子知识点 |
| 规模 | dev 40、frozen test 80；12 类生命周期场景 |
| 输入 | 已校验的结构化 `LearningEvent` |
| Backend | none、native、append-only、vector、lifecycle |
| 代码提交 | `474a27324639be0f74bd976969c9830981ea20cb` |
| test SHA-256 | `9305f29c2110bb9f30438a765c71a6705bf78942f1b957b29cc346eae3352927` |
| 模型 | `minimax_anthropic / MiniMax-M3`，temperature 0 |
| Embedding | `ollama:qwen3-embedding:0.6b:1024` |
| Run | `cs-v3-frozen-test-20260828` |

v2 在运行后审计中发现轨迹正文和契约 ID 残留数学语义，因此只保留为失败审计记录，其
82.08% 不能作为跨学科证据。v3 重写题目、答案、错误证据、Memory 值、Taxonomy/slot、
查询和 Scope，并通过数学词汇残留门禁；正式 test 没有参与后续调参。

## 正式结果

| Lifecycle 指标 | v3 frozen test |
| --- | ---: |
| 完成率 | 97.50%（78/80） |
| Operation accuracy | 94.47%（376/398） |
| Operation macro-F1 | 82.24% |
| Active-state exact | 89.17%（214/240） |
| Stale rate | 3.75%（10/267） |
| Duplicate rate | 2.62%（7/267） |
| False merge / supersede | 6.06% / 0% |
| Cross-scope leakage | 0（0/76） |
| Scope test pass | 100%（6/6） |
| Weak recall@5 | 80.00%（64/80） |
| Archived hit@5 | 0 |
| 推荐知识点准确率 | 80.83%（194/240） |
| 动作类型诊断准确率 | 92.74%（217/234） |
| Over-review rate | 2.99%（7/234） |
| LLM calls | 37 |
| 平均 / P95 case 延迟 | 3.97 s / 7.87 s |

四个 baseline 的推荐知识点准确率均为 55.00%。该 55% 主要来自正确返回 `no_action`，
不能解释成 baseline 已能根据长期状态选择复习知识点。append-only/vector 的 weak
recall@5 为 91.25%，但 stale rate 为 89.28%、active-state exact 为 0，说明“容易召回
历史记录”和“维护正确的当前状态”是不同能力。

## 失败与限制

- 两个 Lifecycle case 失败：一个模型输出的 `error_type` 与候选 slot 不一致，另一个输出
  的知识点与候选 slot 不一致；严格契约在 apply 阶段拒绝写入，没有放宽校验或增加无限
  fallback。
- v3 沿用 v1 的 12 类生命周期形状，以控制变量比较学科变化；它不是全新设计的场景集。
- 少量 opaque `event_id`、`memory_id` 保留模板英文后缀，例如 `eigen`。关系分类 prompt
  不包含这些 ID，只包含 CS slot、Memory 值和候选序号，因此不构成模型的数学语义输入；
  后续新数据版本仍应同时重写这些命名。
- 当前 Native 评测适配器写 L1 JSONL，而新版 Native consolidator 从 snapshot entity 读取，
  本次 Native 没有产生 L2/L3 consolidation 或 LLM call；因此 Native 结果不能当作完整的
  Native Memory 质量结论。
- 测试从结构化事件开始，原始聊天抽取、题目生成、评分质量、难度校准和学习增益均未评估。

机器可读摘要见
[`results/controlled-v3/summary.json`](../../../results/controlled-v3/summary.json)。原始 trace
保留在本机 `/tmp/exammem-controlled-v3/frozen/cs-v3-frozen-test-20260828/`，不进入仓库。
