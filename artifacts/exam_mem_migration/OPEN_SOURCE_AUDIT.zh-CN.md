# DeepTutor + ExamMem 开源发布审计

审计日期：2026-08-17
目标分支：`feat/exam-mem-plugin-migration`
状态：**本地发布候选门禁通过；未推送、发布或部署**

本文记录的是当前工作树证据，不等于对未来依赖、模型提供商或部署环境的永久保证。

## 1. 审计目标

发布前必须同时证明：

- 不启用 ExamMem 时，DeepTutor 原生功能、测试和生产构建不退化；
- ExamMem 只通过中性插件 API 装配，Core 不直接依赖 `exam_mem`；
- 当前大纲、学习、Practice、Grade、Learning Memory、Recommendation、Recovery、Correction、Review、Issues、Configuration 闭环可运行；
- Browser、HTTP、Python SDK、WebSocket 和 PostgreSQL 使用真实入口；
- migrations `0001`～`0006` 与冻结基线一致，唯一 head 正确；
- 安装包、Docker、CI、Runbook 和演示脚本包含运行所需文件；
- 没有凭据、生产数据、未授权数据集或阶段 08/多源摄取功能混入；
- 已知依赖漏洞、限制和未证明的模型效果不被隐藏。

## 2. 基线和所有权

| 项 | 当前证据 |
| --- | --- |
| DeepTutor 迁移基线 | `9228d10abc114ec87321c6861e7e384db022e8ce` |
| 冻结 ExamMem 可执行基线 | `747958725b6e681a3a846a0430b5a21deb163188` |
| 源仓只读 checkout | `c8512ffff5834198b009833e0228543df69b25cb`；仅比可执行基线多 3 份治理/ADR 文档，无源码差异，工作树 clean |
| 目标分支 | `feat/exam-mem-plugin-migration` |
| 源仓库使用方式 | 只读；迁移校验必须引用显式冻结 commit，不能把当前 checkout HEAD 当基线 |
| 许可证 | 根仓库 Apache-2.0；保留 `THIRD_PARTY_NOTICES.md` 和 `CITATION.cff` |

代码所有权边界：

- `exam_mem/`：Taxonomy、Scope、Memory、Lifecycle、Practice、Storage 等领域实现；
- `deeptutor_plugins/exam_mem/`：第一方插件装配、API 与 Host adapter；
- `deeptutor/`：仅包含与业务无关的 Plugin/Host contracts；
- `web/components/exam-mem/` 和对应 `web/lib/`：ExamMem 产品 UI；
- DeepTutor 原生 Chat、Quiz、Mastery Path、Native Memory 仍属于 Host 能力。

## 3. 已发现并修复的问题

### 3.1 Learning Archive 只有 L1、没有 L2/L3

根因：不可变题目 catalog 已固化 canonical `knowledge_point_ids`，作答时却再次调用 LLM
做语义映射；模型返回 `unknown` 后仍允许写 L1，L2/L3 无法形成。

修复：

- 正常运行使用 `CatalogKnowledgeMapper`，只校验已固化 ID 是否存在、active、leaf、unique；
- `unknown` 在任何 Memory 写入前按 `knowledge_point_contract_violation` fail closed；
- mapping Trace 的 `llm_calls` 设为 0；
- UI 默认“全部章节”，只有用户主动选择章节时才发送叶子 ID 过滤。

本地演示库只读盘点确认旧数据为 4 条 `unknown` L1、0 条 L2、0 条 L3；修复没有
直接篡改 append-only 历史。真实 PostgreSQL/入口回归为 `26 passed`。

### 3.2 开源安装缺少 ExamMem 运行依赖和文件

修复：

- `requirements/server.txt` 补 SQLAlchemy、Alembic、asyncpg、pgvector；
- Dockerfile 复制 `exam_mem/`、`deeptutor_plugins/` 和迁移配置；
- wheel 包含 ExamMem、插件、10 个 migration 和包内 `alembic.ini`；
- 增加 `python -m exam_mem.storage.migrations ...`，wheel 安装无需仓库根配置；
- 插件 manifest 的 migration path 指向包内资源。

### 3.3 公共 CI 会静默跳过 PostgreSQL 集成测试

修复：

- CI paths 监听 ExamMem、插件、migration 和 compose 文件；
- Python job 启动 pgvector PostgreSQL service；
- 使用仅供 CI 的数据库凭据与 DSN；
- 测试前执行 `upgrade head`；
- import gate 同时检查 ExamMem domain 和 plugin assembly。

### 3.4 开源入口和安全披露缺失

修复：

- 英文/中文 README 增加 Smart Exam Prep 能力、边界和一键演示入口；
- 中英文 Runbook 同时覆盖源码与 wheel migration 命令；
- 新增 `SECURITY.md`，提供私密报告方式和 ExamMem secret/database 边界；
- 保留明确的延期清单，不把多源摄取或模型评测描述为已完成。

### 3.5 TeX 外部归档可路径穿越

根因：`TexDownloader` 对 arXiv TAR 使用 `os.path.commonprefix` 判断路径，不能阻止同前缀
目录和链接逃逸；ZIP 分支直接调用 `extractall`，没有路径、类型或解压体积约束。显式传入
的 `arxiv_id` 也会进入文件名，缺少格式校验。

修复：

- TAR 使用 Python 官方 `data` extraction filter，并拒绝链接、设备和超限成员；
- ZIP 逐成员验证相对路径、文件类型、加密标志、大小和压缩比，再限额流式写入；
- arXiv ID 只接受当前数字 ID/版本格式；
- 成功、业务失败和异常路径统一清理临时目录；
- 8 个安全回归覆盖正常嵌套目录、TAR/ZIP 穿越、链接、ID 注入和失败清理。

### 3.6 本地无认证启动默认暴露所有网卡

根因：本地 `deeptutor start`、`deeptutor serve` 和直接 API 启动默认监听 `0.0.0.0`；而
单用户默认关闭认证，此时远端请求会进入本地管理员语义。

修复：本地源码/Python 包启动默认只监听 `127.0.0.1`。需要可信局域网访问时，操作者必须
先开启认证，再显式设置 `DEEPTUTOR_BIND_HOST=0.0.0.0` 或传 `--host 0.0.0.0`。Docker
继续使用已有 `BACKEND_HOST`/`FRONTEND_HOST`，MSTeams webhook 和隔离 sandbox runner
也保留其必需的内部外部监听契约。启动与 TeX 安全聚焦回归为 `32 passed`。

### 3.7 知识库删除重试会破坏目录权限

根因：知识库删除失败后的重试路径调用 `chmod(path, stat.S_IWRITE)`。在 Unix 上这不是
“追加可写权限”，而是把原权限替换成 owner-write-only；目录因此失去 read/execute，既
无法继续删除，也会留下 Pytest 无法回收的孤儿临时目录。

修复：读取当前 mode 后只追加 owner-write；若目标是目录，同时追加 owner-execute，再
执行原删除回调。故障注入测试现在同时验证首次失败、重试失败、配置清理和残留目录回收，
并在 `PytestWarning` 提升为错误时得到 `3 passed`。该修改不删除任何现有用户知识库，也
不改变“删除失败时优先清除孤儿配置”的既有产品契约。

## 4. 数据库审计

已确认的本地演示目标：

```text
container: exammem-demo-postgres
host:      127.0.0.1:55434
database:  exammem_demo
head:      0010_learning_observations
```

这只是本机演示库，不是生产或共享数据库。集成测试使用随机隔离 schema 并清理；不得
把自动化测试指向未知数据库。

不变量：

- migrations `0001`～`0006` 内容、revision、顺序和语义不可修改；
- 当前唯一 head 为 `0010_learning_observations`；
- L1、Lifecycle Decision、Change Log、Trace、Review、Observation/Action 等审计流
  受 append-only trigger 保护；
- L2 保留 CAS、provenance 和事务语义；
- L3 可重建，不是真值；
- ExamMem repository 不读写 DeepTutor SQLite/PocketBase/Native Memory 数据库。

## 5. 构建与分发审计

### 5.1 wheel

当前 wheel 检查确认：

- 包含 ExamMem domain、first-party plugin、包内 Alembic 配置和 10 个 migration；
- metadata 声明 Python `>=3.11,<3.14`；
- metadata 包含 SQLAlchemy、Alembic、asyncpg、pgvector；
- `python -m exam_mem.storage.migrations heads` 返回唯一 head。

最终发布流在受支持的 Python 3.11 环境完成正式 build，并在隔离目录安装 wheel 后验证
Python import、`deeptutor --help` 和包内 migration 资源。结果如下：

- wheel：3909 个文件，35,482,477 bytes，325 个 Next static assets，0 个 `.pyc`/`__pycache__`；
- sdist：4754 个文件，33,210,057 bytes，0 个 `.pyc`/`__pycache__`；
- wheel 同时包含 Web standalone server、ExamMem 插件及 migrations `0001`、`0006`、`0010`；
- PyPI workflow 增加等价的 wheel 内容断言，防止源码可运行但发布包漏文件。

### 5.2 Docker

- Compose 配置在提供显式占位环境变量后可解析；
- ExamMem PostgreSQL 使用单独的 pgvector service/volume；
- 演示数据库只绑定 `127.0.0.1`；
- Dockerfile 已包含插件和 migration 资源；
- `production` target 从干净基础层构建成功，Next 16.3.1 生成 63 个路由；
- 临时容器达到 Docker `healthy`，容器内 backend `/` 与 frontend `/` 均返回 200；
- 镜像内 Web standalone/static、ExamMem plugin 和 migrations `0001`/`0006`/`0010` 通过资源断言；
- 临时容器无宿主目录挂载、无 ExamMem DSN，验证后已自动删除；只保留本地审计镜像缓存。

首次 build 曾因 Docker 容器访问 Debian 镜像站中途断连失败；复用已成功层重试后通过。
这是外部下载瞬时故障，不通过修改代码或放宽门禁掩盖。

### 5.3 生成型 Web 构建目录

`web/.next-deeptutor/` 在上游仓库中是已跟踪的发布资产，生产构建会产生大量 dirty
文件。审计不会把这些生成差异与业务源码一起暂存，也不会擅自还原用户/上游资产。
最终 checkpoint 必须精确暂存源码与文档，并单独说明构建缓存状态。

## 6. 测试证据

当前已经得到：

| 门禁 | 最近证据 |
| --- | --- |
| DeepTutor 原生（无 ExamMem DSN） | `3856 passed, 9 skipped, 4 warnings`；排除 `tests/exam_mem` |
| 全仓 Python + 隔离 PostgreSQL | `4312 passed, 9 skipped, 9 warnings`；无 TestClient 排除 |
| TestClient 代表性入口 | `104 passed`；FastAPI/Starlette/httpx2 组合通过 |
| Learning Archive 修复聚焦套件 | `26 passed`（含真实 PostgreSQL、HTTP/SDK/WebSocket） |
| 知识库删除权限/清理回归 | `3 passed`（`PytestWarning` 提升为错误） |
| Web Node tests | `412 passed` |
| TypeScript | `tsc --noEmit` 通过 |
| ESLint | 0 errors；58 个既有 warnings |
| Ruff lint/format | 通过；1346 个文件格式一致 |
| Python compileall | 通过 |
| i18n parity | 通过；非严格 literal audit 报告 27 个候选文件 |
| Web production build | 通过；63 个路由 |
| wheel/sdist 内容与隔离安装 | 通过；无 bytecode 污染 |
| Docker production build/smoke | 通过；容器 `healthy`，前后端 200 |
| migration head | `0010_learning_observations` |
| frozen migrations | `13 passed`；`0001`～`0006` hash/chain 不变 |
| Bandit | 0 issues（仅精确排除不可修改的 `0004`～`0006`） |
| detect-secrets | 通过 |
| pip check | 通过 |
| npm audit high gate | 通过；保留 2 个已审计 moderate |

全量 Python 回归使用空白隔离数据库 `exammem_acceptance_20260817_01`：先从 `0001` 升到
唯一 head `0010_learning_observations`，测试后所有业务表为 0 行、仅保留 Alembic version，
随后精确删除该临时数据库。现有 `exammem_demo` 保持 head `0010` 和原有 4 条
`learning_events`，未被测试修改。

## 7. 静态安全和敏感内容

### 7.1 Python/ExamMem

现有 Bandit 生产范围复核已修复 TeX 归档穿越和本地默认全网卡暴露；重新扫描为
`0 high / 0 medium`。没有发现 ExamMem 动态 `eval/exec`、`shell=True`、不安全
pickle/YAML 或 TLS 绕过。冻结 migration 范围的报告项主要是：

- 领域内部状态断言；
- 冻结 migrations `0004`～`0006` 中由固定表名构造的 trigger SQL。

冻结 migration 不能为了静态数字好看而改写。断言来自严格 Pydantic/状态机前置契约，
目前没有发现外部输入可绕过后造成越权写入；后续可单独评估 `python -O` 下将断言改成
显式 invariant error，但本轮不借安全审计重构成熟流程。

DeepTutor 上游存在显式可配置的 `verify=False` 路径和沙箱 runner 的 `shell=True`
合同。它们不是 ExamMem 新增代码；公开部署必须保持 TLS 校验，命令执行必须位于既有
沙箱边界内。最终报告需把配置风险与代码漏洞分开。

### 7.2 凭据

高置信 token/私钥模式扫描没有命中。通用凭据赋值模式只命中变量传递、空值/占位符，
以及演示与 CI 中的隔离 PostgreSQL 假口令；没有发现被跟踪的真实 `.env`、私钥、数据库
dump 或 API key。演示口令 `exammem-demo-only` 明确只用于回环地址本地库。

`detect-secrets` 按仓库 baseline 执行通过。`pip-audit` 除
`ecdsa 0.19.2 / PYSEC-2026-1325` 外通过；该公告当前无修复版本，依赖来自
`python-jose`。项目认证实现只使用 HS256，Teams RS256 使用 PyJWT，没有调用公告影响的
ECDSA 路径。CI 以漏洞编号做精确例外，不能扩展成忽略整个包或所有审计失败。

## 8. 依赖门禁

### 8.1 Python 测试客户端可复现性

升级授权后，开发依赖显式声明 `httpx2>=2.0.0,<3.0.0`，pytest 收敛到
`>=9.0.3,<10`；CI Python job 安装完整 `requirements/dev.txt`，不再手工拼出一套与本地
不同的测试客户端组合。2026-08-17 验收环境解析为：

```text
fastapi==0.141.1
starlette==1.4.1
httpx==0.28.1
httpx2==2.10.0
```

最小 `TestClient` 与 104 个代表性入口在普通宿主进程通过。相同组合在受限命令沙箱内仍
会卡在线程/IPC 边界；将同一测试移出沙箱立即返回 200，因此这是执行沙箱限制，不是
FastAPI/Starlette/httpx2 不兼容。最终 4312 项全量测试在允许正常线程 IPC 的隔离执行环境
完成，没有跳过或排除 TestClient 文件。运行时 `httpx` 继续保留，因为业务代码仍直接
使用它；测试客户端升级没有被扩大成运行时 HTTP 栈重写。

参考：

- [Starlette 官方发布说明](https://www.starlette.io/release-notes/)
- [HTTPX2 官方 PyPI 项目页](https://pypi.org/project/httpx2/)

### 8.2 前端生产依赖漏洞

2026-08-17 完成 Next、Mermaid、PostCSS 与 lock 的干净升级后执行：

```bash
npm audit --omit=dev --json
```

结果：`0 critical`、`0 high`、`2 moderate`。CI 使用 `npm audit --audit-level=high` 阻止
critical/high 回归。

| package | 当前解析版本/来源 | 风险摘要 | 处理方向 |
| --- | --- | --- | --- |
| Next.js | `16.3.1` | 已升级并获得 Sharp `0.35.3`；auth/proxy/412 Node tests/build 通过 |
| Mermaid | `11.16.1` | 已升级；strict security 和 `htmlLabels: false` 保持 |
| DOMPurify | `3.4.13` | 经 lock 刷新升级 |
| PostCSS | `8.5.26` | 已升级 |
| nanoid | `3.3.18` | 已升级 |
| brace-expansion | `1.1.18` / `2.1.4` | 已升级 |
| ExcelJS/uuid | ExcelJS `4.4.0` → uuid `8.3.2` | 剩余 2 个 moderate；项目依赖路径仅调用 uuid v4，公告的 v3/v5/v6 buffer 路径不可达 |

没有使用 `npm audit fix --force`。其建议会把 ExcelJS 破坏性降级到 3.4.0，既不能证明
uuid 公告路径在本项目可达，也会引入无关兼容风险，因此保留上述可达性审计和 high gate，
不虚构“漏洞归零”。

现有 Mermaid 渲染已设置 `securityLevel: "strict"`、`htmlLabels: false`，降低用户图表内容
直接执行脚本的风险；但生成 SVG 最终仍通过 `dangerouslySetInnerHTML` 注入，所以这只是
纵深缓解，不等于修复公告。ExcelJS 源码可达性检查只发现 `uuid.v4()`，没有调用公告
涉及的 v3/v5/v6 buffer 路径；因此记录为当前不可达残余风险，不采用 audit 建议的破坏性
降级。

参考：

- [Next.js 官方 Security Advisories](https://github.com/vercel/next.js/security/advisories)
- [Mermaid Security Advisories](https://github.com/mermaid-js/mermaid/security)
- [DOMPurify Security Advisories](https://github.com/cure53/DOMPurify/security)
- [Next 16.2.11 仍解析 Sharp 0.34.5 的上游 issue](https://github.com/vercel/next.js/issues/96064)

## 9. 模型效果和数据集

自动化测试使用确定性 fake LLM，因此只证明工程闭环。当前仓库没有可用于声称考研
出题/判题准确率的金标集，也没有在线真实用户实验。

发布文档必须继续说明：

- 公共 C-Eval、EdNet 等数据带非商业许可，未被打包进仓库；
- 多源学习、课程问答和 Stage 08 评测没有提前实现；
- 模型效果评测方案见 `INTERVIEW_GUIDE.zh-CN.md`，它是下一阶段计划，不是当前结果。

## 10. 发布 Go/No-Go 清单

本地发布候选清单：

- [x] 经授权完成前端依赖修复，并复核 `npm audit` 残余项；
- [x] 补充 `httpx2` 测试依赖并统一 Python CI 安装路径；
- [x] 在 Python 3.11 环境完成 wheel/sdist build、隔离 install 和 migration 内容 smoke；
- [x] Docker production image build 与无 DSN 启动 smoke 通过；
- [x] DeepTutor native 与 ExamMem PostgreSQL 全仓测试通过；
- [x] Ruff lint/format、TypeScript、ESLint、Node tests、i18n、production build 通过；
- [x] migrations `0001`～`0006` hash、唯一 head 通过；
- [x] Bandit、detect-secrets、pip-audit/npm-audit 分级门禁通过并记录精确例外；
- [x] 源仓库只读、Core direct-import、diff 与敏感内容门禁通过；
- [x] 最终 checkpoint 精确提交，不含 `.next-deeptutor` 生成差异；
- [x] 未 push、release 或 deploy。

这里的 Go 只表示“本地发布候选工程门禁通过”，不授权远程推送、PyPI/GitHub Release、
部署或迁移任何真实数据库。
