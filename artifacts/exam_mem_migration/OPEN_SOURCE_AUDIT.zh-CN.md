# DeepTutor + ExamMem 开源发布审计

审计日期：2026-08-16
目标分支：`feat/exam-mem-plugin-migration`
状态：**尚未给出发布 Go；存在需要授权处理的 Python 测试依赖和前端安全门禁**

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

本地 `exammem` Python 3.11 环境存在既有工具链冲突：`wheel 0.47.0` 要求
`packaging>=24`，环境安装的是 `packaging==21.3`。审计使用现有 Python 3.14 构建工具配合
`--ignore-requires-python --no-build-isolation --no-deps` 只构建、不安装，成功检查 wheel
内容。发布前仍建议在干净的受支持 Python 3.11 CI 环境执行正式 build。

### 5.2 Docker

- Compose 配置在提供显式占位环境变量后可解析；
- ExamMem PostgreSQL 使用单独的 pgvector service/volume；
- 演示数据库只绑定 `127.0.0.1`；
- Dockerfile 已包含插件和 migration 资源；
- 尚需在最终依赖修复后执行完整镜像 build/smoke，当前不能用 compose parse 代替镜像验收。

### 5.3 生成型 Web 构建目录

`web/.next-deeptutor/` 在上游仓库中是已跟踪的发布资产，生产构建会产生大量 dirty
文件。审计不会把这些生成差异与业务源码一起暂存，也不会擅自还原用户/上游资产。
最终 checkpoint 必须精确暂存源码与文档，并单独说明构建缓存状态。

## 6. 测试证据

当前已经得到：

| 门禁 | 最近证据 |
| --- | --- |
| 全仓 Python + 本地 PostgreSQL | 旧兼容环境证据：`4293 passed, 9 skipped`；当前依赖解析的 `TestClient` 门禁尚未通过 |
| 当前非 TestClient Python 回归 | 排除 27 个直接使用 `TestClient` 的文件后：`3945 passed, 9 skipped`；专用临时库已删除 |
| Learning Archive 修复聚焦套件 | `26 passed`（含真实 PostgreSQL、HTTP/SDK/WebSocket） |
| 知识库删除权限/清理回归 | `3 passed`（`PytestWarning` 提升为错误） |
| Web Node tests | `65/65` |
| TypeScript | `tsc --noEmit` 通过 |
| ESLint | 0 errors；56 个既有 warnings |
| Ruff lint | 通过 |
| Python compileall | 通过 |
| i18n parity | 通过；非严格 literal audit 报告 27 个候选文件 |
| wheel 内容/迁移 CLI | 通过 |
| migration head | `0010_learning_observations` |
| Bandit 生产代码中高风险 | `0 high / 0 medium`（冻结 migrations 排除；两个有意监听已精确审计） |

这些结果需要在最终依赖调整和文档改动后全部重跑。旧兼容环境的 `4293 passed` 证明
业务代码曾完成全量回归，当前 `3945 passed` 进一步证明阻塞范围集中在 TestClient 依赖
边界；但两者都不能证明当前无上限依赖声明可复现，也不能用排除文件或聚焦套件替代最终
全仓回归和 production build。

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

当前环境没有 `detect-secrets`/`pip-audit` 可执行文件，不能把“工具未安装”写成“扫描
通过”。仓库已有 `.secrets.baseline`，最终发布最好在 CI 中恢复对应工具门禁；新增或
安装工具需要依赖授权。

## 8. 依赖门禁

### 8.1 Python 测试客户端可复现性

当前声明是 `fastapi>=0.100.0`、`pytest>=7.0.0`，没有约束 FastAPI/Starlette 上限，也没有
声明 Starlette 新版 `TestClient` 所需的 HTTP 客户端。2026-08-16 当前环境解析为：

```text
fastapi==0.141.1
starlette==1.4.1
httpx==0.28.1
httpx2=MISSING
```

在该组合下，最小 FastAPI `TestClient.get("/ping")` 和
`tests/runtime/test_plugin_manager.py` 都会挂起；Starlette 同时发出“使用 httpx 的
testclient 已弃用，请安装 httpx2”的警告。仓库有 85 处 `TestClient` 使用，因此这不是
某个 ExamMem 测试的局部问题，也不能通过跳过一个测试掩盖。

Starlette 官方发布说明显示 1.2.0 开始支持 httpx2；当前 1.4.1 的 test client 会优先使用
httpx2，缺失时才回退到已弃用的 httpx。干净修复方向是：

- 把 `httpx2` 明确加入 `pyproject.toml` 的 `dev` extra 和 `requirements/dev.txt`；
- CI Python test job 安装 `requirements/dev.txt`，不再手工拼接无上限的 pytest 依赖；
- 在受支持 Python 3.11/3.12/3.13 上重跑全部 85 处 `TestClient` 覆盖；
- 保留运行时 `httpx`，因为业务代码仍直接使用它，不能把测试依赖迁移误做成运行时大改。

新增并安装 `httpx2`、修复本地 `packaging` 工具链都属于依赖变更，按用户约束必须先获
明确授权。授权前不能把旧兼容环境的全量通过记录当作当前可复现性证明。

参考：

- [Starlette 官方发布说明](https://www.starlette.io/release-notes/)
- [HTTPX2 官方 PyPI 项目页](https://pypi.org/project/httpx2/)

### 8.2 前端生产依赖漏洞

2026-08-16 对当前 `web/package-lock.json` 执行：

```bash
npm audit --omit=dev --json
```

结果：409 个生产依赖中，`9` 个 package 级漏洞项，`5 high`、`4 moderate`、`0 critical`。

| package | 当前解析版本/来源 | 风险摘要 | 处理方向 |
| --- | --- | --- | --- |
| Next.js | `16.2.3`（direct） | middleware/proxy bypass、SSRF、DoS、信息泄露等 | 升级到 `16.3.1`，同时获得框架原生的安全 Sharp 范围，并重跑 auth/proxy/build |
| Mermaid | `11.14.0`（direct） | HTML/CSS injection、prototype pollution、DoS | 最小升级到已发布的 `11.16.1`，并测试用户生成图 |
| DOMPurify | `3.4.0`（经 Mermaid/jsPDF） | 多个 sanitizer bypass/XSS | 解析到已发布的 `3.4.13`，并验证 Mermaid/jsPDF 依赖树 |
| PostCSS | `8.4.31`/`8.5.6` | source map 任意文件读取/信息泄露 | 随 Next/lock 刷新到 `>8.5.22` |
| nanoid | `3.3.11` | 特定 size 下无限循环 | lock 刷新到 `>=3.3.18` |
| brace-expansion | `1.1.14`/`2.1.x` | 资源耗尽 DoS | lock 刷新到受修复版本 |
| sharp | `0.34.5`（经 Next） | libvips 继承漏洞 | 需要 Next 支持的 `>=0.35.0` 或上游修复 |
| ExcelJS/uuid | ExcelJS `4.4.0` → uuid `8.3.2` | uuid v3/v5/v6 buffer bounds | 当前 ExcelJS 仅调用 uuid v4，初步不可达；不能按 audit 建议倒退到 ExcelJS 3.4.0 |

Next `16.2.11` 可修复当前 Next 自身公告，但已发布的 `16.2.12` 仍声明
`sharp ^0.34.5`；强制 override 到 0.35.x 会违反框架自己的依赖范围。Next `16.3.1` 已原生
声明 `sharp ^0.35.3`，所以干净升级候选应是 16.3.1，而不是给 16.2.x 叠加兼容补丁。
升级后仍需复核依赖树、reachability 和残余风险，不能运行 `npm audit fix --force` 后直接
宣称归零。

现有 Mermaid 渲染已设置 `securityLevel: "strict"`、`htmlLabels: false`，降低用户图表内容
直接执行脚本的风险；但生成 SVG 最终仍通过 `dangerouslySetInnerHTML` 注入，所以这只是
纵深缓解，不等于修复公告。ExcelJS 源码可达性检查只发现 `uuid.v4()`，没有调用公告
涉及的 v3/v5/v6 buffer 路径；因此记录为当前不可达残余风险，不采用 audit 建议的破坏性
降级。

根据用户约束，安装/升级依赖必须先获得明确授权。授权前不修改 `package.json` 或
`package-lock.json`。这是当前发布 Go 的真实阻塞项。

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

只有以下条件全部满足才可给出 Go：

- [ ] 经授权完成前端依赖修复，并复核 `npm audit` 残余项；
- [ ] 经授权补充 `httpx2` 测试依赖并修复 Python 3.11 打包工具链；
- [ ] 在干净 Python 3.11 环境完成 wheel build/install/migration smoke；
- [ ] Docker image build 与无插件/有插件两种启动 smoke 通过；
- [ ] DeepTutor native 全量测试、ExamMem 全量 PostgreSQL 测试通过；
- [ ] Ruff lint/format、TypeScript、ESLint、Node tests、i18n、production build 通过；
- [ ] migrations `0001`～`0006` hash、唯一 head、源仓库只读状态通过；
- [ ] Core direct-import、secret、diff、敏感数据库副作用和延期边界门禁通过；
- [ ] 最终 checkpoint 精确提交，不含 `.next-deeptutor` 临时构建差异；
- [ ] 不自动 push、release 或 deploy。

在清单完成前，本仓库可以继续本地开发和演示，但不能在审计报告中声称“已通过全部
开源发布安全门禁”。
