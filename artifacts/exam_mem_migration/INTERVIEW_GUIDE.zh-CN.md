# ExamMem 智能备考项目面试手册

更新时间：2026-08-18
适用方向：大模型应用开发、Agent/RAG、AI 后端、Python/FastAPI、教育科技

## 1. 先确定你能诚实声称什么

这个项目最有价值的讲法不是“我调用大模型做了一个题库”，而是：

> 我把一个通用 AI 学习工作区扩展成了垂类考研备考系统。系统先把用户确认的考试大纲发布成版本化知识体系，再把每个叶子知识点连接到辅导对话和版本化检测；一次作答会经过固定评分契约、诊断、可审计学习记忆、推荐、恢复和复盘。迁移时我没有继续维护 Fork，而是补了中性插件 API，让 ExamMem 使用独立 PostgreSQL，DeepTutor Core 不依赖 ExamMem。

当前可以用代码和自动化测试证明：

- 插件发现、Browser HTTP 产品 API、Python `DeepTutorApp` 入口、unified WebSocket 与 PostgreSQL 闭环真实可运行；
- Taxonomy、四维 Scope、`slot_key`、L1 append-only、L2 CAS/provenance、L3 可重建、Lifecycle、Trace、checkpoint 和幂等契约受到测试保护；
- 同一考试可有不可变题集版本和多次 attempt；
- DeepTutor 原生 Chat/Quiz/Mastery Path 与 ExamMem 领域边界清楚；
- 五臂受控 Memory 评测已运行 40 条 dev 和一次性 80 条冻结 test；test 上 Lifecycle
  operation accuracy 95.73%、macro-F1 82.49%、active-state exact 90.42%，但推荐准确率
  仅 30.83%，且 80 个 Lifecycle case 有 1 个严格契约失败；
- 工程回归使用确定性 LLM 替身验证契约；Stage09 另用配置的 Host LLM 和本地 Qwen embedding 评估 Memory，二者证据边界分开报告。

当前不能声称：

- 已经通过线上 A/B 证明能提高考研分数；
- 已有真实用户规模、QPS、留存率或商业收入；
- 已完成多源 RAG、教材知识库、图片/视频/PPT 摄取；
- 公共数据集上的准确率就是考研场景效果；
- LLM Judge 的分数等同于教师评价；
- 受控合成 Memory 轨迹的 95.73% 等同于出题、判题、聊天抽取或真实学习增益准确率。

面试时主动讲清边界，可信度通常比堆砌未经验证的指标更高。

## 2. DeepTutor 原生能力和 ExamMem 新增能力

| 层 | DeepTutor 上游已有 | ExamMem 新增/迁移 |
| --- | --- | --- |
| 通用交互 | Chat、Quiz、Solve、Research、Visualize、Mastery Path | 智能备考统一产品入口和受控调用链 |
| 通用基础设施 | Agent loop、Capability/Tool Registry、模型设置、会话、Native Memory、Web UI | 中性 full-stack Plugin API、Host service ports 和 `exam_practice` capability |
| 学习范围 | 通用学习路径和对话上下文 | 用户导入/生成并确认的大纲草稿、不可变发布版 Taxonomy、叶子知识点 Scope |
| 检测 | 原生 Quiz 可生成题目 | 稳定 assessment ID、不可变题集版本、多次 attempt、题目/答案/rubric/source hash 固化 |
| 记忆 | 通用 L1/L2/L3 前端与 Native Memory | 独立 PostgreSQL Learning Memory：四维 Scope、provenance、CAS、Lifecycle、纠错、投影 |
| 可靠性 | 通用 Turn Host 和流式传输 | Practice checkpoint、append-only Trace、幂等、补偿、Resume、Grade Review、派生 Issues |

一句话回答“这是不是把开源项目包装了一下”：

> DeepTutor 提供通用学习 Host，我负责的 ExamMem 是独立的备考领域插件。我的工作不是把菜单改名，而是定义并实现了版本化考试范围、作答状态机、独立业务存储、学习记忆一致性、失败恢复和真实入口装配；同时补中性 Hook，避免 Core 出现 `if exam_mem`。

## 3. 面经调研结论：面试官实际在核对什么

### 3.1 调研口径

本手册在 2026-08-18 使用 AnySearch 检索，并把证据分成三层：

- A 级：招聘方公开岗位描述，用于确认企业明确写出的能力要求；
- B 级：候选人自述的近期面试复盘，用于收集出现过的追问，不视为逐字录音；
- C 级：面试题整理、付费专栏和模拟面试，只用于扩充复习目录，不用于证明“高频”或公司偏好。

本轮检索主动排除了把模拟阿里面试、培训题库或 SEO 文章当作真实面经的做法。社区内容仍有岗位、年限、幸存者偏差，岗位描述也只代表具体职位。因此这里只归纳跨来源一致的主题，不给出伪精确的“出现频率”，也不声称某家公司必问某题。

### 3.2 高频关注点与本项目证据

| 面试官关注点 | 他真正想确认什么 | ExamMem 可展示的证据 | 不能夸大的边界 |
| --- | --- | --- | --- |
| 项目背景与 ownership | 你解决了什么问题，哪些设计/代码确实由你负责 | Fork 到插件的分类、checkpoint 记录、精确提交和关键代码入口 | 不虚构团队人数、用户量或本人未完成的上游能力 |
| 真实调用链 | 不是背框架名，能画出输入、编排、工具、状态和输出 | Browser HTTP、Python App、WebSocket 汇入同一 `exam_practice` workflow 的 PostgreSQL E2E | Browser HTTP 先经过插件 Router；仓库没有专用 `exam_mem/sdk.py` |
| Agent/工作流取舍 | 为什么某些步骤允许模型自治，某些步骤必须确定 | 辅导复用 Agent loop；评分、记忆和审计使用显式状态机与严格契约 | 不把固定工作流包装成“多 Agent 自主规划” |
| 状态、失败与并发 | 断线是否重复写，状态放哪，如何恢复和纠错 | checkpoint、append-only Trace、幂等键、Grade Artifact 复用、L2 CAS、Review/Correction | 没有线上流量就不声称经过高 QPS 验证 |
| 数据与效果评测 | 数据从哪来，怎么切分，有什么 baseline，数字为何可信 | 40 dev/80 一次性 test、五臂同 harness 消融、冻结 hash 和预注册门槛 | 当前数据只覆盖结构化 Memory，不证明出题、判题或学习增益 |
| RAG 与模型基础 | chunk、召回、rerank、幻觉、结构化输出是否理解 | 能说明 DeepTutor RAG 与当前 ExamMem 闭环边界，模型边界使用 schema/版本 | 当前没有完成教材级多源 RAG，不报 Recall@5 |
| 后端与生产化 | AI 应用是否仍有扎实的存储、事务、API 和运维能力 | FastAPI、PostgreSQL、Alembic、事务、CAS、隔离库、构建和安全门禁 | 熔断、限流、线上告警和 A/B 仍是生产化后续项 |

交叉核对后的共同关注点很稳定：候选人复盘会直接追问“为什么做、谁在用、请求如何路由、文档如何处理、RAG 如何实现、效果怎么样”；公开 Agent/RAG 岗位要求 tool use、reflection、chat history、Context Engineering、质量评测以及 Python/FastAPI/PostgreSQL/前端；字节跳动公开 Agent 评测岗位还把真实业务流程抽象、可复现用例、稳定性、一致性和安全性写进职责。准备顺序因此应该是：先讲场景和 ownership，再画调用链并讲一个工程决策，最后用冻结评测和限制收口，而不是先背框架名。

### 3.3 面试前必须填好的“真实性卡片”

下面内容不能由文档替你编造，请在面试前按真实情况写成一页：

```text
项目性质：个人项目 / 团队项目 / 开源贡献（选真实项）
我的职责：需求、架构、后端、前端、测试、迁移中实际负责哪些
协作边界：DeepTutor 上游已有能力、ExamMem 新增能力、工具辅助完成的部分
时间投入：真实起止时间和主要阶段
真实使用：本人演示 / 邀请测试 / 已上线用户（没有就写没有）
可复现数字：测试数量、入口数量、migration head、构建结果
尚未完成：真实模型金标评测、线上 A/B、成本/延迟压测等
```

如果这是个人迁移与产品化项目，可以诚实说：“我承担了产品建模、迁移方案、领域后端、前端集成和验收；DeepTutor 的通用 Chat/Quiz/Agent loop 是上游能力，我通过中性插件契约复用，没有把它说成自己从零实现。”

## 4. 三种项目介绍版本

### 4.1 30 秒版

> 我做了一个基于 DeepTutor 的垂类考研智能备考插件 ExamMem。用户导入并确认考试大纲后，系统生成版本化、分层的知识体系，每个叶子知识点都能进入原生辅导对话，也能生成版本化检测。作答后通过固定评分契约进入诊断、Learning Memory、推荐和复盘。后端用 FastAPI、Pydantic、SQLAlchemy/Alembic、PostgreSQL/pgvector；迁移采用中性插件 API，DeepTutor Core 不直接依赖 ExamMem。重点解决的是 LLM 不确定性下的状态一致性、可追溯记忆和失败恢复，而不只是生成几道题。

### 4.2 两分钟版

> 背景是通用对话和原生 Quiz 都能“出题”，但不能直接构成可信的备考产品。备考需要先有用户确认的考试范围；题目、评分和学习结论必须绑定同一个知识点与考试版本；网络断开或模型失败后还要能恢复，历史记录不能被后来的模型输出静默覆盖。
>
> 我的方案分三层。第一层是版本化 Study Plan/Taxonomy：大纲先成为可编辑草稿，确认后发布不可变版本，只有活跃叶子知识点能进入练习。第二层是可恢复 Practice 状态机：Question、Grade、Knowledge Mapping、Diagnosis、Memory、Recommendation 每一步都有 checkpoint、Trace 和幂等键，题集在 assessment version 中冻结，同一考试可以多次 attempt。第三层是独立 Learning Memory：L1 是 append-only 事件，L2 用 CAS、provenance 和 Lifecycle 管理事实版本，L3 是可以从 L1/L2 重建的投影。
>
> 架构上我没有把 ExamMem 硬编码进 DeepTutor Core，而是抽出中性插件、Capability、Host Turn、Mastery Path 和 Native Memory adapter；ExamMem 使用独立 PostgreSQL。测试覆盖五种 Memory Backend、迁移不变量、Browser HTTP/Python App/unified WebSocket、PostgreSQL 事务和断线重放。现在能证明工程闭环，下一阶段要补考研金标集与真人评测，不能把 fake LLM 的回归测试当作模型效果。

### 4.3 五分钟版结构

按下面顺序讲，不要从框架名开始：

1. 用户问题：学习、练习、复盘分散，通用聊天里的闲聊不能直接当正式掌握度证据。
2. 产品约束：先确认考试范围；知识点、题目、评分、记忆必须版本一致；考试可重复；历史可审计。
3. 架构决策：插件化而不是 Fork；独立 PostgreSQL；Host Hook 保持中性。
4. 核心闭环：大纲 → Taxonomy → 辅导/出题 → Grade → L1/L2/L3 → 推荐/恢复/复盘。
5. 最难问题：LLM 输出不稳定、断线重试、并发写 L2、历史纠错、跨 Scope 泄漏。
6. 证据：真实入口和 PostgreSQL 测试、迁移 hash、Core import gate、生产构建。
7. 证据和限制：报告冻结 test 的真实数字，同时说明教师金标、出题判题和在线学习效果仍未覆盖。

## 5. 架构与真实调用链

### 5.1 架构图

```text
Browser UI ──HTTP──> ExamMem plugin Router ──PluginTurnHost──┐
                                                            │
Python DeepTutorApp API ─────────────────────────────────────┼──> Turn Runtime
                                                            │
unified WebSocket ───────────────────────────────────────────┘
                                                            │
                                                            ▼
                                              Capability Registry: exam_practice
                  │
                  ▼
       ExamMem PracticeRuntimeProvider
                  │ pins runtime + taxonomy + grader
                  ▼
Question → Grade → Catalog KP validation → Diagnosis
                  │
                  ▼
      Selected Memory Backend (no fallback)
                  │
       ├─ none / native / append_only / vector
       │        → mode-specific declared side effect
       │
       └─ lifecycle
                → L1 + L2 CAS/provenance + lifecycle audit
                  (one database transaction)
                → post-commit L3 rebuild (new transaction)
                  ▼
 Recommendation → checkpoint → append-only Trace
                  ▼
 Resume / Correction / Grade Review / derived Issues
```

这里的“Python 入口”是 `DeepTutorApp.start_turn/stream_turn` 的应用门面，测试中按 SDK-style 入口验证；项目没有另造一个专用 `exam_mem/sdk.py`。Browser 的产品 HTTP API 也不是绕过 Host 直调 workflow，而是由插件 Router 组装上下文，再通过 `PluginTurnHost` 进入相同 Turn Runtime。

### 5.2 从大纲到辅导

```text
PDF/TXT/MD、公开 URL 或模型创建请求
  → StudyPlanOutlineImporter 只提取标题层级
  → 用户编辑草稿
  → 发布不可变 Study Plan version + Taxonomy version
  → 每个叶子 objective 映射一个 Host Mastery Path
  → 首次进入自动发起辅导，后续恢复同一 Chat session
```

关键点：发布之前允许编辑，发布之后不原地修改。新大纲产生新版本，旧考试仍可解释。

### 5.3 从练习到记忆

```text
PracticeWorkbench
  → POST /api/v1/exam-mem/practice/generate
  → 中性 Host Turn 调用原生 Quiz
  → ExamMem 固化 question/answer/rubric/source hash/KP IDs
  → POST /api/v1/exam-mem/practice/answer
  → ExamPracticeCapability
  → PracticeRuntimeProvider
  → ExamPracticeWorkflow
  → grader contract
  → 已固化 catalog KP 的严格 Taxonomy 校验
  → diagnosis
  → TransactionalPracticeMemoryWriter
  → recommendation + checkpoint + Trace
```

主要代码入口：

- 插件装配：`deeptutor_plugins/exam_mem/__init__.py`
- HTTP 产品 API：`deeptutor_plugins/exam_mem/api.py`
- 大纲导入与学习计划适配：`deeptutor_plugins/exam_mem/study_plan.py`
- Capability：`exam_mem/practice/capability.py`
- Runtime 依赖装配：`exam_mem/practice/provider.py`
- 状态机：`exam_mem/practice/workflow.py`
- Learning Memory 生命周期：`exam_mem/backends/lifecycle.py`
- PostgreSQL repositories：`exam_mem/storage/`
- 前端：`web/components/exam-mem/`
- 三入口 PostgreSQL E2E：`tests/exam_mem/practice/test_real_entries_postgres.py`

## 6. 六个从症状到根因的工程决策

讲故障时不要按“报错—加判断—再报错”的时间线复述。统一使用下面的结构：

```text
不变量 → 可复现症状 → 候选假设与实验 → 根因 → 最小模型修正 → 回归证据 → 剩余边界
```

这样讲的是工程判断，而不是补丁数量。

### 6.1 Fork 耦合：从复制功能转为定义插件边界

- 不变量：DeepTutor Core 不认识任何垂类插件，禁用 ExamMem 后原生系统仍可测试、构建。
- 症状：冻结 Fork 的领域代码渗入 Host 注册、配置、数据库和 UI，同步上游时无法判断哪些差异必须保留。
- 分析：不是“复制哪些文件”的问题，而是缺少稳定所有权边界。
- 干净方案：把源实现分为 ExamMem 自有领域代码、中性 Host Hook、不可迁移 Fork 耦合；前两类分别进入 `exam_mem/` 和 `deeptutor.plugins`，第三类丢弃。没有增加 `if exam_mem` 或无限兼容层。
- 证据：Core import gate、插件关闭回归、原生 production build、插件 Browser/Python/WebSocket/PostgreSQL E2E。
- 取舍：多一层 Contribution DTO 和 Host service port，但换来独立发布节奏、可审计装配和更低的上游合并成本。

### 6.2 知识点漂移：canonical ID 不能交给模型重建

- 不变量：一次练习的题目、评分、记忆必须绑定同一个已发布 taxonomy leaf。
- 症状：题目已在不可变 catalog 中绑定叶子知识点，作答阶段又做语义映射；模型返回 `unknown` 时只留下 L1，L2/L3 为空。
- 排查：先验证 taxonomy version 是否存在，再比较 catalog ID、模型映射和持久化 Scope，证明错误发生在重复推断而不是数据库漏写。
- 干净方案：catalog 的 `knowledge_point_ids` 是权威；下游只校验存在、active、leaf、unique。`unknown` 或越界 ID 在副作用前 fail closed。只有没有 canonical ID 的显式入口才允许语义映射。
- 证据：catalog/taxonomy 契约测试、PostgreSQL 闭环和跨 Scope 泄漏测试。
- 原则：确定性身份一旦固化，下游不能让概率模型“再猜一次”。

### 6.3 代理断线与 409：传输失败、业务失败和契约失败必须分层

- 不变量：相同答案的恢复不能重复评分或重复写记忆；版本不兼容不能静默降级。
- 症状：浏览器显示 `socket hang up` 或“返回值不是 JSON”，后端同时可能已经记录 HTTP 409、taxonomy version 不存在或 `grader_contract_version_mismatch`。
- 排查：按浏览器代理日志 → 后端状态码/错误码 → checkpoint → Grade Artifact → 数据库副作用顺序核对，不能仅凭前端断线判断业务成功或失败。
- 干净方案：请求携带稳定 idempotency key；状态转换落 checkpoint；同答案重放复用已有 Grade Artifact；Trace 记录 pinned/saved/effective 版本和失败阶段。网络瞬断按同一键恢复，taxonomy/grader 契约错误 fail closed，由配置或数据迁移修复。
- 拒绝的做法：换幂等键盲重试、捕获所有异常后 fallback、把服务端文本错误强行当成功 JSON。
- 剩余边界：尚未用线上流量证明高并发行为，因此只声称契约和集成测试覆盖。

### 6.4 L1/L2/L3：不是三个页面，而是三种不变量

- L1 是不可变证据，回答“发生过什么”；
- L2 是有 Scope、provenance、CAS 和生命周期的当前事实，回答“如何解释证据”；
- L3 是带 source watermark 的可重建投影，回答“当前整体学习状态是什么”。

尝试把它们塞进一个可更新 JSON 会同时失去来源、并发冲突、纠错历史和重建能力。最终设计不是为了复制 DeepTutor 的三个卡片，而是复用其前端心智模型，后端仍由 ExamMem 独立 PostgreSQL 维护领域真值。L1 明细负责信息透明和纠错入口；考试复盘只呈现题目、答案、题解、诊断和版本进步，不把全部记忆治理细节重复一遍。

### 6.5 Lifecycle 评测：先修证据模型，再看冻结 test

- 不变量：只能用 dev 调整实现；冻结 test 一次性消费，运行后不得据此改代码再挑最好数字。
- 初始现象：dev 中弱证据会过早形成方向性状态，LOW/IMPROVING 的互补证据不能持续累积，多值 error-pattern 的关系决策可能越过 slot 契约。
- 候选方案：增加阈值特判或放宽模型输出虽然能减少报错，但会掩盖错误证据模型，并让生命周期语义随 case 漂移。
- 干净方案：初始 fixture 只作 temporary provenance，不伪造方向；LOW/IMPROVING 在未达晋级阈值时通过 MERGE 累积；关系分类 schema 只允许当前 slot 支持的关系，越界输出严格失败并受限重试。
- 防泄漏：40 条 dev 与 80 条 test 有独立 hash；test release 要求显式授权、完整五臂、无过滤器和原子 claim。
- 结果：冻结 test 上 operation accuracy 95.73%、macro-F1 82.49%、active-state exact 90.42%、cross-scope leakage 0%；Lifecycle 完成 79/80，推荐准确率仅 30.83%。最后两个限制必须和最佳数字一起讲。

### 6.6 TestClient 卡住：先证明环境边界，不为绿色测试改业务

- 症状：若干 FastAPI/Starlette `TestClient` 用例打印进度后不退出，最初看起来像 Starlette、httpx2 或 AnyIO 依赖不兼容。
- 实验：在临时环境比较版本后仍不能形成稳定解释；进一步缩小到纯 Python `asyncio.run_coroutine_threadsafe()`，发现受限命令沙箱连跨线程 event loop wakeup 都无法完成。
- 根因：执行沙箱能力边界，而不是 ExamMem 路由、权限断言或依赖组合。
- 处理：相同解释器和依赖在允许跨线程唤醒的本机进程运行 asyncio 探针、最小 TestClient 和全部 21 个 TestClient 文件，284 passed 并正常退出。
- 拒绝的做法：删除权限断言、加入超时特判、未经证据升级依赖。最终没有修改核心代码或依赖，只把正确执行环境写入 Runbook。

## 7. 高频追问与参考回答

### 7.1 产品和业务

#### Q1：DeepTutor 原来就能 Quiz，你的项目有什么必要？

原生 Quiz 解决一次对话里的题目生成；ExamMem 解决备考域的长期一致性：考试范围先发布、题目绑定 canonical leaf、同一 assessment 多版本多 attempt、评分写入独立 Learning Memory、失败可恢复、结论可纠错和复盘。两者关系是 Host 能力与领域编排，不是重复实现 Quiz。

#### Q2：为什么聊天记录不能直接当学习记忆？

聊天可能包含闲聊、探索和不确定表达。正式 Practice 有固定 Scope、题目、rubric、grader version 和幂等身份，证据质量更高。普通 Chat 只能先成为旁路 observation/clue，必须由用户确认，不能直接改变掌握度。

#### Q3：为什么考试范围必须先发布？

如果范围可以随题目任意创建，同一个考试 ID 下的知识点含义会漂移，历史成绩不可比较。草稿允许编辑，发布版提供不可变引用；修改范围生成新版本。

#### Q4：同一个考试为什么要多个版本？

稳定 assessment ID 表示同一检测目标；immutable version 固定某次题集和 rubric；attempt 表示用户的一次作答。这样既能重复同一卷，也能生成新卷，同时不覆盖历史。

### 7.2 Agent、RAG 和模型

#### Q5：这是 Agent 还是固定工作流？

核心 Practice 是强约束工作流，因为评分、记忆和审计不能让模型自由规划。模型只在出题、评分、错误分析、关系分类等单一工具边界内工作。学习辅导复用 DeepTutor 的 Agent loop。我的选择标准是：开放式辅导适合 Agent，改变业务真值的链路适合显式状态机和结构化契约。

#### Q6：为什么不用 LangGraph？

当前状态只有七个冻结阶段，转移和持久化语义很明确，项目已有 Capability/Tool/Trace 抽象。再引入图框架会增加状态序列化和调试层，而不能消除数据库事务、幂等和领域校验。若以后出现大量条件分支、并行子图和人工审批节点，再重新评估。

#### Q7：项目做了 RAG 吗？

DeepTutor Host 有多种 RAG 引擎，但 ExamMem 当前闭环没有声称完成通用教材 RAG。大纲导入只提取结构标题；练习附件只作为一次出题的临时上下文，持久化的是题目/rubric 和 source hash。未来多源摄取需要单独的版权、分块、索引、引用和评测设计。

#### Q8：怎么防止模型输出非法 JSON？

在模型边界使用结构化 response schema、Pydantic 严格模型、有限重试和版本字段；解析失败不会进入数据库。关键业务身份不从自由文本推断。超过重试上限记录明确 error code 和 checkpoint，由恢复流程处理，不做无限 fallback。

#### Q9：Prompt 如何做中英文？

语言是显式请求契约，不只翻译按钮。中文模式使用中文 system/user 模板并明确要求题目、理由、诊断与建议全部中文；英文同理。结构化字段和值保持稳定，展示文本按语言变化。测试应检查 prompt 选择和输出契约，不测试某个模型的固定措辞。

### 7.3 数据库、事务和一致性

#### Q10：为什么 ExamMem 必须独立 PostgreSQL？

Learning Memory 有自己的迁移、不变量、append-only trigger、CAS 和 pgvector。放进 DeepTutor 内部库会造成隐式耦合、升级冲突和错误的真值共享。中性 Host adapter 只传明确 DTO，数据库之间没有外键或直接读写。

#### Q11：L2 的 CAS 解决什么？

两个并发答案可能都基于同一个当前版本生成新事实。更新时比较预期 `row_version`；只有一个提交成功，另一个得到冲突并重新读取/决策，避免后写静默覆盖先写。provenance 与 Change Log 在同一事务提交。

#### Q12：为什么 L1 要 append-only？

L1 是作答与纠错证据。直接 UPDATE 会让后来的结论无法重放和审计。纠错用新事件表达“旧证据哪里不准确”，不擦除旧事实。数据库 trigger 和 repository 都防止更新/删除。

#### Q13：L3 丢了怎么办？

L3 不是真值。按 Scope 从 L1/L2 和水位重建，写入新的 projection version。重建失败形成 `projection_pending` issue，不能拿旧 L3 反向覆盖事实。

#### Q14：事务边界在哪里？

一次 Lifecycle Memory 写入在 `engine.begin()` 中原子提交 L1、L2 版本/provenance、Lifecycle Decision 和 Change Log。外部 LLM 调用在事务外完成，避免长事务占用连接。L3 明确不在这个事务里：提交成功后由 `refresh_after_commit()` 使用新事务重建；失败时保留可重建请求/Issue，不能回滚已经成立的 L1/L2 真值。这正是 L3 被定义为派生投影而非业务真值的原因。

#### Q15：如何防止跨用户读写？

`user_id` 来自 Host 认证上下文，不接受客户端自报；repository 查询同时带 user/exam/subject，Memory 再带 namespace/slot。API 只允许用户选择自己可见的考试和科目。测试覆盖 Scope 不匹配和跨上下文事件查询失败。

### 7.4 可靠性和可观测性

#### Q16：`socket hang up` 和 HTTP 409 有什么区别？

`socket hang up` 是前端代理看到的连接中断，可能发生在后端异常返回或进程连接关闭；409 是后端已经给出可解释业务冲突，例如 taxonomy/grader contract 不匹配。前端必须优先解析结构化后端错误；不能把所有错误都显示成 JSON parse failure。

#### Q17：为什么契约版本不匹配不能自动 fallback？

评分版本决定后续记忆含义。若偷偷换旧 grader 或忽略字段，历史同分不同义。正确行为是 fail closed、记录 pinned/saved/effective 版本，让运维修配置或恢复 checkpoint。

#### Q18：Trace 和日志有什么区别？

日志用于进程诊断，可能滚动或采样；Practice Trace 是 append-only 业务审计，绑定 trace ID、阶段、输入/输出摘要、版本、LLM 调用数和失败状态。恢复和 Review 依赖 Trace，不依赖 grep 日志。

#### Q19：五个 Memory Backend 为什么存在？

它们是可比较的实验/产品模式：`none`、`native`、`append_only`、`vector`、`lifecycle`。所有模式走同一 Practice 状态机，只改变明确的副作用；缺少依赖时失败，不自动降级。这让后续评测能比较“无记忆/通用记忆/领域记忆”，也防止代码分叉。

### 7.5 插件和开源工程

#### Q20：怎样证明 Core 没依赖 ExamMem？

代码目录和 import gate 双重约束：领域代码在 `exam_mem/`，装配在 `deeptutor_plugins/exam_mem/`；测试扫描 `deeptutor/` 的直接依赖。禁用插件并移除 DSN 后运行 DeepTutor 原生测试和生产构建，仍应通过。

#### Q21：插件如何被加载？

Host 从 `deeptutor.plugins` entry point 和编译期 `deeptutor_plugins` namespace 延迟发现工厂；ExamMem 模块导出 `get_plugin()`，代码中的 `PluginManifest` 声明 capability、tools、router、navigation、settings 和 migration metadata。禁用插件时工厂不会被实例化。Host 只认识通用 Contribution DTO，不认识 ExamMem 表或业务状态。

#### Q22：为什么 migration 也放进插件包？

业务表属于 ExamMem。源码运行可用根 `alembic.ini`；wheel 安装则用随包发布的 `exam_mem/storage/alembic.ini` 和 `python -m exam_mem.storage.migrations`，避免用户必须拥有 Git checkout。`0001`～`0006` 用 hash 测试冻结，新改动只能追加 revision。

#### Q23：开源前你审计什么？

许可证/第三方声明、secret 扫描、依赖漏洞、容器与 wheel 内容、CI 是否真实启动 pgvector、插件禁用测试、迁移 head/hash、全量 pytest、lint/type/build、演示脚本、数据库副作用和延期边界。发现风险会区分代码缺陷、依赖升级和文档限制，不把 warning 隐藏成“全绿”。

### 7.6 前端和产品体验

#### Q24：为什么学习档案默认要“全部章节”？

若默认选择第一个章节，后端 Scope 会隐式只查这一组叶子，用户可能误以为没有记忆。默认全部章节不发送知识点过滤；用户主动选章节/知识点后才收窄。这是查询语义修复，不是用前端造假数据。

#### Q25：如何展示 L1/L2/L3 的版本？

L1 按时间线展示不可变事件；L2 以当前事实为主，同时展开 version/provenance/lifecycle 链；L3 展示当前投影和 source watermark，并提供重建状态。专业、版本、科目、章节、知识点、namespace、lifecycle 都是筛选维度，而不是把不同 Scope 混在一个列表。

### 7.7 安全和隐私

#### Q26：Prompt injection 怎么办？

大纲/附件是非可信内容，只能作为限定工具上下文，不能改变 system policy、数据库 Scope 或工具权限。结构身份由服务端决定；文件类型、大小、URL、超时和输出 schema 都要校验。当前附件不持久化原文，减少数据面，但未来 RAG 仍需来源授权和隔离索引。

#### Q27：数据库密码放哪里？

只读进程环境 `EXAM_MEM_DATABASE_URL`，日志只打印脱敏摘要；不写 JSON/YAML、前端或仓库。演示脚本固定密码仅用于 `127.0.0.1` 隔离库，正式环境必须独立账号、强密码和 secret manager。

#### Q28：为什么依赖漏洞也是发布阻塞？

Next.js、Mermaid、DOM sanitizer 都处在处理用户输入或 HTTP 请求的路径。测试通过不能证明没有已知 CVE。发布门禁必须包含锁文件审计、受控升级、回归和残余风险说明；不能因为漏洞在上游依赖就忽略。

#### Q29：外部大纲或论文归档如何防路径穿越和解压炸弹？

这是 DeepTutor Host 文件处理层已有并经安全补强的能力，ExamMem 导入入口复用中性文本提取服务，不能说成 ExamMem 独有。实现上不能直接使用不带防护的 `extractall`。TAR 使用 Python 官方 `data` extraction filter，并额外拒绝
符号链接、硬链接、设备文件和超限成员；ZIP 先校验每个相对路径、文件类型、加密标志、
单项/总大小和压缩比，再限额流式写入。失败路径必须清理临时目录。测试要同时构造
`../../`、绝对路径、链接、伪造大小、高压缩比和正常嵌套目录，不能只测正常文件。

#### Q30：本地开发服务监听 `0.0.0.0` 有什么风险？

如果认证默认关闭，监听所有网卡会让同一局域网设备进入单用户管理员语义。本地启动应
默认绑定 `127.0.0.1`；确需跨设备访问时先开启认证，再显式选择 `0.0.0.0`。容器内部
runner 或外部 webhook 可以保留全网卡监听，但必须有网络隔离或入站鉴权，并在安全扫描中
写明接受理由。

### 7.8 面经中反复出现的压力追问

#### Q31：这个项目哪些部分是你本人做的？

先按“上游—我的工作—证据”回答。DeepTutor 上游提供 Chat、Quiz、Agent loop、Native Memory 和基础 Web UI；我的工作是 ExamMem 的领域建模、冻结源迁移、插件边界、版本化学习计划/检测、Learning Memory、可靠性链路、产品 UI 和验收。随后指向一个自己最熟的提交、测试和故障故事。若使用了 AI 编程工具，应说明自己负责需求判断、架构取舍、审查与验收，不把工具生成等同于未经理解的本人实现。

#### Q32：没有真实用户，为什么相信它有价值？

分开回答“问题是否存在”和“方案效果是否已证明”。备考范围漂移、成绩不可比较、闲聊污染正式记忆、断线重复写都是可以从产品流程和代码重现的问题；当前工程测试证明解决方案满足契约。但用户学习增益尚未被证明，下一步需要邀请测试、领域金标和前后测/A/B 测试。不能因为没有上线就说项目没有工程价值，也不能把工程正确性偷换成业务效果。

#### Q33：你的评测集多大，为什么是这个规模？

当前有 120 条 `exam_mem_controlled_v1` 受控多轮 Memory 轨迹，覆盖 12 类生命周期场景；40 条 dev 用于修正，80 条 test 通过一次性 release 冻结运行。这个规模适合验证状态机、关系决策、污染、Scope 和检索的早期消融，不足以代表中国考研题目分布，也没有教师双标。下一步仍需 200～500 条按来源/章节隔离的领域金标，分别评估大纲、出题、判题和推荐；有真实用户后再建立时间切片 bad-case 集。

#### Q34：你拿什么做 baseline，提升从何而来？

Memory 子系统使用 `none/native/append_only/vector/lifecycle` 五臂同 harness 消融。冻结 test 上，Lifecycle 的 active-state exact 为 90.42%，比 none 的 7.50% 高 82.92 个百分点；stale rate 为 3.32%，比 append-only/vector 的 89.28% 低 85.96 个百分点；推荐准确率 30.83%，比 baseline 的 3.75% 高 27.08 个百分点。代价是完成率 79/80、Weak Recall@5 低 10 个百分点、平均延迟是 vector 的 1.49 倍。operation accuracy 95.73% 只能与 Gold 比，其他 backend 不暴露 typed operation，所以不能伪造这一项的 baseline。出题和判题的数据集尚未完成，不能借用 Memory 分数。

#### Q35：这是 AI 项目，为什么还会问数据库、Redis、Python 和网络？

因为模型调用之外仍是在线系统：认证上下文决定用户隔离，事务和 CAS 决定记忆是否被覆盖，连接池和超时影响吞吐，HTTP/代理错误决定能否安全重试。ExamMem 可以重点讲 PostgreSQL 事务、append-only trigger、Alembic、幂等和 `socket hang up`；Redis/MQ 没有实际使用就只回答原理和适用场景，不硬说项目中用了。

#### Q36：为什么不用一个更强模型把这些规则都做掉？

模型适合生成、解释和关系判断，不适合承担租户身份、taxonomy 主键、事务提交和幂等这些确定性职责。更强模型也会超时、版本漂移和输出越界。ExamMem 的做法是让模型在窄 schema 内做语义判断，服务端负责 Scope、版本、候选集和副作用边界；模型失败时恢复，不让它重建业务真值。

#### Q37：95.73% 是不是你项目的最终准确率？

不是。它是 80 条冻结受控 Memory 轨迹中 398 个 Gold lifecycle operation 的 accuracy，只评估结构化 LearningEvent 之后的记忆操作。题目生成、判题、聊天抽取和学习增益都是 N/A。面试时应同时报告 macro-F1 82.49%、完成率 79/80、推荐准确率 30.83% 和数据是合成轨迹，避免用一个高数字掩盖边界。

#### Q38：推荐只有 30.83%，为什么还值得讲？

因为这揭示了系统已经把“正确维护当前状态”和“给出好的下一步推荐”拆成两个可独立验证的问题。Lifecycle 显著减少 stale state，但 prerequisite、难度和多样性的 Gold 还不充分。正确后续是单独建设推荐数据与指标，不是调整口径把 30.83% 隐藏掉，也不是让规则无限 fallback。

#### Q39：你如何证明修复不是在 test 上过拟合？

所有实现修正只看 40 条 dev；80 条 test 有独立 hash，release 必须完整五臂、无 case/scenario filter，并通过原子 claim 限制一次。冻结结果发布后没有再修改代码重跑。未来修改只能使用 dev 或创建新的数据版本，不能消费同一 test 挑最好结果。

#### Q40：遇到测试卡住时，为什么没有直接升级依赖？

因为“依赖不兼容”只是候选假设。把问题缩小到纯 asyncio 后，受限沙箱同样无法跨线程唤醒；相同依赖在本机进程中最小探针和 284 个 TestClient 用例都通过。证据指向执行环境，所以正确修复是明确门禁环境，而不是在没有根因的情况下改 Starlette/httpx2/AnyIO 或业务权限代码。

## 8. 到底需不需要数据集

结论：如果要声称“模型效果好”或比较方案优劣，就需要数据集。当前证据分三层：工程回归证明契约；`exam_mem_controlled_v1` 证明受控结构化 Memory 生命周期；教师标注的考研领域金标和真实用户实验仍未完成。

截至 2026-08-18，Memory 冻结 test 已在真实 PostgreSQL、Host LLM 和本地 Qwen embedding 上完成五臂比较。它从已校验的结构化 `LearningEvent` 开始，因此不能回答：聊天抽取是否正确、生成题是否正确、难度是否合适、评分理由是否可靠、推荐是否真的帮助学习。完整数字、防泄漏方法和数据库副作用见 [Stage09 报告](../stage09/STAGE09_REPORT.zh-CN.md)。工程回归中的确定性 fake LLM 则用于证明状态机、事务、迁移、Scope、幂等、恢复、接口和构建没有退化，两类证据不能混写。

### 8.1 公共数据集能做什么

| 数据/框架 | 可用于 | 不能替代 | 许可/适配提醒 |
| --- | --- | --- | --- |
| C-Eval | 中文学科选择题基础能力、相关科目 smoke baseline | 考研大纲解析、主观题评分、个体推荐 | 数据为 CC BY-NC-SA 4.0，不应默认打包进 Apache-2.0 产品 |
| EduMath / EQGEVAL | 教学目标对齐的数学题生成方法与评价维度 | 中国考研数学全部题型 | 16K 数学题，先核对数据发布许可再下载/再分发 |
| QGEval | 流畅、清晰、简洁、相关、一致、可回答、答案一致七维 rubric | 你的考研金标答案 | 更适合借鉴评价协议，而非直接当领域测试集 |
| LearningQ | 长教育文档上的问题生成研究 | 中文考研知识体系和评分 | 230K 文档-问题对；下载与再分发许可需单独核验 |
| EdNet/ASSISTments | Knowledge Tracing 方法研究、交互序列 baseline | ExamMem 的 L1/L2/L3 业务正确性 | EdNet 是 CC BY-NC 4.0；ASSISTments 不同版本条款不同，题干可能需申请 |
| RAGAS | 将检索质量、忠实度和生成质量拆开 | 人类教师金标与学习效果 | ExamMem 当前并未完成通用教材 RAG，不应为了指标硬套 |

### 8.2 分阶段建设领域评测集

不要一开始追求十万条弱标签：

1. 开发冒烟集：30～50 个高价值 case，覆盖主要题型、难度、错因和异常输入；用于每天快速比较 prompt/schema，不用于宣称上线级效果。
2. 冻结金标集：200～500 个高质量 case，按来源、章节和时间隔离，关键样本教师双标；用于正式 baseline/ablation 和面试中的可复现结果。
3. 真实 bad-case 集：有试用用户后，从失败 trace 中脱敏抽样，单独保留时间切片；不能回灌后又继续当未见测试集。

一个 case 至少包含：

```json
{
  "case_id": "math1-limit-0001",
  "source_id": "licensed-syllabus-or-owned-note",
  "exam": "考研数学一",
  "subject": "高等数学",
  "chapter": "函数极限与连续",
  "knowledge_point_id": "published-leaf-id",
  "learning_objective": "能够使用等价无穷小求极限",
  "question_type": "calculation",
  "difficulty": 0.6,
  "question": "...",
  "reference_answer": "...",
  "rubric": ["..."],
  "student_answer": "...",
  "gold_score": 6,
  "gold_correct": false,
  "gold_error_type": "condition_misuse",
  "gold_recommendation": ["..."],
  "language": "zh-CN",
  "annotators": ["teacher-a", "teacher-b"]
}
```

至少覆盖：

- 大纲层级解析、同名知识点、跨章节引用、非法/过粗节点；
- 选择、填空、计算、证明以及部分正确答案；
- 容易/中等/困难、常见错因、无效答案、答非所问；
- 中文与英文输出契约；
- out-of-scope、无答案、材料冲突、prompt injection；
- 同一知识点重复考试和不同 assessment version；
- 断线、重复请求、checkpoint 恢复、并发 CAS、纠错与重建。

### 8.3 如何切分，避免数据泄漏

- 不要随机拆分近似题；按来源、章节和时间分组。
- 测试集至少 hold out 完整章节或整份来源文档。
- prompt 调优只看 train/dev，最终 test 冻结。
- 记录模型版本、prompt hash、taxonomy/grader/config revision。
- 去重既做文本相似度，也检查相同模板只换数字的题。

### 8.4 标注协议

- 关键题由两名标注者独立完成，分歧交给第三人仲裁；
- 先写 rubric 再看模型答案，减少迎合模型；
- 保存分歧率和 Cohen's kappa/一致率，不只保存最终标签；
- LLM Judge 只能做辅助扩展，必须先与人工标签校准；
- 题目版权和考生隐私必须有来源、用途和删除策略。

### 8.5 指标矩阵

| 子系统 | 建议指标 |
| --- | --- |
| 大纲/Taxonomy | 层级 precision/recall/F1、叶子 exact match、重复率、out-of-scope 拒绝率 |
| 题目生成 | 正确性、answerability、objective alignment、难度校准、答案一致性、新颖性/去重、七维 QGEval rubric |
| 判题 | correct/error-type macro-F1、分数 MAE、Quadratic Weighted Kappa、Spearman、理由证据支持率 |
| 推荐 | prerequisite violation rate、覆盖/多样性；有金标时 Recall@K、MRR/NDCG |
| Learning Memory | L1 完整率、L2 provenance 正确率、Scope 泄漏率（目标为 0）、CAS/补偿成功率、L3 重建等价率 |
| 系统 | 任务成功率、恢复成功率、重复副作用率（目标为 0）、P50/P95 延迟、token/成本、重试率 |
| 学习效果 | 同知识点重复 attempt 的变化、前测/后测；没有对照实验时不能声称因果提升 |

### 8.6 必做 baseline/ablation

- 无记忆 vs Native Memory vs append-only vs vector vs lifecycle；
- 直接 LLM 一步输出 vs 结构化 Practice 工作流；
- 题目 knowledge point 二次 LLM 映射 vs catalog canonical ID 校验；
- 无 checkpoint vs checkpoint/replay；
- 不同模型、prompt、温度和 grader version；
- 有/无来源上下文，但必须在合法授权的同一数据切分上比较。

面试时应该这样回答当前证据边界：

> 当前我完成了两层评测：工程回归验证真实数据库闭环；120 条受控 Memory 轨迹用 40 dev/80 一次性 test 比较五个 backend，冻结 test 的 Lifecycle operation accuracy 是 95.73%、state exact 是 90.42%。这只说明结构化 LearningEvent 之后的记忆治理，不代表出题、判题或提分效果。下一步是 200～500 条按来源和章节隔离、教师双标的考研金标；公共 C-Eval/QGEval 只作外部参考，不偷换成产品结论。

## 9. 面试演示脚本

控制在 8～10 分钟：

1. 用 `start-demo.sh --dev` 展示一键隔离 PostgreSQL 和 migration head。
2. 在学习计划导入一份你有权使用的小型大纲，展示“草稿可改、发布版不可变”。
3. 点一个叶子知识点进入 Mastery Chat，说明 session link 和首次自动辅导。
4. 从同一知识点生成检测，展示 assessment ID、version、attempt。
5. 提交一个故意错误答案，展示中文评分、诊断、推荐。
6. 打开 Learning Memory，展示 Scope 筛选、L1、L2 version/provenance、L3。
7. 打开 Review/Trace，解释断线后如何 Resume、为什么同幂等键不会重复写。
8. 最后展示测试和已知限制，不现场演示未完成的多源 RAG。

准备一个“工程决策故事”，从第 6 节选择自己最熟的一条。推荐现场讲 canonical ID 或 TestClient：先写不变量，再展示最小复现和被排除的假设，最后落到根因、修正和回归证据。不要按时间罗列改过多少次代码。

## 10. 简历写法

不要填写不存在的用户量和准确率。可以写：

- 将独立 ExamMem 迁移为 DeepTutor 第一方全栈插件，设计中性 Capability/Router/Settings/Migration/Host Service contributions，保持 Core 对 ExamMem 零直接依赖。
- 实现“版本化大纲 → 叶子知识点辅导 → 不可变题集版本/多次检测 → 评分诊断 → Learning Memory → 推荐恢复复盘”闭环，并用独立 PostgreSQL 隔离业务真值。
- 设计 L1 append-only、L2 CAS/provenance/Lifecycle、L3 可重建投影，以及 checkpoint、Trace、幂等和补偿机制，覆盖五种 Memory Backend。
- 建立真实 Browser HTTP/Python App/unified WebSocket/PostgreSQL 回归与 migration hash 门禁；明确区分确定性工程测试和待建设的模型效果金标集。
- 构建 `none/native/append-only/vector/lifecycle` 五臂受控评测，在 80 条一次性冻结 test 上取得 95.73% Lifecycle operation accuracy 和 90.42% active-state exact；相对 none 提升 82.92 个百分点，同时保留 1/80 执行失败和推荐准确率 30.83% 的限制。

如果量化，只使用可以从当前报告复现的数字，并注明数据集、子系统、分母和限制；不能把 Memory operation accuracy 写成整个产品或模型的准确率。

## 11. 反问面试官

- 团队目前更缺模型能力优化，还是评测、数据闭环和工程可靠性？
- 线上 Agent 最常见失败来自检索、规划、工具、模型输出还是状态持久化？
- 是否已有领域金标集、bad-case ledger 和人工抽检流程？
- 对教育场景，业务最终优化的是答题正确率、学习增益、留存还是教师效率？
- 模型/Prompt 版本与业务数据如何做可追溯和回滚？

## 12. 资料来源与使用方式

以下资料由 AnySearch 于 2026-08-18 检索并复核。等级表示“可用于证明什么”，不表示内容一定正确，也不代表每家公司都会逐题询问。

### 岗位与面经

- A 级，[字节跳动大模型/Agent 评测工程师](https://jobs.bytedance.com/experienced/position/7587252577764870405/detail)：官方岗位把真实业务流程抽象为可复现评测用例，并覆盖能力、稳定性、一致性、安全性、样本和 Benchmark 治理。
- A 级，[大模型应用全栈开发（Agent+RAG）](https://www.nowcoder.com/jobs/detail/315080)：公开职位明确要求 tool use、reflection、chat history、pipeline/agentic RAG、RAG/上下文评测，以及 Python/FastAPI、PostgreSQL、Next.js 和 Docker。它只代表一个初创团队岗位，不能外推为行业统一标准。
- A 级，[海康威视大模型应用开发岗位](https://talent.hikvision.com/home/socity/position?postId=B4F6AAF8C5C1FEB7D6C131231EBAB46F)：官方岗位强调端到端 Agent 链路、真实数据回放、指标驱动、A/B、安全与可观测性。
- B 级，[2 年 Java 转 AI 应用社招一面](https://www.nowcoder.com/discuss/914627656245600256)：候选人自述的问题包括团队定位、为什么做项目、请求路由、真实使用者、效果、RAG 细节、长文档处理和个人行动；它直接支持“先讲场景和调用链”的准备顺序。
- B 级，[字节 AI 应用岗复盘](https://www.nowcoder.com/discuss/882634966025175040)：社区经验强调离线评测与在线链路、数据回流和 bad case。
- B 级，[T 公司 Agent 开发面经](https://www.nowcoder.com/feed/main/detail/aef9769bc0604700bcc8ed4fa8db8377)：社区样本直接追问评测集构造、precision/recall、跨场景检索污染和个人贡献。
- C 级，[RAG/Agent 项目到底要讲什么](https://www.nowcoder.com/discuss/893051252416737280)：整理型文章给出了 chunk、TopK、引用、无答案、工具失败、权限、循环和 trace 等追问，只用于检查准备覆盖面。
- C 级，[百度 Agent 面经整理](https://www.nowcoder.com/discuss/880841659733311488)：内容包含付费专栏推广，不能当真实频次证据；其中“先画业务因果链、再解释框架取舍”和 checkpoint/幂等追问可作为复习线索。
- C 级，[面试官视角的 AI 项目复盘](https://ac.nowcoder.com/discuss/1652755?channel=-1&source_id=discuss_tag_discuss_hot_nctrack&type=0)：社区观点提醒避免术语堆砌，必须讲清 ownership、真实测试、指标和状态同步。

社区面经只能作为定性问题样本，不能当作招聘方官方标准或频率统计；官方岗位描述也只能反映具体岗位。只给“推荐回答”而没有原始经历的内容一律降为 C 级，模拟面试不进入事实依据。

### 评测与数据集

- [RAGAs（EACL 2024）](https://aclanthology.org/2024.eacl-demo.16/)：将检索上下文、忠实度和生成质量拆开评价。
- [QGEval（EMNLP 2024）](https://aclanthology.org/2024.emnlp-main.658/)：题目生成七维评价，并指出自动指标与人类判断可能不一致。
- [EduMath/EQGEVAL（ACL 2025）](https://aclanthology.org/2025.acl-long.628/)：16K 数学题和多维教学目标对齐评价。
- [LearningQ](https://ojs.aaai.org/index.php/ICWSM/article/view/14987)：230K 教育文档—问题对，适合研究长文档问题生成。
- [C-Eval 官方仓库](https://github.com/hkust-nlp/ceval)：中文多学科基线；数据许可为 CC BY-NC-SA 4.0。
- [EdNet 官方仓库](https://github.com/riiid/ednet)：大规模层次化学习交互数据；数据为 CC BY-NC 4.0，仅适合作为研究基线。
- [ASSISTments 数据说明](https://sites.google.com/site/assistmentsdata/home/assistments-problems)与[隐私说明](https://www.assistments.org/blog-posts/how-we-protect-data-at-assistments)：不同数据部分有申请、研究用途和隐私要求，使用前需逐项核验。
- [Google Responsible AI 评测指南](https://ai.google.dev/responsible/docs/evaluation)：除通用 benchmark 外，还应构造贴近真实使用方式的自有评测集，并防止训练/测试泄漏。

## 13. 最后的面试原则

面试官真正要确认的是：

1. 你是否理解用户问题，而不只是会调用 API；
2. 你能否画出一次请求和一次失败的真实调用链；
3. 你是否知道 LLM 哪些地方不可靠，并用契约、数据和恢复机制约束；
4. 你能否用可复现证据证明结果，同时诚实说明尚未证明的效果；
5. 你是否真的做过代码，而不是只熟悉框架名。

围绕这五点讲 ExamMem，比把它包装成“万能教育 Agent”更有说服力。
