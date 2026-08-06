# 阶段 04：实现知识点 Taxonomy、`slot_key` 与四维 Scope

> 阶段性质：学习领域建模与身份归一。  
> 前置条件：[阶段 03](./03_非核心能力裁剪与Feature_Flag.md)验收通过。  
> 退出条件：数学一首批 taxonomy、稳定 `slot_key`、四维隔离和候选查询契约均通过测试。  
> 阶段门禁：标识符和隔离规则未稳定，不得进入[阶段 05](./05_Learning_Memory_Schema与存储.md)建表。

## 1. 阶段目标

`slot_key` 是低成本生命周期治理的地基：把“这两条记忆是否可能描述同一学习属性”从反复的语义比较，前移为写入时的一次结构化标注。若 key 标空或漂移，后续候选召回、冲突判断和状态更新都会漏判。

本阶段完成：

- 考研数学一首批知识点 taxonomy；
- canonical ID、别名、上下位关系和版本管理；
- `mastery`、`error_pattern`、`plan`、`profile`、`preference` 槽位规范；
- `user_id + exam_id + subject_id + memory_namespace` 四维 Scope；
- 规则优先、Embedding 辅助、低置信度复核的归一化链路；
- 严格 Scope 下的候选查询和隔离测试。

## 2. MVP 范围

### 2.1 科目与知识域

MVP 固定：

```text
exam_id    = postgraduate_entrance_exam
subject_id = math_1
domains    = linear_algebra, probability_statistics
```

微积分暂不进入正式轨迹，避免 taxonomy、题库和 Gold 同时膨胀。首批建议建立 20～30 个可出题、可判定的叶子知识点，而不是把教材章节标题全部当作叶子。

线性代数至少覆盖：矩阵运算、秩、线性方程组、向量组、特征值/特征向量、相似对角化、二次型。概率论至少覆盖：随机事件、条件概率、全概率/贝叶斯、随机变量分布、数字特征、大数定律/中心极限定理、参数估计。

### 2.2 Taxonomy 节点

```yaml
taxonomy_version: math1_v1
nodes:
  - id: math1.probability.conditional_probability
    name_zh: 条件概率
    parent_id: math1.probability
    aliases:
      - 条件概率公式
      - 条件概率计算
    prerequisites:
      - math1.probability.random_event
    status: active
```

约束：

- `id` 使用稳定英文小写点分路径，发布后不因中文改名而改变；
- 每个节点只有一个直接父节点，MVP 先保持树结构；
- 别名只负责命名归一，不表达“容易混淆”；
- 前置关系与父子关系分开；
- 节点废弃使用 `deprecated/replaced_by`，不直接删除；
- 每次变更增加 `taxonomy_version`，正式实验引用具体版本。

## 3. `slot_key` 规范

### 3.1 格式

```text
mastery:<canonical_knowledge_point_id>
error_pattern:<canonical_knowledge_point_id>:<error_type>
plan:<exam_id>:<subject_id>
profile:<attribute>
preference:<attribute>
```

示例：

```text
mastery:math1.linear_algebra.eigenvalue
error_pattern:math1.probability.bayes:reverse_condition
plan:postgraduate_entrance_exam:math_1
preference:explanation_style
```

`slot_key` 不重复编码 `user_id` 等 Scope 字段；Scope 由数据库列和查询条件保证。避免生成超长且难以索引的复合字符串。

### 3.2 单值与多值语义

- `mastery:<kp>`：当前状态单值，可被新状态替代；
- `plan:<exam>:<subject>`：当前主计划单值，完成后失效；
- `preference:<attribute>`：每个属性单值；
- `error_pattern:<kp>:<error_type>`：不同 error type 可共存，同一 type 聚合证据；
- 一次具体答题经历属于 L1 event，不创建“一题一个长期 slot”；
- 一道题涉及多个知识点时，一个 event 可映射多个 canonical ID，但每个 L2 Memory 只有一个主 slot。

### 3.3 Error Type 受控词表

MVP 使用固定枚举，LLM 输出必须映射到其中之一：

```text
concept_confusion
formula_misuse
condition_omission
calculation_error
reasoning_gap
reading_error
careless_error
unknown
```

新增类型必须说明它无法被现有类型表示，并补充 Gold 与回归样例；禁止让模型自由生成中文错因名称作为 key。

## 4. 四维 Scope

```python
class MemoryScope(BaseModel):
    user_id: str
    exam_id: str
    subject_id: str
    memory_namespace: Literal[
        "mastery", "error_pattern", "plan", "profile", "preference"
    ]
```

隔离规则：

1. 所有长期 Memory 查询必须提供完整 Scope，不允许只有 `user_id` 的模糊查询；
2. `session_id` 是 provenance，不是长期隔离维度；
3. 候选召回先做 Scope 和 active 状态过滤，再做 slot/向量匹配；
4. 不同用户、考试、科目或 namespace 的记录不能成为更新候选；
5. 管理与评测接口如需跨 Scope 查询，必须使用显式的独立方法，不能复用业务查询漏掉条件；
6. Trace 记录 Scope 哈希或脱敏摘要，便于排查泄漏。

建议为 Scope 提供不可变值对象和统一查询构造器，禁止各 Repository 手写四个条件。

## 5. 归一化技术路线

### 5.1 处理流水线

```text
题目/答案/错因
  → LLM 结构化提取候选名称和置信度
  → Unicode、空白、大小写和标点清理
  → canonical ID 精确匹配
  → alias 词典匹配
  → 父子粒度检查
  → Embedding top-k 辅助匹配
  → 阈值/间隔校验
  → 低置信度二次校正或 unknown
  → 生成 slot_key
```

规则优先级：精确 ID > 精确 alias > 受控规则 > Embedding。Embedding 只返回 taxonomy 中的候选，不能自行创建 canonical ID。

### 5.2 置信度决策

阈值必须在阶段 02 的 `dev` 数据上校准并写入版本化配置，不使用冻结测试集调参。初始策略：

- 精确 ID/唯一 alias：直接接受；
- Embedding 第一名达到接受阈值，且与第二名差值达到 margin：接受；
- 达到复核阈值但不满足 margin：将 top-k、题目上下文交给一次结构化复核；
- 仍不确定：输出 `unknown` 并记录，不猜测 key；
- `unknown` 事件仍进入 L1，待 taxonomy 或规则升级后可重放。

配置示意：

```yaml
normalization_policy: slot_normalizer_v1
embedding_top_k: 5
accept_threshold: <DEV_CALIBRATED>
review_threshold: <DEV_CALIBRATED>
top1_top2_margin: <DEV_CALIBRATED>
```

占位值必须由开发集实测回填，不得把未校准数字写成有效阈值。

### 5.3 多知识点题目

输出：`primary_knowledge_point_id`、`secondary_knowledge_point_ids` 和各自置信度。主知识点用于本次题目推荐归因；每个有充分证据的知识点可产生独立 Learning Memory 更新。禁止把多个 ID 拼成一个新 `slot_key`，否则知识点顺序变化会导致 key 漂移。

## 6. 候选查询契约

候选查询固定顺序：

```text
完整 Scope
  AND lifecycle_state IN (active, contested)
  AND slot_key = normalized_slot_key
  AND memory_id != current_id
  → 必要时再按时间、置信度或向量分数排序
```

对于 `mastery` 和 `plan`，同一 Scope + slot 原则上最多一个 active 主版本；`contested` 可临时有两个分支。对于 `error_pattern`，相同 error type 聚合，不同 type 并存。

向量检索不能绕过 Scope、active 状态或 taxonomy 限制。查询结果必须携带匹配原因：`exact_slot / alias_normalized / embedding_reviewed`。

## 7. 测试设计

### 7.1 Taxonomy 与归一化

- canonical ID、唯一父节点、无环、别名不冲突；
- “条件概率公式”归一为条件概率；
- “先验后验混淆”映射到贝叶斯相关知识点和受控错因；
- 相似的特征值与特征向量不被错误合并；
- 多知识点顺序变化不生成不同 key；
- 标空、乱码、未知知识点保守输出 `unknown`；
- taxonomy 版本升级保留旧 ID 的 `replaced_by`。

### 7.2 Scope 隔离

使用至少两个用户、两个 namespace、两个 subject 组成笛卡尔组合：

- 同 key、不同用户不可见；
- 同用户、不同考试不可见；
- 同用户同考试、不同科目不可见；
- 同知识点、不同 namespace 不冲突；
- archived 记录不进入普通候选；
- contested 只在本 Scope 内参与低权重裁决；
- 管理查询必须显式授权并留下审计记录。

### 7.3 指标

- `slot_key` Precision、Recall、F1；
- canonical knowledge point accuracy；
- unknown rate 和二次校正调用率；
- cross-scope leakage count；
- 候选召回率与错误候选率。

目标值：`slot_key` F1 ≥ 0.85，Scope 隔离测试 100% 通过、泄漏数为 0。它们是阶段目标，不是当前实测成果。

## 8. 引导式编程任务

### 任务 A：亲自设计 taxonomy

从 20 道数学一题目中手工提取知识点，先比较“教材章节”和“可诊断叶子节点”的差别，再建立首版树。AI 只帮助检查遗漏、重复和粒度漂移。

### 任务 B：测试先行实现 Normalizer

先写同义词、上下位、相似概念、多知识点和 unknown 的表格驱动测试，再实现纯规则层，最后才接 Embedding 复核。说明为什么不能一开始全部交给 LLM。

### 任务 C：制造一次 Scope 泄漏

在测试数据库插入同 key 的多个用户记录，故意遗漏一个过滤条件，观察失败测试，再通过统一 Scope 查询构造器修复。用面试语言解释“应用层过滤为什么不够”。

## 9. 运行命令模板

```powershell
# Taxonomy 结构、别名和版本校验
python -m exam_mem.cli taxonomy validate --version math1_v1

# 在协议/开发样例上评测归一化
python -m evaluation.cli evaluate-slot --split protocol_check --taxonomy math1_v1

# 运行 Scope 和候选查询测试
pytest -m "taxonomy or slot_key or scope" -q
```

阈值仍为占位时只输出校准报告，不允许把占位值当成正式策略发布。

## 10. 交付物与验收

### 10.1 交付物

- `math1_v1` taxonomy 和版本说明；
- alias、error type 受控词表；
- `MemoryScope`、`SlotKey`、Normalizer 接口；
- 归一化策略配置与低置信度处理说明；
- 候选查询契约；
- 单元、属性、隔离和 Golden 测试结果；
- taxonomy 变更与回放 Runbook。

### 10.2 验收标准

| 验收项 | 目标值 |
| --- | --- |
| Taxonomy 结构校验 | 100% 通过、无环、无重复 ID |
| Alias 冲突 | 0 个未解释冲突 |
| `slot_key` F1 | ≥ 0.85（目标值） |
| Scope 隔离 | 100% 测试通过 |
| Cross-Scope Leakage | 0 |
| unknown 保守处理 | 不生成虚构 canonical ID |
| 多知识点稳定性 | 输入顺序不影响单独 slot |

回滚方式：按 `taxonomy_version` 和 `normalization_policy` 回退；L1 原始事件不变，L2/L3 可在旧版本下重建。

## 11. 提交清单与 Git 门禁

- [ ] 代码：taxonomy、类型、Normalizer 和 Scope 查询构造器；
- [ ] 测试结果：结构、归一化、Golden、Scope 和泄漏测试；
- [ ] 运行命令：校验 taxonomy、评测 slot 和运行隔离测试；
- [ ] 交付物：第 10.1 节全部存在；
- [ ] 已知问题：unknown、易混知识点和待扩展微积分范围；
- [ ] 独立 Git Commit：不混入数据库 Schema 或状态机。

建议 Commit Message：

```text
feat(domain): add math1 taxonomy slot keys and scoped lookup
```

## 12. 面试复盘卡

你应能回答：

1. `slot_key` 如何降低 LLM 调用成本，又引入了什么单点风险？
2. 为什么 Scope 不直接拼入 `slot_key`？
3. Embedding 为什么只能辅助归一，而不能自由创建知识点？
4. 多知识点题目如何避免 key 漂移？
5. taxonomy 如何版本化并支持旧数据重建？

推荐表述：

> 我先固定数学一 taxonomy 和四维 Scope，再设计数据库。归一化采用规则优先、Embedding 辅助和低置信度复核；所有候选查询先做 Scope 与生命周期过滤，从结构上防止跨用户、跨科目污染。
