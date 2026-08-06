# 阶段 02：确定 ExamMem 架构与评测协议

> 阶段性质：架构冻结与实验预注册。  
> 前置条件：[阶段 01](./01_固定DeepTutor底座与仓库审计.md)验收通过。  
> 退出条件：模块边界、数据契约、Gold、指标、Baseline、切分和成本口径均版本化冻结。  
> 阶段门禁：协议未冻结，不得进入[阶段 03](./03_非核心能力裁剪与Feature_Flag.md)或任何核心 Memory 开发。

## 1. 阶段目标

本阶段先定义“怎样才算做对”，再写核心代码。需要形成一个能够直接驱动测试和实验的 `evaluation_protocol_v1`，避免开发完成后为了让结果好看而改变样本、Gold 或指标。

核心输出包括：

- ExamMem 模块边界、依赖方向和数据流；
- Native Memory 与 Learning Memory 的并行关系；
- 统一核心类型与最小接口；
- 评测 Case、Rollout、Trace 和 Report 契约；
- Gold Label 标注规则、12 类场景和 24 条协议验证样例；
- 五个 Baseline、数据切分、随机种子、指标公式与成本预算。

### 1.1 范围

本阶段可以新增类型、Schema 校验器、协议样例和 ADR，但不实现数据库状态机或业务功能；协议覆盖数学一线性代数与概率论、五种 Memory Backend 和六层评测，微积分、真实用户实验及线上部署不在范围内。

## 2. 技术路线与架构边界

### 2.1 系统分层

```text
CLI / Web / API
       │
       ▼
DeepTutor ChatOrchestrator
       │
       ├── Native capabilities（保留，可关闭）
       └── ExamPractice Capability
               │
               ▼
       Question / Grade / Diagnose / Recommend Tools
               │
               ▼
          MemoryBackend Protocol
      ┌────────┼──────────┬────────┬──────────┐
      none    native   append-only vector  lifecycle
                                           │
                                           ▼
                               ExamMem Learning Memory
                     Extract → Normalize → Retrieve → Decide
                              → Apply → Project → Query
                                           │
                                           ▼
                                PostgreSQL + pgvector

Evaluation Harness 从入口、Trace 和数据库快照同时采集证据。
```

依赖规则：

- DeepTutor 入口只依赖 `MemoryBackend` 接口，不依赖某个具体 Baseline；
- 生命周期服务不依赖 Web/UI；
- L3 Student Model 只能由 L1/L2 投影，不成为不可追溯真值；
- Evaluation 可以驱动真实入口，但生产模块不得反向依赖 Evaluation；
- Native Memory 不写 ExamMem 表，Learning Memory 不改 Native Memory 文件。

### 2.2 核心类型

字段名可在阶段 01 审计后匹配底座风格，但语义不得改变。

```python
class LearningContext(BaseModel):
    user_id: str
    exam_id: str
    subject_id: str

class MemoryScope(LearningContext):
    memory_namespace: str

class LearningEvent(BaseModel):
    event_id: str
    idempotency_key: str
    context: LearningContext
    session_id: str
    question_id: str
    knowledge_point_ids: list[str]
    difficulty: float
    answer_correct: bool
    error_type: str | None
    occurred_at: datetime

class LearningMemory(BaseModel):
    memory_id: str
    scope: MemoryScope
    slot_key: str
    value: dict
    confidence: float
    evidence_count: int
    lifecycle_state: str
    version: int
    valid_from: datetime
    valid_to: datetime | None
    superseded_by: str | None
    provenance: list[str]

class LifecycleDecision(BaseModel):
    operation: Literal[
        "ADD", "NO_OP", "MERGE", "SUPERSEDE", "INVALIDATE", "CONTESTED"
    ]
    target_memory_ids: list[str]
    reason_code: str
    confidence: float
    policy_version: str

class MemoryUpdateCandidate(BaseModel):
    event_id: str
    scope: MemoryScope
    slot_key: str
    proposed_value: dict
    evidence: dict

class StudentModel(BaseModel):
    context: LearningContext
    weak_points: list[str]
    mastered_points: list[str]
    stable_error_patterns: list[str]
    active_plans: list[str]
    projection_version: int
    source_watermark: str
```

`value` 的具体 JSON Schema 按 `mastery / error_pattern / plan / profile / preference` 分型，禁止在业务代码中读取未校验的任意字典。

### 2.3 MemoryBackend 契约

所有 Baseline 使用同一调用位置和 Trace 格式：

```python
class MemoryBackend(Protocol):
    async def record_event(self, event: LearningEvent) -> None: ...
    async def update(
        self, event: LearningEvent, candidates: list[MemoryUpdateCandidate]
    ) -> list[LifecycleDecision]: ...
    async def query_state(self, context: LearningContext) -> StudentModel | None: ...
    async def retrieve(self, scope: MemoryScope, query: str, top_k: int) -> list: ...
    async def snapshot(self, context: LearningContext) -> dict: ...
```

L1 事件使用三维 `LearningContext`，因为一次答题可以同时派生 mastery、error pattern 和 plan 等多个更新；每个 `MemoryUpdateCandidate` 再携带完整四维 `MemoryScope`。所有 L2 查询和更新仍必须包含 `memory_namespace`。

模式语义：

| 模式 | 写入 | 更新 | 检索用途 |
| --- | --- | --- | --- |
| `none` | 不写 | 无 | 对照无长期记忆 |
| `native` | 走 DeepTutor 原生机制 | 原生语义 | 原生能力 Baseline |
| `append_only` | 只追加事件/事实 | 不合并不失效 | 膨胀与过期对照 |
| `vector` | 追加并向量化 | 不执行状态机 | 语义召回对照 |
| `lifecycle` | L1/L2/L3 | 完整生命周期 | ExamMem 实验组 |

## 3. 评测协议

### 3.1 Case Schema

```json
{
  "protocol_version": "evaluation_protocol_v1",
  "case_id": "accidental_error_001",
  "scenario_type": "accidental_error",
  "initial_memory": [],
  "events": [],
  "gold_operations": [],
  "gold_states": [],
  "queries": [],
  "gold_actions": [],
  "metadata": {
    "split": "protocol_check",
    "seed": 20260806,
    "gold_revision": 1
  }
}
```

每一步 Gold 必须同时描述：预期提取、canonical knowledge point、`slot_key`、候选集合、生命周期操作、active/archived 集合、版本关系和下游动作。只标最终回答无法定位 Memory 错误。

### 3.2 12 类场景

1. 语义重复；
2. 互补证据；
3. 掌握度提升；
4. 掌握度下降；
5. 单次偶然错误；
6. 稳定薄弱点；
7. 显式纠错；
8. 低置信度或临时例外；
9. 学习计划完成、取消或过期；
10. 多值错因与错因聚合；
11. 相似知识点及跨 Scope 干扰；
12. 跨会话、长距离状态变化。

阶段 02 每类先写 2 条，共 24 条 `protocol_check` 样例。它们用于检查协议和实现契约，不计入阶段 08 的 120 条正式数据。

### 3.3 Gold 标注规则

- Gold 来自可解释的学习轨迹规则和人工审核，不使用待评模型直接生成最终标签；
- 不充分证据默认 `CONTESTED` 或 `NO_OP`，而不是积极覆盖；
- 单次错误不得自动把稳定高掌握度标为 low；
- 同 Scope、同 canonical slot 才能成为直接冲突候选；
- archived 记录不能进入正常推荐上下文；
- 每个 Gold 操作必须包含简短理由和引用的 event ID；
- 修订 Gold 必须增加 `gold_revision`、记录理由并重跑全部 Baseline；
- 冻结测试集后，禁止根据测试输出逐条修改 Gold。

若只有一名标注者，采用“首次标注 → 至少间隔一天盲审 → 冲突记录”的自校验流程；不得伪造多人一致性指标。

### 3.4 数据切分和随机性

- 固定随机种子：`20260806`；
- 阶段 02：24 条 `protocol_check`，不计正式分数；
- 阶段 08：120 条正式轨迹，分层切为 40 条 `dev` 与 80 条冻结 `test`；
- 所有 Baseline 使用同一题目、事件顺序、模型参数、top-k、超时和重试策略；
- 调参只看 `dev`；`test` 在最终运行前保持冻结；
- 每个正式实验保存数据集哈希、配置哈希、代码 SHA 和运行 ID。

### 3.5 指标与目标值

指标公式必须在实现前写入协议；下列数字都是**目标值**，不是已有成果。

| 层级 | 指标 | 目标值/约束 |
| --- | --- | --- |
| 提取 | Knowledge Point Accuracy、Error Type Macro-F1 | 报告实际值 |
| slot | Precision/Recall/F1 | F1 ≥ 0.85 |
| 生命周期 | Operation Accuracy、Macro-F1 | Macro-F1 ≥ 0.80 |
| 污染 | False Merge、False Supersede | 分别报告且越低越好 |
| 状态 | Active State EM、Stale/Duplicate Rate | Stale、Duplicate ≤ 5% |
| 隔离 | Cross-Scope Leakage | 0，隔离测试 100% 通过 |
| 检索 | Weak Recall@K、Archived Hit@K | Archived Hit@K = 0 |
| 推荐 | 下一题知识点、难度匹配、过度复习率 | 报告实际值 |
| 工程 | 调用数、Token、平均/P95 延迟、Memory Growth | 同配置对比 |

阶段 08 的职责是执行并报告，不允许删除不利指标。阶段 09 的默认优化门槛是：选定核心指标提升至少 5 个百分点、关键保护指标下降不超过 2 个百分点、成本增幅不超过 20%。

## 4. Trace、Rollout 与 Report

统一 Trace 至少包含：

```text
run_id / case_id / trace_id / step_id
backend_mode / protocol_version / policy_version
input_event / extracted_fields / normalized_slot_key
candidate_ids / lifecycle_decision / state_before / state_after
retrieval_ids / recommendation / llm_calls / tokens / latency_ms / errors
```

Rollout Runner 必须：

- 按事件顺序执行，不偷看未来事件；
- 每个 Case 从明确的初始状态开始；
- 超时与失败按协议计分，不能静默跳过；
- 运行后保存数据库快照和 Trace；
- 支持同一 Case 在五种 backend 模式下回放。

Report 同时输出 JSON/JSONL、CSV/Parquet 和 Markdown/HTML 汇总。图表只读取机器结果生成，禁止手工填数字。

## 5. 引导式编程任务

### 任务 A：从失败案例反推可观测点

阅读“最终回答正确但旧状态残留”的场景，先列出只看答案会漏掉的错误，再设计 Trace 字段。写一条失败的 schema 测试后才实现模型。

### 任务 B：亲自标注 24 条协议样例

先独立给出 Gold，再让 AI Review 操作与状态是否一致。AI 可以质疑规则，但不能代替你决定 Gold。你需要口述每个 `MERGE` 与 `SUPERSEDE` 的差别。

### 任务 C：Baseline 公平性检查

逐项解释为什么模型、输入轨迹、top-k、超时和重试必须相同。为“Baseline 因配置不同而虚假落后”写一个配置比较测试。

## 6. 关键决策记录

至少创建以下 ADR：

- 为什么采用模块化单体而不是微服务；
- 为什么 Native Memory 与 Learning Memory 并行；
- 为什么评测协议先于核心开发；
- 为什么 PostgreSQL + pgvector 从 MVP 开始使用；
- 为什么 LLM 只做语义判断、确定性状态机执行写入；
- 为什么 MVP 只覆盖数学一中的线性代数和概率论。

## 7. 运行命令模板

命令名在实际包结构确定后回填，参数语义保持不变：

```powershell
# 校验核心类型和评测 Case Schema
python -m evaluation.cli protocol validate --version evaluation_protocol_v1

# 校验 24 条协议样例和 Gold 状态回放
python -m evaluation.cli dataset validate --split protocol_check
python -m evaluation.cli gold replay --split protocol_check

# 运行协议与配置单测
pytest -m "protocol or schema" -q
```

本阶段只要求命令可验证协议，不得借此提前执行冻结测试集。

## 8. 交付物

- 架构图、数据流图和模块职责表；
- 核心 Pydantic 类型与 JSON Schema 草案；
- `MemoryBackend` 契约与五种模式说明；
- `evaluation_protocol_v1` 文档与配置；
- 24 条协议验证样例及自审记录；
- 指标公式、切分规则、成本口径和随机种子；
- ADR 集合、风险清单和协议变更流程。

## 9. 验收标准

| 验收项 | 目标值 | 验证方法 |
| --- | --- | --- |
| 协议样例 | 12 类 × 2 条 = 24 条 | Schema 校验与人工复核 |
| 核心类型 | 100% 可序列化并校验 | 正反例测试 |
| Gold 完整性 | 每一步均有操作和状态 | 校验器拒绝缺失字段 |
| Baseline 公平性 | 配置差异均显式 | 配置快照比较 |
| 数据版本 | 协议、Gold、数据集可追踪 | 版本号与哈希 |
| 指标措辞 | 0 个目标值被写成实测值 | 人工审阅与文本检查 |

回滚方式：协议错误时创建新版本，不覆盖旧版本；已发布的运行结果继续引用原协议版本。

## 10. 提交清单与 Git 门禁

- [ ] 代码：Schema、校验器或协议骨架；
- [ ] 测试结果：类型、Gold、配置一致性测试；
- [ ] 运行命令：校验 24 条样例的可复制命令；
- [ ] 交付物：第 8 节全部存在；
- [ ] 已知问题：未决规则、数据偏差和模型依赖；
- [ ] 独立 Git Commit：不包含阶段 03 的裁剪实现。

建议 Commit Message：

```text
docs(eval): freeze ExamMem architecture and evaluation protocol v1
```

## 11. 面试复盘卡

你应能回答：

1. 为什么评测协议必须先于 Memory 实现？
2. Gold Operation 和 Gold State 为什么要同时标？
3. 强 LLM 为什么会掩盖 Memory 污染？
4. 如何保证五个 Baseline 公平？
5. Native Memory 与 Learning Memory 的 L1/L2/L3 有何不同？

推荐表述：

> 我把评测拆成提取、候选、操作、状态、检索和下游行为六层，并在开发前冻结 Gold、切分和成本口径。这样即使强模型答对题，也能定位内部记忆是否残留旧状态或错误合并。
