# ExamMem 阶段 09：Lifecycle 收敛与一次性冻结测试

## 1. 结论

Stage09 在不查看冻结 test 内容和中间指标的前提下，只使用 40 条 dev 轨迹修正了
Learning Memory 的证据累积与关系约束，然后通过一次性 release 运行 80 条冻结 test、
五个 Backend。最终代码为 `258e4456018fdd00abf31ce090457a2c4f8d071a`，正式 run 为
`stage09-test-258e4456-configured-final`。

冻结 test 上，ExamMem Lifecycle 的 operation accuracy 为 **95.73%**、macro-F1 为
**82.49%**、active-state exact match 为 **90.42%**，stale rate 和 duplicate rate 均为
**3.32%**，cross-scope leakage 为 **0%**。所有预注册数值门槛通过。

这个结果证明的是：在结构化 LearningEvent 已经正确给出的受控 Memory 场景中，
ExamMem 的 typed lifecycle 比无记忆和只追加记忆更能维护当前学习状态。它不证明题目
生成、判题、原始聊天抽取或真实考研提分效果。

可靠性仍有明确缺口：Lifecycle 完成 79/80；一个 multi-value error-pattern case 中，
模型返回了不属于候选 slot 的知识点，严格契约拒绝应用。推荐知识点准确率只有
30.83%，因此不能把本阶段描述为“所有学习智能都已达到生产质量”。冻结 test 结果只
用于报告，之后没有据此修改代码。

## 2. 数据、冻结与防泄漏

- 数据集：`exam_mem_controlled_v1`；
- 内容：数学一线性代数和概率论的受控合成多轮学习轨迹；
- 场景：12 类，包括重复、互补证据、掌握变化、偶然错误、稳定薄弱、显式纠正、
  低置信度异常、计划迁移、多值错误、跨 Scope 干扰和长期变化；
- dev：40 case，SHA-256
  `b546278941c17f5e7238384cb0ed183c94c139fec6f5c8fbec443e51d982b3ab`；
- test：80 case，SHA-256
  `fd01c3a2eb910ad0476e82e54eacc593d44d867d37c812b69f1a99e8a8553011`；
- seed：`20260806`；Gold revision：3；top-k：5；
- Host LLM：`minimax_anthropic / MiniMax-M3`，temperature 0；
- embedding：本地 `ollama:qwen3-embedding:0.6b:1024`；
- test release：显式 `--allow-frozen-test`、五臂齐全、无 case/scenario filter，并用原子
  claim 保证只允许一个 release identity；中断只允许相同身份恢复。

输入从已校验的结构化 `LearningEvent` 开始，并使用 Gold-normalized slot 隔离评估
Memory 层。因此 slot F1 100% 是评测输入契约，不是聊天抽取模型取得了 100%。原始
文本抽取、题目生成和评分指标均为 N/A。

## 3. 五臂定义

| Backend | 实现 | 比较边界 |
|---|---|---|
| none | 丢弃事件和记忆 | 无记忆下界 |
| native | DeepTutor 原生 L1 JSONL、L2/L3 Markdown consolidation | 自由文本记忆，不暴露 ExamMem typed lifecycle/state |
| append-only | PostgreSQL 事件和 append-only facts | 保留历史，不做版本收敛 |
| vector | append-only facts + Qwen 1024 维生产 embedding 检索 | 只增加语义检索，不做 lifecycle |
| lifecycle | PostgreSQL、Policy v1、Decision Journal、Change Log、L3 rebuild | ExamMem 当前产品路径 |

五臂使用相同数据、Gold、top-k 和顺序。只有 Lifecycle 能提供
`RecommendationPolicyV1` 所需的 typed StudentModel/L2 证据；不把 Native 自由文本
强行转换成 ExamMem state。

## 4. 冻结 test 最终得分

| 指标 | none | native | append-only | vector | Lifecycle |
|---|---:|---:|---:|---:|---:|
| 完成率 | 100% | 100% | 100% | 100% | **98.75% (79/80)** |
| Slot F1 | 100% | 100% | 100% | 100% | **100% (398/398)** |
| Lifecycle operation accuracy | N/A | N/A | N/A | N/A | **95.73% (381/398)** |
| Lifecycle operation macro-F1 | N/A | N/A | N/A | N/A | **82.49%** |
| Active-state exact | 7.50% | N/A | 0% | 0% | **90.42% (217/240)** |
| Stale rate ↓ | N/A | N/A | 89.28% | 89.28% | **3.32% (9/271)** |
| Duplicate rate ↓ | N/A | N/A | **0.88%** | **0.88%** | 3.32% |
| Cross-scope leakage ↓ | N/A | N/A | 0% | 0% | **0% (0/81)** |
| Scope test pass | N/A | N/A | 100% (7/7) | 100% (7/7) | **100% (7/7)** |
| Weak recall@5 | 0% | 0% | **91.25%** | **91.25%** | 81.25% |
| Archived hit@5 ↓ | N/A | N/A | 0% | 0% | **0%** |
| 推荐知识点准确率 | 3.75% | 3.75% | 3.75% | 3.75% | **30.83% (74/240)** |
| Over-review rate ↓ | 3.33% | 3.33% | 3.33% | 3.33% | 13.92% |
| LLM calls | 0 | 680 | 0 | 0 | **37** |
| 平均 case 延迟 | 1.59 ms | 20.80 s | 103.08 ms | 1.49 s | **2.23 s** |
| P95 case 延迟 | 2.00 ms | 33.64 s | 174.98 ms | 1.85 s | **6.40 s** |
| Memory record growth | 0 | 480 | 398 | 398 | **101** |

Host LLM 没有返回 provider token usage，token 和美元成本按协议记为 undefined，不做
静默估算。

## 5. 比 baseline 强多少

同一冻结 test 的可比消融结果如下：

- Active-state exact：90.42% 对 none 的 7.50%，高 **82.92 个百分点**，相对提升
  **1105.56%**；append-only/vector 为 0%。
- 推荐知识点准确率：30.83% 对四个 baseline 的 3.75%，高 **27.08 个百分点**，相对
  提升 **722.22%**（8.22 倍）。绝对值仍低，不能只讲相对增幅。
- Stale rate：3.32% 对 append-only/vector 的 89.28%，下降 **85.96 个百分点**，相对
  减少 **96.28%**。
- Memory record growth：101 对 append-only/vector 的 398，减少 **74.62%**。
- LLM 调用：37 对 Native 的 680，减少 **94.56%**；平均延迟比 Native 低
  **89.30%**。

同时存在退步或代价：

- 完成率比四个 baseline 低 **1.25 个百分点**；
- Weak recall@5 比 append-only/vector 低 **10.00 个百分点**；
- Duplicate rate 高 **2.44 个百分点**；
- Over-review rate 高 **10.59 个百分点**；baseline 几乎不产生有效推荐，因此该低值
  不能单独解释成推荐更好；
- Lifecycle 平均延迟是 vector 的 **1.49 倍**、append-only 的 **21.59 倍**。

DeepTutor 论文在不同任务 TutorBench 上报告 Overall Quality 3.91 对 Naive Tutor
3.53（+10.76%）。该数据、任务和指标均与本评测不同，只能作为方法背景，不能与上表
直接比较：<https://arxiv.org/html/2604.26962#S5>。

## 6. dev 到 test 的泛化

| Lifecycle 指标 | dev 40 | frozen test 80 | test - dev |
|---|---:|---:|---:|
| 完成率 | 100% | 98.75% | -1.25 pp |
| Operation accuracy | 98.48% | 95.73% | -2.75 pp |
| Operation macro-F1 | 91.01% | 82.49% | -8.52 pp |
| Active-state exact | 95.83% | 90.42% | -5.42 pp |
| Stale rate ↓ | 1.52% | 3.32% | +1.81 pp |
| Duplicate rate ↓ | 4.55% | 3.32% | -1.22 pp |
| 推荐知识点准确率 | 30.83% | 30.83% | 0 pp |
| Weak recall@5 | 85.00% | 81.25% | -3.75 pp |

test 较 dev 有合理下降，尤其 macro-F1，说明不能只报 dev。核心门槛仍通过，但单例执行
失败暴露了模型结构化输出在业务写入前仍需严格校验和可恢复重试。

## 7. 预注册门槛与错误分布

| 门槛 | test | 判定 |
|---|---:|---|
| Slot F1 ≥ 85% | 100% | 通过 |
| Operation macro-F1 ≥ 80% | 82.49% | 通过 |
| Stale rate ≤ 5% | 3.32% | 通过 |
| Duplicate rate ≤ 5% | 3.32% | 通过 |
| Cross-scope leakage = 0 | 0% | 通过 |
| Scope pass = 100% | 100% | 通过 |
| Archived hit@5 = 0 | 0% | 通过 |

398 个 Gold operation 的 17 个错误为：

- `CONTESTED → ADD`：4；
- `SUPERSEDE → INVALIDATE`：4；
- `SUPERSEDE → MERGE`：4；
- `NO_OP → NO_DECISION`：4；
- `MERGE → NO_DECISION`：1，即唯一失败 case。

其中 correction 事件如果只有“旧事实无效”而没有结构化 replacement，产品策略选择
`INVALIDATE`，而 Gold 期望 `SUPERSEDE`；这是需要在未来数据契约中显式建模的语义
边界，不在看过 test 后临时加 fallback。

## 8. Stage09 的最小生产修正

1. 合成评测的初始 L1 只作为 provenance fixture，标记为 temporary /
   `insufficient_context`，不伪造方向性证据。
2. LOW/IMPROVING 状态在证据未达到晋级阈值时通过 MERGE 累积；HIGH/MASTERED 的反向
   证据仍进入 CONTESTED，不削弱稳定状态保护。
3. error-pattern slot 的关系分类 schema 只允许该 slot 契约支持的 duplicate 或
   complementary；越界输出严格失败并受限重试，不增加无限 fallback。
4. frozen test release 默认关闭，必须显式授权；完整五臂、固定 split、无过滤器和原子
   claim 共同防止重复查看 holdout 后调参。

## 9. 数据库与文件副作用

正式 test 使用专用本地数据库 `exammem_eval_final`，migration head 为
`0011_assessment_archival`。按 run 前缀只读统计：

| Backend | events | baseline facts | learning memories | decisions | change log | provenance | model snapshots |
|---|---:|---:|---:|---:|---:|---:|---:|
| append-only | 724 | 492 | 97¹ | 0 | 0 | 497 | 0 |
| vector | 724 | 492 | 97¹ | 0 | 0 | 497 | 0 |
| lifecycle | 721 | 0 | 198 | 392 | 892 | 520 | 68 |

¹ shadow memory 只用于满足 correction/plan target 外键，不参与 baseline 行为；因此数据
库物理行数不能替代报告中的 backend `memory_record_growth` 指标。

Native 写入隔离 run 目录，报告记录 480 条 memory record growth、683,867 bytes。没有
读取用户 Native Memory，没有接触 demo/业务数据库。原始 run 目录受 `.gitignore`
管理；报告验收前不执行破坏性清理。

## 10. 验收记录

- `pytest -q tests/evaluation`：100 passed；
- `pytest -q tests/exam_mem/lifecycle`：135 passed；
- 无 DSN 的 storage 单元/契约测试：43 passed，39 skipped；
- 新建隔离库 `exammem_gate_stage09_258e4456`，从空库迁移到
  `0011_assessment_archival` 后运行 Repository/PostgreSQL 与插件闭环：83 passed；
- 在非沙箱本机进程运行全部 21 个 FastAPI/Starlette TestClient 文件（含 Runtime 插件
  管理器、API 权限和 Learning API）：284 passed，正常退出；
- `ruff check .`、文档 JSON、`git diff --check`：通过；
- migrations `0001`～`0006` 同时与冻结源当前工作树和冻结提交
  `747958725b6e681a3a846a0430b5a21deb163188` 逐字节一致；
- `deeptutor/core`、`deeptutor/runtime` 无 `exam_mem` 引用，整个 `deeptutor` 无直接
  `import exam_mem`；
- 冻结源工作树 clean；其 HEAD 是冻结基线后的文档提交 `c8512fff`，本阶段没有写入；
- 新增报告的敏感内容扫描只命中 Runbook 中的 `USER:PASSWORD` 占位符。

此前 TestClient 卡住已证明不是依赖或业务缺陷：受限命令沙箱中，连纯 Python
`asyncio.run_coroutine_threadsafe()` 都无法唤醒跨线程 event loop；相同解释器、相同
依赖在非沙箱本机进程中，asyncio 探针、最小 TestClient 和上述 284 个测试均通过。
因此没有修改 Starlette/httpx2/AnyIO，也没有用超时特判掩盖问题。后续运行这类测试时
必须使用允许跨线程 asyncio 唤醒的执行环境。

## 11. 剩余限制

- 数据是受控合成 Memory 轨迹，不是教师双标的考研真题、真实聊天或用户行为；
- 原始聊天到 LearningEvent 的抽取、出题质量、评分 MAE/Kappa、难度校准均未评估；
- 推荐准确率 30.83%，仍需单独构建 prerequisite、难度和多样性 Gold；
- 一次模型 slot 越界导致 case 失败，需要后续用 checkpoint 恢复语义处理，而不是放宽
  契约；
- 没有真实学习增益、长期留存、线上 A/B、压力和成本数据；
- test 已被一次性消费，后续开发只能使用 dev 或创建新的、重新冻结的数据版本，不能
  重跑本 test 选最好数字。

执行与审计命令见 [Stage09 Runbook](./stage09-runbook.zh-CN.md)，机器可读摘要见
[Stage09 frozen-test summary](../../../results/controlled-v1/summary.json)。原始不可变 run 位于本地
`artifacts/stage08/runs/stage09-test-258e4456-configured-final/`。
