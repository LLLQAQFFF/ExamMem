# ExamMem 第一方插件中文 Runbook

本文用于运行已经迁移到 DeepTutor 的 ExamMem 当前产品闭环：导入大纲、确认并发布
层级化学习计划、按知识点恢复辅导、生成版本化检测、评分、Learning Memory、推荐、
恢复、纠错、Review、Issues 和 Configuration。

本文不授权修改生产数据、执行破坏性降级、发布或部署。

## 1. 架构和数据边界

运行链路如下：

```text
浏览器 HTTP / Python SDK / 统一 WebSocket
  → DeepTutor Turn Host
  → Capability Registry：exam_practice
  → ExamMem PracticeRuntimeProvider
  → Question → Grade → Taxonomy 映射 → Diagnosis
  → 当前 Pinned Memory Backend
  → Recommendation → Checkpoint + append-only Trace
  → Resume / Correction / Grade Review / Issues

大纲文件 / 公开 URL / 模型创建请求
  → ExamMem 只提取计划—科目—章节—叶子知识点标题
  → 草稿确认 → 不可变发布版 Taxonomy
  → 每个叶子知识点 → 中性 Host Hook → 独立原生 Mastery Path
  → 首次进入自动发起辅导，后续恢复同一 Chat session
  → 智能备考「练习」选择同一个发布版 Scope 和叶子知识点
  → 可选 PDF/TXT/Markdown 临时上下文
  → 中性 Host Turn → 原生 Quiz 生成
  → 同一 assessment_id 下的不可变题集版本 + 多次 attempt
  → 题目/答案/规则/来源指纹固定到 Practice Checkpoint
  → 进入上面的 ExamMem 评分和 Learning Memory 闭环
```

必须遵守以下边界：

- ExamMem 使用自己的 PostgreSQL，不能指向 DeepTutor 内部数据库或共享数据库。
- `EXAM_MEM_DATABASE_URL` 只能从进程环境读取，不能写入仓库或普通配置文件。
- DSN 必须使用 `postgresql+asyncpg`，并包含用户名、密码、主机和数据库名。
- DeepTutor Native Memory 不是 Learning Memory 的业务真值。`native` 模式只通过中性
  Host Adapter 调用 Native Memory。
- migrations `0001`～`0006` 已冻结，禁止修改内容、revision、顺序或语义。
- L1、Trace、Lifecycle Decision、Change Log、Baseline Fact 和 Grade Review 是
  append-only 数据，禁止直接 UPDATE 或 DELETE。
- L3 是可重建投影，不是唯一真值，不能用旧 L3 反向覆盖 L1/L2。

## 2. 本地首次启动：最短路径

以下步骤使用仓库提供的 PostgreSQL Compose 文件。示例密码只适用于本地开发，请自行
更换；不要把真实密码提交到 Git。

### 2.1 进入项目和现有 Python 环境

```bash
cd /home/lh/DeepTutor
conda activate exammem
```

不要为运行本 Runbook 临时安装或升级依赖。项目验收使用的是 Python 3.11 环境。

### 2.2 设置本地 PostgreSQL 参数

```bash
export EXAM_MEM_POSTGRES_USER=exammem
export EXAM_MEM_POSTGRES_PASSWORD='请替换为本地专用密码'
export EXAM_MEM_POSTGRES_DB=exammem
export EXAM_MEM_POSTGRES_PORT=55432
```

如果密码包含 `@`、`:`、`/`、`#` 等 URL 特殊字符，拼接 DSN 前必须进行 URL 编码。

### 2.3 启动专用 PostgreSQL

此命令会启动本地容器并写入名为 `exammem-postgres-data` 的 Docker volume；不会修改
DeepTutor 内部数据库。

```bash
docker compose -f compose.exam-mem.yaml up -d postgres
docker compose -f compose.exam-mem.yaml ps
```

健康检查应显示 PostgreSQL 为 `healthy`。也可以执行：

```bash
docker exec exammem-postgres pg_isready -U "$EXAM_MEM_POSTGRES_USER" -d "$EXAM_MEM_POSTGRES_DB"
```

### 2.4 设置 ExamMem DSN

```bash
export EXAM_MEM_DATABASE_URL="postgresql+asyncpg://${EXAM_MEM_POSTGRES_USER}:${EXAM_MEM_POSTGRES_PASSWORD}@127.0.0.1:${EXAM_MEM_POSTGRES_PORT}/${EXAM_MEM_POSTGRES_DB}"
```

只输出脱敏连接摘要，确认目标库不是共享库或 DeepTutor 内部库：

```bash
python -c "from exam_mem.storage import load_database_settings; print(load_database_settings().safe_summary())"
```

预期能看到 driver、host、port 和 database，但不会显示密码。

### 2.5 执行 migration

先只读检查代码中的唯一 head：

```bash
python -m alembic -c alembic.ini heads
python -m alembic -c alembic.ini history
```

预期唯一 head：

```text
0010_learning_observations (head)
└── 0009_assessments
```

通过 PyPI wheel 安装、没有仓库根目录 `alembic.ini` 时，使用随包发布的等价入口：

```bash
python -m exam_mem.storage.migrations heads
python -m exam_mem.storage.migrations history
```

确认数据库目标后执行写操作：

```bash
python -m alembic -c alembic.ini upgrade head
python -m alembic -c alembic.ini current
```

wheel 安装环境对应的升级和检查命令为：

```bash
python -m exam_mem.storage.migrations upgrade head
python -m exam_mem.storage.migrations current
```

`upgrade head` 会在 ExamMem PostgreSQL 中创建或升级表、索引、约束和 trigger。预期
current 为 `0010_learning_observations`。

全新数据库最终包含 22 张 public 表（包括 `alembic_version`）和 10 个不同的
append-only trigger：

```text
tr_learning_events_append_only
tr_lifecycle_decisions_append_only
tr_memory_change_log_append_only
tr_baseline_memory_facts_append_only
tr_practice_trace_spans_append_only
tr_grade_review_events_append_only
tr_study_plan_versions_append_only
tr_assessment_versions_append_only
tr_learning_observations_append_only
tr_learning_observation_actions_append_only
```

### 2.6 确认插件没有被禁用

插件启停文件位于：

```text
<DEEPTUTOR_HOME>/data/user/settings/plugins.json
```

如果没有显式设置 `DEEPTUTOR_HOME`，从 `/home/lh/DeepTutor` 启动时默认路径为：

```text
/home/lh/DeepTutor/data/user/settings/plugins.json
```

启用状态下，`disabled` 不能包含 `exam_mem`：

```json
{
  "version": 1,
  "disabled": []
}
```

若文件不存在，默认不会禁用插件。修改该文件后需要重启 DeepTutor。

### 2.7 启动 DeepTutor

必须在仍然持有 `EXAM_MEM_DATABASE_URL` 的同一 shell 中启动：

```bash
deeptutor start
```

默认前端地址：

```text
http://127.0.0.1:3782
```

首次生产启动可能执行 Web production build。保持终端打开；按 `Ctrl+C` 会同时停止
DeepTutor 后端和前端，但不会停止 PostgreSQL。

开发模式可使用：

```bash
deeptutor start --dev
```

## 3. 启动后验收

### 3.1 浏览器页面

登录后依次检查：

```text
/exam-mem/practice       发布范围选题、试卷版本、多次检测、历史和恢复
/exam-mem/learning       大纲导入、草稿确认、科目/章节/知识点和继续辅导
/exam-mem/review         Grade、Diagnosis、Trace、Lifecycle 和 Grade Review
/exam-mem/memories       Learning Memory、版本链、证据、纠错和派生问题
/exam-mem/issues         兼容旧深链；主入口已合并到 Learning Memory
/exam-mem/configuration  Saved / Effective / Pinned 配置
```

### 3.2 插件装配检查

以下接口需要按当前 DeepTutor 鉴权配置访问：

```text
GET /api/v1/plugins/list
GET /api/v1/plugins/health
GET /api/v1/exam-mem/practice/sessions
GET /api/v1/exam-mem/catalog
GET /api/v1/exam-mem/study-plans
GET /api/v1/exam-mem/assessments
GET /api/v1/exam-mem/issues
GET /api/v1/exam-mem/configuration
```

`/api/v1/plugins/list` 应包含：

- 插件 `exam_mem`；
- Capability `exam_practice`；
- migration head `0010_learning_observations`；
- 单一的「智能备考」导航入口；学习路径、练习、学习记忆、考试复盘和配置作为其内部工作区。

注意：`/api/v1/plugins/health` 只表示插件生命周期装配成功。当前 ExamMem 没有主动连接
数据库的 health hook，因此它不能替代以下两项检查：

```bash
python -m alembic -c alembic.ini current
```

以及至少一次经过鉴权的 ExamMem 只读 API 请求。

### 3.3 最短业务 Smoke Test

1. 打开 `/exam-mem/learning`，点击「新建学习计划」，从 PDF/TXT/MD、公开 URL 或模型
   创建请求中选择一种。解析只生成科目、章节和叶子知识点标题，不生成课程正文和题目。
2. 在结构化草稿中检查或改名，保存后点击「发布为考试范围」。未发布草稿不能用于练习；
   发布后形成不可变版本，后续修改会形成新版本。
3. 点击一个叶子知识点的「继续学习」。第一次应自动创建一条只含该知识点的原生
   Mastery Path 和 Chat session，并自动发起一次“说明目标、前置知识和学习安排”的请求；
   再次点击应恢复同一 session，而不是重复创建。
4. 从同一知识点进入 `/exam-mem/practice`，确认学习计划、科目和知识点已经选中，且
   Scope 来自同一个发布版 Taxonomy，不再出现跨 Taxonomy 文本猜测映射。
5. 可选添加 PDF、TXT 或 Markdown。来源只服务本次生成；DOCX、PPT/PPTX、图片、视频和
   音频不在当前范围。
6. 点击「生成并开始检测」，确认页面显示第一题。提交到最后一题后，attempt 应为
   `completed`，Practice 的冻结七状态仍停在 `MEMORY_UPDATED`，不会新增状态。
7. 在“考试版本与多次作答”中点击「重考当前版本」，应使用相同 assessment ID 和题集
   version、新的 attempt；点击「生成新版」应在相同 assessment ID 下创建下一 version。
8. 打开 Review，确认能看到 Grade、Diagnosis、Trace 和 Lifecycle 信息。
9. 打开 Learning Memory，确认能查看 Scope、版本链、evidence、纠错和派生问题。
10. 返回 Practice 历史并执行 Resume，确认恢复的是服务端 checkpoint，而不是只依赖
   浏览器缓存。

上述提交会按当前 Backend 写入真实 ExamMem PostgreSQL。生产环境执行前必须确认用户、
考试和科目 Scope。

如果使用仓库的 `exammem-demo-postgres` 做验收，不要假设 public 表为空，也不要为了测试
清理已有演示记录。自动化数据库测试使用随机 schema 并在结束时删除；验收后应只读确认
没有随机 schema 残留，保留原有 public 数据。

## 4. 配置语义和五种 Backend

ExamMem 非敏感设置位于：

```text
<DEEPTUTOR_HOME>/data/user/settings/plugin_exam_mem.json
```

默认配置为：

```json
{
  "enabled": true,
  "subject": "postgraduate_math_1",
  "memory_backend": "lifecycle",
  "capabilities": {
    "exam_practice": true
  }
}
```

Configuration 页面中的三个概念不能混用：

- **Saved**：已经保存到设置文件，等待进程重启生效。
- **Effective**：当前进程实际使用的配置。
- **Pinned**：某次 Practice 创建时冻结的 Backend、配置 revision 和副作用集合。

修改 Saved 后，已有 Practice 仍使用原 Pinned 配置；新 Practice 在重启后使用新的
Effective 配置。

五种 Backend 的预期持久化副作用：

| Backend | 预期副作用 |
| --- | --- |
| `none` | Checkpoint、Practice Trace |
| `native` | Checkpoint、Practice Trace、Host Native Memory Adapter |
| `append_only` | Checkpoint、Practice Trace、L1 Learning Event |
| `vector` | Checkpoint、Practice Trace、L1、Vector Baseline Fact |
| `lifecycle` | Checkpoint、Practice Trace、L1、L2、Provenance、Decision Journal、Change Log、重建 L3 |

模式切换不会启用 fallback。所需依赖缺失时必须显式失败，不能自动改用其他 Backend。

## 5. 恢复、纠错和 Review

### 响应丢失

使用完全相同的答案请求和相同 idempotency key 重试。当前标签页的
`sessionStorage` 会保存待提交请求，但它只是响应丢失优化，不是业务真值。

### 关闭标签页或稍后返回

从 Practice 历史使用 Resume。服务端会读取最新 Scope-bound checkpoint、原 Trace、上下文
和 Pinned Backend，并创建新的 Host transport session。

### 工作流失败

在 Review 查看 Trace，在 Issues 查看派生问题。只重试标记为 retryable 的错误。
grader contract/version mismatch 会 fail closed，不能通过换 Backend 绕过。

### L3 投影待恢复

`projection_pending` 会保持 open，直到 checkpoint 恢复完成投影刷新。禁止手工伪造 L3
记录，也不能从旧投影反推并覆盖 L1/L2。

### Learning Memory 不准确

提交经过确认的 Correction。Correction 会追加 L1 和 Lifecycle evidence，不会原地编辑或
删除旧 Memory。

### Grade 有争议

学习者提交 Grade Review dispute；管理员可以 Uphold。Overturn 目前通过 API 完成，并且
必须提供完整的结构化 replacement Grade。Grade Review 本身不会自动修改 Learning Memory。

### Plan 取消

用户取消必须显式确认，并作为 Lifecycle transition 记录，不能直接修改数据库行。

所有产品读写中的 `user_id` 均来自 DeepTutor 鉴权上下文，浏览器不能伪造或跨用户访问
Scope。

## 6. 日常验证命令

### Python 静态检查和测试

下面的 PostgreSQL 测试会使用 `EXAM_MEM_DATABASE_URL`，并通过随机 schema 或事务隔离；
不要把它指向未授权的共享库。

```bash
cd /home/lh/DeepTutor
python -m ruff check deeptutor exam_mem deeptutor_plugins tests
python -m pytest -q
python -m pytest -q -m backend_mode tests/exam_mem
```

### Web 检查和生产构建

```bash
cd /home/lh/DeepTutor/web
npm run lint
npm run test:node
npm run build
```

生产构建可能把 `web/next-env.d.ts` 改成对应构建目录的引用。这是生成物漂移，不要把该
变化作为功能修改提交。

迁移完成时的验收基线为：

- 禁用 ExamMem 且无 DSN 的 DeepTutor 原生测试：`3593 passed, 9 skipped`；
- 启用 ExamMem 和隔离 PostgreSQL 的全仓测试：`4293 passed, 9 skipped`；
- 五 Backend 专项：`33 passed`；
- Web Node：`65/65`；
- Web production build：63 个路由。

这些数字是迁移时兼容依赖环境的历史基线，不是对任意“最新版依赖”组合的永久保证。
截至 2026-08-16，无上限的 FastAPI 声明会解析到 Starlette 1.4.x；其 `TestClient` 已优先
使用 `httpx2`。在项目显式补充该测试依赖并完成全量重跑前，干净环境若在首个
`TestClient` 请求处停住，应按开源审计报告处理，不能跳过测试或把超时当成通过。

## 7. PostgreSQL 备份、停止和清理

### 停止 DeepTutor

在运行 `deeptutor start` 的终端按：

```text
Ctrl+C
```

### 停止 PostgreSQL但保留数据

```bash
cd /home/lh/DeepTutor
docker compose -f compose.exam-mem.yaml stop postgres
```

再次启动：

```bash
docker compose -f compose.exam-mem.yaml up -d postgres
```

### 备份

以下命令读取数据库并在当前目录生成 SQL dump；dump 可能包含学习者数据和 Grade Review
理由，必须按敏感数据保管：

```bash
docker exec exammem-postgres pg_dump \
  -U "$EXAM_MEM_POSTGRES_USER" \
  -d "$EXAM_MEM_POSTGRES_DB" \
  --format=custom \
  --file=/tmp/exammem.backup

docker cp exammem-postgres:/tmp/exammem.backup ./exammem.backup
```

不要在未验证备份前删除数据库或 Docker volume。

### 禁止的清理方式

日常停止不要执行：

```text
docker compose -f compose.exam-mem.yaml down -v
alembic downgrade base
```

`down -v` 会删除持久化数据库 volume；`downgrade base` 会破坏业务 schema。`0007`～
`0010` 中存在 Review、学习计划、会话链接、考试版本、attempt 或 Agent 观察数据时均拒绝
自动降级。

## 8. 必须暂停并找管理员处理的情况

遇到以下任一情况，不要继续试错：

- 无法确认 `EXAM_MEM_DATABASE_URL` 指向哪个数据库；
- 目标是共享库或生产库，但没有明确变更窗口和备份；
- 需要获取、更换或迁移凭据；
- migration head 不是 `0010_learning_observations`，或出现多 head/分叉；
- 需要执行 destructive downgrade、删除 schema、删除 Docker volume 或覆盖历史数据；
- 需要发布、部署或推送远端；
- 需要通过切换 Backend、绕过 Scope、直接写 Native Memory、编辑 append-only 数据或更换
  idempotency identity 来“修复”失败。

## 9. 当前已知限制

### 学习档案与 Agent 的使用边界

1. 先在“学习路径”导入并发布大纲；“学习档案”的专业、科目、大纲版本、章节和知识点筛选
   都来自不可变的发布版本，不再使用固定数学一 Scope。
2. L1 页展示正式刷题、纠正和计划转换证据；已经绑定知识点的学习路径摘要会在同页作为
   “非 L1 侧记”明确标识。L2 展示当前值、历史版本和考试来源；L3 首版只展示当前可重建投影。
3. “对话线索”只分析用户主动选择的一次普通 Chat。闲聊不落库；相关线索先进入待确认区，
   即使确认也不会改变掌握度、判题、L2 或 L3。
4. 学习路径知识点已有会话后，可点击星形 Agent 按钮整理本次学习接触。知识点 ID 固定为
   已发布大纲中的叶子节点，模型不能跨 Scope 改写。
5. Agent 摘要依赖已配置的 LLM；查看现有 L1/L2/L3、版本链、来源和图谱不需要再次调用模型。

- 自动化验收固定了外部 LLM/Embedding 结果，验证的是调用链、事务和数据库语义，不代表
  线上模型质量、延迟或成本。
- 当前可以在同一个 assessment ID 下保存不可变题集版本并多次作答；没有共享题库管理、
  内容授权工作流或大规模质量评估后台。
- 大纲文件和 URL 仅用于提取层级结构；当前辅导不会自动检索或引用导入来源。把来源接入
  后续 Chat/RAG 需要独立的权限、版本、引用和保留策略，明确延期。
- PDF/TXT/Markdown 仅通过中性 Host Turn 进入一次临时原生 Quiz 会话。ExamMem 不保存
  原文件，只在 checkpoint 保存生成题和文件名、MIME、SHA-256；临时 Host 会话随后删除。
- Browser 的待提交请求只在当前标签页保存；长期恢复依赖服务端 Practice 历史和 Resume。
- Grade Overturn 暂为 API-only；UI 提供 Dispute 和管理员 Uphold。
- Issues 是权威事实的派生视图，没有 assignment、comment、SLA 或通知 ledger。
- 插件 health 不代表 PostgreSQL 连通性。
- Saved 配置需要重启才成为 Effective；已有 Practice 始终使用 Pinned 快照。
- 持久文件库、DOCX、视频、图片、音频、笔记、PPT/PPTX 摄取、Learning Journey
  Memory、课程问答、大规模来源驱动题库和 Stage 08 优化均未实现，不能通过现有 API
  冒充支持。
