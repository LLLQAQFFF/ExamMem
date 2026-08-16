# ExamMem 智能备考项目面试手册

更新时间：2026-08-16
适用方向：大模型应用开发、Agent/RAG、AI 后端、Python/FastAPI、教育科技

## 1. 先确定你能诚实声称什么

这个项目最有价值的讲法不是“我调用大模型做了一个题库”，而是：

> 我把一个通用 AI 学习工作区扩展成了垂类考研备考系统。系统先把用户确认的考试大纲发布成版本化知识体系，再把每个叶子知识点连接到辅导对话和版本化检测；一次作答会经过固定评分契约、诊断、可审计学习记忆、推荐、恢复和复盘。迁移时我没有继续维护 Fork，而是补了中性插件 API，让 ExamMem 使用独立 PostgreSQL，DeepTutor Core 不依赖 ExamMem。

当前可以用代码和自动化测试证明：

- 插件发现、HTTP、Python SDK、WebSocket、Browser API 与 PostgreSQL 闭环真实可运行；
- Taxonomy、四维 Scope、`slot_key`、L1 append-only、L2 CAS/provenance、L3 可重建、Lifecycle、Trace、checkpoint 和幂等契约受到测试保护；
- 同一考试可有不可变题集版本和多次 attempt；
- DeepTutor 原生 Chat/Quiz/Mastery Path 与 ExamMem 领域边界清楚；
- 外部 LLM 在自动化测试中使用确定性替身，所以测试证明系统契约，不证明真实模型质量。

当前不能声称：

- 已经通过线上 A/B 证明能提高考研分数；
- 已有真实用户规模、QPS、留存率或商业收入；
- 已完成多源 RAG、教材知识库、图片/视频/PPT 摄取；
- 公共数据集上的准确率就是考研场景效果；
- LLM Judge 的分数等同于教师评价。

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

## 3. 三种项目介绍版本

### 3.1 30 秒版

> 我做了一个基于 DeepTutor 的垂类考研智能备考插件 ExamMem。用户导入并确认考试大纲后，系统生成版本化、分层的知识体系，每个叶子知识点都能进入原生辅导对话，也能生成版本化检测。作答后通过固定评分契约进入诊断、Learning Memory、推荐和复盘。后端用 FastAPI、Pydantic、SQLAlchemy/Alembic、PostgreSQL/pgvector；迁移采用中性插件 API，DeepTutor Core 不直接依赖 ExamMem。重点解决的是 LLM 不确定性下的状态一致性、可追溯记忆和失败恢复，而不只是生成几道题。

### 3.2 两分钟版

> 背景是通用对话和原生 Quiz 都能“出题”，但不能直接构成可信的备考产品。备考需要先有用户确认的考试范围；题目、评分和学习结论必须绑定同一个知识点与考试版本；网络断开或模型失败后还要能恢复，历史记录不能被后来的模型输出静默覆盖。
>
> 我的方案分三层。第一层是版本化 Study Plan/Taxonomy：大纲先成为可编辑草稿，确认后发布不可变版本，只有活跃叶子知识点能进入练习。第二层是可恢复 Practice 状态机：Question、Grade、Knowledge Mapping、Diagnosis、Memory、Recommendation 每一步都有 checkpoint、Trace 和幂等键，题集在 assessment version 中冻结，同一考试可以多次 attempt。第三层是独立 Learning Memory：L1 是 append-only 事件，L2 用 CAS、provenance 和 Lifecycle 管理事实版本，L3 是可以从 L1/L2 重建的投影。
>
> 架构上我没有把 ExamMem 硬编码进 DeepTutor Core，而是抽出中性插件、Capability、Host Turn、Mastery Path 和 Native Memory adapter；ExamMem 使用独立 PostgreSQL。测试覆盖五种 Memory Backend、迁移不变量、HTTP/SDK/WebSocket、PostgreSQL 事务和断线重放。现在能证明工程闭环，下一阶段要补考研金标集与真人评测，不能把 fake LLM 的回归测试当作模型效果。

### 3.3 五分钟版结构

按下面顺序讲，不要从框架名开始：

1. 用户问题：学习、练习、复盘分散，通用聊天里的闲聊不能直接当正式掌握度证据。
2. 产品约束：先确认考试范围；知识点、题目、评分、记忆必须版本一致；考试可重复；历史可审计。
3. 架构决策：插件化而不是 Fork；独立 PostgreSQL；Host Hook 保持中性。
4. 核心闭环：大纲 → Taxonomy → 辅导/出题 → Grade → L1/L2/L3 → 推荐/恢复/复盘。
5. 最难问题：LLM 输出不稳定、断线重试、并发写 L2、历史纠错、跨 Scope 泄漏。
6. 证据：真实入口和 PostgreSQL 测试、迁移 hash、Core import gate、生产构建。
7. 限制和下一步：金标数据集、真实模型评测、成本/延迟、在线学习效果。

## 4. 架构与真实调用链

### 4.1 架构图

```text
Browser / Python SDK / unified WebSocket
                  │
                  ▼
          DeepTutor Turn Host
                  │ neutral contracts
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
       ┌──────────┼───────────┐
       ▼          ▼           ▼
 L1 append-only  L2 CAS     L3 rebuildable
 event           provenance projection
       └──────────┼───────────┘
                  ▼
 Recommendation → checkpoint → append-only Trace
                  ▼
 Resume / Correction / Grade Review / derived Issues
```

### 4.2 从大纲到辅导

```text
PDF/TXT/MD、公开 URL 或模型创建请求
  → StudyPlanOutlineImporter 只提取标题层级
  → 用户编辑草稿
  → 发布不可变 Study Plan version + Taxonomy version
  → 每个叶子 objective 映射一个 Host Mastery Path
  → 首次进入自动发起辅导，后续恢复同一 Chat session
```

关键点：发布之前允许编辑，发布之后不原地修改。新大纲产生新版本，旧考试仍可解释。

### 4.3 从练习到记忆

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
- Capability：`exam_mem/practice/capability.py`
- Runtime 依赖装配：`exam_mem/practice/provider.py`
- 状态机：`exam_mem/practice/workflow.py`
- Learning Memory 生命周期：`exam_mem/backends/lifecycle.py`
- PostgreSQL repositories：`exam_mem/storage/`
- 前端：`web/components/exam-mem/`

## 5. 四个最值得讲的工程问题

### 5.1 为什么不继续改 Fork

问题：Fork 中业务代码会逐渐渗透到 Host 注册表、配置、数据库和 UI；以后同步上游成本越来越高。

方案：把实现分成三类：

- ExamMem 自有领域代码，迁移到 `exam_mem/`；
- DeepTutor 真正需要的中性 Host Hook，进入 `deeptutor.plugins`；
- 只为 Fork 服务的硬编码直接丢弃。

验收：扫描 `deeptutor/` 不允许直接 import `exam_mem`；插件关闭时 DeepTutor 原生测试和构建仍通过。

取舍：中性 API 增加了一层协议，但换来独立演进、可测试装配和更低上游合并成本。

### 5.2 为什么不能让 LLM 再猜一次知识点

一次真实缺陷是：题目已经在不可变 catalog 中绑定叶子知识点，作答时却又让模型做语义映射。模型可能返回 `unknown`，结果只写 L1，L2/L3 都为空。

干净修复：

- catalog 中的 `knowledge_point_ids` 是权威；
- 作答时只验证 ID 是否存在、active、leaf、unique；
- `unknown` 或非法 ID 在写 L1 前 fail closed；
- LLM 语义映射只保留给没有 canonical ID 的显式旧场景，不是正常运行权威。

这展示了一个通用原则：确定性业务身份一旦在上游固化，下游不能再交给概率模型重建。

### 5.3 如何处理断线、重试和重复写

HTTP 的 `socket hang up` 只表示代理连接断了，不等于后端业务一定失败。若客户端盲目换 idempotency key 重试，会重复评分和记忆写入。

方案：

- 请求携带稳定幂等键；
- 每个状态转换持久化 checkpoint；
- Trace 记录阶段、版本、重试次数和失败码；
- 同一个答案重放时复用已有 checkpoint/Grade Artifact；
- 诊断和 Memory 副作用仍按当前答案 Scope 执行；
- retryable 与 contract mismatch 分开，契约错误 fail closed。

### 5.4 L1/L2/L3 为什么不能是一个 JSON

- L1 是不可变证据，回答“发生过什么”；
- L2 是有 Scope、有 provenance、有版本链的业务事实，回答“当前如何解释证据”；
- L3 是跨事实的可重建投影，回答“当前整体学习状态是什么”。

如果只保存一个可更新 JSON，无法解释结论来源、并发覆盖、纠错历史，也无法可靠重建。代价是表和事务更多，但它们服务的是不同不变量，不是为了炫技分层。

## 6. 高频追问与参考回答

### 6.1 产品和业务

#### Q1：DeepTutor 原来就能 Quiz，你的项目有什么必要？

原生 Quiz 解决一次对话里的题目生成；ExamMem 解决备考域的长期一致性：考试范围先发布、题目绑定 canonical leaf、同一 assessment 多版本多 attempt、评分写入独立 Learning Memory、失败可恢复、结论可纠错和复盘。两者关系是 Host 能力与领域编排，不是重复实现 Quiz。

#### Q2：为什么聊天记录不能直接当学习记忆？

聊天可能包含闲聊、探索和不确定表达。正式 Practice 有固定 Scope、题目、rubric、grader version 和幂等身份，证据质量更高。普通 Chat 只能先成为旁路 observation/clue，必须由用户确认，不能直接改变掌握度。

#### Q3：为什么考试范围必须先发布？

如果范围可以随题目任意创建，同一个考试 ID 下的知识点含义会漂移，历史成绩不可比较。草稿允许编辑，发布版提供不可变引用；修改范围生成新版本。

#### Q4：同一个考试为什么要多个版本？

稳定 assessment ID 表示同一检测目标；immutable version 固定某次题集和 rubric；attempt 表示用户的一次作答。这样既能重复同一卷，也能生成新卷，同时不覆盖历史。

### 6.2 Agent、RAG 和模型

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

### 6.3 数据库、事务和一致性

#### Q10：为什么 ExamMem 必须独立 PostgreSQL？

Learning Memory 有自己的迁移、不变量、append-only trigger、CAS 和 pgvector。放进 DeepTutor 内部库会造成隐式耦合、升级冲突和错误的真值共享。中性 Host adapter 只传明确 DTO，数据库之间没有外键或直接读写。

#### Q11：L2 的 CAS 解决什么？

两个并发答案可能都基于同一个当前版本生成新事实。更新时比较预期 `row_version`；只有一个提交成功，另一个得到冲突并重新读取/决策，避免后写静默覆盖先写。provenance 与 Change Log 在同一事务提交。

#### Q12：为什么 L1 要 append-only？

L1 是作答与纠错证据。直接 UPDATE 会让后来的结论无法重放和审计。纠错用新事件表达“旧证据哪里不准确”，不擦除旧事实。数据库 trigger 和 repository 都防止更新/删除。

#### Q13：L3 丢了怎么办？

L3 不是真值。按 Scope 从 L1/L2 和水位重建，写入新的 projection version。重建失败形成 `projection_pending` issue，不能拿旧 L3 反向覆盖事实。

#### Q14：事务边界在哪里？

一次 Learning Memory 写入把 L1、L2 版本/provenance、Lifecycle Decision、Change Log 和必要的 L3 刷新放在受控连接/事务中。外部 LLM 调用在事务外完成，避免长事务占用连接；事务内只执行确定性校验和持久化。

#### Q15：如何防止跨用户读写？

`user_id` 来自 Host 认证上下文，不接受客户端自报；repository 查询同时带 user/exam/subject，Memory 再带 namespace/slot。API 只允许用户选择自己可见的考试和科目。测试覆盖 Scope 不匹配和跨上下文事件查询失败。

### 6.4 可靠性和可观测性

#### Q16：`socket hang up` 和 HTTP 409 有什么区别？

`socket hang up` 是前端代理看到的连接中断，可能发生在后端异常返回或进程连接关闭；409 是后端已经给出可解释业务冲突，例如 taxonomy/grader contract 不匹配。前端必须优先解析结构化后端错误；不能把所有错误都显示成 JSON parse failure。

#### Q17：为什么契约版本不匹配不能自动 fallback？

评分版本决定后续记忆含义。若偷偷换旧 grader 或忽略字段，历史同分不同义。正确行为是 fail closed、记录 pinned/saved/effective 版本，让运维修配置或恢复 checkpoint。

#### Q18：Trace 和日志有什么区别？

日志用于进程诊断，可能滚动或采样；Practice Trace 是 append-only 业务审计，绑定 trace ID、阶段、输入/输出摘要、版本、LLM 调用数和失败状态。恢复和 Review 依赖 Trace，不依赖 grep 日志。

#### Q19：五个 Memory Backend 为什么存在？

它们是可比较的实验/产品模式：`none`、`native`、`append_only`、`vector`、`lifecycle`。所有模式走同一 Practice 状态机，只改变明确的副作用；缺少依赖时失败，不自动降级。这让后续评测能比较“无记忆/通用记忆/领域记忆”，也防止代码分叉。

### 6.5 插件和开源工程

#### Q20：怎样证明 Core 没依赖 ExamMem？

代码目录和 import gate 双重约束：领域代码在 `exam_mem/`，装配在 `deeptutor_plugins/exam_mem/`；测试扫描 `deeptutor/` 的直接依赖。禁用插件并移除 DSN 后运行 DeepTutor 原生测试和生产构建，仍应通过。

#### Q21：插件如何被加载？

Host 发现 first-party plugin entry，读取启停设置后调用 `get_plugin()`；manifest 声明 capability、tools、router、navigation、settings 和 migration metadata。Host 只认识通用 Contribution DTO，不认识 ExamMem 表或业务状态。

#### Q22：为什么 migration 也放进插件包？

业务表属于 ExamMem。源码运行可用根 `alembic.ini`；wheel 安装则用随包发布的 `exam_mem/storage/alembic.ini` 和 `python -m exam_mem.storage.migrations`，避免用户必须拥有 Git checkout。`0001`～`0006` 用 hash 测试冻结，新改动只能追加 revision。

#### Q23：开源前你审计什么？

许可证/第三方声明、secret 扫描、依赖漏洞、容器与 wheel 内容、CI 是否真实启动 pgvector、插件禁用测试、迁移 head/hash、全量 pytest、lint/type/build、演示脚本、数据库副作用和延期边界。发现风险会区分代码缺陷、依赖升级和文档限制，不把 warning 隐藏成“全绿”。

### 6.6 前端和产品体验

#### Q24：为什么学习档案默认要“全部章节”？

若默认选择第一个章节，后端 Scope 会隐式只查这一组叶子，用户可能误以为没有记忆。默认全部章节不发送知识点过滤；用户主动选章节/知识点后才收窄。这是查询语义修复，不是用前端造假数据。

#### Q25：如何展示 L1/L2/L3 的版本？

L1 按时间线展示不可变事件；L2 以当前事实为主，同时展开 version/provenance/lifecycle 链；L3 展示当前投影和 source watermark，并提供重建状态。专业、版本、科目、章节、知识点、namespace、lifecycle 都是筛选维度，而不是把不同 Scope 混在一个列表。

### 6.7 安全和隐私

#### Q26：Prompt injection 怎么办？

大纲/附件是非可信内容，只能作为限定工具上下文，不能改变 system policy、数据库 Scope 或工具权限。结构身份由服务端决定；文件类型、大小、URL、超时和输出 schema 都要校验。当前附件不持久化原文，减少数据面，但未来 RAG 仍需来源授权和隔离索引。

#### Q27：数据库密码放哪里？

只读进程环境 `EXAM_MEM_DATABASE_URL`，日志只打印脱敏摘要；不写 JSON/YAML、前端或仓库。演示脚本固定密码仅用于 `127.0.0.1` 隔离库，正式环境必须独立账号、强密码和 secret manager。

#### Q28：为什么依赖漏洞也是发布阻塞？

Next.js、Mermaid、DOM sanitizer 都处在处理用户输入或 HTTP 请求的路径。测试通过不能证明没有已知 CVE。发布门禁必须包含锁文件审计、受控升级、回归和残余风险说明；不能因为漏洞在上游依赖就忽略。

#### Q29：外部大纲或论文归档如何防路径穿越和解压炸弹？

不能直接使用 `extractall`。TAR 使用 Python 官方 `data` extraction filter，并额外拒绝
符号链接、硬链接、设备文件和超限成员；ZIP 先校验每个相对路径、文件类型、加密标志、
单项/总大小和压缩比，再限额流式写入。失败路径必须清理临时目录。测试要同时构造
`../../`、绝对路径、链接、伪造大小、高压缩比和正常嵌套目录，不能只测正常文件。

#### Q30：本地开发服务监听 `0.0.0.0` 有什么风险？

如果认证默认关闭，监听所有网卡会让同一局域网设备进入单用户管理员语义。本地启动应
默认绑定 `127.0.0.1`；确需跨设备访问时先开启认证，再显式选择 `0.0.0.0`。容器内部
runner 或外部 webhook 可以保留全网卡监听，但必须有网络隔离或入站鉴权，并在安全扫描中
写明接受理由。

## 7. 到底需不需要数据集

结论：需要，而且必须分清“工程回归集”和“模型效果集”。

当前 4000+ 自动化测试主要证明：状态机、事务、迁移、Scope、幂等、恢复和接口没有退化。测试中的确定性 fake LLM 不能回答：生成题是否正确、难度是否合适、评分理由是否可靠、推荐是否真的帮助学习。

### 7.1 公共数据集能做什么

| 数据/框架 | 可用于 | 不能替代 | 许可/适配提醒 |
| --- | --- | --- | --- |
| C-Eval | 中文学科选择题基础能力、相关科目 smoke baseline | 考研大纲解析、主观题评分、个体推荐 | 数据为 CC BY-NC-SA 4.0，不应默认打包进 Apache-2.0 产品 |
| EduMath / EQGEVAL | 教学目标对齐的数学题生成方法与评价维度 | 中国考研数学全部题型 | 16K 数学题，先核对数据发布许可再下载/再分发 |
| QGEval | 流畅、清晰、简洁、相关、一致、可回答、答案一致七维 rubric | 你的考研金标答案 | 更适合借鉴评价协议，而非直接当领域测试集 |
| LearningQ | 长教育文档上的问题生成研究 | 中文考研知识体系和评分 | 230K 文档-问题对；下载与再分发许可需单独核验 |
| EdNet/ASSISTments | Knowledge Tracing 方法研究、交互序列 baseline | ExamMem 的 L1/L2/L3 业务正确性 | EdNet 是 CC BY-NC 4.0；ASSISTments 不同版本条款不同，题干可能需申请 |
| RAGAS | 将检索质量、忠实度和生成质量拆开 | 人类教师金标与学习效果 | ExamMem 当前并未完成通用教材 RAG，不应为了指标硬套 |

### 7.2 必须自建的考研金标集

建议先做 200～500 个高质量 case，而不是马上追求十万条弱标签。一个 case 包含：

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

### 7.3 如何切分，避免数据泄漏

- 不要随机拆分近似题；按来源、章节和时间分组。
- 测试集至少 hold out 完整章节或整份来源文档。
- prompt 调优只看 train/dev，最终 test 冻结。
- 记录模型版本、prompt hash、taxonomy/grader/config revision。
- 去重既做文本相似度，也检查相同模板只换数字的题。

### 7.4 标注协议

- 关键题由两名标注者独立完成，分歧交给第三人仲裁；
- 先写 rubric 再看模型答案，减少迎合模型；
- 保存分歧率和 Cohen's kappa/一致率，不只保存最终标签；
- LLM Judge 只能做辅助扩展，必须先与人工标签校准；
- 题目版权和考生隐私必须有来源、用途和删除策略。

### 7.5 指标矩阵

| 子系统 | 建议指标 |
| --- | --- |
| 大纲/Taxonomy | 层级 precision/recall/F1、叶子 exact match、重复率、out-of-scope 拒绝率 |
| 题目生成 | 正确性、answerability、objective alignment、难度校准、答案一致性、新颖性/去重、七维 QGEval rubric |
| 判题 | correct/error-type macro-F1、分数 MAE、Quadratic Weighted Kappa、Spearman、理由证据支持率 |
| 推荐 | prerequisite violation rate、覆盖/多样性；有金标时 Recall@K、MRR/NDCG |
| Learning Memory | L1 完整率、L2 provenance 正确率、Scope 泄漏率（目标为 0）、CAS/补偿成功率、L3 重建等价率 |
| 系统 | 任务成功率、恢复成功率、重复副作用率（目标为 0）、P50/P95 延迟、token/成本、重试率 |
| 学习效果 | 同知识点重复 attempt 的变化、前测/后测；没有对照实验时不能声称因果提升 |

### 7.6 必做 baseline/ablation

- 无记忆 vs Native Memory vs append-only vs vector vs lifecycle；
- 直接 LLM 一步输出 vs 结构化 Practice 工作流；
- 题目 knowledge point 二次 LLM 映射 vs catalog canonical ID 校验；
- 无 checkpoint vs checkpoint/replay；
- 不同模型、prompt、温度和 grader version；
- 有/无来源上下文，但必须在合法授权的同一数据切分上比较。

面试时若还没做完数据集，可以这样回答：

> 当前阶段我已经用确定性替身把工程不变量和真实数据库闭环测全，但这不能代表模型效果。我设计的下一阶段是 200～500 条考研金标集，按来源和章节隔离切分，教师双标；分别评估大纲、出题、评分、推荐和记忆，公共 C-Eval/QGEval 只做外部基线，不直接作为产品结论。这个缺口我会明确写在报告里，而不是报一个无法复现的“准确率”。

## 8. 面试演示脚本

控制在 8～10 分钟：

1. 用 `start-demo.sh --dev` 展示一键隔离 PostgreSQL 和 migration head。
2. 在学习计划导入一份你有权使用的小型大纲，展示“草稿可改、发布版不可变”。
3. 点一个叶子知识点进入 Mastery Chat，说明 session link 和首次自动辅导。
4. 从同一知识点生成检测，展示 assessment ID、version、attempt。
5. 提交一个故意错误答案，展示中文评分、诊断、推荐。
6. 打开 Learning Memory，展示 Scope 筛选、L1、L2 version/provenance、L3。
7. 打开 Review/Trace，解释断线后如何 Resume、为什么同幂等键不会重复写。
8. 最后展示测试和已知限制，不现场演示未完成的多源 RAG。

准备一个“故障故事”：用 taxonomy/grader contract mismatch 或旧 `unknown` 映射说明你如何从日志 → checkpoint → 数据库 → 根因 → 契约修复 → 回归测试定位问题。

## 9. 简历写法

不要填写不存在的用户量和准确率。可以写：

- 将独立 ExamMem 迁移为 DeepTutor 第一方全栈插件，设计中性 Capability/Router/Settings/Migration/Host Service contributions，保持 Core 对 ExamMem 零直接依赖。
- 实现“版本化大纲 → 叶子知识点辅导 → 不可变题集版本/多次检测 → 评分诊断 → Learning Memory → 推荐恢复复盘”闭环，并用独立 PostgreSQL 隔离业务真值。
- 设计 L1 append-only、L2 CAS/provenance/Lifecycle、L3 可重建投影，以及 checkpoint、Trace、幂等和补偿机制，覆盖五种 Memory Backend。
- 建立真实 HTTP/SDK/WebSocket/PostgreSQL 回归与 migration hash 门禁；明确区分确定性工程测试和待建设的模型效果金标集。

如果必须量化，只使用可以从当前测试日志复现的数字，并注明它是“测试数量/入口覆盖”，不是“模型准确率”。

## 10. 反问面试官

- 团队目前更缺模型能力优化，还是评测、数据闭环和工程可靠性？
- 线上 Agent 最常见失败来自检索、规划、工具、模型输出还是状态持久化？
- 是否已有领域金标集、bad-case ledger 和人工抽检流程？
- 对教育场景，业务最终优化的是答题正确率、学习增益、留存还是教师效率？
- 模型/Prompt 版本与业务数据如何做可追溯和回滚？

## 11. 资料来源与使用方式

以下资料用于归纳面试关注点，不代表每家公司都会逐题询问：

### 岗位与面经

- [海康威视大模型应用开发岗位](https://talent.hikvision.com/home/socity/position?postId=B4F6AAF8C5C1FEB7D6C131231EBAB46F)：官方岗位强调端到端 Agent 链路、真实数据回放、指标驱动、A/B、安全与可观测性。
- [大模型应用开发面经（5 年经验）](https://www.nowcoder.com/feed/main/detail/129eaa1c20444651ac3b932e200d3da4)：社区经验，突出项目落地、RAG 难点、效果评估和基础知识。
- [百度 Agent 面经整理](https://www.nowcoder.com/discuss/880841659733311488)：社区经验，集中讨论记忆、工具协议、失败重放、沙箱、评测和 trace。
- [字节 AI 应用岗复盘](https://www.nowcoder.com/discuss/882634966025175040)：社区经验，强调离线评测与在线链路、数据回流和 bad case。
- [京东大模型应用开发实习面经](https://www.nowcoder.com/feed/main/detail/5ecaca5990d74c94840f01d83835eb69)：社区经验，包含检索准确率、混合检索、向量索引和 RAG 评测追问。
- [RAG/Agent 项目是否做数据集验证的面经](https://www.nowcoder.com/feed/main/detail/d770696f3495465d9e3d40c3d631d54c)：社区样本，直接出现“是否用数据集验证”的追问。

社区面经只能作为问题样本，不能当作招聘方官方标准；官方岗位描述也只能反映一个岗位。

### 评测与数据集

- [RAGAs（EACL 2024）](https://aclanthology.org/2024.eacl-demo.16/)：将检索上下文、忠实度和生成质量拆开评价。
- [QGEval（EMNLP 2024）](https://aclanthology.org/2024.emnlp-main.658/)：题目生成七维评价，并指出自动指标与人类判断可能不一致。
- [EduMath/EQGEVAL（ACL 2025）](https://aclanthology.org/2025.acl-long.628/)：16K 数学题和多维教学目标对齐评价。
- [LearningQ](https://ojs.aaai.org/index.php/ICWSM/article/view/14987)：230K 教育文档—问题对，适合研究长文档问题生成。
- [C-Eval 官方仓库](https://github.com/hkust-nlp/ceval)：中文多学科基线；数据许可为 CC BY-NC-SA 4.0。
- [EdNet 论文](https://arxiv.org/abs/1912.03072)：大规模层次化学习交互数据；CC BY-NC 4.0，仅适合作为研究基线。
- [ASSISTments 数据说明](https://sites.google.com/site/assistmentsdata/home/assistments-problems)：不同数据部分有申请、研究用途和隐私要求，使用前需逐项核验。

## 12. 最后的面试原则

面试官真正要确认的是：

1. 你是否理解用户问题，而不只是会调用 API；
2. 你能否画出一次请求和一次失败的真实调用链；
3. 你是否知道 LLM 哪些地方不可靠，并用契约、数据和恢复机制约束；
4. 你能否用可复现证据证明结果，同时诚实说明尚未证明的效果；
5. 你是否真的做过代码，而不是只熟悉框架名。

围绕这五点讲 ExamMem，比把它包装成“万能教育 Agent”更有说服力。
