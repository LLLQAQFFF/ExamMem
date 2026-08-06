# 阶段 01：固定 DeepTutor 底座与完成仓库审计

> 阶段性质：工程基线与事实核查。  
> 前置条件：无。当前 `ExemMem` 目录只有设计文档，不是 Git 仓库，也没有 DeepTutor 源码。  
> 退出条件：底座 Tag/Commit、运行环境、启动命令、测试基线和保留/关闭能力都有可复查证据。  
> 阶段门禁：本阶段未验收，不得进入[阶段 02](./02_ExamMem架构与评测协议.md)。

## 1. 阶段目标

本阶段不开发 ExamMem 功能，只回答五个事实问题：

1. ExamMem 究竟基于 DeepTutor 的哪个 Release、Tag 和完整 Commit SHA；
2. 固定版本在本机能否通过 CLI、Web/API 和测试入口运行；
3. DeepTutor 的 Tool、Capability、Quiz/Mastery Path、Knowledge Base 和原生 Memory 分别在哪里接线；
4. 当前依赖、配置、测试、存储和安全基线是什么；
5. 后续哪些能力保留、哪些仅通过 Feature Flag 关闭。

成功标准不是“看过 README”，而是其他人只使用本阶段交付的 Runbook，也能复现同一版本和同一基线。

## 2. 必须掌握的概念

- **Tag 不等于不可变版本**：报告中必须同时保存 Tag 和 `git rev-parse HEAD` 得到的完整 SHA。
- **可运行不等于可扩展**：除启动成功，还要找到 Chat Orchestrator、Tool Registry、Capability Registry、配置入口和 Memory 写入路径。
- **单测通过不等于主链路接线**：至少完成一次带 Trace 的真实入口调用，并验证可观察副作用。
- **基线不是承诺值**：测试数量、覆盖率、延迟和错误数只记录本次实测，不沿用第三方宣传或历史数据。
- **配置脱敏**：密钥只允许通过 DeepTutor 官方配置机制或本机环境注入，不得进入 Git、日志、截图或报告。

DeepTutor 官方当前描述了 CLI、WebSocket API、Python SDK 三个入口，以及 Tool/Capability 两层扩展模型；实际锁定版本仍以本阶段源码审计为准：

- <https://github.com/HKUDS/DeepTutor/blob/main/AGENTS.md>
- <https://docs.deeptutor.info/cli/commands/>

## 3. 范围

### 3.1 本阶段要做

- 创建 ExamMem 自有 Git 仓库或 Fork，并增加官方 DeepTutor `upstream`；
- 从稳定 Release 候选中选择一个版本，记录选择理由和未选版本的风险；
- 建立 Python、Node、包管理器、Docker、数据库和操作系统版本快照；
- 跑通最小 CLI、后端服务、必要 Web 页面和原生测试；
- 审计原生三层 Memory、Quiz/Mastery Path、问题库、知识库和注册机制；
- 形成能力清单、依赖清单、启动 Runbook、测试基线和风险清单。

### 3.2 本阶段不做

- 不修改 Memory 数据结构；
- 不删除或重写 DeepTutor 原生模块；
- 不接入 PostgreSQL、pgvector 或 ExamMem Capability；
- 不把最新 `main` 自动当作稳定底座；
- 不声称“上线”“灰度”或“效果提升”。

## 4. 技术路线

### 4.1 获取与固定底座

推荐采用“ExamMem Fork + 官方 upstream”的方式，后续所有开发均在 ExamMem 分支完成，实验期间不升级底座。

```powershell
# 命令模板；先将占位符替换为真实地址，不要原样执行
git clone <EXAMMEM_FORK_URL> ExamMem
Set-Location ExamMem
git remote add upstream https://github.com/HKUDS/DeepTutor.git
git fetch upstream --tags
git tag --list
git checkout -b exam-mem/main <SELECTED_TAG>
git rev-parse HEAD
git status --short --branch
```

版本选择规则：

1. 优先正式 Release，不直接选择每日变化的 `main`；
2. Python 版本、启动方式和关键依赖在本机可满足；
3. CLI、后端、Memory 和至少一个学习相关 Capability 可运行；
4. 原生测试不存在无法解释的大面积失败；
5. 许可证允许二次开发，并在 README 保留上游声明。

将选定 Tag 和 SHA 写入 `BASELINE.md` 或等价文件。后续升级必须新建 ADR，并重跑阶段 01、02 和 08 的基线。

### 4.2 环境与依赖审计

至少记录以下事实：

| 项目 | 审计内容 | 证据 |
| --- | --- | --- |
| 操作系统 | 名称、版本、CPU/内存 | 命令输出文件 |
| Python | 解释器版本、虚拟环境、锁文件 | `python --version`、依赖快照 |
| Node | Node/npm/pnpm 版本 | 版本命令输出 |
| Docker | Engine、Compose 版本 | 版本命令输出 |
| DeepTutor | Release、Tag、完整 SHA、分支 | Git 命令输出 |
| LLM/Embedding | Provider 名称、模型名、配置位置 | 脱敏配置摘要 |
| 测试 | 命令、通过/失败/跳过、耗时、覆盖率 | 原始日志 |

依赖快照必须来自锁文件和安装环境两侧，检查“声明依赖”和“实际安装依赖”是否一致。若需要改依赖才能启动，先记录原始失败，再单独提交最小修复。

### 4.3 启动与真实链路基线

根据锁定版本 README 回填真实命令。建议至少验证：

1. `deeptutor config show` 或等价配置检查；
2. `deeptutor run <capability> <message>` 或等价 CLI 调用；
3. FastAPI 健康检查与 WebSocket/HTTP 入口；
4. Web 页面能打开并发起一次最小交互；
5. 原生 Memory `show`、写入或 Consolidation 的最小链路；
6. Quiz 或 Mastery Path 的一个最小学习场景；
7. 全量或官方推荐测试命令。

每次运行保存：命令、开始时间、结束时间、退出码、关键日志、Trace ID、输入和脱敏输出。禁止只保存截图而丢失文本日志。

### 4.4 代码结构审计

按“入口 → 编排 → 注册 → 工具/能力 → 存储 → 输出”追踪一次调用，产出一张实际调用链图。重点回答：

- CLI、Web/API 和 SDK 是否汇合到同一 Orchestrator；
- Tool 与 Capability 的注册、发现、开关和调用协议；
- Quiz/Mastery Path 的输入输出模型和持久化位置；
- 原生 L1/L2/L3 的真实语义、文件或数据库布局、更新与 Undo 能力；
- 多用户隔离和配置目录；
- Trace、Token、延迟与错误日志如何采集；
- 哪些入口适合挂载 `exam_practice` Capability 和 Learning Memory Adapter。

此处要明确命名：

- **Native Memory**：DeepTutor 原生文件型或锁定版本实际提供的三层记忆；
- **Learning Memory**：ExamMem 后续新增的数据库型学习事件、结构化状态和学生模型。

两者不能因为都叫 L1/L2/L3 而混为同一个数据模型。

### 4.5 安全与配置审计

- 使用秘密扫描检查仓库历史和工作区，但不得在报告中复制秘密值；
- 确认 `.env`、用户设置、OAuth 凭据、数据库连接串和测试日志的忽略策略；
- 发现疑似密钥时立即停止传播，记录文件位置和处置状态，通知所有者轮换；
- 检查外部 Skill/Plugin、文件上传、工具执行和路径访问的安全边界；
- 输出只写“已确认安全/疑似风险/未查到”，不能凭空判断。

## 5. 引导式编程任务

遵循固定节奏：**阅读源码 → 预测行为 → 测试先行 → 自主操作 → AI Review → 复述原理**。

### 任务 A：追踪一个 Capability

1. 先阅读入口和注册表，画出你预测的调用路径；
2. 在不修改源码前提下，用日志或调试器验证预测；
3. 标出上下文对象、流式事件和错误传播边界；
4. 用三分钟口述“Tool 与 Capability 为什么分层”。

### 任务 B：追踪一次 Native Memory 更新

1. 先写出你认为 L1、L2、L3 的输入输出；
2. 执行一个最小写入或 Consolidation；
3. 比较运行前后文件/记录变化，并找到引用链；
4. 说明为什么 Native Memory 适合作为 Baseline，却不能直接替代结构化学习状态机。

### 任务 C：复现测试基线

1. 先预测哪些测试依赖网络、模型或外部服务；
2. 分别运行纯单测和需要外部依赖的测试；
3. 对每个失败给出“产品缺陷/环境问题/配置缺失/不稳定测试”分类；
4. AI 只审查证据和推理，不替你编造成功结果。

## 6. 阶段审计回填表

以下字段必须在实际审计时填写，当前统一标记为“未查到数据”。

| 字段 | 实测值 | 证据位置 | 状态 |
| --- | --- | --- | --- |
| Fork 地址 | 未查到数据 | 待填写 | 待审计 |
| DeepTutor Release/Tag | 未查到数据 | 待填写 | 待审计 |
| 完整 Commit SHA | 未查到数据 | 待填写 | 待审计 |
| Python/Node 版本 | 未查到数据 | 待填写 | 待审计 |
| CLI 启动命令 | 未查到数据 | 待填写 | 待审计 |
| Web/API 启动命令 | 未查到数据 | 待填写 | 待审计 |
| 测试通过/失败/跳过数 | 未查到数据 | 待填写 | 待审计 |
| 覆盖率 | 未查到数据 | 待填写 | 待审计 |
| 基线延迟与 Token | 未查到数据 | 待填写 | 待审计 |
| 已知问题 | 未查到数据 | 待填写 | 待审计 |

## 7. 交付物

- 固定版本说明与上游关系记录；
- 环境和依赖快照；
- CLI、Web/API、原生 Memory、Quiz/Mastery Path 运行 Runbook；
- 原始测试日志与基线汇总；
- 真实代码调用链和模块依赖图；
- 功能保留/关闭候选清单；
- 脱敏检查与已知风险清单；
- 阶段复盘和下一阶段输入清单。

## 8. 验收标准

| 验收项 | 目标值 | 验证方法 |
| --- | --- | --- |
| Tag 与 SHA 固定 | 100% 完整 | 两次克隆解析到同一 SHA |
| 最小 CLI 链路 | 通过 | 保存退出码、日志和输出 |
| Web/API 健康检查 | 通过 | 保存请求与响应证据 |
| 原生 Memory 链路 | 至少 1 条可追踪记录 | 比较运行前后状态 |
| 学习相关链路 | 至少 1 个最小场景 | Quiz 或 Mastery Path 运行记录 |
| 测试基线 | 命令和所有结果均记录 | 不要求虚构全绿 |
| 配置脱敏 | 0 个已知秘密进入 Git | 扫描报告和人工复核 |
| 能力清单 | 关键模块 100% 有去向 | 保留/关闭/待定及理由 |

回滚方式：本阶段不改业务逻辑；若错误选择底座，删除开发分支并从已记录的 Tag/SHA 重新创建。不得使用破坏历史的强制重置掩盖审计过程。

## 9. 提交清单与 Git 门禁

- [ ] 代码：仅允许初始化、Runbook 或启动所需的最小修复；
- [ ] 测试结果：保留原始日志，不只写结论；
- [ ] 运行命令：可复制，并说明工作目录和前置环境；
- [ ] 交付物：第 7 节全部存在；
- [ ] 已知问题：每项包含影响、证据、临时规避方式；
- [ ] 独立 Git Commit：工作区无混入后续功能。

建议 Commit Message：

```text
chore(baseline): pin DeepTutor and record repository audit
```

## 10. 面试复盘卡

你应能回答：

1. 为什么固定 Tag 仍要记录 Commit SHA？
2. 如何证明功能真的接入主链路，而不是只有单元测试？
3. 为什么保留 DeepTutor Native Memory，而不是立即替换？
4. Tool 与 Capability 的差别是什么，ExamMem 应挂在哪一层？
5. 你如何区分“未查到数据”“测试失败”和“功能不存在”？

推荐表述：

> 我先固定 DeepTutor 的不可变提交并建立 CLI、API、Memory 和测试基线，沿真实入口追踪 Tool/Capability 与持久化链路。这个阶段让我避免了“各层代码存在但主链路未接线”的工程风险，也为后续 Baseline 对比保留了可复现底座。
