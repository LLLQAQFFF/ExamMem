# ExamMem 阶段 08：评测体系、Baseline 与真实结果

> 历史说明：本文保留 Stage08 在 dev 上的原始混合/负面 baseline，不回写优化后数字。
> Stage09 的 dev 修正与一次性冻结 test 结果见
> [Stage09 报告](./stage09-frozen-test.md)。

## 1. 结论

本阶段已经完成可复现的 `Dataset → Rollout → Trace → Judge → Report` 流水线，
在 40 条 dev 多轮学习轨迹上真实比较五个 Memory Backend。最终 run 为
`dev-stage08-final-174c354c-all`，绑定提交
`174c354cda299ed29df762a6d4228e339340a06b`。

ExamMem Lifecycle 不是“全面胜出”：它显著改善最终状态和下一知识点推荐，且比
DeepTutor Native Memory 少用 86.96% 的 LLM 调用；但当前关系分类准确度不足，只有
34/40 case 完成，Lifecycle operation macro-F1 仅 39.02%，false-merge 达 78.46%。
因此本结果证明了 typed lifecycle 的产品价值，也明确证明当前 classifier/policy
尚未达到上线质量门槛。

## 2. 数据集与冻结身份

- 数据集：`exam_mem_controlled_v1`，数学一的线性代数与概率论；
- 12 类场景：语义重复、互补证据、掌握提升/下降、偶然错误、稳定薄弱、显式纠正、
  低置信度异常、计划迁移、多值错误模式、跨 Scope 干扰、长期变化；
- 120 case：dev 40，冻结 test 80；seed `20260806`；
- dev SHA-256：`b546278941c17f5e7238384cb0ed183c94c139fec6f5c8fbec443e51d982b3ab`；
- test SHA-256：`fd01c3a2eb910ad0476e82e54eacc593d44d867d37c812b69f1a99e8a8553011`；
- 公平配置 SHA-256：`52ae07a3832eb73bc12c0590f5991df1c13c1ca4e61ce38beff09f737a398d5e`；
- LLM：`minimax_anthropic / MiniMax-M3`，temperature 0，top-k 5；
- test 在 Stage08 只校验 schema、数量、可加载性和冻结哈希，没有 rollout、没有看逐
  case 结果。按冻结源流程，一次性 test 验证属于 Stage09。

输入从已经校验的结构化 `LearningEvent` 开始。原始对话抽取不在本数据集内，因而
抽取指标是 N/A；不能用 slot F1 代替聊天抽取准确率。

## 3. 五臂定义

| Backend | 真实实现 | 可比较边界 |
|---|---|---|
| none | 丢弃事件和记忆 | 最弱下界 |
| native | DeepTutor 原生 L1 JSONL、L2/L3 Markdown consolidation | 无 ExamMem typed lifecycle/state |
| append_only | PostgreSQL 事件与 append-only fact repository | 能保留事实，不能消歧和收敛 |
| vector | append-only fact + 冻结的本地 1024 维 feature-hash 检索 | 可复现检索基线，不代表生产 embedding |
| lifecycle | PostgreSQL repository、Lifecycle Policy v1、Decision Journal、Change Log、L3 rebuild | 当前产品路径 |

Recommendation 使用生产 `RecommendationPolicyV1`；只有 Lifecycle 提供其所需的
typed StudentModel/L2 证据，这与当前产品装配一致。Native 的自由文本摘要不会被伪装
成 ExamMem typed state。

## 4. 最终得分

| 指标 | none | native | append-only | vector | Lifecycle |
|---|---:|---:|---:|---:|---:|
| 完成率 | 100% | 100% | 100% | 100% | **85.00% (34/40)** |
| Slot F1 | 100% | 100% | 100% | 100% | **100% (197/197)** |
| Lifecycle operation accuracy | N/A | N/A | N/A | N/A | **27.92% (55/197)** |
| Lifecycle operation macro-F1 | N/A | N/A | N/A | N/A | **39.02%** |
| Active-state exact | 10.00% | N/A | 0.00% | 0.00% | **30.00% (36/120)** |
| Stale rate ↓ | N/A | N/A | 87.10% | 87.10% | **52.61%** |
| Duplicate rate ↓ | N/A | N/A | **1.19%** | **1.19%** | 1.90% |
| Cross-scope leakage ↓ | N/A | N/A | 0% | 0% | **0% (0/40 retrieved)** |
| Scope test pass | N/A | N/A | 100% (3/3) | 100% (3/3) | 100% (2/2)¹ |
| Weak recall@5 | 0% | 0% | **92.50%** | **92.50%** | 75.00% |
| Archived hit@5 ↓ | N/A | N/A | 0% | 0% | **0%** |
| 推荐知识点准确率 | 2.50% | 2.50% | 2.50% | 2.50% | **21.67% (26/120)** |
| Over-review rate ↓ | 0% | 0% | 0% | 0% | 19.23% (20/104) |
| LLM calls | 0 | 322 | 0 | 0 | **42** |
| 平均 case 延迟 | 1.64 ms | 18.23 s | 184.11 ms | 327.59 ms | **2.34 s** |
| P95 case 延迟 | 1.87 ms | 31.49 s | 304.44 ms | 535.45 ms | **4.94 s** |

¹ 一个跨 Scope case 在产生可评估 retrieval 前发生 Lifecycle 契约失败；因此 2/2 的
scope pass 不能掩盖总完成率 85%。

Host LLM 接口没有返回 provider token usage，token 和美元成本按协议记为 undefined，
没有静默估算。

## 5. 比 baseline 强多少

以下差值全部来自同一数据集、同一 Gold、同一配置的五臂消融，是可直接比较的数字：

- Active-state exact：Lifecycle 30.00%，比 none 的 10.00% 高 **20.00 个百分点**
  （相对 +200%）；比 append-only/vector 的 0% 高 **30.00 个百分点**。
- 推荐知识点准确率：21.67% 对 2.50%，高 **19.17 个百分点**，相对提升
  **766.67%**（8.67 倍）。失败 case 已保留在共同的 120 分母中。
- Stale rate：52.61% 对 append-only/vector 的 87.10%，下降 **34.50 个百分点**，
  相对减少 **39.60%**。
- 调用代价：42 次 LLM call 对 Native 的 322 次，减少 **86.96%**；平均延迟比
  Native 低 **87.19%**。

同时存在明确退步：

- 完成率比四个 baseline 低 **15.00 个百分点**；
- Weak recall@5 比 append-only/vector 低 **17.50 个百分点**（相对 -18.92%）；
- Duplicate rate 高 **0.71 个百分点**；
- Over-review 高 **19.23 个百分点**；
- Lifecycle 平均延迟仍是 append-only 的 **12.69 倍**。

## 6. 预注册门槛

| 门槛 | 结果 | 判定 |
|---|---:|---|
| Slot F1 ≥ 85% | 100% | 通过 |
| Operation macro-F1 ≥ 80% | 39.02% | **未通过** |
| Stale rate ≤ 5% | 52.61% | **未通过** |
| Duplicate rate ≤ 5% | 1.90% | 通过 |
| Cross-scope leakage = 0 | 0 | 通过 |
| Scope pass = 100% | 100% (2/2) | 指标通过，但有覆盖缺口 |
| Archived hit@5 = 0 | 0 | 通过 |

整体不能判定为达标。Stage09 的首要问题应是 relation decision/convergence，不是
taxonomy：混淆矩阵中 149 个 Gold `NO_OP` 被大量错判为 `ADD` 或 `MERGE`，而 slot
已是 197/197。

## 7. Bad Case 与场景结论

Lifecycle 的 6 个执行失败中，5 个是 error-pattern 关系不满足“仅 duplicate 或
complementary 才能 update”的状态机契约，1 个是 proposed mastery 与当前/争议方向不
一致。完整失败 case：

- `cross_scope_interference:01`；
- `low_confidence_exception:05`；
- `mastery_improvement:09`；
- `multi_value_error_pattern:05`；
- `multi_value_error_pattern:09`；
- `stable_weakness:04`。

场景上，long-range change 的 operation accuracy 最高（61.54%），其次是 mastery
decline（61.11%）；multi-value error pattern 最差（6.67%）。这支持“关系判定与
NO_OP/合并边界是首层错误”的诊断，但 Stage08 不据此修改策略。

## 8. 与 DeepTutor 论文的关系

DeepTutor 论文的 TutorBench 有 270 个任务、90 个画像、30 个知识库；通过模拟学生和
LLM judge 在十个 1–5 分维度上评价对话与出题。论文报告 DeepTutor Overall Quality
3.91、Naive Tutor 3.53，即 +10.76%。来源：
<https://arxiv.org/html/2604.26962#S5>。

该数字只作方法背景，不能与本报告的 Lifecycle accuracy、state exact 或 stale rate
直接相减：数据、任务、指标、模型和 judge 均不同。本项目“比 baseline 强多少”的
主张只采用第 5 节的同 harness 消融。

## 9. 数据库与文件副作用

最终 run 使用专用本地库 `exammem_eval_final`，migration head 为
`0011_assessment_archival`。按 run 前缀统计：

| Backend | events | baseline facts | learning memories | decisions | change log | provenance | model snapshots |
|---|---:|---:|---:|---:|---:|---:|---:|
| append-only | 366 | 243 | 48² | 0 | 0 | 258 | 0 |
| vector | 366 | 243 | 48² | 0 | 0 | 258 | 0 |
| lifecycle | 350 | 0 | 184 | 164 | 416 | 601 | 36 |

² shadow memory 只满足 correction/plan target 外键，不被 baseline 行为读取。

Native 在隔离 run 目录写 259 个文件、476,534 bytes；全部 raw run 产物约 26.8 MB，
受 `.gitignore` 管理。没有读取或修改用户 Native Memory，也没有接触业务数据库。

## 10. 验收记录

- `pytest -q tests/evaluation`：96 passed；
- ExamMem、插件装配与 Core import 边界回归：412 passed，52 skipped；
- `ruff check .`：通过；
- Web production build：通过，共生成 63 个静态页面；
- Web Node tests：65/65 passed；ESLint 为 0 error、58 个既有 warning；
- 中英文 i18n key parity：通过；静态文案审计仍报告 27 个可能的 UI literal，作为
  人工审阅提示，不是 key 缺失；
- migrations `0001`～`0006` 与冻结源逐字节一致，隔离评测库 head 为
  `0011_assessment_archival`；冻结源工作树没有文件改动。

完整 `pytest -q` 不是绿色门禁：部分 DeepTutor chat/agent 测试已经打印 `PASSED`，但
后台任务或线程没有释放，pytest 进程无法结束；排除已定位的 chat 目录后仍能复现同类
停顿。本阶段没有修改这些上游模块，也没有把“无断言失败但进程挂住”记作通过。该项
是仓库级剩余限制，不影响上面可独立退出的 Stage08、ExamMem 和 Web 结果。

## 11. 可复现入口与剩余限制

执行与恢复命令见 [Stage08 Runbook](./stage08-runbook.zh-CN.md)，机器可读摘要见
[Stage08 dev summary](./stage08-dev-summary.json)。
原始不可变 run 在本地 `artifacts/stage08/runs/dev-stage08-final-174c354c-all/`。

本报告是开发集上的负面/混合基线；后续冻结测试结果与当前限制见
[Stage09 报告](./stage09-frozen-test.md)和[延期边界](../docs/exam-mem/deferred-items.md)。

本阶段应形成“可复现的负面/混合 baseline”：结构化状态与推荐有明显收益，但当前
关系分类器造成的污染和失败率不允许宣称已达到生产效果。
