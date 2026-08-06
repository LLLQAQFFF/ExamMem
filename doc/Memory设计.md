# 记忆生命周期管理：成本与污染两大矛盾的工程解法分析

> **文档性质**：对一套已投产的 Agent 长期记忆引擎的机制分析，面向"如何低成本、低污染地维护
> 长期记忆库"这一研究问题。
> **说明**：文中代码片段为实现要点的去标识化摘录，标识符已泛化，逻辑结构与原实现一致。

---

## 0. 摘要

学界主流的记忆管理方案把 LLM 当作**判官**：每来一条新记忆，调用 LLM 判断它是否有价值、
与哪些旧记忆相关、是否冲突、该怎么合并。这带来两个结构性矛盾：

| 矛盾 | 表述 |
|------|------|
| **准确性 vs 效率** | 判断越充分，LLM 调用、检索与维护成本越高 |
| **可塑性 vs 稳定性** | 既要及时接受新事实，又不能因一次误判永久破坏已有正确记忆 |

该系统的解法可以压缩成一句话：

> **把 LLM 从"判官"降级为"一次性的结构化标注器"；冲突判定交给确定性的 `slot_key` 状态机；
> 写入用 CAS + 归档，而非覆盖 + 删除。**

三个动作分别对应三层收益：标注器化把 LLM 调用摊薄到 `O(对话窗口)` 而非 `O(记忆条数)`；
状态机化让绝大多数冲突判定退化为字符串比较，零 LLM、零向量；CAS + 归档让任何一次误判
在物理上都是可追溯、可撤销的状态位翻转，而不是不可逆的内容覆写。

**同时必须说明**：本文档描述的机制中，有一部分（八阶段治理 Pipeline）已完成骨架但**尚未接入
主链路**，且**业务级回滚入口目前缺失**。详见 [§8 实现落差](#8-实现落差如实标注)。

---

## 1. 总体架构：两条时间线

理解成本控制的前提，是先看清写入路径被拆成了**同步**与**离线**两条时间线。

```
                     ┌─────────────────────── 同步路径（在线，用户等待） ──┐
对话轮次 ──► 会话队列 ──► [窗口聚批] ──► ① LLM 统一提取 ──► ② 批量向量化
                                                              │
                                                              ▼
                                             ③ 候选召回 + ④ LLM 冲突重写 ──► 写库
                                                                              │
                     ┌─────────────────────── 离线路径（后台巩固） ──────────┘
                     │
              ⑤ 确定性冲突分类（零 LLM）──► ⑥ LLM 批量裁决（仅冲突组）
                     │
                     ▼
              ⑦ CAS 归档 + 血缘 + 变更日志 ──► ⑧ 投影失效 → 场景摘要重建
```

**同步路径**的窗口摄入入口是一个四阶段流程，源码里的阶段注释直接对应上图 ①②③④：

```
阶段二：统一提取（事实 + 分类 + 场景标签）
阶段二.5：batch embed 全部 fact 一次
阶段三：路由写库
阶段四：user_profile 归纳检查
```

**离线路径**由后台巩固调度器驱动，分 light / deep / rem 三档，冲突治理挂在 light 档。

**这个拆分本身就是第一层成本控制**：昂贵的全库级冲突扫描不在用户等待的路径上，
而是在后台按用户批量做。同步路径只做"局部去重"，离线路径做"全局治理"。

---

## 2. 核心机制一：`slot_key` —— 通用结构化表示

这是整套低成本方案的地基。没有它，后面所有的确定性判定都不成立。

### 2.1 定义

`slot_key` 是一条记忆在"实体-属性"空间中的坐标。它的判定标准写死在提取 prompt 里：

> **判定标准**：事实是否描述了"<某实体>的<某属性> = <某值>"，且该属性**只允许有一个当前值**
> （新值会替代旧值）？
> - 是 → 填具体 slot_key
> - 否 → 填 `""`（一次性事件/经历、多值列表、通用知识/第三方事实）

格式为英文小写 + 下划线，可选 `<domain>:<attribute>` 或 `<entity>.<attribute>`：
`home_address` / `mentor` / `job_role` / `spouse` / `preference:cuisine` / `user.phone_number`。

### 2.2 为什么这个设计能同时解决两个矛盾

`slot_key` 的精妙之处在于它把"是否可能冲突"这个语义问题，**在提取时一次性转化成了一个
可索引的符号**：

- **有 `slot_key`** ⇒ 该属性单值 ⇒ 新值必然取代旧值 ⇒ 进冲突通道
- **无 `slot_key`** ⇒ 一次性事件 / 多值 / 第三方事实 ⇒ **物理上不可能冲突** ⇒ 直接判 stable

第二条是成本控制的关键。用户的历史经历（"去年夏天去了冰岛"）、多值偏好（"喜欢跑步"+"喜欢
游泳"）根本不需要任何冲突判断——它们可以无限共存。这一刀砍掉的是记忆库里占比最大的那部分。

对应的候选过滤只有两个条件：

```c
/* 过滤条件：active + 有 slotKey */
if (IsActiveAtom(atom) && HasSlotKey(atom)) {
    AddCandidate(batch, atom);
}
```

### 2.3 与之配套的其它结构化维度

`slot_key` 不是孤立的，同一次提取还产出三个正交维度，共同构成冲突判定的坐标系：

| 维度 | 取值 | 作用 |
|------|------|------|
| `slot_key` | 自由符号或 `""` | 冲突的**主键**：同 key 才可能冲突 |
| `memory_type` | semantic / episodic / procedural / prospective / mentalizing / generic | 冲突的**类型隔离**：跨类型不比较 |
| `category` | preference / personal_history / daily_event / todo / short_intent / long_intent / user_profile / general_fact | 决定走哪条差异化管线与哪张扩展表 |
| `scene_tag` | 英文稳定 ID | 场景归属，供聚类与摘要用 |

此外每个 `category` 还强制输出该分类专属的**结构化时间字段**（prompt 明确要求"所有字段必须
输出，无法确定时填 `""`，不要猜测"）：

| category | 结构化字段 |
|----------|-----------|
| preference / user_profile | `historical_time`（当前有效填 `"now"`） |
| personal_history / daily_event / general_fact | `time` |
| todo | `deadline` |
| short_intent / long_intent | `expected_start`、`expected_duration` |

这些字段是后续时间约束判定的输入，同样**零额外 LLM 调用**——它们搭了提取那一次调用的便车。

---

## 3. 核心机制二：LLM 调用预算的实际分布

这是回答"成本矛盾"最直接的证据。逐个统计写入路径上的 LLM 调用点。

### 3.1 调用点清单

| # | 调用点 | 粒度 | 摊到每条记忆 |
|---|--------|------|------------|
| ① | 窗口统一提取 | **1 次 / 对话窗口**，输出 N 条记忆 | **1/N 次** |
| ② | 批量向量化 | **1 次 / 窗口**（batch embed 全部 fact） | 1/N 次 |
| ③ | 同步冲突重写 | 1 次 / 新记忆（1 新 + ≤10 老候选打包） | ≤1 次，且仅在有候选时 |
| ④ | 离线冲突裁决 | **1 次 / 冲突组**（批量输入） | ≪1 次，且仅冲突组 |
| ⑤ | 场景涌现 / 摘要 | 1 次 / 场景 | 与记忆条数解耦 |

**关键在于 ① 的合并**。传统方案里"判断价值"→"分类"→"打标签"→"提取结构化字段"是四次调用，
这里合并成一次：一个窗口的对话进去，一个 JSON 数组出来，每个元素自带
`content / category / scene_tag / slot_key` + 分类专属字段。

② 同样重要但常被忽略——向量化是按窗口批量做的，不是逐条 embed。

### 3.2 冲突裁决为什么是"批量"而非"两两"

③ 的 prompt 输入结构是 **1 新 + N 老**，一次调用完成 N 次比较：

```json
{
    "m_id": "000001",
    "memory": "用户叫小红，职业是护士",
    "related_memory": [
        {"m_id": "000002", "memory": "用户叫小白，喜欢吃火锅，职业是老师"},
        {"m_id": "000003", "memory": "用户叫小红"},
        {"m_id": "000004", "memory": "用户喜欢玩王者荣耀"}
    ]
}
```

输出一次性给出合并后的新内容与所有应作废的旧 ID：

```json
{"updateContent": "用户叫小红，职业是护士，喜欢吃火锅", "invalidMid": ["000002", "000003"]}
```

prompt 里还明确约束"`related_memory` 中的任意两条之间**无需**两两比较"——
把 O(N²) 的比较空间显式压到 O(N)。

④ 更进一步，输入是一整批 active 记忆，输出 `conflict_groups[]` 数组，
一次调用完成全库级分组裁决。

### 3.3 硬预算：调用次数是有上限的

单次治理运行有显式的资源天花板：

```c
typedef struct GovernanceBudget {
    int maxCandidates;
    int maxConflictLlmCalls;   /* 冲突裁决 LLM 调用上限 */
    int maxSceneLlmCalls;      /* 场景摘要 LLM 调用上限 */
    int maxPromptBytes;
    int64_t deadlineMs;
} GovernanceBudget;
```

超限的终态是 `BUDGET_EXHAUSTED` —— 退出并等下一轮，**不是无限重试**。
这让最坏情况下的成本可预测。

---

## 4. 核心机制三：确定性冲突判定

有了 `slot_key`，冲突判定的主体退化成纯算法。

### 4.1 判定式

冲突判定的核心只有三个条件的纯字符串比较：

```c
static int HasSlotConflict(const ConflictAtom *seed, const ConflictAtom *related)
{
    const char *seedKey    = ConflictKey(seed);
    const char *relatedKey = ConflictKey(related);
    return seed->recordId != related->recordId        /* 不是同一条 */
        && seed->memoryType == related->memoryType    /* 同记忆类型 */
        && NonEmptyTextEquals(seedKey, relatedKey);   /* slot_key 非空且相等 */
}
```

**没有向量运算，没有 LLM，没有语义相似度。** 分类主循环也极简：

```c
for (int i = 0; i < seedCount; ++i) {
    const ConflictAtom *old = FindConflict(&seeds[i], related, relatedCount);
    int rc = (old == NULL) ? AddStableId(outResult, seeds[i].recordId)
                           : AddConflictGroup(outResult, &seeds[i], old);
}
```

找不到同 key 冲突 → `stable`，一次 LLM 都不调；找到 → 进 `conflict_group`，
才交给 ④ 批量裁决。

### 4.2 作用域约束：冲突只在隔离域内比较

冲突判定还叠加了两层隔离，进一步缩小比较空间：

- **memoryType 隔离**：见上式，跨类型不比较（语义记忆不会和情景记忆冲突）
- **四维 scope 隔离**：作用域哈希参与所有查询的 `WHERE` 条件，
  同一用户在不同作用域的记忆互不可见

同步路径的候选召回过滤器把这两层写在一起：

```c
filter->userId          = record->userId;
filter->matchUserId     = 1;
filter->matchMemoryType = 1;
filter->memoryType      = record->memoryType;
filter->stableView      = 1;     /* 只看 active，排除已归档 */
filter->includeArchived = 0;
```

`stableView` 这一位很关键：**已归档的记忆不参与新一轮冲突判定**，
避免"复活"已被取代的旧事实。

### 4.3 时态归一化：另一类确定性处理

时态冲突（"上周三" vs "2026-07-22"）通常被认为必须靠 LLM 消解。
该系统用规则吃掉了一部分：在入库**前**把相对时间替换成绝对日期。

两类模式：

```c
/* 静态模式表 */
static const StaticPattern kStatic[] = {
    { "yesterday", -1 }, { "the day before yesterday", -2 },
    { "today", 0 },      { "tomorrow", 1 },
    { "last week", -WEEK_DAYS },
    { "last month", -MONTH_DAYS },
    { "last year", -YEAR_DAYS },  /* ... */
};

/* 动态模式：N day(s)/week(s)/month(s)/year(s) ago */
static int TryDynamicAgo(const char *text, size_t remaining,
                         size_t *matchLen, int *dayOffset);
```

扫描在词边界进行，避免误匹配。中文侧的相对时间归一化则由提取 prompt 承担
（"相对时间→绝对时间"规则，含农历/公历不混用的约束），同样搭 ① 的便车。

配合 schema 上的**双时间轴**（事实发生时刻 `occur_time`、记忆写入时刻 `create_time`），
以及 slot 更新裁决 prompt 里的 `valid_from_ms` 时间锚，构成了完整的时间约束体系：

```
新 claim 时间锚 > 旧 + 内容互斥 + 置信度更高  →  supersede
时间锚相近且内容不互斥                        →  coexist
新 claim 置信度显著低于旧                     →  keep_old
三者均不显著                                  →  contested=true，保留双方
```

---

## 5. 核心机制四：防污染的五道防线

这一节回答第二个矛盾。设计的总原则是：**LLM 说了不算**。它的每一个决定都要过确定性复核。

### 防线① LLM 拿不到真实 ID

系统不把数据库主键暴露给 LLM，而是分配 6 位顺序编号：

```c
#define NEW_DISPLAY_INDEX  1   /* 新记忆恒为 000001 */
#define OLD_DISPLAY_BASE   2   /* 老候选从 000002 起 */

static int CandidateDisplayIndex(int candidateIndex)
{
    return candidateIndex + OLD_DISPLAY_BASE;
}
```

回程严格校验并映射（要求恰好 6 位纯数字，且必须落在候选池范围内）：

```c
static int DisplayCandidateIndex(int displayIndex, int count)
{
    int idx = displayIndex - OLD_DISPLAY_BASE;
    if (idx < 0 || idx >= count) {
        return -1;          /* 越界即拒绝 */
    }
    return idx;
}
```

**效果**：LLM 幻觉出的编号落不到任何真实记忆上。这从结构上消除了"误删一条无关记忆"这类
最严重的污染形态——LLM 的破坏力被限制在本次候选池的 ≤10 条之内。

### 防线② LLM 的裁决要过确定性阈值复核

即使 LLM 说"作废 000003"，这个决定还要过一道分数门禁：

```c
float bestScore = 0.0F;
if (!ValidateInvalidIds(invalid, pool, count, &bestScore)
    || bestScore < NormalizeConfig(input->config).decisionThreshold) {
    return OK;              /* 整个 plan 丢弃，hasDecision 保持 0 */
}
```

两级阈值分工明确：

| 常量 | 值 | 作用 |
|------|-----|------|
| `CANDIDATE_THRESHOLD` | 0.1 | 决定谁能**进候选池**给 LLM 看 |
| `DECISION_THRESHOLD` | 0.2 | 决定 LLM 的作废裁决能否**生效** |
| `TOP_K` | 10 | 候选池上限 |

含义是：**LLM 认为冲突，但向量空间不支持这个判断时，以向量为准，拒绝执行。**
这是一个明确的"双签名"机制——语义判断与统计判断都同意才动手。

### 防线③ 所有失败路径都偏向"不动老记忆"

失败语义是系统性的保守：

| 失败情形 | 处理 |
|---------|------|
| LLM 不可用 / 调用失败 | 记 WARN，返回 OK，无 decision |
| LLM 输出非法 JSON | 解析返回 NULL → 返回 OK |
| `invalidMid` 非数组或为空 | 返回 OK |
| `invalidMid` 含非法编号 | 校验失败 → 丢弃整个 plan |
| `updateContent` 为空串 | 直接返回 OK |
| 分数不达标 | 丢弃整个 plan |

所有分支的共同结果是：**新记忆照常写入，老记忆一个字节都不动**。

即使是最上游的提取环节也遵循同样方向——LLM 提取失败时降级为"整窗原文兜底"，
把整个窗口作为一条未分类记忆存下来。**宁可存原文，不丢数据。**

### 防线④ 归档而非删除

冲突裁决的结果不是 `DELETE`，而是状态位翻转：

```c
atom->lifecycleState = "archived";
atom->supersededBy   = seedId;              /* 指向接替者 */
atom->deleteReason   = "conflict_archive";
atom->deleteTime     = nowMs;
```

**旧记忆的 `content` 一个字节都没改。** 加上随后写入的归档血缘行：

```c
ArchiveLineageRow row = {
    .recordId     = oldId,
    .archivedAtMs = nowMs,
    .archivedBy   = "conflict",
    .reason       = reason,
};
```

数据层面保留了完整的取代链：`旧记忆 --supersededBy--> 新记忆`，可正向追溯也可反向回溯。

### 防线⑤ 写入是 CAS，不是盲写

这是最硬的一道。归档应用阶段实现了完整的乐观并发控制：

```c
/* 1. 写前重读权威行 */
status = port.Load(&identity, &current);

/* 2. 幂等检查：已是目标状态则直接返回，重放安全 */
if (ArchiveIdempotent(&current, planned)) {
    affected->applyState = APPLY_IDEMPOTENT;
    return;
}

/* 3. CAS 校验：与计划时的快照逐字段比对（含 rowVersion） */
if (!SnapshotMatches(&current, planned)) {
    affected->applyState = APPLY_STALE;
    affected->errorCode  = ERR_CONFLICT;
    return;                 /* 拒绝写入 */
}

/* 4. 执行 + 结果复验 */
status = port.Archive(&command, &updated);
if (status == OK && ArchiveResultValid(&current, planned, &updated)) {
    SetArchiveApplied(&updated, affected);
} else {
    affected->applyState = APPLY_FAILED;
}
```

快照比对是全字段的：

```c
return SliceEquals(current->uid, expected->uid)
    && SliceEquals(current->atomId, expected->atomId)
    && SliceEquals(current->lifecycleState, expected->lifecycleState)
    && SliceEquals(current->supersededBy, expected->supersededBy)
    && current->rowVersion == expected->rowVersion;   /* 行版本号 */
```

**含义**：从"计划裁决"到"执行裁决"之间，如果这条记忆被任何其它路径改过（`rowVersion` 变了），
本次归档就**作废重来**，绝不覆盖别人的写入。

此外还有第三种动作 `HOLD_CONTESTED` —— 裁决拿不准时两版都留，降权参与场景合成：

```c
if (planned->action == ACTION_HOLD_CONTESTED) {
    affected->applyState       = APPLY_CONTESTED;
    affected->sceneEligibility = SCENE_INCLUDE_LOW_WEIGHT;
    return 1;                   /* 不做任何破坏性写 */
}
```

这是对"可塑性 vs 稳定性"矛盾的直接应答：**拿不准时不做选择，而是保留歧义并降权**，
把决定权推迟到证据更充分的时候。

---

## 6. 核心机制五：生命周期状态机与审计

### 6.1 状态机

记忆的生命周期是一个显式的、字段化的状态机，而非隐式的存在/不存在：

```
                  ┌──────────────┐
   写入 ─────────►│    active    │
                  └──────┬───────┘
                         │ 冲突裁决 / GC 衰减 / 用户删除
                         ▼
                  ┌──────────────┐
                  │   archived   │  + supersededBy = <接替者 id>
                  └──────┬───────┘  + deleteReason = <动因>
                         │ GC 物理清理           + deleteTime
                         ▼
                    （行消失，但变更日志保留全量快照）
```

治理层还定义了更完整的动作与状态枚举：

| 枚举 | 取值 |
|------|------|
| 冲突裁决 | NO_CONFLICT / KEEP_NEW / KEEP_OLD / KEEP_BOTH / ARCHIVE_OLD / **CONTESTED** |
| 计划动作 | KEEP_ACTIVE / ARCHIVE / **HOLD_CONTESTED** |
| 应用状态 | NOT_ATTEMPTED / APPLIED / **IDEMPOTENT** / **CONTESTED** / **STALE** / SKIPPED / FAILED |

加粗的几个是防污染的关键状态——它们让"没能安全执行"成为一等公民，而非被吞掉的异常。

### 6.2 三层审计

| 层 | 载体 | 记什么 | 状态 |
|----|------|--------|------|
| 阶段级 | 阶段日志端口 | 八阶段每阶段的 in/out 计数、状态、失败分类、耗时 | 已实现 |
| 决策级 | 决策日志（Decision Journal） | plan / checkpoint / result 三类 snapshot，可重放 | 已实现，未接线 |
| 字段级 | 变更日志表 | 逐行 before/after diff | 设计完成（见下） |

变更日志表的设计要点：

- **8 列全 TEXT**，消除多后端类型漂移；变更时刻零填充 13 位使字典序 = 时序
- **变更 ID = `%013lld-%08u`**（毫秒 + 进程内自增序号），单列即是完备的续拉游标
- **变更明细**是字段级 diff JSON，`created` 只有 `after`，`updated` 只含变化字段，
  **`deleted` 记录全部纳管字段的 `before`**——这使得源行消失后，日志仍是该记忆最后形态的
  完整快照
- **纳管字段白名单**（原子层 10 项 / 场景层 5 项），源表加列默认**不进**日志
- **隐私分流**：系统删除保留明细；用户主动删除置明细为 NULL
  **并连带清除该行的全部历史日志**

这份设计对"可审计"的支撑是充分的：给定任一条记忆，可以按行 ID 顺时序播放它的全部
字段变化，还原完整演化过程。

---

## 7. 与研究问题的逐项映射

原研究问题：

> 如何在不对每条 memory 进行多次 LLM 调用的条件下，通过**通用的结构化表示**、**候选索引**、
> **时间与作用域约束**以及**确定性状态机**，低成本地判断新旧记忆关系并执行**安全、可审计、
> 可回滚**的生命周期更新，同时控制错误覆盖造成的长期污染。

| 研究要素 | 对应实现 | 状态 |
|---------|---------|------|
| 不对每条 memory 多次 LLM 调用 | 窗口级统一提取（1 次/N 条）+ 批量向量化 + 批量冲突裁决 | ✅ 已实现 |
| 通用的结构化表示 | `slot_key` + `memory_type` + `category` + `scene_tag` + 分类专属时间字段 | ✅ 已实现 |
| 候选索引 | 向量/文本 topK 召回 + 候选阈值预筛 + 稳定视图过滤 | ✅ 已实现 |
| 时间约束 | 规则化时态归一 + 双时间轴 + `valid_from_ms` 时间锚 | ✅ 已实现 |
| 作用域约束 | 四维作用域哈希隔离 + `memory_type` 隔离 | ✅ 已实现 |
| 确定性状态机 | `lifecycle_state` + `supersededBy` 版本链 + CAS + 8 种应用状态 | ✅ 已实现 |
| **安全**（不误伤） | 五道防线：ID 隔离 / 阈值复核 / 保守失败 / 归档非删除 / CAS | ✅ 已实现 |
| **可审计** | 阶段日志 + 决策日志 + 字段级变更日志 | ⚠️ 部分未接线 |
| **可回滚** | 数据结构完备（archived + supersededBy + before 快照），**无业务级入口** | ❌ **缺失** |
| 控制长期污染 | 上述全部 + `HOLD_CONTESTED` 歧义保留 | ✅ 已实现 |

---

## 8. 实现落差（如实标注）

分析必须区分"设计里有"和"线上在跑"。以下三点是该系统的真实状态。

### 8.1 八阶段治理 Pipeline 尚未接入主链路

代码中定义了完整的八阶段治理框架：

```
CANDIDATE_SOURCE → ATOM_HYDRATOR → CONFLICT_PLANNER → DECISION_JOURNAL
    → ATOM_APPLIER → PROJECTION_INVALIDATOR → SCENE_PROJECTOR → CANDIDATE_FINALIZER
```

配套能力包括 SHADOW/ACTIVE 双模式、运行租约互斥、11 级 checkpoint 恢复、
预算控制、能力位声明。但入口函数的文档注释自己写明：**该入口只提供内部 Pipeline 能力，
不接入任何一条线上巩固路径**。

代码检索证实：该治理入口的调用方**只有单元测试**。

线上实际运行的是两条更轻的路径：

| 路径 | 特征 |
|------|------|
| fast 路径 | slotKey 预筛 + 单次 LLM 裁决 + 直接归档 |
| governance 路径 | 纯算法分类 + 直接归档 + 血缘写入 |

这两条路径**没有** CAS 复核、没有决策日志、没有 shadow 模式。
也就是说，§5 的防线⑤（CAS）目前只存在于未接线的框架里；
线上归档走的是"读-改-写"，缺少行版本比对。

> **对研究的含义**：这套设计的完整形态是自洽的，但"已验证可运行"的部分要打折。
> 引用时应区分"架构设计"与"生产实测"。

### 8.2 没有业务级回滚

全仓检索恢复/撤销类接口，只命中存储层的 SQL 事务 `Rollback` ——
那不是业务语义的"撤销一次记忆裁决"。

数据结构是支持回滚的：

- `archived` 是可逆的状态位，`content` 未被破坏
- `supersededBy` 保存了取代关系，可反查
- 变更日志的 `deleted` 事件保存了全部纳管字段的 `before` 值
- 归档血缘记录了归档动因与时刻

**但没有任何一个 API 把这条路走通。** 这是设计与实现之间最明显的一处缺口，
也恰好是原研究问题中点名的要素。

### 8.3 `slot_key` 是地基，也是单点软肋

冲突 key 的推导里有一段硬编码兜底：

```c
static int IsHomeAddressContent(const char *text)
{
    return strstr(text, "家地址") != NULL || strstr(text, "家庭住址") != NULL
        || strstr(text, "新地址") != NULL || strstr(text, "换房") != NULL
        || strstr(text, "搬到了") != NULL;
}

static const char *ConflictKey(const ConflictAtom *atom)
{
    if (atom->slotKey[0] != '\0') {
        return atom->slotKey;
    }
    if (IsHomeAddressContent(atom->content)) {
        return "home.address";
    }
    return "";
}
```

这段代码本身是个 demo 味很重的补丁，但它**诚实地暴露了方案的软肋**：

> 整条低成本链路的地基是 `slot_key`，而 `slot_key` 由 LLM 在提取时**一次性**标注。
> 标错或标空，冲突就直接漏判，且当前**没有二次校正机制**。

这是"把成本前移"的必然代价：省下了后续所有的 LLM 判断，
代价是把全部准确性风险压在了那一次提取调用上。上面那五个中文关键词，
就是这个风险在真实数据上暴露后打的第一个补丁。

---

## 9. 机制评价与研究机会

### 9.1 这套设计真正的贡献点

抛开工程细节，有两个想法是可迁移的：

**① 成本结构的重构：把"判断"前移为"标注"。**
传统方案在**写入后**反复问 LLM"这两条冲突吗"，成本随记忆库规模增长。
本方案在**写入时**问一次"这条记忆的属性坐标是什么"，之后所有冲突判断都是坐标比对。
成本从 `O(N²) 次 LLM 调用` 降到 `O(窗口数) 次 LLM 调用 + O(N²) 次字符串比较`。
**关键洞察是：冲突性是记忆的固有属性，可以在提取时一次性判定，而不必等到比较时。**

**② 防污染协议：LLM 决策必须过确定性复核 + CAS 写入。**
"LLM 说要删，但向量相似度不支持 → 拒绝执行"这个双签名机制，
以及"从计划到执行之间行版本变了 → 作废重来"的 CAS，
共同把 LLM 的错误从"永久污染"降级为"本次操作失败"。
配合归档而非删除，任何单次误判都不产生不可逆后果。

### 9.2 可做贡献的缺口

| 缺口 | 说明 | 研究价值 |
|------|------|---------|
| **`slot_key` 召回率保障** | 目前无二次校正，标空即漏判 | 高。可研究：基于向量聚类的 slot_key 反向补全、跨记忆的 slot 一致性校验 |
| **回滚闭环** | 数据完备但无入口 | 中高。可研究：基于变更日志的记忆库时间旅行、误判检测后的自动回退 |
| **阈值的自适应** | `0.1 / 0.2` 是硬编码常量 | 中。可研究：按 slot_key 类型 / 用户历史裁决准确率动态调整 |
| **CONTESTED 的收敛** | 保留歧义后，何时、凭什么证据消解未定义 | 高。这是"可塑性 vs 稳定性"矛盾最本质的部分 |
| **污染的事后检测** | 目前只有事前防御，无事后审计告警 | 高。变更日志提供了数据基础，可研究基于变更序列的异常裁决检测 |

其中 **CONTESTED 的收敛**最值得做——现有设计只做到"拿不准就都留着并降权"，
但没有回答"留着的两个版本，什么时候、依据什么证据、由谁来消解"。
这正是记忆可塑性与稳定性矛盾的核心，而当前方案是把它悬置了。
