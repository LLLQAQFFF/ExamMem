# 阶段 08：构建评测体系与 Baseline

> 阶段性质：可复现实验基础设施与开发集评测。  
> 前置条件：[阶段 07](./07_刷题端到端链路.md)验收通过。  
> 退出条件：120 条轨迹、五个 Baseline、六层 Evaluator 和自动报告可复现运行；冻结测试集未被用于调参。  
> 阶段门禁：评测体系未验收，不得进入[阶段 09](./09_正式实验_问题优化与最终报告.md)。

## 1. 阶段目标

把阶段 02 冻结的协议实现为可执行的 Dataset → Rollout → Trace → Judge → Report 流水线。阶段 08 主要在 `protocol_check` 和 `dev` 上调试评测代码与 Baseline，`test` 只做 Schema、哈希和可加载性检查，不查看逐 Case 输出，不用于修改规则。

本阶段不以“ExamMem 一定优于 Baseline”为验收条件。验收的是：同一输入、同一资源配置下五种系统可公平回放，所有失败和成本都进入报告。

### 1.1 范围

本阶段构建受控离线轨迹和自动评测，不采集真实学生数据、不进行线上 A/B 测试，也不训练模型。正式结果的适用范围仅限锁定的数学一 taxonomy、题库、模型和受控轨迹。

## 2. 数据集构造

### 2.1 数量与分布

正式数据共 120 条受控学习轨迹，12 类场景各 10 条：

1. 语义重复；
2. 互补信息；
3. 掌握度提升；
4. 掌握度下降；
5. 偶然错误；
6. 稳定薄弱点；
7. 显式纠错；
8. 低置信度/临时例外；
9. 计划完成、取消、过期；
10. 多值错因；
11. 相似知识点/跨 Scope 干扰；
12. 跨会话长距离变化。

分层切分为 40 条 `dev`、80 条冻结 `test`：前四类各 4 dev + 6 test，其余八类各 3 dev + 7 test。切分脚本使用固定 seed `20260806`，输出 manifest 和内容哈希。

### 2.2 轨迹要求

- 每条包含初始 Memory、3～20 个按时序排列的事件和至少一个查询/推荐点；
- 覆盖 2 个以上 session，长距离场景包含明确时间间隔；
- 题目和答案来自受控题库，知识点限定 `math1_v1`；
- 难度、错因和正确性能够人工核查；
- 不把模板只换人名后当作新样本；
- Scope 干扰 Case 显式包含另一个用户/namespace/subject 的同 key 记录；
- 每步 Gold 包含 operation、active/archived/contested 状态和版本关系。

### 2.3 数据质量

运行以下检查：Schema 完整性、ID 唯一、时间单调、引用存在、taxonomy 有效、Gold 状态可由操作回放、切分无 case/模板泄漏。所有修订增加数据版本；测试集冻结后只能修复明确标注错误，并生成新的 protocol revision 和完整审计记录。

## 3. 五个 Baseline

| Baseline | 模式 | 目的 |
| --- | --- | --- |
| No Memory | `none` | 测量无长期记忆时的下游行为 |
| Append-only | `append_only` | 测量重复、过期和膨胀问题 |
| Vector Memory | `vector` | 测量纯语义召回、不治理状态的效果 |
| DeepTutor Native | `native` | 与底座原生三层 Memory 对比 |
| ExamMem Lifecycle | `lifecycle` | 完整 `slot_key`、状态机、版本和学生模型 |

公平性约束：

- 相同底座 Commit、LLM/Embedding 模型、temperature、超时、重试和题目；
- 相同初始状态语义，通过各 Backend Adapter 注入；
- 相同事件顺序，不允许 Baseline 偷看未来；
- 相同检索 `top_k` 和 Prompt 信息预算；
- 每个运行从干净、独立 Scope 开始；
- 失败、超时和非法输出按统一规则计分，不静默重跑到成功；
- Native Memory 无法提供某个内部指标时标记 `not_available`，不伪造等价数据。

Legacy Mem0 不进入固定五组；只有完成许可、接口和公平性审计后才能作为额外实验，不替换已冻结 Baseline。

## 4. 技术路线与评测流水线

```text
DatasetAdapter
  → CaseMaterializer
  → BackendReset/Seed
  → RolloutRunner
  → TraceCollector + DB/Native Snapshot
  → LayeredEvaluators
  → ResultStore
  → ReportGenerator
```

### 4.1 Rollout Runner

- 支持选择 split、case、scenario、backend 和并发数；
- 默认按用户 Scope 串行，同一用户不并发写；
- 每个 Case 设置硬超时和调用预算；
- 生成唯一 `run_id`，记录 Git SHA、配置哈希、数据哈希和环境摘要；
- Case 失败后保存已产生 Trace，并继续其他 Case；
- 支持 `--resume`，但已完成 Case 不重算，除非创建新 run。

### 4.2 Judge 分层

优先使用确定性 Judge：

- canonical ID、slot、operation、状态集合、版本链和推荐知识点直接与 Gold 比较；
- 文本错因先比较受控 error type，再对 explanation 做可选语义质量 Judge；
- LLM Judge 只用于难以规则判断的解释质量，必须锁定模型/Prompt，并保存原始判断与理由；
- LLM Judge 不能推翻数据库状态的确定性错误；
- 任何人工复核结果单独标记，不覆盖原自动结果。

## 5. 六层指标

### 5.1 提取层

- Knowledge Point Accuracy；
- Error Type Precision/Recall/Macro-F1；
- `slot_key` Precision/Recall/F1；
- unknown rate、二次校正调用率。

### 5.2 生命周期层

- Operation Accuracy 与 Macro-F1；
- False Merge Rate；
- False Supersede Rate；
- CONTESTED precision、recall 和 convergence rate；
- 幂等重放错误数。

### 5.3 状态层

- Active State Exact Match；
- Stale Memory Rate；
- Duplicate Memory Rate；
- Version Chain Accuracy；
- Archived Memory Residual Rate；
- Cross-Scope Leakage Count。

### 5.4 检索与推荐层

- Weak-Knowledge Recall@K；
- Archived Memory Hit@K；
- 下一题知识点选择准确率；
- 难度匹配准确率；
- 过度复习率；
- 推荐依据完整率。

### 5.5 学习轨迹层

在受控模拟轨迹上报告：达到目标掌握度所需题目数、薄弱点覆盖率、已掌握点重复练习率、计划完成率。必须称为“受控轨迹指标”，不能外推为真实学生学习效果。

### 5.6 工程层

- LLM/Embedding Calls；
- Input/Output Tokens 与估算费用；
- 平均、P50、P95 延迟；
- 超时和错误率；
- Memory 行数、字节增长与单事件增量；
- 单条成功更新成本。

所有分母为 0 的指标返回 `not_available`，不返回误导性的 0。

## 6. 结果与报告

目录建议：

```text
evaluation/results/<run_id>/
├── manifest.json
├── config.json
├── cases.jsonl
├── traces.jsonl
├── snapshots/
├── metrics.json
├── metrics.csv
├── bad_cases.jsonl
└── report.md
```

报告包含：数据版本、运行配置、完成/失败数、总体指标、分场景指标、成本、混淆矩阵、Bad Case 索引和已知限制。图表从 `metrics.json/csv` 自动生成，不手工改图中数字。

每个 Bad Case 至少记录：首次错误层、期望/实际、相关 Trace/Memory ID、可能根因、影响指标、是否可稳定复现。首次错误层分类为：extract、normalize、candidate、decision、apply、projection、retrieve、recommend、infrastructure。

## 7. 开发集与测试集纪律

- `protocol_check`：调试 Schema、Runner 和 Judge；
- `dev`：校准阈值、排查错误、选择阶段 09 优化问题；
- `test`：阶段 08 只验证文件存在、Schema、数量和哈希，不运行可见结果；
- 阶段 09 冻结代码后才运行 test；
- 若提前看到 test 个例，必须记录污染并重新构造受影响的 holdout，不能假装未看过。

## 8. 引导式编程任务

### 任务 A：先实现一个 Case 的垂直切片

不要先写通用框架。让一个 `protocol_check` Case 从加载、回放、快照到报告完全跑通，再抽取最小公共接口。解释每层为什么需要独立证据。

### 任务 B：制造一个“答案正确、状态错误”案例

让强模型仍答对下一题，但数据库保留 stale Memory。确认答案指标通过、状态指标失败，证明分层评测的必要性。

### 任务 C：公平性审查

故意改变一个 Baseline 的 top-k 或重试次数，让配置比较器拒绝聚合。AI Review 检查是否还有隐藏的不公平输入。

### 任务 D：Bad Case 归因

随机选择 5 个 dev 失败，只定位首次错误层，不急着修。用 Trace 证明后续错误是传播结果，而不是多个独立根因。

## 9. 运行命令模板

目标 CLI 可按项目实际包名调整，但参数语义必须保持：

```powershell
# 校验数据与冻结 manifest
python -m evaluation.cli dataset validate --protocol evaluation_protocol_v1
python -m evaluation.cli dataset freeze --seed 20260806

# 运行协议样例和开发集
python -m evaluation.cli run --split protocol_check --backend all
python -m evaluation.cli run --split dev --backend all --config configs/eval_v1.yaml

# 生成报告
python -m evaluation.cli report --run-id <RUN_ID>

# test 在阶段 08 只做不可见内容校验
python -m evaluation.cli dataset verify-holdout --split test --no-content-output
```

命令、退出码和原始日志必须进入阶段交付；不得只提交最终 Markdown 报告。

## 10. 交付物与验收

### 10.1 交付物

- 120 条轨迹、40/80 manifest、数据哈希和标注说明；
- 五个 Backend Adapter 与公平性配置检查器；
- Dataset、Runner、Trace、Snapshot、Evaluator、Report 模块；
- 六层指标实现和单元测试；
- protocol_check/dev 的完整结果与 Bad Case；
- test 冻结证明，不含用于调参的逐 Case 输出；
- 运行、恢复、清库和报告 Runbook。

### 10.2 验收标准

| 验收项 | 目标值 |
| --- | --- |
| 数据量 | 12 类 × 10 = 120 条 |
| 切分 | dev 40 / test 80，哈希固定 |
| Schema/Gold 回放 | 100% 通过 |
| Baseline | 五组均可运行或明确失败原因 |
| 指标 | 六层指标均有值或 `not_available` 理由 |
| 配置公平性 | 差异配置不能被错误聚合 |
| Bad Case | 每个失败有首次错误层 |
| 测试集纪律 | 未用于规则或阈值调整 |

回滚方式：结果按 `run_id` 不可变保存；评测代码出错时创建新 run，不覆盖旧结果。数据修订必须升版本并保留旧 manifest。

## 11. 提交清单与 Git 门禁

- [ ] 代码：Dataset、Runner、Evaluator、Report 和五个 Adapter；
- [ ] 测试结果：指标公式、Runner 恢复、配置公平性和报告测试；
- [ ] 运行命令：数据校验、dev 回放、报告和 holdout 检查；
- [ ] 交付物：第 10.1 节全部存在；
- [ ] 已知问题：数据代表性、LLM Judge 偏差和预算限制；
- [ ] 独立 Git Commit：不包含针对 dev Bad Case 的优化修复。

建议 Commit Message：

```text
feat(eval): add controlled trajectories baselines and layered reports
```

## 12. 面试复盘卡

你应能回答：

1. 为什么阶段 08 不直接用 test 调参？
2. 如何比较无法暴露内部状态的 Native Memory？
3. 为什么要保存数据库快照，而不只保存 Trace？
4. 如何处理分母为 0、超时和失败 Case？
5. 受控轨迹的“学习增益”为什么不能宣传为真实用户效果？

推荐表述：

> 我实现了 Dataset、Rollout、Trace、Snapshot、Evaluator 和 Report 分层评测，用同一轨迹公平比较五种 Memory。测试集在协议冻结后保持隔离，报告同时展示正确性、污染、推荐和 Token/延迟成本，失败不会被静默过滤。
