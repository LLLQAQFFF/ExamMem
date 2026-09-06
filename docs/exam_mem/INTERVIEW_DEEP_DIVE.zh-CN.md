# DeepTutor × ExamMem 技术架构、源码走读、评测与面试指南

> 基线：当前仓库 `main`，ExamMem migration head 为 `0014_textbook_grounding`。
>
> 这份文档只描述当前源码事实。`ExamMem-local-archive/2026-08-18` 中的材料可作历史参考，但其中“尚未实现教材 grounding”等结论已经过时。

本文是 DeepTutor × ExamMem 架构、实现、评测和面试准备的**唯一综合入口**，已合并原
`TECHNICAL_OVERVIEW.zh-CN.md` 的技术全景与最新实验口径。阅读时可以按目的选择路径：

- 快速准备项目介绍：第 0、1、19、20、24 节；
- 理解 DeepTutor 框架：第 2～7 节；
- 理解 ExamMem Memory：第 8～15 节；
- 理解教材 PDF 到 RAG：第 16～18 节；
- 准备源码追问：第 21～23 节。

文中的数字分为论文实验、ExamMem 离线受控实验和工程测试。三者回答的问题不同，不能把
某个局部高分解释成整个产品的准确率或真实学习增益。当前评测口径更新至 2026-08-28。

## 0. 先把三个最容易混淆的概念分开

项目里至少有三种都可能被口头叫作“记忆”的东西：

| 名称 | 解决什么问题 | 数据位置 | 读取方式 | 更新方式 |
| --- | --- | --- | --- | --- |
| 会话上下文（Conversation Context） | 当前聊天怎样带上历史消息且不撑爆上下文窗口 | Session Store 中的消息、压缩摘要 | `ContextBuilder` 按 token budget 组织历史 | 新消息正常持久化；历史过长时由摘要 Agent 压缩 |
| DeepTutor Native Memory | 通用助教记住跨会话偏好、近期事实、画像和范围信息 | 用户工作区的 L1 JSONL、L2/L3 Markdown | 当前实现是 L3 四个文档全文拼接，不做向量 top-k | 显式 `write_memory` 或 Memory Workbench 的 L2/L3 consolidation |
| ExamMem Learning Memory | 对“这个学生在这个考试/学科/知识点上的掌握状态”建立可审计业务事实 | 独立 PostgreSQL + pgvector | 精确 slot、语义检索、命名空间快照、L3 投影按场景分别读取 | 练习状态机提交正式评分事件，再执行生命周期决策和事务更新 |

面试时第一句话就应主动区分它们。否则后面即使细节都对，也会给人“数据模型没有想清楚”的感觉。

---

## 1. 30 秒总览答案

### 1.1 DeepTutor 是什么框架

DeepTutor 没有用 LangChain、LangGraph 或 CrewAI 做 Agent 编排，而是自研了一套轻量 Agent Runtime：

```text
CLI / WebSocket / Python SDK
          ↓
DeepTutorApp / TurnRuntimeManager
          ↓  构造 UnifiedContext
ChatOrchestrator
          ↓  按 active_capability 路由
Capability（拥有整轮控制权）
          ↓
AgentLoop（多轮 LLM tool-calling）
          ↔ ToolRegistry / ToolDispatcher
          ↓
StreamBus → thinking/content/tool/result/done 等统一事件
```

其中 Tool 是一次函数调用，Capability 是接管整轮的多阶段工作流。默认 `chat` Capability 使用专门的 `AgentLoop`；`deep_solve` 等能力也可以使用带标签协议的通用循环。

#### 完整理解：这里的“框架”到底是什么

这里的框架不是某一个 `Agent` 类，而是一套把“接收一次用户请求、组织上下文、选择处理流程、调用模型和工具、持续输出结果、保存会话”串起来的运行时。它同时规定扩展协议和执行边界，使 CLI、网页和 Python SDK 不需要各自实现一套 Agent。

可以按七层理解：

| 层 | 中文职责 | 关键对象 |
| --- | --- | --- |
| 入口层 | 接收 CLI、WebSocket 或 SDK 请求，转换为统一请求 | CLI、统一 WebSocket API、`DeepTutorApp` |
| 单轮运行层 | 创建 turn、校验配置和权限、恢复历史、处理附件并持久化消息 | `TurnRuntimeManager` |
| 上下文层 | 把用户消息、历史摘要、附件、知识库、Memory、人格和配置放进同一对象 | `UnifiedContext` |
| 编排层 | 根据 `active_capability` 选择本轮唯一的 Capability，默认选择 `chat` | `ChatOrchestrator`、`CapabilityRegistry` |
| 能力层 | 接管整轮控制权，决定阶段、模型调用、工具范围和结束条件 | `BaseCapability` 及其实现 |
| 模型与工具执行层 | 运行“模型调用 → 工具调用 → 工具结果回填 → 再调用模型”的循环 | `AgentLoop`、`ToolDispatcher`、`ToolRegistry` |
| 事件与持久化层 | 把执行过程转换为统一流事件，供网页、CLI、SDK 展示和会话存储 | `StreamBus`、`StreamEvent`、Session Store |

以默认聊天为例，一次完整调用是：

```text
用户通过网页发送消息
  → TurnRuntimeManager 创建 session/turn，校验模型与工具权限
  → ContextBuilder 按 token budget 恢复历史
  → 附件、知识库、Memory、人格等被组装进 UnifiedContext
  → ChatOrchestrator 从 CapabilityRegistry 选择 chat
  → ChatCapability 创建 AgenticChatPipeline 和 AgentLoop
  → AgentLoop 直接调用 LLM
      ├─ LLM 没有请求工具：流式输出最终回答并结束
      └─ LLM 请求工具：ToolDispatcher 校验并调用 ToolRegistry
             → ToolResult 作为 role=tool 消息放回同一段对话
             → AgentLoop 再调用 LLM
  → 执行期间产生的内容、工具、进度、来源和错误事件写入 StreamBus
  → TurnRuntimeManager 订阅事件，分配 seq，推给客户端并持久化 assistant 消息
  → Orchestrator 发出 done，turn 更新为 completed/failed/cancelled
```

这套框架有两种扩展点：

- 新增 Tool：实现一次原子能力，并注册到 `ToolRegistry`；模型可以在循环中按需调用。
- 新增 Capability：实现一整轮工作流，并注册到 `CapabilityRegistry`；编排器可以把一个 turn 路由给它。

因此所谓“Agent-native”不是“所有事情都交给模型自由发挥”，而是把模型循环、工具调用、确定性阶段、流式事件、会话恢复和插件扩展都放在明确协议下。Capability 可以包含自由度较高的 AgentLoop，也可以像 ExamMem Practice 一样，在内部运行严格状态机，只把评分或分类等狭窄环节交给 LLM。

### 1.2 ExamMem 是什么框架

ExamMem 是 DeepTutor 上的领域插件，不是把所有业务交给自由 Agent：

```text
DeepTutor Host（会话、流、模型、插件边界、RAG）
          ↓
deeptutor_plugins/exam_mem（适配层）
          ↓
exam_mem（考试领域、练习状态机、Learning Memory、PostgreSQL）
```

正式练习由可恢复、可幂等、带 CAS checkpoint 的确定性状态机驱动。LLM 只出现在评分、错误诊断、少数记忆关系分类等有明确输入输出契约的边界；状态推进、持久化和生命周期策略由代码决定。

### 1.3 Memory 怎么做

ExamMem 用三层 Learning Memory：

- L1：不可变学习事件，例如一次正式作答、显式纠正、计划迁移。
- L2：按四维 scope 和稳定 slot 维护的版本化业务记忆，有 active / archived / invalidated / contested 生命周期。
- L3：从 L1 + L2 可重建的 Student Model 投影，用于快速读取薄弱点、已掌握点、错误模式和计划。

核心原则是：L1/L2 是业务真相，L3 是可丢弃投影；更新候选先做精确 slot 匹配，不用相似向量“猜”要修改哪条记忆。

---

## 2. DeepTutor 的两层插件模型

### 2.1 Level 1：Tool

源码契约：`deeptutor/core/tool_protocol.py`

Tool 的特征：

- 是一个受 JSON Schema 约束的单次调用。
- 暴露 `get_definition()` 和 `execute()`。
- 返回 `ToolResult`，包括 `content`、`sources`、`metadata`、`success`。
- 可以设置 `terminate_loop` 终止循环。
- 可以设置 `pause_for_user` 暂停当前 turn，例如 `ask_user`。

Tool 本身不拥有整轮状态机。比如 `rag` 只负责检索，是否调用、检索后是否再调用其他工具、何时回答，由 AgentLoop 决定。

### 2.2 Level 2：Capability

源码契约：`deeptutor/core/capability_protocol.py`

Capability 的特征：

- 实现 `run(context, stream)`。
- 有 `CapabilityManifest`，声明名称、阶段、工具、配置 schema、session surface。
- 接管一次用户 turn 的完整执行过程。
- 可以是一个 Agent 循环，也可以是确定性多阶段 pipeline。

内置 Capability 的注册入口是：

- `deeptutor/runtime/bootstrap/builtin_capabilities.py`
- `deeptutor/runtime/registry/capability_registry.py`

插件也可以贡献 Capability，所以 `ChatOrchestrator` 不需要知道 ExamMem 的具体实现。

### 2.3 为什么要分两层

这是一个很好的面试设计题：

- Tool 适合原子的、模型按需选择的能力。
- Capability 适合有阶段、有恢复语义、有专属 UI/配置、需要接管一整轮的流程。
- 如果把 `deep_research` 或正式考试练习塞成一个 Tool，生命周期、阶段进度、失败恢复和最终结果协议都会变得模糊。
- 如果把每个检索函数都做成 Capability，又会让路由和组合成本过高。

---

## 3. 一次普通聊天请求到底经历什么

以下是从入口到持久化的真实调用链。

### 第 1 步：入口归一化

关键文件：

- `deeptutor/app/facade.py`
- `deeptutor/api/routers/unified_ws.py`
- `deeptutor/services/session/turn_runtime.py`

CLI、WebSocket 和 SDK 最终通过 `DeepTutorApp.start_turn()` 进入 `TurnRuntimeManager.start_turn()`。

`TurnRuntimeManager.start_turn()` 会：

1. 解析请求的 Capability，并确定它使用哪个 session surface。
2. 校验 Capability 的公开配置；运行时私有字段单独保留。
3. 创建或恢复 Session。
4. 恢复 session 级的 persona、模型选择、knowledge bases、context sources 等偏好。
5. 创建持久化 Turn 记录。
6. 启动后台 `_run_turn()`，调用方通过订阅接口接收流式事件。

它还负责进程重启后的“孤儿 running turn”处理：数据库仍是 running、但本进程已经没有执行任务时，将其标记为失败，而不是永远挂住。

### 第 2 步：准备上下文

`TurnRuntimeManager._run_turn()` 会依次准备：

- 附件存储与文本抽取。
- 当前分支的聊天历史。
- Notebook、引用的历史会话、书籍、题库等上下文。
- 用户显式选择的 Native Memory。
- 插件贡献的 session context，例如 ExamMem learning context。
- Persona、Skills、Source manifest。
- 本轮模型配置与 `ask_user` 的恢复队列。

然后组装 `UnifiedContext`。契约位于 `deeptutor/core/context.py`，主要字段包括：

```text
session_id / user_message / conversation_history
enabled_tools / active_capability
knowledge_bases / attachments / config_overrides
language / memory_context / persona_context
skills_manifest / source_manifest / context_blocks / metadata
```

这里有一个重要边界：`UnifiedContext` 是 Host 的中立协议。ExamMem 通过 `context_blocks` 贡献经过授权、限长的文本，而不是让 DeepTutor core import ExamMem 模型。

### 第 3 步：路由 Capability

关键文件：`deeptutor/runtime/orchestrator.py`

`ChatOrchestrator.handle()`：

1. 取 `context.active_capability`，缺省为 `chat`。
2. 从 `CapabilityRegistry` 获取实例。
3. 为这一 turn 创建并注册 `StreamBus`。
4. 后台执行 `capability.run(context, bus)`。
5. 前台持续 yield 总线事件。
6. 无论成功失败，发出带状态的 `DONE` 并关闭总线。
7. 最后向全局 EventBus 发布 `CAPABILITY_COMPLETE`。

这里把“业务执行”和“事件传输”分开：Capability 只向 StreamBus 发事件，不直接操作 WebSocket。

### 第 4 步：默认 Chat Capability 进入 AgentLoop

关键文件：

- `deeptutor/agents/chat/capability.py`
- `deeptutor/agents/chat/agentic_pipeline.py`
- `deeptutor/agents/chat/agent_loop.py`
- `deeptutor/agents/chat/prompt_blocks.py`

`ChatCapability` 委托给 `AgenticChatPipeline`。Pipeline 会：

1. 解析 deferred provider tools。
2. 建立知识库和执行环境的挂载标志。
3. 计算本轮真正可用的工具集合。
4. 从 ToolRegistry 生成 OpenAI tool schemas。
5. 注入知识库、源文件、session 等工具运行参数。
6. 组合系统提示词。
7. 创建并运行专用 `AgentLoop`。

系统提示词不是一个巨型字符串，而是按块组合：通用规则、runtime、loop protocol、Capability、persona、partner、memory、插件 context、tools、skills、sources 等。

### 第 5 步：工具怎样挂载

关键文件：`deeptutor/agents/_shared/tool_composition.py`

工具集合不是固定全开，大体按以下顺序合成：

1. 用户显式启用的可选工具。
2. 上下文满足时自动挂载的工具，例如有 KB 才挂 `rag`。
3. Capability 自己拥有的工具。
4. 始终可用或由策略决定的工具，例如 `write_memory`、`ask_user`。

典型 gating：

- 有知识库才挂 `rag`。
- 有源文件 manifest 才挂 `read_source`。
- L3 Memory 非空才挂 `read_memory`。
- sandbox 可用才挂 `exec` / `code_execution`。
- Mastery Path 相关工具由路径上下文决定。

这样既减少 tool schema 占用的上下文，也避免模型调用当前不可能成功的工具。

### 第 6 步：AgentLoop 每一轮怎样跑

默认 Chat 的专用循环可以理解为：

```python
messages = system_prompt + history + current_user_message
for round in budget:
    response = stream_llm(messages, tools)
    if response.has_tool_calls:
        messages.append(assistant_tool_calls)
        results = await dispatch_tools(response.tool_calls)
        messages.extend(results_as_role_tool)
        continue
    return response.text
force_final_answer_without_tools()
```

具体行为：

- 一次“轮”是一次模型流式调用，不等于一个用户 turn。
- 模型输出普通 token 时，转换成 thinking/content 流事件。
- 模型输出 tool-call delta 时，增量拼接工具名和 JSON 参数。
- 有工具调用：先把 assistant tool-call 消息加入 conversation，再把工具结果以 `role=tool` 加入，继续下一轮。
- 无工具调用：这一轮就是最终答案。
- exploration 默认有有限预算；随后还有 settlement 预算；耗尽时强制禁用工具，要求模型结算最终答案，避免无限循环。
- `ask_user` 返回 pause 后，循环等待 runtime 注入的 reply queue；收到用户回复后继续同一个 turn。
- 上下文窗口守卫会在每轮模型调用前检查消息长度。

### 第 7 步：ToolDispatcher 怎样执行

关键文件：`deeptutor/core/agentic/tool_dispatch.py`

Dispatcher 的责任：

1. 规范化模型产生的参数。
2. 去除同一批中的重复调用。
3. 校验 schema 中的必填参数。
4. 为每个调用发 trace / tool-call 事件。
5. 在并发上限内用 `asyncio.gather` 执行相互独立的工具。
6. 把结果转换成标准 `role=tool` 消息。
7. 汇总 sources、pause 和 terminate 信号。

工具错误通常会成为一个失败的 tool result，让模型有机会解释或改正，而不是直接把整个 turn 重跑。后者可能重复执行有副作用的工具。

### 第 8 步：事件和答案持久化

关键文件：

- `deeptutor/core/stream.py`
- `deeptutor/core/stream_bus.py`
- `deeptutor/services/session/turn_runtime.py`

事件类型覆盖 session、stage、thinking、content、tool call/result、progress、sources、result、error、wait input、done。

`TurnRuntimeManager` 一边把事件推给订阅者，一边保存事件序列；完成后把可见 content 拼成 assistant message，保存生成附件，并根据终态事件更新 turn 状态。客户端断线重连可以从持久化序号后继续 replay。

---

## 4. DeepTutor 不只有一种 AgentLoop

### 4.1 默认 Chat 专用循环

`deeptutor/agents/chat/agent_loop.py` 为聊天场景处理了流式 tool calls、暂停恢复、thinking 标签、结算预算和 provider 差异，是当前默认 `chat` 的真实执行器。

### 4.2 通用标签协议循环

`deeptutor/core/agentic/loop.py`、`labeled_step.py` 定义另一套可复用循环：

- `LoopHost` 适配模型和工具执行。
- `LabelProtocol` 定义 intermediate、final、tool、terminal 等标签。
- 标签不合法时可以 protocol repair。
- 到达最大迭代数后强制 finalize。

它适合 solve 等需要显式阶段/标签语义的 Capability。面试时不要说“项目只有一个 AgentLoop”，也不要把通用循环误说成默认 chat 当前使用的循环。

### 4.3 `BaseAgent`

`deeptutor/agents/base_agent.py` 提供模型配置、提示词加载、流式/非流式调用、重试和 token 统计等公共能力。具体业务 Agent 实现 `process()`。它是公共 Agent 基类，不等于整个 Runtime 编排框架。

---

## 5. 会话历史如何构造与压缩

关键文件：`deeptutor/services/session/context_builder.py`

### 5.1 为什么它不等于长期记忆

会话历史回答的是“这段对话前面说了什么”，生命周期跟 session/分支绑定；长期记忆回答的是“跨会话需要保留什么”。二者的选择、证据和更新条件都不同。

### 5.2 ContextBuilder 的预算

当前策略把模型有效上下文窗口的一部分分配给 history。大致流程：

1. 沿当前消息分支取历史，而不是无脑取 session 的所有分叉。
2. 估算消息 token 数。
3. 在预算内直接使用原始历史。
4. 超预算时保留较新的消息，把较老前缀交给 `_ContextSummaryAgent`。
5. 保存压缩摘要及摘要覆盖到的消息 ID。
6. 后续继续在已有摘要上增量折叠；为了抑制摘要漂移，条件允许时重新从原始前缀构建。

摘要是有损的上下文压缩，不应作为 ExamMem 正式掌握度的证据。

---

## 6. DeepTutor Native Memory：从 L1 到 L3

关键目录：`deeptutor/services/memory/`

### 6.1 文件布局

逻辑布局如下：

```text
memory root/
├── trace/<surface>/<date>.jsonl       # L1 原始 trace，append-only
├── L2/chat.md                         # 各 surface 的归纳记忆
├── L2/notebook.md
├── L2/quiz.md / kb.md / book.md / ...
├── L3/recent.md
├── L3/profile.md
├── L3/scope.md
└── L3/preferences.md
```

具体路径由 `deeptutor/services/memory/paths.py` 管理，不应在业务代码里拼路径。

### 6.2 L1：Trace

`trace.py` 以 JSONL 追加 `TraceEvent`：

- 每个 surface/date 独立文件。
- 每个 surface 有异步锁，避免同进程并发追加互相覆盖。
- trace 写失败不会拖垮原业务生产者。

这里追求“低侵入地记录原始活动”，不是强事务业务账本。因此它和 ExamMem 的 PostgreSQL L1 不能混为一谈。

### 6.3 Snapshot：识别哪些事实是新的

Memory snapshot adapters 会读取聊天、Notebook、书籍、Partner、KB 等 Host 数据，形成稳定 entity 和 fingerprint，并保存：

```text
snapshot/<surface>/state.json
snapshot/<surface>/changes.jsonl
```

Consolidator 根据当前 snapshot 与 metadata 中的 seen ID 做增量，而不是每次重新把所有原始数据喂给模型。

### 6.4 L2 文档格式

`document.py` 定义可解析、可幂等序列化的 Markdown 文档：

- section 下是事实 bullet。
- 每条事实有稳定 `m_<ULID>` ID。
- 脚注保存来源引用。
- parse → serialize 保持稳定。

`ops.py` 以 add/edit/delete 原子操作更新文档：先验证整批操作，任一冲突则拒绝整批，避免写出半份状态。

### 6.5 L2 如何更新

`consolidator/modes/update.py` 的 L2 流程：

1. 读取该 surface 的 snapshot。
2. 根据 seen entity refs 计算未处理 entity。
3. 按时间排序。
4. 按 token budget 切 chunk，并带有限 overlap。
5. LLM 从 chunk 中抽取严格 JSON facts。
6. 校验模型返回的引用只能来自允许的 entity pool。
7. 转成 `AddOp`，原子写入 L2 Markdown。
8. 每个 chunk 成功后更新 checkpoint/seen metadata。
9. 可选执行 dedup 和 merge。

因此模型负责“从原始材料抽取候选事实”，代码负责引用约束、文档操作和 checkpoint。

### 6.6 L3 如何更新

L3 consolidation 类似，但输入变为所有 L2 文档的新条目：

1. 读取各 surface 的 L2。
2. 根据 L3 metadata 找出未见过的 L2 entry ID。
3. 分块抽取 recent/profile/scope 等高层事实。
4. L3 引用的是 L2 surface；证据链仍可回到 L2 脚注和 L1 实体。
5. 原子更新文档与 checkpoint。

`preferences.md` 是例外：用户偏好主要由 `write_memory` 显式写入，不依赖自动 consolidation。

#### 为什么设计成三层，可以增加更多层吗

三层不是因为“三”本身特殊，而是当前系统正好有三种不同的数据职责：

| 层 | 保留的信息 | 优先目标 | 为什么不能由相邻层替代 |
| --- | --- | --- | --- |
| L1 原始轨迹 | 每个 surface 的原始事件和稳定来源 ID | 完整、便宜写入、可追溯 | 如果只保存摘要，模型漏掉或误写的内容无法复核 |
| L2 分来源记忆 | chat、quiz、book 等各自的长期事实摘要及 L1 引用 | 降噪、去重、保持来源边界 | 直接让 L3处理全部 L1，上下文会随历史增长，成本和噪声都过高 |
| L3 跨来源画像 | recent、profile、scope、preferences 四类高价值信息 | 快速读取、跨来源综合 | 只读 L2 会把每个 surface 的大量细节都注入每次对话，增加 token 和冲突 |

所以三层解决的是三个相互冲突的目标：L1 保真，L2 压缩，L3 面向读取。证据链仍然可以从 L3 回到 L2，再回到 L1；压缩结果出错时也能重新 consolidation，而不是失去原始依据。

技术上当然可以增加层数，但不能因为“数据更多了”就机械地增加 L4。新增一层必须具有独立且稳定的语义，例如不同的读取对象、权限边界、保留周期或聚合范围。每多一层都会带来：

- 新的增量 checkpoint 和幂等问题；
- 更长的 provenance 链；
- consolidation 延迟和模型成本；
- 摘要误差逐层累积；
- 删除、纠错和跨层一致性更加困难。

以当前 Native Memory 为例，L3 只有四份有界 Markdown，仍可全文注入，因此没有必要再增加 L4。未来如果 L3 也大到不能完整读取，更自然的第一选择通常是给 L3 增加检索、排序或按需加载，而不是再做一次有损摘要。只有出现明确的跨用户组织画像、不同权限的团队记忆等新语义边界时，才有理由设计新层，并同时定义来源、更新、失效和重建契约。

### 6.7 Native Memory 怎样读

`MemoryStore.read_l3_concat()` 会把 `recent/profile/scope/preferences` 四份 L3 Markdown 拼接起来。

当前不是以下流程：

```text
query embedding → vector top-k → rerank
```

而是：

```text
用户选择 Memory 或模型调用 read_memory
            ↓
读取四份 L3 文档全文
            ↓
拼接后注入模型上下文
```

有两条入口：

- 前端请求带非空 `memory_references`：`TurnRuntimeManager` 在调用模型前 eager 注入全文。
- L3 非空时自动挂载 `read_memory`：模型可以在 AgentLoop 中按需调用；工具同样返回 L3 全文。

目前 individual memory reference 更像 UI/意图提示；任一非空选择都会触发完整 L3 concat，并没有逐引用过滤。

### 6.8 Native Memory 怎样写

`write_memory` 只接受用户明确要求保存的偏好：

1. Agent 判断用户明确表达“记住……”并调用工具。
2. 工具记录 preference trace。
3. `MemoryStore.write_preference()` 原子更新 `L3/preferences.md`。
4. 完全相同的 add 会去重为 no-op。

普通聊天不会自动把每句话写成长期偏好，这是防止污染记忆的重要策略。

### 6.9 Workbench

Memory Workbench API 可以启动 update、audit、dedup、merge 等后台 run，并提供事件、恢复和 undo。也就是说 Native Memory 的 L2/L3 consolidation 是一个可观察的维护流程，不是每个 chat turn 的同步尾步骤。

---

## 7. RAG 和 Memory 的关系

这两个词在面试中也经常被混用：

- Memory 检索“关于用户/学习状态的长期事实”。
- RAG 检索“外部知识源里的内容证据”。

### 7.1 ParseService 怎样把文件变成统一文档

`ParseService` 是解析适配层，不是某一个 PDF 库：

```text
文件字节
→ 计算 source content hash
→ 根据格式和设置选择 text-only / MinerU / Docling / MarkItDown /
  PyMuPDF4LLM / LiteParse 等 parser
→ 用 source hash + parser signature 查询缓存
→ parser 输出 Markdown、blocks、图片等原始结果
→ 归一化为 ParsedDocument
→ 保存可复用缓存
```

`parser signature` 表示解析器及其关键配置版本；相同文件但解析器配置变化时不会错误复用旧
结果。稳定的 `ParsedDocument` 把第三方解析差异隔离在 RAG 和 ExamMem 章节恢复之前。PDF
本身通常没有可靠的“章节”语义，因此章节识别主要依赖解析后 heading、编号、目录/正文对应、
字体或 block 信息；证据不足时保留低置信度 `Full text`，而不是让 LLM 凭空制造目录。

### 7.2 RAG 怎样建立和查询索引

DeepTutor 的 RAG 有多个 provider，不能一概而论。工厂见 `deeptutor/services/rag/factory.py`。默认 LlamaIndex pipeline 的关键路径是：

```text
文档解析/切块
  → LlamaIndex index + 可选 FAISS vector store
  → 查询时 vector 或 hybrid profile
  → hybrid = vector candidates + BM25 candidates
  → reciprocal-rank fusion
  → top-k nodes + source metadata
```

默认 FAISS 路径使用 `IndexFlatIP`。文档向量与查询向量在写入/检索前做 L2 归一化，所以内积
排序等价于 cosine similarity；`IndexFlatIP` 是精确扫描，不是 HNSW 近似索引。BM25 根据
词频、逆文档频率和文档长度归一化做词法召回，适合专有名词、公式符号和精确关键词；hybrid
模式用 Reciprocal Rank Fusion 按双方排名合并，不要求直接比较两种检索器数值尺度不同的分数。

关键文件：

- `deeptutor/services/rag/pipelines/llamaindex/pipeline.py`
- `deeptutor/services/rag/pipelines/llamaindex/storage.py`
- `deeptutor/services/rag/pipelines/llamaindex/retrievers.py`
- `deeptutor/services/rag/pipelines/llamaindex/vector_store.py`

若带 metadata filters，当前实现先把候选数扩大到 `top_k * 8`，检索后在应用层严格过滤 metadata，再截取 top-k。这对 ExamMem 固定教材章节检索尤其重要。

此外项目还支持 GraphRAG、PageIndex、LightRAG、本地/远端 provider 等，因此面试回答应说“RAG provider 可插拔；默认 LlamaIndex 路径如何做”，不要把一种实现冒充成整个项目唯一方案。

---

## 8. ExamMem 与 DeepTutor 的边界

关键文件：

- `docs/exam_mem/architecture.md`
- `deeptutor_plugins/exam_mem/__init__.py`
- `deeptutor/plugins/contracts.py`
- `deeptutor/plugins/manager.py`

### 8.1 四层所有权

```text
1. DeepTutor Host
   会话、流协议、模型接入、工具/Capability 注册、通用 RAG、插件接口、Web 壳

2. deeptutor_plugins/exam_mem
   把 ExamMem Capability、API、导航、设置、migration、session context 接到 Host

3. exam_mem
   考试领域模型、题目目录、练习工作流、Learning Memory、PostgreSQL repository

4. evaluation
   对不同 memory backend 和策略进行可重复评估
```

DeepTutor core 不直接 import `exam_mem`。这是插件隔离的核心证据。

### 8.2 PluginManifest 注册了什么

当前 ExamMem manifest 注册：

- Capability：`exam_practice`。
- Tools：题目检索、答案评分、知识映射、错误分析、Memory 读写、推荐。
- API router：`/api/v1/exam-mem`。
- Web navigation 和 settings。
- Session context contributor：`exam_mem_learning`。
- Migration head：`0014_textbook_grounding`。

插件 manager 负责名称碰撞校验、生命周期、贡献聚合和失败隔离。

---

## 9. ExamMem 练习不是“让 Agent 自己决定一切”

关键文件：

- `exam_mem/practice/workflow.py`
- `exam_mem/practice/provider.py`
- `exam_mem/practice/tools.py`
- `exam_mem/practice/checkpoint.py`

### 9.1 状态机

```text
IDLE
  → QUESTION_READY
  → ANSWER_RECEIVED
  → GRADED
  → DIAGNOSED
  → MEMORY_UPDATED
  → RECOMMENDED
```

每次 advance 使用持久化 checkpoint 和 `row_version` 做 CAS。并发请求拿到旧版本时不能静默覆盖新进度。

### 9.2 开始练习

`ExamPracticeWorkflow.start()`：

1. 确认运行时绑定的用户、考试、学科、题库版本、taxonomy 和 backend。
2. 读取推荐。
3. 从固定 Question Catalog 选择题目。
4. 把 checkpoint 推进到 `QUESTION_READY`。
5. 返回题目，不泄露答案/评分规则。

题目必须属于当前冻结 catalog；不会因为模型“觉得有一道更好的题”而越界生成并替换。

### 9.3 提交答案

`ExamPracticeWorkflow.submit()` 的真实顺序：

1. 验证提交的 question 正是该 session 发出的题。
2. 把状态推进到 `ANSWER_RECEIVED`。
3. 计算 grade artifact identity：题目内容 hash、规范化答案 hash、rubric hash、grader contract、配置 revision。
4. 已存在相同 grade artifact 时复用，否则调用受约束 Grader。
5. checkpoint 到 `GRADED`。
6. 用冻结 taxonomy 做知识点映射；未知知识点直接拒绝。
7. 正确答案产生确定性“无错误”诊断；错误答案才调用 ErrorAnalyzer。
8. 构造一个确定性的 `LearningEvent` 和 L2 candidates。
9. checkpoint 到 `DIAGNOSED`。
10. 在事务中追加 L1、更新 L2、写 lifecycle audit。
11. checkpoint 到 `MEMORY_UPDATED`。
12. 事务提交后单独刷新 L3 StudentModel 投影。
13. 完成目录或推荐下一题，进入 `RECOMMENDED`。

LLM 的输出在这里是受 schema 约束的领域判断，不拥有状态推进权。

### 9.4 幂等与恢复

- Learning event ID 由用户和 idempotency key 等稳定信息确定。
- 同一正式作答重复提交可以识别为同一事件/评分产物。
- Checkpoint 保存运行时快照，恢复时必须验证题库、taxonomy、backend 和配置身份。
- 不会在恢复时偷偷切换另一个 memory backend。
- L1/L2 的事务成功后，即使 L3 projection 失败，也不回滚业务真相；L3 可以从 checkpoint 重试和重建。

---

## 10. ExamMem Learning Memory 数据模型

关键文件：

- `exam_mem/contracts/memory.py`
- `exam_mem/storage/models.py`
- `exam_mem/storage/repository.py`
- `exam_mem/memory/`

### 10.1 L1：LearningEvent

事件类型包括：

- `answer_attempt`
- `explicit_correction`
- `plan_transition`

每个事件至少绑定三维 `LearningContext`：

```text
(user_id, exam_id, subject_id)
```

并携带 idempotency key、题目、知识点、答案、错误、证据和 provenance。L1 append-only，不能把“学生后来答对了”实现为覆盖旧的答错事件。

### 10.2 L2：LearningMemory

L2 使用四维 `MemoryScope`：

```text
(user_id, exam_id, subject_id, namespace)
```

namespace 常见值：

- `mastery`
- `error_pattern`
- `plan`
- `profile`
- `preference`

每个 scope 下用稳定 `slot_key` 标识同一个逻辑槽位：

```text
mastery:<canonical_knowledge_point>
error_pattern:<knowledge_point>:<error_type>
plan:<exam>:<subject>
profile:<attribute>
preference:<attribute>
```

一条 L2 还包含：typed value、confidence、evidence count、semantic version、CAS row version、有效时间、superseded_by、provenance 和生命周期状态。

### 10.3 生命周期状态

- `active`：当前稳定版本。
- `archived`：被新版本替代的历史版本。
- `invalidated`：被明确判定无效。
- `contested`：证据冲突，尚未达到稳定决策门槛。

这让“错一次、对一次”不会被粗暴覆盖成最后一次结果。

### 10.4 L3：StudentModel

L3 汇总：

- weak knowledge points
- mastered knowledge points
- stable error patterns
- active plans
- projection version 和 source watermark

它只是一份加速读取的投影。L1/L2 才是审计和重建依据。

---

## 11. 一次作答如何更新 Memory

### 11.1 从评分结果构造 candidates

一次正式 grade：

- 对每个关联知识点生成一个 mastery candidate。
- 正确通常产生高掌握分数，错误产生低掌握分数。
- 错误且有 error type 时，再生成一个 error-pattern candidate。

candidate 必须与同一个 LearningEvent 和三维 context 对齐；同一批次不允许重复 namespace + slot。

### 11.2 为什么更新时先精确 slot，而不是向量相似度

Lifecycle backend 为每个 candidate 查询当前记忆时，强制过滤：

```text
user_id
exam_id
subject_id
namespace
lifecycle IN (active, contested)
slot_key == candidate.slot_key
```

理由是“要修改哪一条业务状态”必须确定。向量相似只适合发现相关内容，不适合决定覆盖哪条掌握度记录，否则可能把相似知识点误合并。

### 11.3 关系判定

- Mastery 的 duplicate / contradictory 主要按 typed score 确定性判断。
- Error pattern 或显式纠正等复杂关系，可以调用严格 schema 的 relation classifier。
- 如果同一 event 已在 provenance 中，判为 replay no-op。

### 11.4 纯策略决策

`decide_lifecycle` 根据旧状态、新候选、关系和证据聚合，产生以下 operation 之一：

- `ADD`
- `NO_OP`
- `MERGE`
- `SUPERSEDE`
- `INVALIDATE`
- `CONTESTED`

策略中特别防止单次噪声：低置信度、临时例外、孤立 careless mistake 不应立刻改写稳定 mastery。

### 11.5 冲突证据怎样累积

当前证据质量会考虑：

- candidate confidence
- 题目难度：答对难题比答对简单题证据更强；答错简单题比答错难题证据更强。
- 错误类型：concept error 比 careless mistake 更能说明掌握不足。
- 时间衰减：当前策略使用 30 天半衰期。
- 是否临时/异常事件。

要成为稳定 winner，还需同时满足方向事件数、独立 session 数、置信度和支持 margin 等门槛。达不到门槛时可以进入 contested，而不是“最后写入者胜”。

### 11.6 Applier 怎样落库

Lifecycle applier 把 decision、planned audit、数据变更和 terminal audit 放在事务边界内：

- `ADD`：创建 active v1。
- `MERGE`：归档旧版本，创建合并 provenance 的新版本。
- `SUPERSEDE`：归档旧版本，新建胜出的 active 版本。
- `INVALIDATE`：终止目标并记录证据。
- `CONTESTED`：保留/标记当前分支，同时建立有 contested group 的冲突分支。

更新使用 expected `row_version` 做 CAS。发生 stale write 时重新读取并重新决策，重试次数有界；不会盲目覆盖。

新版本写入时生成 `search_document` embedding，供语义检索使用，但 embedding 不参与确定 update target。

---

## 12. ExamMem 到底有几种“检索”

这是最值得在面试中主动讲清楚的一张表：

| 场景 | 查询范围 | 方法 | 为什么 |
| --- | --- | --- | --- |
| 更新某个 L2 candidate | 精确四维 scope + namespace + slot | SQL 精确匹配 active/contested | 业务更新目标必须确定，不能靠相似度猜 |
| `LifecycleMemoryBackend.retrieve()` 输入合法 slot | 同上 | 精确 slot 查询 | 最可靠且便宜 |
| `LifecycleMemoryBackend.retrieve()` 输入自然语言 | 四维 scope 内 | query embedding + pgvector cosine top-k | 用于发现语义相关记忆 |
| Memory Workbench 列表 | 某个完整 namespace | 读取 snapshot，再做大小写无关 substring filter | 产品列表要完整、可分页、可解释，不是语义搜索 |
| Learning Profile | mastery/error/plan 全量当前快照 + L1 + 最新 L3 | 确定性聚合 | 推荐和画像不能漏掉被向量 top-k 排除的业务状态 |
| 推荐下一题 | 当前画像和目录 | 策略排序 | 需要全局状态与覆盖度 |
| Chat 注入 ExamMem context | 当前 session 链接的精确 plan/version/objective | 读取确定 scope 后渲染 bounded context block | 防止普通聊天污染/越权读取正式学习状态 |
| 教材证据 | 固定教材版本 + 映射章节 | Host RAG top-k + section metadata filter | 既要语义相关，又不能越出被冻结的教材章节 |

所以不能简单回答“ExamMem 就是 pgvector 检索”。pgvector 只是自然语言语义读取的一条路径。

### 12.1 最新检索实现：exact-slot 与自然语言是两条路径

当前统一入口是 `exam_mem/retrieval/service.py` 中的
`LearningMemoryRetrievalService.retrieve()`，返回
`MemoryRetrievalResult(items, decision, intent)`。`items` 不再只是 Memory，而是：

```text
ScoredLearningMemory
├── memory：完整 LearningMemory
├── distance：第一阶段 cosine distance，越小越接近
└── relevance_score：第二阶段 reranker 分数，越大越相关
```

第一条是 canonical slot 快路径：

```text
合法 slot_key
→ 校验 slot namespace == scope namespace
→ SQL 精确过滤 user/exam/subject/namespace/slot
→ 只读取 active/contested
→ distance=0，relevance_score=1
→ 返回 0..top_k 条当前分支
```

它不生成 query embedding，也不调用 reranker。普通状态最多有一个 active；未解决冲突可以
有多个 contested 分支，所以 exact-slot 不是强行“只取最新一条”。历史 archived 和
invalidated 已由 Repository 排除。当前 `top_k` 在 Service 中截断，Repository 本身会先加载
该 slot 的全部当前分支；正常冲突组很小，但这仍是一个可继续优化的分页边界。

第二条是自然语言语义路径：

```text
自然语言 query + 已确定的四维 MemoryScope
→ TaxonomyRetrievalIntentResolver
→ 指代不足 / 显式未知目标时提前拒答
→ Qwen3 search_query instruction embedding（1024 维）
→ PostgreSQL 四维 Scope + active/contested + embedding 非空过滤
→ distance-only 内层 Top-4N，外层 (distance, memory_id) 稳定 Top-N
→ 按知识点、错误类型、掌握状态过滤 RetrievalIntent
→ HostLearningMemoryReranker 对 query-memory pair 精排
→ acceptance_threshold=max(0.003, top1_score−0.03)
→ 候选逐条通过动态门限
→ 返回 0..K 条结果或明确 RetrievalDecision
```

`RetrievalDecision` 区分：

- `answered`：至少一条候选通过最终置信门；
- `no_match`：结构化约束后没有候选；
- `insufficient_reference`：如“上次那个错误”，本轮指代不足；
- `out_of_scope_target`：显式指定了当前 Taxonomy 中不存在的目标；
- `below_confidence`：有召回候选，但 reranker 分数均未过门。

意图解析器故意保守：只识别 Taxonomy 的 canonical ID、中文名、alias，以及注册过的错误类型
和掌握状态，不用 LLM 猜知识点 ID。它解决的是“硬约束和可靠拒答”，不是替代向量语义。

Qwen3 查询文本实际会变为：

```text
Instruct: Given a user query, retrieve relevant passages that directly answer the query
Query:<原始查询>
```

写入侧 `search_document` 保持 canonical Memory 文本，不加查询指令。这样 document vector
无需因 query instruction 改变而重建。Repository 设置
`hnsw.iterative_scan='strict_order'` 和 `hnsw.ef_search=100`；内层只按 distance 排序，使 SQL
形状与 pgvector HNSW 兼容，外层再用 `memory_id` 做稳定 tie-break。

第二阶段通过中性 Host Reranking Hook 调用本地
`Qwen/Qwen3-Reranker-4B`，NF4 量化。reranker 看到的不是数据库 JSON，而是有界证据文本，
例如：

```text
知识点：矩阵的秩。掌握状态：low；掌握分数：0.320
知识点：条件概率。错误表现：把 P(A|B) 与 P(B|A) 混用
```

默认绝对阈值 `0.003` 和最大 Top-1 分差 `0.03` 都是在 dev 上冻结的 reranker score
门限，不等于“业务置信度只有 0.3%”，也不是 cosine similarity；不同模型或序列化变化后
必须重新校准，不能照搬。最终接受条件是：候选分数不低于
`max(0.003, top1_score - 0.03)`。

### 12.2 评测数据集到底测什么

数据协议是 `semantic_retrieval_v2`。它从已经结构化的 L1/L2 开始，专门评估：

```text
LearningMemory 存储正确性
+ 自然语言到既有 LearningMemory 的检索、排序、拒答与隔离
+ 10k 同 Scope 下的 HNSW 工程行为
```

它不评估“原始作答能否正确抽取知识点”，也不评估“推荐题目是否正确”或“学生是否真的
提分”。Extraction、Lifecycle、Recommendation 和学习效果必须由各自数据集负责。

数据构造顺序是先冻结 Schema 和指标，再由三个相互隔离的数据 Agent 并行构造分片：

1. mastery：掌握、薄弱、进步、退步、冲突；
2. error pattern：公式误用、条件遗漏、概念混淆和相邻错因难负例；
3. language/safety：口语、简称、中英混合、模糊指代、无答案和 Scope 干扰。

主线程只做契约校验、去重、`write_index` 重排、覆盖统计和 hash，不根据最终分数反向修改
Gold。规模画像的 10,000 条程序化 Memory 与语义 Gold 分开，不能拿规模噪声冒充人工相关性
标注。

当前发布数据：

| 内容 | dev | 冻结 test |
| --- | ---: | ---: |
| L2 corpus | 396 | 396 |
| 总 query | 170 | 310 |
| answerable | 140 | 250 |
| no-answer | 30 | 60 |
| mastery query | 50 | 100 |
| error-pattern query | 120 | 210 |
| 每 query `top_k` | 5 | 5 |
| 每 query hard negative | 最少 10，平均 11.41 | 最少 10，平均 11.35 |
| 每个 answerable query 的 Gold | 平均 1.32 条 | 平均 1.36 条 |

396 条 corpus 的组成是：334 条 error pattern、61 条 mastery、1 条 plan；278 条 active、52 条
contested、35 条 archived、31 条 invalidated。Taxonomy 是 `math1_v1`，覆盖线性代数和概率论
的 30 个 active leaf。test 中三类大 query family 各有 100 条 mastery、110 条 errors、100 条
language/safety。

一条 `RetrievalQuery` 同时保存：

- `relevant_memory_ids`：应该命中的 Gold；
- `relevance_grades`：1..3 级相关性，用于 nDCG；
- `hard_negative_memory_ids`：同 Scope、可检索、语义相邻但不应替代 Gold 的候选；
- `must_not_return_memory_ids`：terminal 或跨 Scope 的绝对禁返项；
- `expected_no_answer`：本次是否应该返回空；
- 完整四维 scope 和固定 `top_k=5`。

每条查询的同 Scope 可检索候选不少于 50，hard negative 不少于 10。dev/test 按
`query_family` 分割，同一模板的同义改写不随机跨 split。test 的 canonical SHA-256 固定为：

```text
14c9e0730449671a74b3bc7ff66dea490f550e4a8f4b885f762b6b15c044ddb5
```

最新 metric catalog SHA-256 是：

```text
09e273ab785cf4a537e935fac1a3f5ceddf89655e237df6af59d2e74cd368250
```

向量不写死在 JSON 中。评测运行时通过中性 Host Hook，用
`ollama:qwen3-embedding:0.6b:1024` 对 canonical document 和 query 生成真实 1024 维向量。
因此数据集冻结的是业务记录、输入文本与 Gold，而不是某个模型的浮点输出。

### 12.3 指标分别是什么意思

设某条 query 的 Gold 是 `{A}`，系统返回 `[A, B, C]`，配置 `K=5`：

| 指标 | 计算方式 | 回答的问题 |
| --- | --- | --- |
| Recall@5 | `命中的 Gold 数 / Gold 总数`，再对 query 做 macro 平均 | 应找的记忆找全了吗 |
| Precision@5 | `命中的 Gold 数 / 5`，再做 macro 平均 | 固定五个位置里有多少是 Gold |
| Hit@5 | 只要 Top-5 命中至少一个 Gold 就记 1 | 查询有没有至少找到一个正确答案 |
| MRR | 第一个 Gold 排名的倒数；rank 1=1，rank 2=0.5 | 正确答案是否尽量排第一 |
| nDCG@5 | 用 1..3 relevance grade 计算 DCG，再除以理想排序 IDCG | 高相关结果是否排在低相关结果之前 |
| pairwise accuracy | 对每个 Gold×hard-negative 对，比较 Gold cosine distance 是否更小 | 第一阶段 embedding 能否区分相邻概念 |
| no-answer accuracy | 应拒答 query 中，实际返回 0 条的比例 | 没有答案时会不会乱给 Memory |
| hard-negative@1 | answerable query 的第一名是显式难负例的比例 | 最危险的“把相邻概念当答案”有多少 |
| hard-negative@K | 任一返回项包含 hard negative 的 query 比例 | Top-2..K 是否仍夹带相邻概念 |
| accepted-result hard-negative rate | 所有最终通过门限的返回项中，hard negative 所占比例 | 真正交给下游的结果污染率 |
| archived/invalidated hit rate | terminal 返回数 / 总返回数 | 历史或失效记忆是否泄漏 |
| cross-Scope leakage | scope 不完全相等的返回数 / 总返回数 | 是否串用户、考试、学科或 namespace |
| ANN Recall@5 | HNSW Top-5 与同 snapshot exact cosine Top-5 的重合率 | 近似索引相对精确扫描损失多少 |
| P95 latency | 排序后第 `ceil(0.95N)` 个耗时 | 95% 请求能在多长时间内完成 |

几个容易误解的点：

- Gold 通常只有 1.36 条，而 Precision@5 固定除以 5；只有一个 Gold 时理论上限就是 0.2，
  所以它只是稀疏 Gold 下的诊断项，不能压过 Hit、MRR 和 nDCG。
- Recall@5 和 Hit@5 在“每 query 只有一个 Gold”时数值相同；有多个 Gold 时 Recall 才能反映
  找全程度。
- MRR 只关心第一个 Gold，无法评价第二名以后；nDCG 才同时评价多个结果和相关等级。
- pairwise accuracy 使用同一个 query vector 下的 cosine distance，主要评价第一阶段 embedding，
  不能替代最终 reranker 污染率。
- ANN Recall 只有在 `EXPLAIN` 证明生产计划实际使用 HNSW 时才成立。顺序扫描与 exact 一致，
  不能冒充“ANN Recall=1”。

### 12.4 实验具体是怎么跑的

存储阶段先在真实 PostgreSQL 16 + pgvector 0.8.2、migration head
`0014_textbook_grounding` 上执行生产 Repository：

1. `LearningEventRepository.append()` 写 L1 provenance；
2. Host embedding 以 `search_document` 编码 canonical Memory 文本；
3. `LearningMemoryRepository.insert_version()` 写 L2 和 1024 维 vector；
4. 立即 round-trip 检查 Memory、provenance 和 vector；
5. 重放同一记录，要求幂等且行数不增长；
6. 运行 32 个非法写：跨 Scope/缺失 provenance、错误 version、重复 active、非法时间区间、
   错误维数、NaN、全零向量；每个都必须报错且零增长。

存储通过后才允许检索评分。对每条 query：

1. 只生成一次 `search_query` vector；
2. 在同一个 committed snapshot 上关闭 index/bitmap scan，执行 exact cosine reference；
3. 用同一个 vector 调生产 `LearningMemoryRetrievalService`；
4. 记录生产排名、distance、relevance score、decision 和耗时；
5. 单独执行 `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)`，验证是否实际命中 HNSW；
6. 对所有 Gold 和 hard negative 读取同 query vector 下的距离，再计算指标。

Runner 不从 exact 结果回填 production，也不按 Gold 重排。模型、instruction、candidate N 和
阈值只在 dev 上选择；冻结后才运行一次 test。需要特别说明：报告中的最终语义检索 P95
包含 Repository、意图过滤和本地 reranker，但 query embedding 已预先批量生成，不包含在该
594.78 ms 中；它也不是浏览器到服务端的完整用户请求延迟。

HNSW 另有独立规模画像：在同一个完整四维 Scope 中放 10,000 条 active Memory，对 100 条
query 同时跑 production、强制 exact 和强制 HNSW 控制组。只有生产 `EXPLAIN` 命中索引时，
才报告 production ANN Recall。

### 12.5 v2 基线修复前后效果

冻结 test 内容没有改变。基线和未启用相对截断的 v2 实现都在 250 条 answerable + 60 条
no-answer 上评分：

| 指标 | 修复前 | 最终实现 | 解读 |
| --- | ---: | ---: | --- |
| Recall@5 | 0.9760 | 0.9920 | 少量漏召回进一步减少 |
| Hit@5 | 0.9760 | 0.9920 | 250 条可回答查询约 248 条至少命中一个 Gold |
| MRR | 0.8950 | 0.9713 | Gold 更稳定地排在第一位 |
| nDCG@5 | 0.9160 | 0.9767 | 分级相关排序明显改善 |
| relevant-hard-negative pairwise | 0.9880 | 0.9895 | embedding 本身原本就较强，提升有限 |
| no-answer accuracy | 0.0000 | 0.9833 | 从“永远填满 Top-5”变成可靠拒答 |
| answerable hard-negative@1 | 0.0800 | 0.0280 | 第一名错取相邻概念降到门槛 5% 以下 |
| hard-negative@K | 0.6580 | 0.3871 | Top-2..K 污染下降，但仍明显存在 |
| accepted-result hard-negative | 未记录 | 0.2628 | 约四分之一最终返回项仍是显式难负例 |
| archived/invalidated hit | 0 | 0 | 生命周期过滤保持安全 |
| cross-Scope leakage | 0 | 0 | 四维隔离保持安全 |
| 检索决策 P95 | 约 7.83 ms | 594.13 ms | v2 基线含 4B reranker；两者工作量不同，不能当纯 SQL 回归比较 |

最终 test 通过了预注册的主门禁：Recall ≥ 0.90、Hit ≥ 0.95、MRR/nDCG ≥ 0.85、拒答
≥ 0.95、hard-negative@1 ≤ 0.05、安全泄漏为 0、检索决策 P95 ≤ 1000 ms。

dev 上冻结的最终配置是：

```text
Embedding       qwen3-embedding:0.6b / 1024 维 / query instruction
Reranker        Qwen3-Reranker-4B / NF4
candidate N     5（Repository 内层扫描 4N）
score threshold 0.003
maximum score gap 0.03
```

dev 结果为 Recall/Hit 0.9298 / 0.9643、MRR 0.9208、nDCG 0.9057、no-answer 0.9667、
hard-negative@1 0.0500、hard-negative@K 0.2765。0.6B reranker 在相同 dev 上降低排序质量，因此没有选为默认模型；这是
用 dev 做模型选择，而不是在冻结 test 上挑最好看的结果。

随后在同一 frozen test 上启用 `maximum score gap=0.03` 的动态截断，结果为 Recall/Hit
0.9547 / 0.9800、MRR 0.9660、nDCG 0.9505、no-answer 0.9833、hard-negative@K
0.1290、accepted-result hard-negative 0.1176。相对于不做相对截断的 v2 基线，召回略降，
但返回列表的难负例污染明显减少；`top_k` 现在只是上限，不再要求填满。

### 12.6 HNSW 实验怎样解释

修复前，10k Scope 的 100/100 个生产计划都是：

```text
Limit → top-N Sort → Seq Scan(10,000 candidates)
```

所以当时只能说 exact 结果一致，不能声称生产使用 ANN。去掉不兼容 tie-breaker 并强制
planner 的诊断控制组能达到 ANN Recall@5 0.972、P95 约 2.98 ms，证明索引本身没坏。

修复后的 10k 画像：

| 指标 | 结果 |
| --- | ---: |
| production HNSW plan rate | 1.000 |
| production ANN Recall@5 | 1.000 |
| Repository production P95 | 64.77 ms |
| exact reference P95 | 56.87 ms |
| 强制索引、只取 ID 的控制组 P95 | 3.29 ms |

这组结果应该拆成两句话讲：

1. **正确性和查询形状已经修好**：生产 planner 确实选择 HNSW，Top-5 在这 100 条 query 上
   与 exact 完全一致。
2. **端到端性能还没有证明更好**：生产路径要做 Scope 后置过滤、稳定排序、完整 Memory 与
   provenance 回读，因此 10k 下反而比 exact reference 慢。3.29 ms 控制组只取 ID，不能冒充
   完整 Repository 延迟。

因此“HNSW 已上线”和“HNSW 已让产品更快”不是同一句话。1k、50k、100k 梯度、冷缓存、并发
和不同过滤选择性仍未测；当前 100 条 query 也是有限工程画像，不应外推为所有规模的性能
保证。

### 12.7 做得好的地方

- **更新与发现分离**：L2 更新目标永远 exact slot，向量只用于自然语言发现，不会相似就误
  合并业务状态。
- **拒答成为一等结果**：无答案准确率从 0 提升到 98.33%，而 answerable Recall 仍为
  99.2%，不是靠全部拒绝换分。
- **首位排序可靠**：MRR 0.9713、hard-negative@1 2.8%，说明下游最先看到的证据质量较高。
- **隔离约束没有为相关性让路**：用户、考试、学科、namespace、terminal 过滤保持零泄漏。
- **实验纪律较好**：Schema 和指标先冻结；dev 负责选择，test 有 hash；exact 与 production
  使用同 vector、同 snapshot、同 Scope；失败指标没有从报告中删除。
- **结果可审计**：每条输出保留 Memory ID、版本、provenance、distance、reranker score、
  intent 和 decision，可以解释“为何返回/为何拒答”。

### 12.8 做得不好的地方和下一轮实验

- **Top-2..K 仍不够干净**：`hard-negative@K=38.71%`，最终通过门限的结果中
  `26.28%` 是显式难负例。调用方不能把所有返回项当成同等事实；下一轮应做 per-rank
  precision、阈值/候选 N 消融和只返回 top-1 的产品对照。
- **结构过滤发生在有限向量召回之后**：当前先召回 N=5，再按知识点/错因/掌握状态过滤。
  显式目标若在第一阶段排到第六名以后，会被提前截掉。应评测扩大 N、可索引结构过滤或
  “exact known slot + semantic evidence”的组合，但不能跨 Scope fallback。
- **4B reranker 有成本**：本机 NF4 实测约占 2.7 GiB GPU 显存，P95 约 594 ms 且不含 query
  embedding。缺少可选依赖或合适硬件时会影响部署；需要 CPU、小模型、远程 Host reranker
  的质量/成本消融。
- **意图解析仍是规则系统**：它对已注册表达可审计，但对自由改写、复杂指代和新错误类型
  泛化有限。应加入真实日志，按 `insufficient_reference`、`out_of_scope_target` 和误拒答类型
  分桶，而不是只看总 no-answer accuracy。
- **数据仍是单学科合成集**：只有数学一、线性代数和概率论、30 个 active leaf、两个主要
  namespace；三个 Agent 保证作者独立性，但不是教师双盲标注，也不是学生真实查询分布。
- **dev/test 仍来自同一生成机制**：两边各有 396 条 corpus，`memory_id` 完全不重合，
  query family 也隔离，但 121 个 canonical slot 重合，且都使用同一个 `math1_v1` Taxonomy。
  这适合评估独立 Memory 表述和查询改写，仍不能证明面对全新知识点、全新考试或真实新
  corpus 时保持同样分数。
- **性能实验边界有限**：单机热缓存、串行 query、100 条 10k HNSW 画像；没有冷启动、并发、
  50k/100k、持续写入时查询、GPU 抢占和真实浏览器端到端延迟。
- **检索不是学习效果**：这些数字证明“找到已存在 Memory”，不证明推荐正确、教学答案正确、
  用户掌握或考试分数提升。下一阶段还需要教师标注、真实线上日志和学习成效实验。

最诚实的实验结论是：

> v2 已把一个“固定返回 Top-5 的向量搜索”修成“有 Scope、安全过滤、结构约束、二阶段排序
> 和可靠拒答的 Memory 检索决策器”。首位相关性、拒答与隔离已经达到上线门禁；多结果纯度、
> reranker 成本、意图泛化和 HNSW 的真实规模收益仍是下一轮重点。

完整协议、基线与修复证据分别见：

- [语义检索 v2 协议](./evaluation/semantic-retrieval-v2.zh-CN.md)
- [修复前本地结果](./evaluation/semantic-retrieval-v2-results.zh-CN.md)
- [修复方案与最终结果](./evaluation/semantic-retrieval-v2-remediation.zh-CN.md)

---

## 13. L3 StudentModel 如何投影

投影在 L1/L2 事务提交后运行：

1. 分页读取该 scope 的全部 L1。
2. 读取 mastery、error_pattern、plan 等 L2 当前快照。
3. 校验 scope 和 provenance。
4. 只把合适的 active 稳定事实投影为 mastered/weak/error/plan。
5. contested 和 terminal 记忆不冒充稳定事实。
6. 以 deterministic snapshot 保存 projection version、event watermark、memory watermark。

投影失败不会把已经提交的正式作答和 L2 决策回滚。恢复时从 watermarks/checkpoint 重建。这是典型的“事务内业务真相 + 事务后可重建读模型”。

---

## 14. Learning Profile 与推荐怎样算

当前 profile policy 使用：

- 冻结 taxonomy 的 active leaf。
- 正式 answer events。
- active/contested L2。
- 最新 StudentModel。

知识点可处于：

- unassessed
- developing
- weak
- mastered
- contested

复习间隔会根据未测、冲突、最近答错、稳定错误、低/高掌握度等确定。当前推荐链路是：

```text
完整当前 Profile + Taxonomy active leaves
→ 为每个知识点计算 weakness / stable error / forgetting /
  active plan / coverage gap / contested / scheduled 信号
→ 确定性可行动门控
→ 无候选时返回 no_recommendation
→ 生成有界 canonical 候选集并做基础排序
→ LLM 只能在候选 ID 中选择知识点和动作
→ 模型失败、越界或低置信度时确定性回退
→ 记录候选、策略、selector 版本和理由
```

候选至少需要 weakness、stable error、active plan priority，或 `forgetting_risk >= 0.5` 才能
打开门控。遗忘风险近似按“距最近有效记忆天数 / 30”截断到 0～1，因此当前阈值约为 15 天；
coverage gap 可以影响排序，但不能单独强迫系统出题。

所以推荐既不是纯规则，也不是让 LLM 读取全部历史后自由编造。确定性代码负责权限、状态、
是否推荐和候选边界，LLM 只负责候选内的语义选择，失败时仍能得到可复现结果。

---

## 15. ExamMem Learning Context 怎样进入普通 Chat

关键文件：`deeptutor_plugins/exam_mem/learning_context.py`

只有 session 显式绑定 `exam_mem_learning` context source 且能解析到合法的 ExamMem session link 时才注入：

1. 取当前认证用户。
2. 校验 Host session 与 ExamMem 学习计划的链接。
3. 读取精确 plan/version/objective/taxonomy。
4. 构建 Learning Profile。
5. 读取与当前 objective 对齐的已确认学习路径观察。
6. 读取固定教材来源 snapshot。
7. 渲染为有长度上限的 `ContextBlock`。

渲染内容明确区分：

- 正式 assessment memory：强证据。
- 学习路径观察：弱证据。
- 教材来源及页码/章节：知识证据。

普通聊天中的自述不会直接升级 mastery。聊天可以利用正式学习状态做个性化讲解，但不能反向把一句“我懂了”当作正式测评。

---

## 16. 当前教材 Grounding 全链路

关键文件：

- `deeptutor_plugins/exam_mem/textbooks.py`
- `exam_mem/textbooks/structure.py`
- `deeptutor_plugins/exam_mem/grounded_learning.py`
- `exam_mem/storage/textbook_repository.py`
- `exam_mem/storage/grounded_learning_repository.py`
- `deeptutor/plugins/host_services.py`

### 16.1 上传和摄取

```text
上传 PDF/TXT/Markdown
  → Host 按内容 hash 保存原件并返回 opaque source_ref
  → ParseService 解析为 Markdown + blocks
  → build_section_tree 恢复章节树
  → section_documents 只在章节边界内部切块
  → PluginKnowledgeIndexHost 调用通用 RAGService 建索引
  → 保存 host_index_ref、index_version 和 job checkpoint
```

摄取 job 有 saved、parsing、structuring、chunking、indexing、completed/failed 等阶段；每阶段写 safe checkpoint，可观察和重试。

### 16.2 章节树和切块

`build_section_tree()`：

- 从 Markdown heading 恢复层级。
- 标题缺失时回退成置信度较低的 `Full text`，而不是假装推断出章节。
- section key/id 由版本和路径等稳定材料生成。
- 尽可能从 parser blocks 恢复页码。

`section_documents()`：

- 默认约 1800 字符一块，200 overlap。
- 不跨章节切块。
- 每块保留 textbook/version/section/path/page/source ref 等完整 provenance。

### 16.3 教材绑定和目标映射

- 教材版本完成后才能绑定。
- 学习计划绑定的是精确 textbook version，不是浮动“最新版”。
- 一个 plan version 只能有一个 confirmed primary textbook。
- objective 到 textbook section 的 mapping 也版本化，并区分 candidate/confirmed/rejected。

### 16.4 学习时检索

`GroundedLearningService.evidence_package()`：

1. 按 user + plan + plan version + objective 获取 grounding scope。
2. 对每个绑定教材拿到允许的 section keys 和固定 index ref。
3. 调 Host RAG，query 为本轮问题，metadata filter 为这些 section keys，单教材 top-k 目前为 4。
4. 保存 chunk、score、section、page、source ref。
5. 按教材 priority 排序。
6. 多教材证据首段不一致时标记 `comparison_required`，不静默融合。

### 16.5 快照冻结和恢复

Evidence snapshot 记录：

- 固定 textbook versions。
- 允许的 section keys。
- index refs。
- index versions。
- 检索证据和冲突状态。

恢复练习时会验证 index 是否仍可用、version 是否仍与快照一致。索引被重建成另一版本时要求显式恢复，而不是悄悄用新证据继续旧 assessment。

---

## 17. PostgreSQL 表的职责

阅读 `exam_mem/storage/models.py` 时至少要认识这些表群：

### Learning Memory

- `learning_events`：L1，用户 + idempotency key 唯一。
- `learning_memories`：L2 版本；scope + slot + version 唯一，并用 partial unique 限制稳定 active 叶子。
- provenance 关联表：一条 memory 来自哪些 event、关系是什么。
- lifecycle decision / change log：为什么做了这次操作、改了哪些行。
- `student_model_snapshots`：L3 投影。
- practice checkpoints / traces：状态机恢复与观测。

### 教材 Grounding

- `textbooks`
- `textbook_versions`
- `textbook_sections`
- `textbook_ingestion_jobs`
- `study_plan_textbook_bindings`
- `objective_textbook_section_mappings`
- source/evidence snapshots 相关表

数据库用 check、unique、foreign key 和 pgvector index 把部分领域不变量下沉，不只依赖 Python `if`。

---

## 18. 一致性、并发与失败语义

### 18.1 DeepTutor turn

- Turn 先持久化，再后台执行。
- 事件有 sequence，支持 replay。
- 进程重启后的 running orphan 会被识别。
- `ask_user` 队列只在本进程本 turn 存活；turn 结束后清理。
- 工具失败通常留在 AgentLoop 中供模型处理。

### 18.2 ExamMem practice

- idempotency key 防重复事件。
- checkpoint row version 防并发覆盖。
- grade artifact identity 防止恢复时用不同 rubric/config 重评分却假装同一产物。
- L1 + L2 + audit 同事务。
- L3 异步/事务后投影，可重建。
- stale CAS 必须重算，不能盲写。
- 被固定的 taxonomy、catalog、教材和索引版本在恢复时都要重新验证身份。

这套设计的目标不是“永不失败”，而是失败后知道：已经提交到哪、哪些可以重试、哪些必须显式人工恢复。

---

## 19. DeepTutor Native Memory 与 ExamMem Learning Memory 对照

| 维度 | DeepTutor Native Memory | ExamMem Learning Memory |
| --- | --- | --- |
| 所有者 | DeepTutor Host | ExamMem 领域插件 |
| 用途 | 通用跨会话偏好/画像/近期事实 | 正式考试学习状态 |
| 主存储 | JSONL + Markdown | PostgreSQL + pgvector |
| L1 可靠性 | 尽力追加的通用 trace | 幂等、事务化业务事件 |
| L2 模型 | LLM 抽取的 Markdown facts | 强类型、版本化、带生命周期的业务记忆 |
| L3 | 四份 Markdown 高层事实 | 可重建 StudentModel read model |
| 默认检索 | L3 全文拼接 | 按场景精确 slot、快照全读或向量 top-k |
| 更新触发 | Workbench consolidation / 显式偏好写入 | 正式练习工作流和显式纠正 |
| 冲突策略 | audit/dedup/merge 文档维护 | deterministic evidence + contested/supersede/invalidate |
| 能否作为掌握度真相 | 不能 | L1/L2 可以，L3 只是投影 |

为什么 ExamMem 不直接复用 Native Memory 当业务真相：正式学习状态需要四维隔离、幂等、事务、版本、CAS、冲突分支、审计和可重建投影；通用 Markdown Memory 的契约不够强。

---

## 20. 评测全景：测了什么、结果怎样、边界在哪

面试时不要把所有数字混成一个“系统准确率”。当前证据分为四层：DeepTutor 整体辅导、
ExamMem 生命周期、ExamMem 语义检索和工程契约；它们分别回答不同问题。

### 20.1 DeepTutor 整体辅导：TutorBench

DeepTutor 论文通过 TutorBench 比较完整系统和 Naive Tutor：

| 项目 | 内容 |
| --- | --- |
| 规模 | 270 个任务、90 个模拟学生画像、30 个知识库 |
| 方法 | 模拟学生进行多轮交互，LLM judge 在十个维度上按 1～5 分评分 |
| 对象 | 完整辅导、问答和出题表现，不是 Native Memory 单模块 |
| 结果 | DeepTutor 3.91，Naive Tutor 3.53，相对提升 10.76% |

它能支持“多能力协同改善整体辅导质量”，但不能直接证明 Native Memory 准确，也不能证明
真实学生成绩提高。模拟学生和 LLM judge 还可能存在模型偏好与评分漂移。截至 2026-09-03，
官方 `eval` 分支公开了评测代码和提示词，但论文使用的 270 条 frozen task 与 30 个成品知识库
没有随 `main`、Release 或该分支一并公开，因此这里引用的是论文结果，不声称已在本仓库复现。

### 20.2 ExamMem Controlled Lifecycle Evaluation

这套评测从已经校验的结构化 `LearningEvent` 开始，测试 L1/L2 生命周期维护、当前状态、
检索安全和推荐，不测试“原始聊天能否正确抽取事件”。`exam_mem_controlled_v1` 有 120 个
case：40 个 dev、80 个一次性 frozen test，共 12 类多轮学习轨迹。五个 backend 使用相同
Gold、顺序、`top_k` 和 seed：

- `none`：不保存长期记忆；
- `DeepTutor native`：通用 Markdown Memory；
- `append-only`：只追加、不处理生命周期；
- `vector`：追加后用向量找相关记录；
- `lifecycle`：ExamMem 的 typed lifecycle。

主要指标不能混淆：

| 指标 | 回答的问题 |
| --- | --- |
| Operation accuracy / macro-F1 | 每次 ADD、MERGE、SUPERSEDE、CONTESTED 等操作是否正确 |
| Active-state exact | 一系列操作后，每个检查点的完整当前状态是否与 Gold 一致 |
| Stale / duplicate rate | 返回了多少过时状态或重复状态，越低越好 |
| Cross-scope leakage | 是否读到其他用户、考试、科目或 namespace，必须为 0 |
| 推荐知识点准确率 | 推荐的 canonical 知识点是否匹配 Gold |
| 推荐动作准确率 | `review / advance / no_action` 等动作类型是否匹配 Gold |

v1 frozen test 是生命周期算法的历史正式基线：

| Lifecycle 指标 | v1 frozen test |
| --- | ---: |
| 完成率 | 98.75%（79/80） |
| Operation accuracy / macro-F1 | 95.73%（381/398）/ 82.49% |
| Active-state exact | 90.42%（217/240） |
| Stale / duplicate rate | 3.32% / 3.32% |
| Cross-scope leakage | 0 |
| 推荐知识点准确率 | 30.83%（74/240） |

95.73% 是逐次操作准确率，90.42% 是操作序列完成后的整体状态准确率；一次错误操作可能持续
污染多个后续检查点，所以两者不会相等。30.83% 则说明当时 Memory 维护较好，但推荐策略仍
是明显短板，不能用前两个高分替它辩护。

### 20.3 推荐修复、校准与跨学科 frozen test

推荐修复把“现在是否应该推荐”和“门控通过后推荐哪个知识点”分开：无可行动证据时返回
`no_recommendation`；coverage gap 不能独自强制出题；遗忘、薄弱、稳定错因或计划优先级
可以打开门控。随后 LLM 只能从服务端生成的有界 canonical 候选集中选择，越界、低置信度
或模型失败时回退到确定性排序。

三组数字必须按实验身份分别报告：

| 实验 | 推荐知识点 | 推荐动作 | 证据性质 |
| --- | ---: | ---: | --- |
| v1 80-case frozen test | 30.83% | 当时未单列 | 修复前历史正式基线 |
| v1 40-case dev 单臂重跑 | 83.33%（100/120） | 93.33%（112/120） | 用于校准，不能当 holdout |
| v1 已公开 test post-hoc | 82.08%（197/240） | 92.83%（220/237） | test 已参与诊断，不能重新包装成盲测 |
| v3 跨学科 frozen test | **80.83%（194/240）** | **92.74%（217/234）** | 新学科语义上的一次性正式结果 |

`exam_mem_controlled_v3` 将题目、答案、错误证据、Memory 文本、Taxonomy/slot、查询和 Scope
改成计算机数据结构与算法语义，并对 80 个 case、五个 backend 一次性运行。Lifecycle 完整
结果为：

| Lifecycle 指标 | v3 frozen test |
| --- | ---: |
| 完成率 | 97.50%（78/80） |
| Operation accuracy / macro-F1 | 94.47%（376/398）/ 82.24% |
| Active-state exact | 89.17%（214/240） |
| Stale / duplicate rate | 3.75% / 2.62% |
| Cross-scope leakage | 0 |
| Weak recall@5 / archived hit@5 | 80.00% / 0 |
| 推荐知识点准确率 | **80.83%（194/240）** |
| 推荐动作准确率 | **92.74%（217/234）** |
| Over-review rate | 2.99%（7/234） |

其余四个 backend 的推荐知识点准确率均为 55.00%，主要来自正确输出 `no_action`，不能解释
成有效选题能力。v3 复用了 v1 的生命周期形状，只替换了学科语义，因此支持的是有限跨科目
迁移，不是真实用户泛化。完整证据见[跨学科 Memory 冻结评测](./evaluation/controlled-v3-frozen-test.zh-CN.md)。

### 20.4 语义检索结果怎样放进全局结论

第 12 节的 `semantic_retrieval_v2` 使用 396 条 L2 corpus、310 条 frozen query，专门评价
自然语言检索、排序、拒答、难负例和 Scope 隔离。启用 Top-1 相对分差动态截断后的关键结果：

| 指标 | Frozen test |
| --- | ---: |
| Recall@5 / Hit@5 | 0.9547 / 0.9800 |
| MRR / nDCG@5 | 0.9660 / 0.9505 |
| Pairwise accuracy | 0.9892 |
| No-answer accuracy | 0.9833 |
| Hard-negative@1 | 0.0280 |
| Hard-negative@K / accepted-result | 0.1290 / 0.1176 |
| Archived/invalidated hit / Cross-scope leakage | 0 / 0 |
| 检索决策 P95 | 594.78 ms |

这些结果说明首位相关性、拒答和安全过滤较强，但 Top-2～K 仍偶尔混入相邻概念，4B reranker
也带来显存和延迟成本。它只证明“能否找到已存在的 Memory”，不证明事件抽取、推荐、教学
回答或学习效果正确。

### 20.5 当前证据覆盖与缺口

| 能力 | 当前证据 | 可以下的结论 | 不能下的结论 |
| --- | --- | --- | --- |
| DeepTutor 整体辅导 | TutorBench 论文实验 | 相对 Naive Tutor 的模拟交互质量更高 | Native Memory 单独有效、真实提分 |
| Native Memory | 工程测试 + Controlled baseline arm | 接口可运行，可作系统对照 | 抽取与长期帮助度已被充分验证 |
| Lifecycle | v1/v3 controlled benchmark | 结构化事件下的状态维护较强 | 原始聊天抽取同样准确 |
| 语义检索 | 396 corpus、310 frozen query、10k 画像 | 排序、拒答、隔离达到当前门禁 | 所有学科、真实查询和更大规模均成立 |
| 教材章节识别 | 单元、集成与样例验证 | 当前支持结构恢复、版本固定和引用 | 对扫描件及各种版式均准确 |
| 出题、判题、错因 | 契约与流程测试 | 工作流可执行、可恢复 | 达到教师水平 |
| 推荐 | v3 controlled frozen test | 受控场景下知识点与动作选择明显改善 | 能提高真实考试成绩 |
| 学习增益 | 尚无真实用户实验 | 无 | 不能声称已提高成绩或长期保持率 |

下一轮最有价值的实验不是继续扩充工程功能，而是补齐因果链：原始聊天/作答到结构化事件的
教师双标数据，出题与判题的人类一致性，真实或严格外部 holdout，多模型和强 Memory 基线
消融，以及延迟回忆、完成率和考试成绩等学习效果。若使用 LLM-as-judge，应抽样由教师盲标，
报告人与模型、人与人之间的一致性，不能只报告模型自评分。

面试时最稳妥的一句话是：

> ExamMem 已分别验证了结构化学习事件下的生命周期维护、推荐决策和自然语言 Memory 检索；
> 结果支持这些子模块达到当前离线门禁，但原始聊天抽取、教师级出题判题和真实学习增益仍是
> 明确未覆盖的研究问题。

完整评测材料：

- [评测方法](./evaluation/methodology.md)
- [v1 Stage09 frozen test](./evaluation/stage09-frozen-test.md)
- [v3 跨学科 frozen test](./evaluation/controlled-v3-frozen-test.zh-CN.md)
- [语义检索 v2 协议](./evaluation/semantic-retrieval-v2.zh-CN.md)
- [语义检索修复与最终结果](./evaluation/semantic-retrieval-v2-remediation.zh-CN.md)
- [DeepTutor 论文 TutorBench](https://arxiv.org/html/2604.26962#S5)
- [DeepTutor 公开 eval 分支](https://github.com/HKUDS/DeepTutor/tree/eval/benchmark)

---

## 21. 推荐的源码走读顺序与断点

不要从所有文件平铺着读。按一条真实请求走，理解会快很多。

### 路线 A：普通 Chat

1. `deeptutor/app/facade.py::DeepTutorApp.start_turn`
2. `deeptutor/services/session/turn_runtime.py::TurnRuntimeManager.start_turn`
3. `TurnRuntimeManager._run_turn`
4. `deeptutor/services/session/context_builder.py::ContextBuilder.build`
5. `deeptutor/runtime/orchestrator.py::ChatOrchestrator.handle`
6. `deeptutor/agents/chat/capability.py::ChatCapability.run`
7. `deeptutor/agents/chat/agentic_pipeline.py`
8. `deeptutor/agents/chat/agent_loop.py`
9. `deeptutor/core/agentic/tool_dispatch.py`
10. 回到 `TurnRuntimeManager._run_turn` 看事件和 assistant message 落库。

观察变量：

```text
payload.capability
UnifiedContext.context_blocks
enabled tool names / tool schemas
messages 每轮新增的 assistant tool_calls 和 role=tool
StreamEvent.type / seq
turn status / assistant persisted content
```

### 路线 B：Native Memory

1. `services/memory/paths.py`
2. `services/memory/trace.py`
3. `services/memory/snapshot/`
4. `services/memory/document.py`
5. `services/memory/ops.py`
6. `services/memory/consolidator/modes/update.py`
7. `services/memory/store.py`
8. `tools/builtin/__init__.py` 中 `ReadMemoryTool` / `WriteMemoryTool`
9. `turn_runtime.py` 搜 `read_l3_concat`
10. `prompt_blocks.py` 看 memory block 如何进入 system prompt。

### 路线 C：ExamMem 正式练习与 Learning Memory

1. `deeptutor_plugins/exam_mem/__init__.py`
2. `exam_mem/practice/provider.py`
3. `exam_mem/practice/workflow.py::ExamPracticeWorkflow.run/start/submit`
4. `exam_mem/contracts/memory.py`
5. candidate builder 与 lifecycle backend
6. relation classifier / lifecycle policy
7. lifecycle applier
8. PostgreSQL repository 和 `storage/models.py`
9. StudentModel projector
10. learning profile / recommendation policy。

观察变量：

```text
PracticeState
checkpoint.row_version
grade artifact identity
LearningEvent.idempotency_key
MemoryScope + slot_key
relation → lifecycle decision → operation
old/new memory semantic version 和 row_version
projection watermarks
```

### 路线 D：教材 Grounding

1. ExamMem API 上传教材 endpoint。
2. `deeptutor_plugins/exam_mem/textbooks.py`
3. `exam_mem/textbooks/structure.py`
4. `PluginSourceHost` / `PluginKnowledgeIndexHost`
5. textbook 与 grounded-learning repositories。
6. `GroundedLearningService.evidence_package`
7. assessment/learning snapshot 创建与恢复。
8. `learning_context.py` 看证据怎样进入 Chat。

---

## 22. 面试高频问题与回答骨架

### Q1：你们用了什么 Agent 框架？

> DeepTutor 没有使用 LangChain 或 LangGraph 负责智能体编排，而是实现了自己的两层运行时。单轮运行管理器先把消息历史、附件、知识库、Memory、模型配置和权限组装为“统一上下文”；对话编排器再根据统一上下文中的能力名称，从能力注册表中选择一个“能力流程”。被选中的能力流程拥有这一整轮的控制权，可以执行确定性阶段，也可以启动“智能体循环”。智能体循环直接调用大模型；如果模型返回工具请求，工具调度器会校验参数和权限，通过工具注册表执行工具，再把工具结果作为 `role=tool` 消息放回对话，进入下一轮模型调用。执行过程中产生的文本、进度、工具调用、工具结果、来源和错误会作为统一事件发送到流事件总线，供网页、CLI、SDK 和会话持久化消费。LlamaIndex 在这里是知识检索实现，不是智能体编排框架。

对应术语如下：

| 英文/源码名 | 中文含义 |
| --- | --- |
| Turn | 一轮用户请求 |
| UnifiedContext | 统一上下文 |
| ChatOrchestrator | 对话编排器 |
| Capability | 能力流程 |
| AgentLoop | 智能体循环 |
| ToolRegistry | 工具注册表 |
| ToolDispatcher | 工具调度器 |
| StreamBus | 流事件总线 |
| StreamEvent | 流事件 |

特别要纠正一个常见误解：**模型请求和工具请求不是“发给 StreamBus 执行”的。**这里有两条并行但职责不同的链路：

```text
控制流：
AgentLoop → 调用 LLM 客户端 → 得到文本/工具请求
AgentLoop → ToolDispatcher → ToolRegistry.execute() → 得到 ToolResult
ToolResult → role=tool 消息 → 下一轮 LLM

事件流：
Capability / AgentLoop / ToolDispatcher
  → StreamBus.emit(StreamEvent)
  → TurnRuntimeManager 订阅
  → WebSocket / CLI / SDK + Session Store
```

`StreamBus` 是观察和传输执行过程的事件通道，不是任务队列或 RPC 总线。真正的 LLM 返回值直接回到 `AgentLoop`，真正的 `ToolResult` 直接回到 `ToolDispatcher`；总线上的 `content`、`tool_call`、`tool_result`、`result` 等事件是给外部消费者看的可观察表示。总线关闭后，编排器发出 `done`，单轮运行管理器再根据事件和持久化结果更新 turn 状态。

#### 追问：探索预算、收束阶段、暂停恢复和强制结束是什么

可以这样回答：

> 默认聊天不是无限循环。每一轮先调用一次模型；模型如果请求工具，就执行工具并把结果放回对话，然后进入下一轮。配置中的 `max_rounds` 是探索阶段的模型轮数预算，当前默认值为 8。预算用完后系统不会立刻粗暴截断，而是进入最多 3 轮的收束阶段，提示模型完成必要的后续操作并给出答案。如果模型在收束阶段仍持续调用工具，系统最后再进行一次禁用工具的模型调用，强制它基于已经收集的信息输出最终答案。因此正常硬上限是“探索预算 + 3 个收束轮次 + 1 次强制结束”。

> 暂停恢复主要用于 `ask_user`。工具返回的不是“结束本轮”，而是 `pause_for_user` 信号。工具调度器仍然生成与该 tool call 配对的 `role=tool` 消息，运行时保持同一个 turn 和 AgentLoop 存活，同时等待前端提交用户回答。回答到达后，系统把它写进对应的 `role=tool` 消息，下一轮模型看到原始问题、工具调用和用户回答后继续执行。这不是创建一个新 turn，所以不会丢失本轮已经调用工具得到的上下文。

> 强制结束是防止模型无限调用工具或在预算边界无法收尾的安全机制。最终调用不再提供工具 schema，因此模型只能输出答案。如果中途模型调用失败但前面已经取得有效工具结果，也会尝试用同样的无工具调用做降级收尾；如果第一轮就失败，因为还没有任何可用工作成果，错误会正常向上抛出，而不是伪造答案。

这三个机制分别解决：预算控制、人在回路以及循环终止性。它们都是运行时代码控制，不依赖模型自觉遵守一句提示词。

#### 面试需要掌握的 LangChain 与 LangGraph

按当前官方定位，LangChain 是较高层的智能体框架，提供统一模型接口、工具抽象、`create_agent` 智能体执行框架和中间件。它的典型循环也是“模型判断 → 调用工具 → 把结果返回模型 → 直到完成”。当前 LangChain Agent 构建在 LangGraph 运行时之上，而不是与 LangGraph 完全独立的另一套执行内核。

LangGraph 是更低层的、有状态的智能体编排运行时。它把工作流表示为：

- State：共享状态，即当前应用快照；
- Node：节点，执行普通代码、LLM 或外部副作用，并返回状态更新；
- Edge：边，根据状态决定下一个节点；
- Checkpointer：检查点，支持持久化、故障恢复和长时间运行；
- Interrupt / Resume：中断与恢复，用于人在回路。

它适合需要显式分支、循环、并行节点、持久化状态和恢复的复杂工作流。LangGraph 可以单独使用，也可以复用 LangChain 的模型和工具组件。

与 DeepTutor 的概念对应关系是：

| DeepTutor | LangChain/LangGraph 中近似概念 | 关键差异 |
| --- | --- | --- |
| `AgentLoop` | LangChain Agent 循环 | DeepTutor 自己维护消息、预算、工具回填和强制结束 |
| Tool + ToolRegistry | LangChain Tool | 都向模型暴露 schema，但 DeepTutor 有自己的授权视图和事件协议 |
| Capability | LangGraph 中的一张图或子图 | Capability 只是整轮控制权协议，内部不要求表示成图 |
| `UnifiedContext` | LangGraph State + Runtime Context | DeepTutor 上下文主要按一次 turn 组装，不是通用图状态容器 |
| `StreamBus` | LangGraph streaming | StreamBus 只广播事件，不负责节点调度和状态归并 |
| `ask_user` + reply queue | LangGraph interrupt/resume | DeepTutor 在 AgentLoop 和会话运行时中显式实现暂停恢复 |
| ExamMem checkpoint 状态机 | LangGraph durable workflow | ExamMem 使用领域表、CAS 和业务状态机，而不是通用图 checkpointer |

面试回答不要说“LangChain 只是调用模型”或“LangGraph 就是画流程图”。更准确的表达是：

> LangChain 提供较高层的模型、工具和智能体执行框架；LangGraph 提供低层的有状态编排、持久执行和人在回路能力。DeepTutor 当前没有依赖它们，而是在自己的 Capability、AgentLoop、ToolDispatcher、StreamBus 和 TurnRuntimeManager 中实现了相近但更贴合现有事件协议与多入口会话模型的能力。对于 ExamMem，正式练习又进一步采用领域状态机和 PostgreSQL checkpoint，因为评分、记忆和恢复需要数据库业务约束，不能只依赖自由智能体循环。

官方资料：[LangChain 总览](https://docs.langchain.com/oss/python/langchain/overview)、[LangGraph 总览](https://docs.langchain.com/oss/python/langgraph/overview)、[LangGraph Graph API](https://docs.langchain.com/oss/python/langgraph/graph-api)。

### Q2：Tool 和 Capability 有什么区别？

> Tool 是模型一次按需调用的原子函数；Capability 是拥有整个 turn 的工作流，能定义多阶段、配置、工具面和 session surface。RAG 是 Tool，deep research 或 exam practice 更适合 Capability。

### Q3：Memory 是不是向量数据库？

> 不能一概而论。DeepTutor Native Memory 当前读取 L3 Markdown 全文，不走向量 top-k。ExamMem Learning Memory 存 PostgreSQL，L2 更新目标靠四维 scope + exact slot；只有自然语言相关记忆检索走 pgvector。Profile 和推荐会读完整当前快照，也不是 top-k。

### Q4：一次答题如何改变掌握度？

> 正式评分生成不可变 L1 event，再按知识点生成 mastery/error candidates；对同 scope 同 slot 的 active/contested 记忆做关系判断；纯策略输出 add/merge/supersede/invalidate/contested/no-op；applier 在事务里做版本化变更和 audit；提交后刷新可重建 L3 StudentModel。

### Q5：答错一次再答对一次，会不会直接覆盖？

> 不会最后写入者胜。旧事件保留；策略按置信度、难度、错误类型、时间衰减、独立事件数和 session 数累计证据。证据不足可以 contested，达到稳定门槛才 supersede 或 merge。

### Q6：为什么 L3 放在事务外？

> L1/L2 是业务真相，必须原子提交；L3 是读优化投影，可由 L1/L2 重建。把昂贵投影放在主事务里会扩大锁和失败面。事务外失败后按 watermark 重试即可。

### Q7：怎样避免重复提交和并发写坏数据？

> 请求有 idempotency key；event 和 grade artifact 有稳定 identity；状态机有持久化 checkpoint；L2 使用 row_version CAS；stale 后重新读取和决策；数据库还有 unique/check/foreign-key 约束。

### Q8：普通聊天为什么不能直接更新正式掌握度？

> 聊天自述是弱、易受 prompt 影响的证据，不能等同于带题目、答案、rubric 和诊断的正式测评。ExamMem context contributor 可以把正式状态只读注入聊天做个性化，但状态更新只从受控学习事件进入。

### Q9：教材 RAG 如何保证不串版本、不串章节？

> 计划绑定精确 textbook version；objective 映射精确 section keys；检索带 metadata filter；证据 snapshot 冻结 index ref/version、section 和 chunk provenance；恢复时版本不一致会要求显式恢复，不静默切换。

### Q10：为什么 ExamMem 做成插件？

> DeepTutor 保持通用 Agent Host，ExamMem 独立拥有考试 taxonomy、练习状态机和强事务 Learning Memory。插件只通过 manifest、Host services 和 bounded context blocks 集成，避免领域模型污染 core，也能独立 migration、测试和演进。

### Q11：Operation accuracy 95.73% 和 Active-state exact 90.42% 有什么区别？

> Operation accuracy 按每一次生命周期操作计分，判断 ADD、MERGE、SUPERSEDE、CONTESTED
> 等决策是否正确；Active-state exact 按检查点计分，要求经历一串操作后，当前全部稳定状态与
> Gold 完全一致。一次错误操作可能让后续多个检查点持续错误，因此操作准确率较高时，最终状态
> 完全一致率仍可能更低。这两个数字来自 v1 历史 frozen test；v3 对应结果是 94.47% 和
> 89.17%。

### Q12：推荐知识点准确率和推荐动作准确率有什么区别？

> 推荐知识点准确率检查“具体选中了哪个 canonical 知识点”，要求目标 ID 与 Gold 一致；
> 推荐动作准确率检查“应该复习、前进还是不行动”等决策类型，即使知识点选错，动作类型仍可能
> 正确。v3 frozen test 中两者分别是 80.83% 和 92.74%，差距说明系统较容易判断当前应采取
> 哪类动作，但在多个相近候选中精确选择目标仍更难。

---

## 23. 容易答错的说法

以下说法都不够准确：

- “DeepTutor 用 LlamaIndex 做 Agent。”——LlamaIndex 用于一条 RAG pipeline。
- “ExamMem 是多 Agent 自动规划系统。”——正式练习主链是确定性状态机，LLM 只在受控边界判断。
- “所有 Memory 都在 pgvector 里。”——Native Memory 是 JSONL/Markdown；ExamMem 才有 PostgreSQL/pgvector。
- “Memory 检索就是 embedding top-k。”——多个读取场景采用 exact slot、全量 snapshot 或全文 concat。
- “答对就把答错覆盖掉。”——L1 不可变，L2 版本化并有冲突生命周期。
- “L3 是最终真相。”——L3 是可重建读模型。
- “Chat 会自动学习用户说的所有东西。”——偏好需要明确写入；正式 mastery 更不能靠聊天自述更新。
- “ExamMem 还没有教材 RAG。”——当前 migration head `0014_textbook_grounding` 已实现版本化教材摄取、绑定、章节过滤检索和证据快照。

---

## 24. 用一句设计原则收尾

DeepTutor 把“不确定的模型推理”限制在 AgentLoop 和工具调用协议内；ExamMem 进一步把正式学习状态的改变限制在确定性工作流、强类型事件、事务化生命周期和可审计证据内。

模型负责理解和判断，代码负责边界、状态、一致性与恢复。这就是两者底层设计最值得讲清楚的主线。
