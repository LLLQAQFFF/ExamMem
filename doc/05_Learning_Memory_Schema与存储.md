# 阶段 05：实现 Learning Memory Schema 与存储

> 阶段性质：持久化模型、迁移与数据一致性。  
> 前置条件：[阶段 04](./04_知识点Taxonomy_slot_key与四维Scope.md)验收通过。  
> 退出条件：PostgreSQL + pgvector 中的 L1/L2/L3 Schema、迁移、Repository、CRUD 和重建通过测试。  
> 阶段门禁：Schema 和事务不稳定，不得进入[阶段 06](./06_生命周期状态机与审计.md)。

## 1. 阶段目标

将阶段 02 的类型和阶段 04 的标识符落到可约束、可迁移、可重建的数据库模型中。MVP 从 PostgreSQL + pgvector 开始，避免先做 SQLite 再处理方言、并发和向量差异；Redis 暂不进入核心链路。

三层语义固定为：

- **Learning L1**：只追加的答题与学习事件，事实来源；
- **Learning L2**：按 `slot_key` 组织、带版本链和生命周期的结构化记忆；
- **Learning L3**：面向推荐和查询的学生模型投影，可由 L1/L2 重建。

这套 Learning L1/L2/L3 与 DeepTutor Native Memory 是并行系统，不能共用表名、路径或更新语义。

### 1.1 范围

本阶段只实现 PostgreSQL + pgvector 的 Schema、迁移、Repository、CRUD、事务与 L3 重建；不实现生命周期决策、推荐、Redis、分布式任务或多数据库兼容。审计表只建立最小契约，具体决策与变更写入留到阶段 06。

## 2. 存储边界

### 2.1 采用

- PostgreSQL；
- pgvector 扩展；
- 与底座技术栈一致的异步数据库驱动和 Repository；
- Alembic 或阶段 01 审计后确认的迁移工具；
- Docker Compose 本地数据库；
- pytest 集成测试使用独立测试库或事务隔离。

### 2.2 暂不采用

- Redis 缓存和任务状态；
- 读写分离、分库分表；
- Kafka/Flink；
- 多数据库兼容层；
- 自动物理删除；
- 为未知规模提前建立复杂分区。

## 3. 技术路线与 Schema 设计

### 3.1 `learning_events`：L1 只追加事件

核心字段：

```text
event_id UUID/ULID PRIMARY KEY
idempotency_key TEXT NOT NULL
user_id / exam_id / subject_id
session_id / question_id
knowledge_point_ids JSONB
primary_knowledge_point_id TEXT
difficulty DOUBLE PRECISION CHECK 0..1
answer_correct BOOLEAN
error_type TEXT NULL
raw_payload JSONB
occurred_at TIMESTAMPTZ
created_at TIMESTAMPTZ
trace_id TEXT
schema_version INTEGER
```

约束：

- `(user_id, idempotency_key)` 唯一，防止同一事件重复摄入；
- L1 不绑定单一 `memory_namespace`；一个事件通过 provenance 可派生多个不同 namespace 的 L2 更新；
- `knowledge_point_ids` 必须通过应用 Schema 校验，数据库至少保证非空数组；
- 不提供普通业务 Update/Delete 方法；
- 更正通过追加 correction event 表达，不覆盖原始事件；
- 原始输入保留在脱敏后的 `raw_payload`，避免未来无法重放。

### 3.2 `learning_memories`：L2 版本化记忆

核心字段：

```text
memory_id UUID/ULID PRIMARY KEY
user_id / exam_id / subject_id / memory_namespace
slot_key TEXT NOT NULL
value JSONB NOT NULL
confidence DOUBLE PRECISION CHECK 0..1
evidence_count INTEGER CHECK >= 1
lifecycle_state TEXT
version INTEGER CHECK >= 1
row_version INTEGER CHECK >= 1
valid_from TIMESTAMPTZ
valid_to TIMESTAMPTZ NULL
superseded_by UUID NULL
contested_group_id UUID NULL
content_embedding VECTOR(<MODEL_DIMENSION>) NULL
policy_version TEXT NOT NULL
created_at / updated_at TIMESTAMPTZ
```

四维 Scope 从 L2 开始严格生效：`user_id / exam_id / subject_id / memory_namespace` 必须同时参与候选、唯一约束和普通业务查询。

`<MODEL_DIMENSION>` 必须从锁定 Embedding 模型的实测维度生成迁移，不在文档中猜测数值。

约束与索引：

- 外键 `superseded_by → learning_memories.memory_id`；
- 同一 Scope、slot、version 唯一；
- 普通稳定状态下，同一 Scope + slot 最多一个 active 主版本；
- contested 分支通过 `contested_group_id` 显式关联，不破坏 active 唯一性策略；
- Scope + slot + lifecycle_state 复合 B-tree 索引用于候选查询；
- 向量索引只用于已通过 Scope 过滤的候选扩展；
- `valid_to >= valid_from`；
- archived/invalidated 记录必须有终止时间和原因。

部分唯一索引的具体 SQL 要根据 contested 状态建模验证后确定，但必须由数据库约束关键不变量，不能只靠应用层判断。

### 3.3 `memory_provenance`

```text
memory_id FK
event_id FK
relation_type: created_by | merged_from | contradicted_by | invalidated_by
created_at
PRIMARY KEY(memory_id, event_id, relation_type)
```

禁止只在 L2 的 JSON 数组中保存 provenance；关系表支持外键、追溯和增量重建。API 可将其聚合为 `provenance: list[str]`。

### 3.4 `student_model_snapshots`：L3 投影

```text
snapshot_id
user_id / exam_id / subject_id
model JSONB
projection_version
source_event_watermark
source_memory_watermark
created_at
```

L3 不是唯一真值：删除 L3 快照后，投影器必须能从 L1/L2 重建等价状态。初期可只保存最新快照和必要历史，不引入复杂流处理。

### 3.5 审计预留

本阶段创建生命周期需要的最小表契约，具体写入在阶段 06 完成：

- `lifecycle_decisions`：输入摘要、候选、operation、reason、policy version；
- `memory_change_log`：before/after、apply state、trace、时间；
- 所有审计记录只追加。

## 4. Repository 与事务

### 4.1 Repository 边界

```python
class LearningEventRepository(Protocol):
    async def append(self, event: LearningEvent) -> AppendResult: ...
    async def list_after(self, scope, watermark, limit) -> list[LearningEvent]: ...

class LearningMemoryRepository(Protocol):
    async def find_candidates(self, scope, slot_key, *, for_update=False): ...
    async def insert_version(self, memory: LearningMemory): ...
    async def compare_and_archive(self, memory_id, expected_row_version, ...): ...
    async def snapshot(self, scope): ...

class StudentModelRepository(Protocol):
    async def save_projection(self, model: StudentModel): ...
    async def get_latest(self, scope): ...
    async def clear_projection(self, scope): ...
```

业务层不得拼接 SQL，也不得绕过 Repository 直接更新生命周期字段。

### 4.2 写入事务边界

一次 Learning Memory 更新的目标事务：

```text
append L1（若幂等重复则读取已有事件）
→ 读取/锁定 L2 候选
→ 保存 Lifecycle Decision
→ CAS 应用归档/插入新版本
→ 写 provenance 与 change log
→ 提交事务
→ 事务后触发/标记 L3 投影更新
```

L3 投影失败不回滚已提交的 L1/L2，但必须记录失败并可从 watermark 重试。L2 写入、版本关系、provenance 和审计日志必须同事务，避免“状态变了但证据链缺失”。

### 4.3 幂等语义

- 同一 `(user_id, idempotency_key)` 重放返回原 event，不新增 L1；
- 同一 event 和 policy version 重放不得生成第二条 L2 版本；
- Repository 返回 `created / existing / conflict`，调用方不得用捕获所有异常模拟幂等；
- 幂等键来自稳定业务事件 ID，不使用随机重试 ID。

## 5. 迁移策略

### 5.1 初始迁移

迁移顺序：扩展 → 枚举/约束 → L1 → L2 → provenance → L3 → audit → 索引。每次迁移在空库和含测试数据的上一版本库上验证。

### 5.2 Upgrade/回退

- Upgrade 先扩展兼容字段，再部署读写代码；
- 破坏性列删除不进入 MVP；
- 回退脚本只回退本阶段创建且确认无生产数据依赖的对象；
- 有数据后优先向前修复，不自动删除表；
- 回退前导出 Schema 和行数快照；
- pgvector 不可用时 `lifecycle`/`vector` 模式快速失败，不偷偷换存储。

### 5.3 L3 重建

提供一个按 Scope 重建的管理命令：

```text
validate scope
→ 清理/标记旧投影
→ 按 watermark 顺序读取 L1/L2
→ 生成 StudentModel
→ 保存新 projection_version
→ 比较重建前后差异
```

重建必须可重复，固定输入和 policy version 下结果一致。

## 6. 测试设计

### 6.1 单元与 Schema

- Pydantic 正反例；
- confidence、difficulty、version 边界；
- 非法 lifecycle state；
- 缺 Scope 字段；
- JSON value 与 namespace 不匹配；
- 时间区间非法。

### 6.2 数据库集成

- 空库 upgrade 到 head；
- 上一迁移 upgrade 到 head；
- downgrade/upgrade 循环不破坏测试数据；
- L1 普通 Update/Delete 被禁止；
- 幂等键并发插入只产生一条事件；
- 同 Scope/slot 非法双 active 被数据库拒绝；
- 不同 Scope 相同 slot 可共存；
- provenance 外键和级联策略符合预期；
- pgvector 写入、距离查询和 Scope 前置过滤；
- 事务中途失败时 L2、provenance、审计全部回滚。

### 6.3 重建与一致性

- 删除 L3 后可以重建；
- 同输入重建两次结果相同；
- watermark 不丢事件、不重复消费；
- L2 的 evidence_count 与 provenance 聚合一致；
- archived 版本仍可审计但不进入 active 查询。

## 7. 引导式编程任务

### 任务 A：先写不变量再建表

列出“L1 不可变、同 slot 单 active、版本连续、provenance 可追溯”等不变量，逐项决定由数据库、应用或两者共同保证。先写会失败的数据库测试，再写迁移。

### 任务 B：实现幂等追加

从两个并发请求使用同一 idempotency key 开始，观察唯一约束冲突，设计稳定的 `AppendResult`。解释为什么“先查再插”仍有竞态。

### 任务 C：做一次故障注入

在写 L2 后、写 provenance 前注入异常，验证整个事务回滚。再在 L3 投影阶段注入异常，验证 L1/L2 保留且投影可重试。

AI Review 重点：是否过度建模、是否只依赖应用约束、是否存在跨 Scope SQL、是否把 L3 当唯一真值。

## 8. 运行命令模板

迁移工具和包名根据阶段 01 的实际技术栈回填：

```powershell
# 启动数据库并检查 pgvector
docker compose up -d postgres

# 迁移、回退和重新升级
alembic upgrade head
alembic downgrade -1
alembic upgrade head

# 数据库集成、事务与重建测试
pytest -m "database or migration or repository" -q
python -m exam_mem.cli memory rebuild --user <USER_ID> --subject math_1 --dry-run
```

对含数据环境执行 downgrade 前必须备份；`--dry-run` 先输出影响 Scope 和差异，不直接覆盖投影。

## 9. 交付物

- Docker Compose 数据库配置和脱敏示例；
- 数据库迁移、ER 图与字段字典；
- L1/L2/L3 Pydantic 模型和 Repository；
- CRUD、幂等、事务、向量与重建命令；
- 迁移、集成和一致性测试结果；
- 备份、升级、回退和重建 Runbook；
- 已知限制与容量假设。

## 10. 验收标准

| 验收项 | 目标值 |
| --- | --- |
| 空库迁移到 head | 100% 通过 |
| Upgrade/回退测试 | 100% 通过 |
| L1 不可变与幂等 | 关键测试全部通过 |
| 同 Scope/slot 稳定状态约束 | 非法状态被数据库拒绝 |
| 跨 Scope 共存 | 100% 通过 |
| 事务原子性 | 故障注入后无半写入 |
| L3 重建 | 固定输入结果一致 |
| 密钥与连接串 | 0 个进入 Git |

回滚方式：应用关闭 `lifecycle`/`vector` 模式，数据库按已验证迁移回退；若已有重要数据，保留表并向前修复，不执行破坏性自动清理。

## 11. 提交清单与 Git 门禁

- [ ] 代码：迁移、模型、Repository、事务和重建入口；
- [ ] 测试结果：Schema、迁移、并发幂等、事务和重建；
- [ ] 运行命令：启动数据库、迁移、测试、备份和重建；
- [ ] 交付物：第 9 节全部存在；
- [ ] 已知问题：规模假设、未启用 Redis、向量模型限制；
- [ ] 独立 Git Commit：不包含生命周期策略实现。

建议 Commit Message：

```text
feat(memory): add versioned learning memory schema and repositories
```

## 12. 面试复盘卡

你应能回答：

1. 为什么 L1 只追加，错误数据如何更正？
2. 哪些不变量应由数据库保证，哪些由状态机保证？
3. 为什么“先查再插”不能保证并发幂等？
4. 为什么 L3 投影失败不应回滚 L1/L2？
5. pgvector 在本项目中解决什么问题，又不能替代什么？

推荐表述：

> 我把原始事件、版本化状态和可重建学生模型分别建模为 L1/L2/L3，并用唯一约束、事务和 provenance 保证可追溯性。L3 只是投影，失败后可以从 L1/L2 按 watermark 重建。
