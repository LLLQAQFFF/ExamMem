# DeepTutor × ExamMem 技术架构、Memory 系统与智能学习工程全解

本文面向项目讲解、源码走读和技术面试，目标是在不过度展开实现细节的前提下，完整说明：

- DeepTutor 的 Agent 框架、原生 Memory 和原生评估；
- ExamMem 的领域框架、Learning Memory 和主要业务模块；
- 教材从 PDF 到章节、chunk、RAG 证据和学习上下文的完整转换；
- 关键技术选择、工程难点、解决方案、评估结果与仍未解决的问题。

文中的“评估结果”分成三类：论文中的系统能力实验、ExamMem 的离线受控实验、仓库中的工程测试。三者回答的问题不同，不能把某一项高分解释成整个产品的准确率。

**当前实现状态（2026-08-27）**：ExamMem 语义检索默认使用 `0.003` 绝对相关性下限和
Top-1 相对分差 `0.03`，最终返回 `0..K` 条结果；本页第 9.2 节的数字是启用该策略后的
frozen test 结果。代码、评估脚本和本页必须保持同一套策略口径。

---

## 1. 系统总览

### 1.1 两个系统分别解决什么问题

DeepTutor 是通用的 Agent-Native 学习助手框架，负责模型调用、工具执行、多阶段能力、流式事件、会话、文件解析、知识库和 RAG。

ExamMem 是面向考试学习的领域系统，负责学习计划、考试 Taxonomy、教材版本、练习、评分、错因诊断、长期学习状态、画像、复习与推荐。

整体调用关系只需记住一次：

```text
Browser / CLI / Python SDK
        ↓
DeepTutor ChatOrchestrator
        ↓
Tool 或 Capability
        ↓
通用能力：Chat / Parse / RAG / Stream / Session
        ↓
ExamMem 插件入口
        ↓
Taxonomy / Textbook / Practice / Memory / Profile / Review
        ↓
ExamMem PostgreSQL + Host 管理的文件与 RAG 索引
```

DeepTutor 中性的 Host Hook 连接通用能力与领域能力；ExamMem 的学习事实保存在自己的 PostgreSQL 中，教材原文件、解析缓存和向量索引由 Host 管理。这个边界在这里说明一次，后文只讨论各模块自身的职责。

### 1.2 两条最重要的业务链

教材链：

```text
PDF → 原文件引用 → Parse IR → 章节树 → 章节内 chunk
    → embedding / BM25 索引 → 计划绑定与章节映射
    → 固定版本检索 → 带章节和页码的教学证据
```

学习链：

```text
选择知识点 → 生成/选择题目 → 用户作答 → 结构化评分
    → 知识点归一化 → 错因诊断 → L1 学习事件
    → L2 生命周期决策 → L3 StudentModel → 复习与推荐
```

---

## 2. DeepTutor 框架

### 2.1 核心组件

| 组件 | 职责 | 关键技术 |
| --- | --- | --- |
| Entry Points | 接收 CLI、WebSocket 和 SDK 请求 | Typer、FastAPI/WebSocket、Python facade |
| `ChatOrchestrator` | 归一化上下文并选择 Capability | `UnifiedContext`、Registry、异步执行 |
| Tool | 一次模型可调用的原子操作 | JSON Schema、`BaseTool`、动态挂载 |
| Capability | 接管一整个 turn 的多阶段流程 | `BaseCapability`、阶段状态、统一结果 envelope |
| AgentLoop | 模型—工具—观察—继续推理循环 | tool calling、上下文预算、终止条件 |
| `StreamBus` | 传输内容、思考、工具、进度、来源和错误事件 | async fan-out、统一 `StreamEvent` |
| Session/Context | 保存消息分支并构造模型上下文 | 分支历史、token 预算、增量摘要 |
| Parse/RAG | 将文件转换为可检索知识 | 可插拔 parser、LlamaIndex、FAISS、BM25 |

Tool 适合“调用后返回结果”的单步能力，例如 RAG、搜索或读写 Memory；Capability 适合需要多个阶段、状态和流式反馈的任务，例如深度研究、可视化或 mastery path。

### 2.2 一次普通聊天如何运行

```text
请求进入
→ 构造 UnifiedContext
→ Registry 选择 Capability，默认是 chat
→ 创建本 turn 的 StreamBus
→ ContextBuilder 拼接系统提示、会话历史、附件和可选 Memory
→ AgentLoop 请求模型
→ 模型如需工具，ToolDispatcher 校验参数并执行
→ 工具结果回到 AgentLoop，模型继续
→ Capability 发出统一 result / cost_summary / done
→ Web、CLI、SDK 各自消费同一组事件
```

`StreamBus` 是进程内的异步事件总线，不是数据库，也不是消息队列。它的价值是把业务执行和表现层分离：Capability 不需要知道消费者是 WebSocket、CLI 还是测试程序。

### 2.3 文件解析与通用 RAG

`ParseService` 不是一个固定 PDF 库，而是一个可插拔解析桥：

1. 对源文件字节计算内容 hash；
2. 选择 text-only、MinerU、Docling、MarkItDown、PyMuPDF4LLM 或 LiteParse 等引擎；
3. 用“源 hash + parser signature”查缓存；
4. 未命中时解析为统一 `ParsedDocument`，主要包含 Markdown、blocks 和资源目录；
5. 保存缓存，下次相同内容和相同解析器配置直接复用。

通用 LlamaIndex RAG 将文本节点 embedding 后写入 FAISS。当前 FAISS 使用 `IndexFlatIP`，写入和查询前做 L2 归一化，因此内积排序等价于 cosine similarity。可选的 BM25 负责关键词召回，混合模式用 Reciprocal Rank Fusion 合并向量与 BM25 排名。旧的 SimpleVectorStore 索引仍可读取，但大语料会退化为较慢的线性扫描。

### 2.4 主要工程难点

| 难点 | 处理方式 | 原因 |
| --- | --- | --- |
| 多入口行为容易不一致 | 统一经过 Orchestrator、Context 和 Stream 协议 | 避免 CLI、Web、SDK 各写一套执行逻辑 |
| 长会话超过上下文窗口 | 保留近期消息，对较老分支做增量摘要 | 在成本、连续性和信息损失之间折中 |
| 工具不是所有 turn 都需要 | 按 KB、附件、Memory、sandbox 等上下文动态挂载 | 减少无关工具描述和误调用 |
| 文件格式与解析质量差异大 | parser adapter + signature cache + readiness check | 将第三方解析器差异隔离在稳定 IR 之前 |
| 大知识库精确扫描过慢 | FAISS 向量化检索，并可混合 BM25 | 提升规模下的检索吞吐，同时保留词法命中 |

---

## 3. DeepTutor Native Memory

### 3.1 三层数据如何变化

```text
聊天、Quiz、Book、Notebook、KB 等原始活动
        ↓ 记录/快照
L1：trace/<surface>/<date>.jsonl
        ↓ LLM 抽取 + 引用校验 + 文档操作
L2：chat.md、quiz.md、book.md、kb.md 等分来源事实
        ↓ LLM 跨来源归纳 + 引用校验
L3：recent.md、profile.md、scope.md、preferences.md
        ↓
按需或显式选择后注入模型上下文
```

| 层 | 存储形式 | 主要目标 | 更新方法 |
| --- | --- | --- | --- |
| L1 | 按 surface/date 追加的 JSONL trace | 尽量保留原始活动和来源 ID | 低侵入 append-only |
| Snapshot | `state.json` 与 `changes.jsonl` | 找出相对上次新增或变化的实体 | 稳定 entity ID + fingerprint |
| L2 | 分 surface 的 Markdown | 降噪、去重并保持来源边界 | LLM 输出严格 JSON facts，代码执行 add/edit/delete |
| L3 | 四份跨来源 Markdown | 提供近期事实、画像、范围和偏好 | L2 增量 consolidation；偏好可显式写入 |

L2/L3 每条事实有稳定 ID 和脚注引用。代码验证模型只能引用当前允许的 entity 或 L2 entry，整批文档操作先校验再原子替换；成功后才推进 seen/checkpoint。这样模型负责语义压缩，代码负责边界、幂等和可恢复性。

### 3.2 为什么需要三层

- L1 优先保真，便于错误后回看原始依据；
- L2 按来源压缩，避免把全部历史反复交给模型；
- L3 面向读取，只保留跨来源、对后续对话最有价值的信息。

如果直接从 L1 生成每次对话上下文，历史越长，token、噪声和延迟都会持续增加；如果只保留 L3，又会丢失纠错与重建依据。

### 3.3 当前如何读写

Native Memory 当前不使用 embedding 检索。读取时将 `recent/profile/scope/preferences` 四份 L3 Markdown 全文拼接：前端选择 Memory 时可预先注入，模型也可以调用 `read_memory` 按需读取。

`write_memory` 只允许显式偏好写入 `preferences.md`。普通聊天不会自动把每句话变成长时记忆；其他 L2/L3 内容通过 Memory Workbench 的 update、audit、dedup、merge 等维护流程生成。

### 3.4 已知局限

- L1 是尽力记录的通用 trace，不具备业务账本级事务保证；
- L2/L3 使用 LLM consolidation，可能出现遗漏、错误归纳和摘要漂移；
- 当前 L3 全文拼接适合四份有界文档，数据继续增长后需要检索或按需加载；
- 仓库中没有一套独立衡量 Native Memory 抽取、事实一致性和长期帮助度的公开 benchmark。

---

## 4. DeepTutor 原生评估

### 4.1 论文级能力评估：TutorBench

DeepTutor 论文使用 TutorBench 评估整体辅导能力：

| 项目 | 设计 |
| --- | --- |
| 数据规模 | 270 个任务、90 个模拟学生画像、30 个知识库 |
| 评估对象 | DeepTutor 与 Naive Tutor 的完整辅导、问答和出题表现 |
| 评估方式 | 模拟学生交互，LLM judge 在十个维度上按 1–5 分评分 |
| 核心指标 | Overall Quality，即十个维度的整体质量汇总 |
| 论文结果 | DeepTutor 3.91，Naive Tutor 3.53，相对提升 10.76% |

这项实验说明多能力协同、知识库和教学流程对整体辅导质量有帮助，但它不是 Memory 专项评估，也不能直接推出真实学生成绩提升。模拟学生与 LLM judge 还会带来模型偏好、评分漂移和与真实课堂不一致的问题。

### 4.2 工程验证与证据边界

仓库测试覆盖 Orchestrator、工具协议、流事件、Session、解析器、RAG、Learning、API 和 Web 交互，主要回答“契约是否稳定、失败能否处理、入口是否一致”，不等于教学质量评估。

ExamMem 的五臂受控实验中包含 Native Memory backend，但它没有暴露 ExamMem 所需的 typed lifecycle/state，因此不能用 Active-state exact 等指标公平衡量 Native Memory 的通用能力。当前可以准确地说：DeepTutor 有整体 TutorBench 结果和较完整工程测试，但 Native Memory 仍缺少独立、真实用户、长期性的质量评估。

---

## 5. ExamMem 框架

### 5.1 领域结构

```text
学习计划 / Taxonomy / 教材版本
              ↓
     Learning 与 Practice
              ↓
  Grading → Diagnosis → Memory
              ↓
 Profile → Recommendation → Review
```

ExamMem 的核心不是“多一个聊天页面”，而是把每一步都绑定到确定的用户、考试、科目、计划版本、Taxonomy 版本、知识点、题目版本和教材版本。

### 5.2 关键身份与不变量

| 身份 | 用途 |
| --- | --- |
| Taxonomy version | 固定一版考试知识结构 |
| canonical knowledge-point ID | 消除同义词和自然语言名称歧义 |
| `MemoryScope(user, exam, subject, namespace)` | 隔离不同用户、考试、科目和记忆类型 |
| `slot_key` | 定位同一个逻辑学习状态 |
| assessment/question version | 确保恢复后仍使用同一题目和 rubric |
| textbook version + section key | 固定教学证据的教材版本与章节 |
| idempotency key + row version | 防重复提交和并发覆盖 |

### 5.3 各模块采用的技术与算法

| 模块 | 输入到输出 | 技术/算法 | 为什么这样做 |
| --- | --- | --- | --- |
| Study Plan / Taxonomy | 大纲或教材结构 → 科目、模块、知识点 | Pydantic 严格契约、版本化树、canonical ID、alias 归一化、唯一性与环检测 | 自然语言标签会重复或变化，业务关联必须使用稳定 ID |
| Textbook Library | 文件 → 不可变教材版本和 ingestion job | 内容 hash、opaque source ref、异步阶段、checkpoint、幂等恢复 | 大文件处理慢且可能失败，不能用一次 HTTP 请求赌全部成功 |
| Section Recovery | Parse IR → 章节树 | 优先 PDF outline/页文本；其次 Markdown heading；最后低置信度 `Full text` | 章节是教材语义边界，不能把固定长度 chunk 冒充章节 |
| Chunking / RAG | 章节正文 → 可检索 chunk | 约 1800 字符、200 overlap、优先换行断点、不跨章节、完整 metadata | 控制上下文大小，同时保留章节和页码 provenance |
| Binding / Mapping | 计划目标 ↔ 教材章节 | 版本化多对多映射、candidate/confirmed/rejected、人工确认 | Taxonomy 和教材目录是两棵树，名称相似不等于身份相同 |
| Grounded Learning | 用户问题 → 固定版本教材证据 | 章节 metadata filter、每教材 top-k、优先级、source snapshot、冲突标记 | 防止检索串章节、串版本或静默融合冲突教材 |
| Practice | 知识点 → 出题、作答、结果 | 七状态持久化状态机、checkpoint、CAS、artifact identity | 网络中断和重复请求不能造成重复评分或重复写 Memory |
| Grading | 题目、rubric、答案 → `GradeResult` | temperature 0、严格 JSON Schema、rubric item 白名单、提示注入隔离 | LLM 可做语义判断，但不能自由改变评分契约 |
| Knowledge Mapping | 题目语义 → canonical 知识点 | LLM 只提候选名称，规则归一化器决定 ID；catalog 路径直接校验 ID | 模型可以理解文本，但不应凭空创造业务 ID |
| Diagnosis | 评分证据 → 错因与知识点 | 严格词表、候选 ID 白名单、结构化输出 | 防止自由文本错因无法聚合或越界写入 |
| Learning Memory | 正式证据 → 当前学习状态 | L1/L2/L3、确定性 lifecycle、CAS、provenance、pgvector | 同时满足审计、更新、冲突和语义读取 |
| Profile / Recommendation | 当前状态 → 画像与下一步任务 | 确定性投影；五特征加权排序；稳定 tie-break | 推荐需要可解释、可复现，不能完全交给 LLM 临场选择 |
| Review / Correction | 历史结果 → 复盘或纠正 | 版本化读取、追加式 review event、重建投影 | 纠错不能删除原证据或直接覆盖历史 |
| API / UI / SDK | 多入口请求 → 相同领域服务 | 插件路由、严格 DTO、Repository、React/Next.js | 保持入口一致并避免页面直接拼数据库语义 |

---

## 6. 教材如何从 PDF 变成可用的学习证据

### 6.1 数据类型转换

```text
PDF bytes
→ Host source_ref + source hash
→ ParsedDocument(markdown, blocks, pages, outline, parser_signature)
→ TextbookSection(id, key, parent, level, path, pages, content_hash)
→ Chunk(text, textbook_version, section_key, path, pages, source_ref)
→ RAG index + index_version
→ PlanBinding + ObjectiveSectionMapping
→ EvidenceSnapshot(chunks, scores, versions, citations, conflict_state)
→ LLM 教学上下文
```

PostgreSQL 保存教材、不可变版本、章节结构、摄取任务、计划绑定、知识点—章节映射和来源快照。大段教材正文、解析产物、embedding 与索引文件由 Host 的文件和知识库系统保存；因此在 DBeaver 中可以看到教材元数据和章节，而不会看到完整 FAISS 向量文件。

### 6.2 章节识别和切块算法

章节恢复按可信度排序：

1. PDF parser 提供可用 outline 时，用 outline 的 level、title、page 建树，并按相邻目录项切页内正文；
2. 没有可用 outline 时，从 Markdown 的 `#` 到 `######` heading 恢复层级；
3. 两者都没有时，生成一个置信度 0.5 的 `Full text`，明确表示“未可靠识别章节”。

section ID/key 由版本、路径和顺序的 hash 派生，重复摄取能得到稳定身份。chunk 只在单个 section 内滑动，默认大小 1800 字符、overlap 200；遇到后半段换行会优先在换行处截断。每块都带教材版本、章节路径、页码、顺序和源文件引用。

### 6.3 检索和使用

创建学习路径时，用户可以绑定整本教材或具体章节。确认后的 objective—section 映射决定允许检索的 `section_key` 集合。

学习会话中，本轮用户问题就是主要 RAG query；模型也可以通过 RAG tool 提出可变查询，但可检索范围仍由服务器固定。系统不会把某一章全文一次性交给 LLM，而是在绑定版本和章节过滤后，为每本教材取少量相关 chunk，并渲染版本、章节和页码引用。

若有多本教材，按 role 和 priority 排序；当前基于各教材首条证据的规范化文本判断是否一致，不一致时标记 `comparison_required`，提示模型分别引用。这个算法能防止静默融合，但仍只是保守的文本差异启发式，不等价于真正的语义矛盾识别。

### 6.4 难点与解决方式

| 难点 | 解决方式 | 仍有限制 |
| --- | --- | --- |
| PDF 视觉标题未必有 Markdown 格式 | 优先使用 PDF outline 和逐页文本，再退回 heading | 扫描件、坏目录和复杂排版仍依赖更强 parser/OCR |
| 前言、目录等被识别成章节 | 允许用户查看结构和控制映射，不把自动结果直接当考试 Taxonomy | 章节树人工编辑能力仍可继续增强 |
| 大文件处理时间长 | 异步 job、真实阶段进度、safe checkpoint 和重试 | 当前仍受本机 CPU/GPU、解析器和 embedding 速度限制 |
| 重新建索引可能改变结果 | 快照固定 `index_ref + index_version`，恢复时校验 | 索引变化后必须显式恢复，不能无感继续 |
| 多教材结论冲突 | 来源优先级、分别检索、冲突状态、逐来源引用 | 深层事实冲突仍需更细粒度 NLI 或人工治理 |

---

## 7. ExamMem Learning Memory

### 7.1 三层模型

| 层 | 数据 | 存储 | 作用 |
| --- | --- | --- | --- |
| L1 | 一次正式作答、显式纠正、计划迁移等 `LearningEvent` | PostgreSQL append-only event | 原始业务证据与幂等入口 |
| L2 | mastery、error pattern、plan 等版本化 `LearningMemory` | PostgreSQL + provenance + pgvector | 当前业务状态、冲突与历史版本 |
| L3 | weak/mastered points、稳定错因、active plans | 可重建 `StudentModel` snapshot | 快速画像和推荐读取 |

L1/L2 是业务真相，L3 是可删除并重建的 read model。教材内容不进入 Learning Memory，因为教材是知识来源，不是“用户已经掌握”的证据。

### 7.2 一次作答怎样更新 L2

```text
GradeResult + DiagnosisResult
→ mastery / error-pattern candidate
→ 四维 Scope + canonical slot 精确查当前 active/contested 记录
→ 关系判断：duplicate / complementary / contradictory / correction
→ 纯策略产生 ADD / NO_OP / MERGE / SUPERSEDE / INVALIDATE / CONTESTED
→ 同事务写 L1、L2、provenance、decision journal、change log
→ 提交后刷新可重建 L3
```

更新目标必须使用精确 slot，而不是向量相似度。两个名称相近的知识点可能是不同业务状态，如果靠相似度决定覆盖对象，会把数据写错位置。

冲突也不是简单“最后一次作答获胜”。策略综合置信度、题目难度、错误类型、时间衰减、独立 session 数、方向证据数和支持 margin；证据不足时进入 `contested`。写入使用 expected `row_version` 做 CAS，stale writer 必须重新读取和决策。

### 7.3 embedding 如何生成、存储和检索

L2 新版本会先把结构化 value 序列化为稳定、可读的 `search_document`，再通过 Host embedding 能力编码。当前检索评估采用 Qwen3 Embedding 0.6B、1024 维；向量存入 PostgreSQL `pgvector` 列，并建立 HNSW 索引。

这里有两种查询：

- canonical slot：SQL 精确过滤 Scope、namespace、slot 和 active/contested 状态，不生成 query embedding；
- 自然语言：query instruction embedding → Scope 和状态过滤 → pgvector cosine 候选召回 → 结构化意图过滤 → Qwen3 Reranker 4B 重排 → 阈值拒答 → 返回 0..K 条。

所以系统不是在 Python 中把数据库每条向量逐个拿出来计算。小范围时 PostgreSQL planner 可能选择顺序扫描；大范围可使用 HNSW 近似索引。是否走索引由查询形状、数据规模和 planner 成本共同决定。

### 7.4 L3 如何重建

L3 重建读取一个固定 context 下的 L1/L2 snapshot：

1. 校验所有 event 属于相同 context，并包含 source watermark；
2. 校验每条 L2 provenance 都能回到 L1，且 evidence count 一致；
3. 只投影稳定 `active` 记录；contested、archived、invalidated 仍保留审计，但不当作稳定当前状态；
4. mastery low 进入 weak points，high/mastered 进入 mastered points；
5. 聚合稳定错因和活动计划；
6. 检查同一知识点不能同时 weak 和 mastered；
7. 写入带 projection version 和 source watermark 的快照。

算法是确定性的、与输入顺序无关，不调用 LLM。这样 L3 损坏或刷新失败时，可以从 L1/L2 重新构建，而不影响已经提交的正式学习事实。

---

## 8. 从教材学习到练习、记忆和推荐

### 8.1 学习会话

学习入口固定 plan、plan version、taxonomy version、objective 和教材 source snapshot。mastery path 自动检索允许章节中的相关 chunk，将教材版本、章节、页码和冲突说明一起交给模型。普通对话中的“我懂了”只能作为弱上下文，不能直接提升正式 mastery。

### 8.2 七状态练习工作流

```text
IDLE
→ QUESTION_READY
→ ANSWER_RECEIVED
→ GRADED
→ DIAGNOSED
→ MEMORY_UPDATED
→ RECOMMENDED
```

每个完成步骤都保存 checkpoint。恢复时校验 question version、答案 hash、rubric version、grader contract、运行配置、Taxonomy 和教材/索引快照；身份不同就要求显式恢复，而不是复用旧结果伪装成同一次执行。

### 8.3 推荐算法

推荐调用链是 `PracticeRuntimeProvider.recommend()` → 候选构建 → `RecommendationPolicyV1.rank()` → 推荐门控 → 有界 LLM 选择或规则回退 → 题库取题。系统先对每个知识点计算五个归一化特征：

```text
priority = 0.40 × weakness
         + 0.25 × stable_error
         + 0.15 × forgetting_risk
         + 0.10 × active_plan_priority
         + 0.10 × coverage_gap
```

争议证据和近期重复练习会降权；同分时按优先级、考纲顺序和 canonical ID 稳定排序。优点是可解释、可测试、同输入同输出；缺点是权重目前是工程策略，还没有真实学习增益实验支持。

这里的“门控”专指完成一道题后是否生成下一条练习推荐，不是 RAG 检索、身份权限或模型调用门控。它分为三层：

1. **触发证据门控**：临时异常证据或置信度低于 `0.5` 的事件不能触发后续推荐。
2. **可行动信号门控**：候选必须至少具有 weakness、stable error、active plan priority，或达到 `forgetting_risk >= 0.5`。遗忘风险按 `min(1, 距最近有效记忆天数 / 30)` 计算，因此当前阈值约为 15 天。coverage gap 只参与排序，不能单独迫使系统出题；所有候选均未通过时返回显式 `no_recommendation`。
3. **LLM 选择结果门控**：通过前两层后，Host LLM 只能从服务端给出的最多 12 个 canonical 候选中选择，结果必须通过 JSON Schema、候选 ID 白名单且置信度不低于 `0.55`；失败、越界或低置信度时回退到确定性排序。

首次进入练习的 `IDLE` 状态仍允许按已发布范围从考纲取起始题；上述“无推荐”语义主要约束完成作答后的连续推荐，避免没有复习依据时硬推下一题。门控决定“要不要推荐”，五特征权重与 LLM/规则选择决定“推荐哪个候选”，两者不能混为同一个调参问题。核心实现位于 `exam_mem/practice/provider.py` 和 `exam_mem/practice/recommendation.py`。

---

## 9. ExamMem 评估

### 9.1 Controlled Lifecycle Evaluation

#### 数据与方法

| 项目 | 内容 |
| --- | --- |
| 数据集 | `exam_mem_controlled_v1`，数学一线性代数和概率论的合成多轮学习轨迹 |
| 规模 | 120 case：dev 40、一次性 frozen test 80；共 12 类场景 |
| 输入边界 | 从已经校验的结构化 `LearningEvent` 开始，不评测原始聊天抽取 |
| 比较对象 | none、DeepTutor native、append-only、vector、lifecycle 五个 backend |
| 控制方法 | 相同 Gold、顺序、top-k 和 seed；只用 dev 调整，test 一次性 claim |

主要指标含义：

| 指标 | 含义 |
| --- | --- |
| Operation accuracy / macro-F1 | ADD、MERGE、SUPERSEDE、CONTESTED 等生命周期操作是否正确 |
| Active-state exact | 每个检查点的当前稳定状态是否与 Gold 完全一致 |
| Stale / duplicate rate | 返回过时状态或重复状态的比例，越低越好 |
| Cross-scope leakage | 是否读到了其他用户/考试/科目的记忆，必须为 0 |
| Recommendation accuracy | 推荐知识点是否匹配 Gold |

#### v1 Frozen test 历史结果

| Lifecycle 指标 | 结果 |
| --- | ---: |
| Operation accuracy | 95.73%（381/398） |
| Operation macro-F1 | 82.49% |
| Active-state exact | 90.42%（217/240） |
| Stale rate / duplicate rate | 3.32% / 3.32% |
| Cross-scope leakage | 0 |
| 推荐知识点准确率 | 30.83%（74/240） |
| 完成率 | 98.75%（79/80） |

95.73% 衡量每次 Memory 操作是否正确；90.42% 衡量一系列操作完成后当前状态是否完全一致。前者高但后者较低，是因为一次操作错误可能持续影响后续多个状态检查点。

#### 发现的问题

- Stage08 中 lifecycle operation accuracy 只有 27.92%，主要暴露关系分类、NO_OP/合并边界、候选污染和完成率问题；
- 修复层间契约和确定性策略后，frozen test 明显提升，但少数稀有 operation 拉低 macro-F1；
- 推荐准确率只有 30.83%，说明 Memory 状态维护得较好，不代表下一知识点推荐已经成熟；
- 输入从结构化事件开始，因此不能声称聊天抽取、出题或判题准确率达到 95.73%。

#### 推荐决策修复（冻结评测之后）

当前实现已把“是否应该推荐”和“推荐哪个候选”拆开：服务端使用上述三层门控；没有可行动证据时显式返回 `no_recommendation`，coverage gap 不能独自强行出题，而达到复习阈值的遗忘风险可以打开门控。门控通过后，LLM 只在有界 canonical 候选集中选择；模型失败、越界或低置信度时回退到确定性排序，并记录候选集、选择策略和 selector 版本。

30.83% 是 v1 在推荐修复前的一次性 frozen 五后端历史结果，不能被后续对同一 test 的重跑替换。

2026-08-27 使用真实 Host MiniMax 和隔离 PostgreSQL 对 lifecycle 单臂重跑了 40-case dev：知识点准确率为 **83.33%（100/120）**，动作类型诊断准确率为 **93.33%（112/120）**，过度复习率为 **2.50%（3/120）**。这证明校准方向有效，但它是 dev 单臂结果，不替代 30.83% 的正式 frozen 五臂基线。

随后对已经公开并参与过诊断的 80-case test 做了五后端 post-hoc 重跑：lifecycle 知识点准确率为 **82.08%（197/240）**，动作类型诊断准确率为 **92.83%（220/237）**，过度复习率为 **2.95%（7/237）**；79/80 case 完成。其余四个无 lifecycle 状态的 backend 均为 55.00%（132/240），主要来自对低置信度 padding 的正确拒绝。唯一 lifecycle 失败是 MiniMax 关系分类返回了与候选 slot 不一致的知识点，严格契约将其拒绝。由于 test 已被查看过，这只能作为 post-hoc 泛化证据，不能重新包装成未见 holdout 成绩。

#### v3 跨学科 frozen test

为避免把已公开数学 test 当成新 holdout，项目新增了计算机数据结构与算法数据集。第一次
转换 v2 在运行后审计中发现轨迹正文仍残留数学语义，因此其 82.08% 不作为跨学科证据；
修正后的 `exam_mem_controlled_v3` 重写题目、答案、Memory 值、Taxonomy/slot、查询和
Scope，并在提交 `474a2732` 上一次性运行 80-case 五后端 test。

| Lifecycle 指标 | v3 frozen test |
| --- | ---: |
| 完成率 | 97.50%（78/80） |
| Operation accuracy / macro-F1 | 94.47%（376/398）/ 82.24% |
| Active-state exact | 89.17%（214/240） |
| Stale / duplicate rate | 3.75% / 2.62% |
| Cross-scope leakage | 0 |
| Weak recall@5 / archived hit@5 | 80.00% / 0 |
| 推荐知识点准确率 | **80.83%（194/240）** |
| 动作类型诊断准确率 | 92.74%（217/234） |
| Over-review rate | 2.99%（7/234） |

其余四个 backend 的推荐知识点准确率均为 55.00%，主要来自正确 `no_action`；这不是有效
选题能力。两个 Lifecycle case 因模型输出与候选 slot 不一致被严格拒绝。v3 复用 v1 的
生命周期形状，只改变学科语义，因此支持有限的跨科目迁移结论，不等价于真实用户泛化。
少量 opaque 记录 ID 仍保留模板英文后缀，但关系分类 prompt 明确不包含这些 ID。
完整证据见[跨学科 Memory 冻结评测](./evaluation/controlled-v3-frozen-test.zh-CN.md)。

### 9.2 Semantic Retrieval v2

#### 数据集来源与规模

数据仍是合成数学一场景，但为了保留问题独立性，由多个独立生成过程分别构造 mastery、error pattern、language/safety 数据后合并。

| Split | Corpus | Queries | Answerable / No-answer |
| --- | ---: | ---: | ---: |
| dev | 396 | 170 | 140 / 30 |
| frozen test | 396 | 310 | 250 / 60 |

Test query 包括 mastery 100、error 110、language/safety 100；Corpus 包含 active、contested、archived 和 invalidated 状态，并刻意加入相邻知识点、相似错因、跨 Scope 和无答案难例。

#### 指标和结果

| 指标 | 它在测什么 | Frozen test |
| --- | --- | ---: |
| Recall@5 / Hit@5 | Gold 是否被 Top-5 找回 | 0.9547 / 0.9800 |
| MRR | 第一个 Gold 排得是否靠前 | 0.9660 |
| nDCG@5 | 多结果排序质量 | 0.9505 |
| Pairwise accuracy | Gold 是否排在相关难负例之前 | 0.9892 |
| No-answer accuracy | 没有合格记忆时是否返回空 | 0.9833 |
| Hard-negative@1 | 第一名被难负例占据的比例，越低越好 | 0.0280 |
| Hard-negative@K / accepted-result | 返回列表中是否混入难负例 / 返回项中的难负例比例 | 0.1290 / 0.1176 |
| Archived/invalidated hit | 是否召回终止状态 | 0 |
| Cross-scope leakage | 是否越过 Scope | 0 |
| 端到端 P95 | embedding、数据库、reranker 的检索决策延迟 | 594.78 ms |

由于检索现在允许少于 `K` 条返回，当前 `Precision@K` 仍按评估协议用固定 `K` 作分母，
不能直接解释为“每一条实际返回结果的精确率”。判断动态截断是否减少污染，应同时看
`accepted-result hard-negative`、Recall/Hit 和 no-answer accuracy。

#### 修复前为什么不可靠

1. 固定返回 Top-K，没有可靠拒答，所以 no-answer accuracy 为 0；
2. 仅靠 embedding 能找回 Gold，但 Top-5 混入大量相邻概念；
3. query instruction 没有真正进入 query embedding 路径；
4. SQL 排序形状让 HNSW 难以进入生产 plan；
5. 单一 cosine threshold 无法同时保持高召回和高拒答。

最终采用“保守结构化意图 + instruction embedding + HNSW-compatible candidate query + 4B reranker + 绝对阈值 + Top-1 相对分差 + 可变 0..K 返回”。当前策略是：

```text
acceptance_threshold = max(0.003, top1_score - 0.03)
```

`0.03` 只在 dev 集校准，并在 frozen test 上一次性验证。它把 test 的难负例混入率明显降低，同时保留 0.98 的 Hit@5；代价是 Recall@5 从未截断基线的 0.992 降至 0.9547。

#### 仍未解决的问题

- `hard-negative@K=0.1290`、accepted-result hard-negative rate 为 0.1176，说明第 2 到第 5 名仍偶尔会混入相邻概念；
- 4B reranker 带来显存、模型下载和约 594 ms P95 成本；
- 10k Scope 上 HNSW Recall@5 为 1.0，但完整 Repository P95 64.77 ms，仍略慢于 exact reference 56.87 ms；强制只取 ID 的索引控制组为 3.29 ms，瓶颈还包括 planner 选择和完整行/provenance 回读；
- 数据集中学科和表述分布仍窄，尚未覆盖真实学生长期历史。

### 9.3 评估覆盖边界

| 能力 | 当前证据 | 结论 |
| --- | --- | --- |
| DeepTutor 整体辅导 | TutorBench | 有论文级相对结果 |
| DeepTutor Native Memory | 工程测试 + ExamMem baseline arm | 缺独立质量 benchmark |
| ExamMem Lifecycle | 120 条受控轨迹 | 当前状态维护较强，但输入从结构化事件开始 |
| ExamMem 语义检索 | 396 corpus、310 frozen queries | 排序和拒答达门槛，Top-2..K 仍有污染 |
| 教材章节识别 | 单元、集成与样例验证 | 缺多版式人工标注 PDF 基准 |
| 出题、判题、错因 | 契约和流程测试 | 缺教师双标准确率、Kappa/MAE 等结果 |
| 推荐 | Controlled benchmark + 新门控/选择契约测试 | v1 历史基线 30.83%；跨学科 v3 frozen test 80.83% |
| 真实学习增益 | 尚无 | 不能声称系统已提高真实考试成绩 |

---

## 10. 最重要的难点、原因与解决思路

### 10.1 LLM 擅长语义，但不适合拥有业务写权限

评分、错因和候选名称允许 LLM 参与；canonical ID、数据库操作、生命周期决策、推荐门控和版本恢复由严格 schema 与确定性代码控制。推荐门控通过后，LLM 只可在服务端有界候选集中选择 canonical ID，低置信度或无效输出回退到确定性排序。这样既利用模型理解能力，又防止幻觉直接变成不可审计事实。

### 10.2 “相关”不等于“同一个状态”

向量检索适合回答“哪些记忆与问题有关”，不适合回答“应该更新数据库里的哪一条记录”。因此更新走 exact slot，发现相关内容才走 embedding + rerank。

### 10.3 自动化不能伪装成确定事实

识别不出 PDF 章节时返回低置信度 `Full text`；教材映射先 candidate 再确认；多教材不同就标记比较；Learning Memory 冲突进入 contested。这些设计共同避免系统在证据不足时表现得过度自信。

### 10.4 可恢复比“所有步骤一次成功”更现实

教材摄取和练习都可能跨越数秒到数分钟。系统把它们拆成阶段，保存 checkpoint、输入身份和中间产物；恢复时只执行缺失步骤，并验证依赖版本未变化。

### 10.5 评估必须隔离层次

结构化 Memory benchmark 只评价 Memory，不把 Gold slot 当成抽取模型成果；语义检索用 dev 校准后冻结 test；论文结果、离线结果和工程测试分别报告。这样可以避免“某个局部指标很高，所以整个产品很好”的错误结论。

---

## 11. 当前优势、限制与下一步

### 做得较好的地方

- Agent、工具、流事件和入口协议统一，通用能力可以复用；
- 正式学习事实具备版本、Scope、provenance、CAS 和可重建投影；
- 教材检索固定版本与章节，证据能回到教材、章节和页码；
- 练习和摄取都能幂等恢复，失败语义清楚；
- 检索不仅追求召回，还显式评估拒答、难负例、终止状态和越界；
- 评估报告明确写出哪些指标不能证明产品效果。

### 当前限制

- PDF 章节恢复仍受原始目录、OCR 和 parser 质量影响；
- 多教材冲突检测较粗，缺少句级事实对齐和人工治理队列；
- Native Memory consolidation 与 ExamMem 的聊天抽取都缺专项人工标注数据；
- 推荐权重没有通过真实用户或学习增益实验校准；
- 语义检索依赖较大的 reranker，Top-2..K 仍有难负例；
- 尚无教师双标判题集、跨学科教材集、真实长期用户与线上 A/B 实验。

### 建议的下一轮评估

1. 建立多版式、含扫描件的教材章节与页码人工 Gold；
2. 建立教师双标的出题、评分、错因数据，报告 accuracy、macro-F1、MAE/Kappa 和分歧；
3. 为 Native Memory 建立事实抽取、引用正确性、冲突处理和长期帮助度专项集；
4. 扩展跨学科、跨考试和真实历史的 Memory 检索数据，并做 reranker/阈值/Top-K 消融；
5. 用真实用户实验比较固定策略与个性化推荐对延迟回忆、完成率和考试成绩的影响。

---

## 12. 面试时可以怎样概括

可以用下面这段话收尾：

> DeepTutor 提供 Agent 运行时、Tool/Capability、StreamBus、解析和 RAG；ExamMem 在此基础上把考试范围、教材版本、练习、评分和长期学习证据组织成可恢复闭环。系统让 LLM 负责语义理解，让确定性契约负责身份、状态和写入。教材通过 Parse IR、章节感知切块和固定范围 RAG 进入教学；正式作答通过 L1 证据、L2 生命周期和可重建 L3 进入画像与推荐。评估显示 Lifecycle 状态维护和语义检索已经有较强离线结果，但推荐、PDF 结构质量、教师判题和真实学习增益仍需要更强数据验证。

---

## 13. 主要源码与证据入口

- DeepTutor 框架：`deeptutor/runtime/orchestrator.py`、`deeptutor/core/stream_bus.py`、`deeptutor/core/tool_protocol.py`、`deeptutor/core/capability_protocol.py`
- Native Memory：`deeptutor/services/memory/`
- Parse/RAG：`deeptutor/services/parsing/`、`deeptutor/services/rag/`、`deeptutor/tools/rag_tool.py`
- ExamMem 领域：`exam_mem/`
- ExamMem 插件：`deeptutor_plugins/exam_mem/`
- 教材链路：`exam_mem/textbooks/structure.py`、`deeptutor_plugins/exam_mem/textbooks.py`、`deeptutor_plugins/exam_mem/grounded_learning.py`
- Learning Memory：`exam_mem/contracts/memory.py`、`exam_mem/lifecycle/`、`exam_mem/projection.py`、`exam_mem/storage/`
- 练习闭环：`exam_mem/practice/workflow.py`、`grading.py`、`error_analyzer.py`、`recommendation.py`
- Lifecycle 方法与结果：[评估方法](./evaluation/methodology.md)、[Stage09 frozen test](./evaluation/stage09-frozen-test.md)、[跨学科 v3 frozen test](./evaluation/controlled-v3-frozen-test.zh-CN.md)
- 语义检索：[协议](./evaluation/semantic-retrieval-v2.zh-CN.md)、[修复与最终结果](./evaluation/semantic-retrieval-v2-remediation.zh-CN.md)
- 更深入源码走读：[INTERVIEW_DEEP_DIVE.zh-CN.md](./INTERVIEW_DEEP_DIVE.zh-CN.md)
- DeepTutor 论文：[TutorBench 评估](https://arxiv.org/html/2604.26962#S5)
