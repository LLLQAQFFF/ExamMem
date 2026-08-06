# ExamMem：评测驱动的自适应考研刷题 Agent

## 1. 项目定位

ExamMem 是一个面向考研复习场景的长期记忆 Agent。项目基于成熟教育 Agent 架构进行二次开发，保留题目交互、知识库、Agent 编排和工具调用能力，重点新增：

- 面向学习状态的结构化长期记忆；
- 基于 `slot_key` 的记忆归组；
- 可审计的生命周期状态机；
- 学生知识状态建模；
- 分层、可归因的评测框架；
- 失败样例驱动的回归与迭代闭环。

核心问题不是“能否回答考研题”，而是：

> 如何通过跨会话长期记忆，持续维护用户的知识掌握度、稳定错因和学习计划，并证明 Memory 更新确实改善了后续选题、复习和讲解效果。

---

## 2. 项目需求

### 2.1 目标用户

- 考研数学、英语、政治或专业课备考学生；
- 需要跨天、跨周追踪学习状态的用户；
- 需要个性化选题、错题复习和学习计划调整的用户。

### 2.2 核心业务问题

系统需要解决：

1. 同一错题或错因被重复记录，Memory 持续膨胀；
2. 用户掌握度已经变化，但旧状态仍然 active；
3. 一次偶然错误错误覆盖长期能力判断；
4. 不同题目的相似错误无法聚合为稳定错因；
5. 已完成或失效的学习计划继续参与推荐；
6. 已掌握知识点被过度练习；
7. 薄弱知识点未被持续追踪；
8. 最终答案看似正确，但 Memory 内部状态已污染；
9. 缺少能够定位提取、召回、更新、状态执行和检索错误的评测方法。

### 2.3 核心功能

MVP 至少包含：

- 单科目刷题；
- 题目读取、检索或生成；
- 用户作答与答案判定；
- 知识点映射和错因分类；
- 学习事件写入；
- `slot_key` 生成与归一；
- Memory 生命周期更新；
- 学生状态查询；
- 个性化下一题推荐；
- 学习计划创建、更新和失效；
- 评测数据回放；
- Baseline 对比；
- 自动生成指标与 Bad Case 报告。

### 2.4 典型场景

#### 知识掌握度更新

```text
连续答错概率论题目
→ 记录“条件概率掌握度较低”

经过讲解和练习后连续答对
→ 新掌握状态替代旧状态
```

#### 偶然错误识别

```text
过去 20 道线性代数题正确率较高
本次因计算粗心答错 1 题
→ 不应直接把 mastery 从 high 覆盖为 low
```

#### 错因聚合

```text
不会使用全概率公式
条件概率方向混淆
先验概率与后验概率混淆
```

系统需要判断这些信息是重复、互补、上下位关系还是独立错误。

#### 学习计划失效

```text
“本周完成概率论复习”
→ 计划完成后 INVALIDATE
```

---

## 3. 总体设计目标

### 3.1 工程目标

构建一个能够独立运行、可复现、可测试、可演示的考研刷题 Agent。

### 3.2 Memory 目标

建立三层学习 Memory：

```text
L1：原始学习事件，只追加
L2：结构化学习记忆，可版本化更新
L3：学生模型，由 L1/L2 聚合并可重建
```

生命周期操作：

```text
ADD
NO_OP
MERGE
SUPERSEDE
INVALIDATE
CONTESTED
```

### 3.3 评测目标

评测不只看最终答案，还要判断：

1. 是否正确提取知识点和错因；
2. `slot_key` 是否正确；
3. 冲突候选是否被召回；
4. 生命周期操作是否正确；
5. active / archived 状态是否正确；
6. 旧状态是否残留；
7. 检索是否使用过期 Memory；
8. 下一题推荐是否改善；
9. 改善付出了多少 Token、延迟和存储成本。

### 3.4 简历与展示目标

最终项目应具备：

- 完整代码仓库；
- 清晰架构图；
- 100～500 条受控学习轨迹；
- 3～5 个 Baseline；
- 自动化评测脚本；
- 至少一个“失败—定位—修复—量化验证”闭环；
- Bad Case 报告；
- 可运行 Demo；
- 修复前后真实指标。

---

## 4. 技术栈

### 4.1 基础框架

- 应用底座：基于成熟教育 Agent 项目二次开发，优先参考 DeepTutor；
- 评测架构：参考 Youtu-Agent 的 Dataset、Rollout、Judge、Report 抽象；
- Memory 机制：结合 Mem0 生命周期逻辑和 `slot_key` 设计；
- 系统形态：模块化单体，不为简历强行拆微服务。

### 4.2 后端与 Agent

- Python 3.11+
- FastAPI
- Pydantic
- asyncio
- Uvicorn
- LangGraph 或底座原生 Orchestrator
- Tool Registry
- Capability / Workflow Registry
- Structured Output
- JSON Schema 校验

### 4.3 数据存储

- PostgreSQL
- pgvector
- Redis：缓存、任务状态或短期会话
- 本地开发可使用 SQLite + Qdrant

### 4.4 评测与工程

- pytest
- pandas
- scikit-learn
- JSONL / Parquet
- Matplotlib
- Docker / Docker Compose
- GitHub Actions
- Ruff
- mypy
- pre-commit

### 4.5 可观测性

第一阶段：

- Structured Logging
- Trace ID
- LLM 调用次数、Token、延迟和错误记录

第二阶段：

- OpenTelemetry
- Prometheus
- Grafana

不优先引入 Kafka、Flink、Kubernetes 等缺少实际规模需求的重型组件。

---

## 5. 总体架构

```text
Web / CLI
   │
   ▼
Learning Orchestrator
   ├── Practice Capability
   ├── Diagnose Capability
   ├── Review Capability
   └── Plan Capability
   │
   ▼
Tool Registry
   ├── Question Retriever
   ├── Answer Grader
   ├── Knowledge Mapper
   ├── Memory Reader
   ├── Memory Writer
   └── Recommendation Tool
   │
   ▼
Learning Memory Lifecycle
   ├── Event Extractor
   ├── slot_key Normalizer
   ├── Candidate Retriever
   ├── Relation Classifier
   ├── Deterministic State Machine
   ├── Version / Provenance Manager
   └── Student Model Projector
   │
   ▼
PostgreSQL + pgvector
   │
   ▼
Evaluation Harness
   ├── Dataset Adapter
   ├── Rollout Runner
   ├── Trace Collector
   ├── Memory State Recorder
   ├── Evaluators
   └── Report Generator
```

---

## 6. Memory 设计

### 6.1 L1：学习事件

```json
{
  "event_id": "event_0001",
  "user_id": "user_001",
  "exam_id": "postgraduate_math_1",
  "question_id": "q_1024",
  "knowledge_point": "linear_algebra.eigenvalue",
  "difficulty": 0.7,
  "answer_correct": false,
  "error_type": "concept_confusion",
  "timestamp": "2026-08-05T20:00:00"
}
```

L1 只追加，不修改。

### 6.2 L2：结构化学习记忆

```json
{
  "memory_id": "memory_001",
  "slot_key": "mastery:math:linear_algebra:eigenvalue",
  "value": "weak",
  "confidence": 0.78,
  "evidence_count": 5,
  "lifecycle_state": "active",
  "version": 3,
  "valid_from": "2026-08-05T20:00:00",
  "valid_to": null,
  "superseded_by": null,
  "provenance": ["event_0001", "event_0004", "event_0008"]
}
```

### 6.3 L3：学生模型

包括：

- 当前薄弱知识点；
- 稳定掌握知识点；
- 稳定错因；
- 学习趋势；
- 推荐复习顺序；
- 当前学习计划；
- 解释风格偏好。

L3 必须能够由 L1、L2 重建，不能成为不可追溯的唯一真值。

---

## 7. `slot_key` 与 Scope

### 7.1 四维 Scope

本项目自行定义：

```text
scope = (
    user_id,
    exam_id,
    subject_id,
    memory_namespace
)
```

`session_id` 作为来源信息，不作为长期 Memory 隔离维度。

### 7.2 槽位格式

```text
mastery:<subject>:<knowledge_point>
error_pattern:<subject>:<knowledge_point>:<error_type>
plan:<exam>:<subject>
profile:target_school
preference:explanation_style
```

### 7.3 归一化要求

需要处理：

- 同义知识点；
- 知识点上下位关系；
- 命名格式不一致；
- 多知识点题目；
- 单值与多值属性；
- 跨会话命名漂移；
- 标空或低置信度结果的二次校正。

---

## 8. 生命周期状态机

### ADD

不存在对应 Memory，创建新记录。

### NO_OP

旧 Memory 已覆盖新信息，无有效变化。

### MERGE

新证据补充旧 Memory，例如增加证据数、置信度或错因细节。

### SUPERSEDE

新状态替代旧状态。旧记录归档，新记录通过版本关系接续。

### INVALIDATE

学习计划完成、取消或过期，Memory 失效。

### CONTESTED

新证据与长期状态冲突，但不足以安全覆盖，暂时保留争议状态。

状态机决策输入包括：

- 旧 Memory；
- 新事件；
- `slot_key`；
- 题目难度；
- 错误类型；
- 连续表现；
- 历史证据数量；
- 时间间隔；
- 模型置信度；
- 来源可靠性。

原则：

- LLM 负责语义提取与关系判断；
- 确定性状态机负责数据变更；
- 不物理覆盖旧内容；
- 所有替代保留版本关系；
- 低置信度证据不直接覆盖高置信度状态；
- 不确定时保守处理。

---

## 9. 评测框架

### 9.1 数据格式

```json
{
  "case_id": "case_001",
  "initial_memory": [],
  "events": [],
  "gold_operations": [],
  "gold_states": [],
  "queries": [],
  "gold_actions": []
}
```

### 9.2 场景覆盖

- 语义重复；
- 互补信息；
- 掌握度提升；
- 掌握度下降；
- 偶然错误；
- 稳定薄弱点；
- 显式纠错；
- 临时例外；
- 学习计划完成或取消；
- 多值错因；
- 相似知识点干扰；
- 跨会话更新；
- 长距离状态变化。

### 9.3 指标

#### 提取层

- Knowledge Point Accuracy
- Error Type Macro-F1
- `slot_key` Precision / Recall / F1

#### 生命周期层

- Operation Accuracy
- Operation Macro-F1
- False Merge Rate
- False Supersede Rate

#### 状态层

- Active State Exact Match
- Stale Memory Rate
- Duplicate Memory Rate
- Version Chain Accuracy
- Archived Memory Residual Rate

#### 检索与推荐层

- Weak-Knowledge Recall@K
- 错题召回率
- Archived Memory Hit@K
- 下一题知识点选择准确率
- 难度匹配准确率
- 过度复习率

#### 学习效果层

- 达到目标掌握度所需题目数
- 薄弱知识点覆盖率
- 已掌握知识点重复练习率
- 学习增益
- 计划完成率

#### 工程层

- LLM Calls
- Token Cost
- 平均延迟
- P95 延迟
- Memory Growth
- 单条更新成本

### 9.4 Baseline

1. No Memory
2. Append-only Memory
3. Vector Memory
4. 原生教育 Agent Memory
5. Legacy Mem0
6. ExamMem Lifecycle

---

## 10. 核心难点总结

### 10.1 偶然错误与真实薄弱点区分

单次错误不能直接证明能力下降。需要综合：

- 历史正确率；
- 题目难度；
- 错误类型；
- 连续表现；
- 时间间隔；
- 证据数量；
- 置信度；
- 时间衰减。

这是最适合作为项目核心算法点的问题。

### 10.2 `slot_key` 稳定性

风险：

- 同义命名；
- 粒度不一致；
- 标空；
- 多知识点题目；
- 跨会话漂移。

解决方向：

- 固定知识点 taxonomy；
- 规则归一；
- Embedding 辅助匹配；
- 低置信度二次校正。

### 10.3 错因聚合

需要区分：

- 重复错因；
- 互补错因；
- 上下位错因；
- 独立错误；
- 偶发粗心。

### 10.4 Memory 状态正确性

最终回答正确不代表 Memory 正确。必须直接检查：

- active 集合；
- archived 集合；
- stale 残留；
- 版本链；
- 检索误用。

### 10.5 评测数据构造

主要难点：

- 构造真实但可控的学生轨迹；
- 定义 Gold Operation；
- 定义 Gold Memory State；
- 避免模板化；
- 公平比较 Baseline；
- 区分模型能力和 Memory 能力。

### 10.6 强 LLM 掩盖 Memory 错误

强模型可能在状态错误时仍答对，因此必须分阶段评测：

```text
提取
→ 候选召回
→ 更新操作
→ 最终状态
→ 检索
→ 下游行为
```

### 10.7 `CONTESTED` 收敛

需要定义：

- 何时重新裁决；
- 需要多少新证据；
- 最终选择哪个状态；
- 如何归档旧状态；
- 如何避免争议长期堆积。

### 10.8 成本与准确率平衡

更多 LLM 调用可能提升效果，但会增加 Token、延迟和系统复杂度。需要通过候选召回、结构化规则、批处理和状态机控制成本。

### 10.9 版本、审计与回滚

需要保证：

- Memory 变更可追踪；
- 版本链无环；
- 重复执行幂等；
- 错误更新可恢复；
- 学生模型投影可以重建。

---

## 11. 一人开发边界

### 第一阶段：MVP

- 单科目；
- 基础刷题；
- L1/L2 Memory；
- 生命周期状态机；
- 50～100 条受控样本；
- 2～3 个 Baseline；
- 基础指标报告。

预计 2～4 周。

### 第二阶段：完整实验

- 100～500 条轨迹；
- 3～5 个 Baseline；
- 学生模型；
- 个性化推荐；
- 完整分层指标；
- 一个核心问题优化；
- Bad Case 报告。

预计 4～8 周。

### 暂不优先

- 多模态；
- 复杂多 Agent Swarm；
- 微服务拆分；
- 大规模分布式部署；
- Kafka / Flink / Kubernetes；
- 大规模用户实验；
- 强化学习训练。

---

## 12. 最终交付物

```text
exam_mem/
├── app/
├── agent/
├── tools/
├── memory/
│   ├── schema/
│   ├── slot_key/
│   ├── lifecycle/
│   ├── retrieval/
│   └── student_model/
├── evaluation/
│   ├── datasets/
│   ├── baselines/
│   ├── evaluators/
│   ├── runners/
│   └── reports/
├── tests/
├── configs/
├── docs/
└── docker/
```

最终交付：

- 项目代码；
- README；
- 架构图；
- 数据集；
- Baseline；
- 自动化评测；
- 指标报告；
- Bad Case 报告；
- Demo；
- 简历表述；
- 面试讲解稿。

---

## 13. 一句话总结

> 基于成熟教育 Agent 架构构建考研刷题场景，引入 `slot_key`、版本链和确定性生命周期状态机，持续维护用户的知识掌握度、稳定错因与学习计划，并通过分层、可归因的评测框架验证 Memory 更新是否真正改善后续选题、复习和学习效果。
