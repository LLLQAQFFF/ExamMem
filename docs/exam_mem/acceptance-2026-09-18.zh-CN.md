# ExamMem 未提交修复验收（2026-09-18）

## 范围与结果

仅检查当前未提交的 ExamMem 修复和直接调用链，保留原有 diff、`artifacts/`、`papers/`，
未重做全仓分析、未修改 AGENTS.md、未提交或推送。已有离线回归不作全量重跑。

本轮数据库与受控模型下的使用流程通过。浏览器点击和真实远程模型效果尚未验收，不能将
下面的工程结果解释为完整 UI 验收或真实判题准确率。

| 验证 | 结果 |
| --- | --- |
| 六个相关 PostgreSQL 测试文件 | 19 passed，25.00s |
| contracts / adapters / workflow / product_api 局部 Python 回归 | 64 passed |
| Web `exam-mem-practice.test.ts` 专项 | 通过 |
| TypeScript `--noEmit --incremental false` | 通过 |
| 四个相关 Web 文件 ESLint | 通过 |
| 当前变更 Python 文件 Ruff check / format check | 通过 |
| `git diff --check` | 通过 |
| 临时 Uvicorn + 真正 localhost HTTP/TCP 请求 | 下列五条使用流程通过 |

PostgreSQL 测试文件：

```text
tests/exam_mem/practice/test_corrections_postgres.py
tests/exam_mem/practice/test_provider_postgres.py
tests/exam_mem/practice/test_plugin_closure_postgres.py
tests/exam_mem/practice/test_real_entries_postgres.py
tests/exam_mem/storage/test_practice_runtime_postgres.py
tests/exam_mem/storage/test_study_assessment_postgres.py
```

## 数据库目标与隔离

- 已确认本地容器 `exammem-demo-postgres`，只绑定 `127.0.0.1:55434`；未连接生产库。
- 原库 `exammem_demo` 的实际 head 是 `0014_textbook_grounding`。验收前后均为
  22 条学习事件、23 条记忆、39 条 checkpoint；未对原库执行升级。
- 新建本轮专用临时库 `exammem_accept_20260918`，真实执行从空库到
  `0015_textbook_plan_source` 的 migrations。确认 30 张 public 表、14 个 append-only
  trigger；测试结束无随机 schema 残留。
- 数据库集成使用随机 schema 或回滚事务；本地 HTTP 服务使用随机 schema、临时 SQLite
  和受控模型输出。LLM/Embedding 均未调用远端服务。
- 验收结束删除本轮临时库，并将原本停止的演示容器恢复为停止状态；原有 volume 保留。

隔离过程中发现并修复了一次测试配置遗漏：新增缓存测试未绑定临时 `DeepTutorApp`，
向开发 SQLite 写入了 8 个测试会话。已备份至
`/tmp/exammem-accept-chat-history-before-cleanup.db`，按本轮专用标识精确删除这 8 个会话及
24 个关联 turn；其余 sessions、turns、messages、turn_events 的记录摘要前后一致。
修正后重跑，开发库中本轮测试会话数量为 0。

## 实际使用流程与新增覆盖

真实本地 HTTP 服务验证：

1. 已发布的动态学习计划与 assessment 启动正式考试。
2. `/configuration` 携带动态 exam/subject Scope 返回 Pinned；错误 Scope 不返回快照。
3. 提交带首行缩进和末尾换行的答案，Review 保留原文；重复提交只评分一次。
4. 诊断 confidence `0.9` 和 `ambiguous_response` 经正式作答持久化到学习事件。
5. 计划发布新版、移除旧知识点后，旧记忆仍可凭发布历史纠错；再次提交纠错复用事件，
   目标记忆为 `invalidated`。

新增 PostgreSQL 回归还验证：未发布草稿不能成为纠错依据；跨用户纠错被拒绝；相同原文
跨考试复用评分，首行缩进变化或模型配置变化重新评分；旧 JSON 中缺失 `grader_revision`
仍可读取，但不会匹配新评分身份；user/exam/subject 任一不一致均不能命中缓存。

已有入口回归覆盖 HTTP、SDK、WebSocket、生成题持久化、历史 Review、Resume、重复提交和
记忆事务。这里的 WebSocket 使用现有入口测试工具，不等同于浏览器自动化。

本次环境没有可直接使用的 Linux Playwright 浏览器，未下载或安装浏览器依赖。
真实远程 LLM/Embedding 验收涉及外部请求和费用，未获得确认，因此没有执行。

临时使用验收脚本与日志位于 `/tmp/exammem_live_accept.py`、
`/tmp/exammem-live-result.log`；最终数据库日志位于 `/tmp/exammem-accept-final.log`。
这些是本机本次运行证据，长期可重复验证以仓库内测试为准。

## 处女原则审查

| 位置/现象 | 判断与处理 |
| --- | --- |
| workflow 改为原样答案 hash，但浏览器与后端仍 trim/strip | 本轮修复未贯通调用链。已统一前端提交、HTTP 和领域 `AnswerText`：保留有效原文，仅拒绝全空白输入；没有叠加 hash fallback。 |
| 两处 PostgreSQL 模型桩仍返回 `analyzer_version` | 旧测试契约。删除桩中的字段，由服务端写版本；没有放宽生产 Schema。首次实测 5 failed、10 passed，修正后原 15 项全部通过。 |
| 入口测试替换整个模型激活函数 | 跳过真实 ContextVar 作用域，无法证明模型配置改变会使缓存失效。改为只替换配置解析，保留生产激活/恢复逻辑。 |
| `GradeArtifactIdentity.grader_revision=None` | 有存量 JSON 兼容依据；历史可读，新跨考试缓存不命中。不是任意 fallback。 |
| `normalized_answer_hash` 名称仍保留 | 已存在的序列化字段名，当前语义为原文 hash。避免为了命名整洁修改持久化契约。 |
| `pin_completion` 与 lazy `BoundCompletion` | 对应单次工作流和共享工具不同生命周期，避免跨用户共享固定配置；不承诺整场考试冻结远程模型。 |
| 纠错服务中的固定 math1 Scope 分支 | 对应既有固定考试兼容边界，动态计划必须提供发布 taxonomy，不会落入此分支。解析职责分散可作为后续设计讨论，本轮不扩大重构。 |
| 原有推荐器捕获异常后按规则选题 | 既有、可观察的 `rule_fallback` 策略；本轮未修改，不把合法降级一概判为补丁。 |

在上述限定范围内，未发现需要推翻状态机或重写成熟模块的根本模型错误；不作“全仓没有
补丁式代码”的结论。旁路发现：`scripts/exam_mem_demo/status-demo.sh` 的预期 head 提示仍是
`0012_study_plan_archival`，与当前代码 head 不符；本轮未修改该历史脚本。

## 源码走读与面试追问

DeepTutor 上游提供 `TurnRuntimeManager`、请求级模型配置、Host completion 和会话事件流；
ExamMem 新增评分身份、确定性练习 checkpoint、正式学习事件与 PostgreSQL 记忆事务。
`BoundCompletion` 是本轮加在中性 Host 边界上的配置绑定能力，领域评分仍由 ExamMem 拥有。

```text
PracticeWorkbench 原文提交
  → PracticeAnswerBody / AnswerText
  → Host TurnRuntimeManager 激活请求模型配置
  → PracticeRuntimeProvider / BoundCompletion
  → ExamPracticeWorkflow / grade_artifact_identity
  → Grader → Diagnosis → LearningEvent.evidence_quality
  → PostgreSQL checkpoint 与记忆事务 → Review / Archive
```

适合追问：为什么字符串归一化会改变代码答案语义？为什么幂等重放与跨考试评分缓存需要
不同身份？为什么历史 JSON 能读取却不能继续命中新缓存？为什么只返回配置的测试桩无法
代替真正的请求作用域激活？为什么动态计划纠错必须读取历史发布版而不是当前草稿？
