# 阶段 01 当前进度

更新日期：2026-08-06

## 1. 已确认的底座

- ExamMem 仓库：`https://github.com/LLLQAQFFF/ExamMem.git`
- DeepTutor upstream：`https://github.com/HKUDS/DeepTutor.git`
- 固定 Tag：`v1.5.9`
- 固定底座 Commit：`37c3db6df7e886aee4f61c97ec5e618b8ab379e8`
- 当前开发分支：`exam-mem/main`
- 当前 HEAD：`fe51c0b8c47ad0626e2125deacbb68311aa29fa6`

说明：当前 HEAD 包含底座之后的项目文档提交，因此不等于底座 Commit；两者都需要保留。

## 2. 已建立的本机环境

- Conda 环境：`exammem`
- Python：3.11 系列，已执行源码开发安装 `python -m pip install -e ".[dev]"`
- Node：`v22.14.0`
- npm：`11.12.1`
- Docker：`28.4.0`
- Docker Compose：`v2.39.4-desktop.1`
- Web 依赖已安装，Next.js：`v16.2.3`

已处理的环境问题：

- GitHub 访问通过本机代理 `127.0.0.1:10808` 和 Git OpenSSL 后端完成。
- Conda 下载通过会话级 `HTTP_PROXY`/`HTTPS_PROXY` 完成。
- npm 全局缓存无写权限，改用项目外可写缓存目录完成安装。
- 测试所需的 `data/user/settings/main.yaml` 最小配置已创建。
- 测试收集缺少的 Partner 依赖已补装。

通用排障经验记录在仓库根目录的 `TROUBLESHOOTING.md`。

## 3. Windows 测试审计

### 3.1 首次收集

首次收集得到 3 个错误：缺少 `main.yaml`、缺少 Telegram 依赖、Windows 不提供 POSIX `resource` 模块。

修复配置和依赖后，排除直接依赖 `resource` 的测试文件，Windows 测试收集无错误。

### 3.2 首次完整执行

结果：

```text
10 failed, 2239 passed, 10 skipped, 1333 errors
```

其中 1333 个 error 的共同原因是 pytest 默认临时目录无权限，并不代表 1333 个产品缺陷。

### 3.3 修正临时目录和编码后重试

使用 UTF-8 模式和独立 `--basetemp` 后，结果为：

```text
24 failed, 1316 passed, 3 skipped, 356 deselected
```

剩余 24 项已分类为 Windows/POSIX 平台差异，包括路径语义、虚拟环境 `Scripts/bin` 布局、POSIX 权限、NTFS 文件名规则、POSIX 命令或模块，以及 Windows SQLite 文件锁。

这 24 项保存在 `windows-deselected-tests.txt`，必须在 Linux 中重新执行，不能视为已经通过。

原始日志：

- `logs/pytest-collect.txt`
- `logs/pytest-collect-windows.txt`
- `logs/pytest-windows.txt`
- `logs/pytest-windows-retry1.txt`

尚未执行一次从头开始、排除上述 24 项的 Windows 兼容完整基线。由于后续将切换到 WSL，可以保留为可选的补充证据，不作为 Linux 完整基线的替代品。

## 4. 阶段 01 尚未完成

- 在 WSL/Linux 中重建 Python、Node 和依赖环境快照。
- 在 Linux 中运行不排除 24 项的官方完整测试，记录通过、失败、跳过和耗时。
- 配置 MiniMax Provider，并保存脱敏配置摘要。
- 验证最小 CLI 真实调用，保存输入、输出、退出码、Trace、Token 和延迟。
- 启动后端与 Web，验证健康检查、页面和一次最小交互。
- 验证一次 Native Memory 写入或 Consolidation，并比较前后状态。
- 验证一次 Quiz 或 Mastery Path 最小场景。
- 审计入口、Orchestrator、Tool/Capability Registry、Memory 和存储调用链。
- 形成能力保留/关闭清单、依赖快照、Runbook、风险清单和脱敏扫描结果。
- 回填阶段 01 审计表并完成独立基线提交。

## 5. WSL 迁移原则

- 推荐将仓库克隆到 WSL 的 Linux 文件系统，例如 `/home/lh/code/ExamMem`。
- 不建议把 `/mnt/d/intern/goup/ExemMem` 作为长期运行目录；它仍受 Windows 文件系统语义影响。
- 迁移前先保存当前未跟踪的 `TROUBLESHOOTING.md` 和 `artifacts/`。
- WSL/Linux 的完整测试结果是后续开发的主要基线；Windows 结果作为平台审计补充。
