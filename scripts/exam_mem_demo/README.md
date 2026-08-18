# ExamMem 本地演示脚本

这组脚本用于先看迁移后的产品，不代替正式配置。它使用：

- 容器：`exammem-demo-postgres`
- 数据库：`exammem_demo`
- 端口：`127.0.0.1:55434`
- volume：`exammem-demo-postgres-data`
- 固定本地口令：`exammem-demo-only`

数据库只绑定本机回环地址，但固定口令仍然只能用于本地演示。

## 一条命令启动

```bash
cd /home/lh/DeepTutor
./scripts/exam_mem_demo/start-demo.sh
```

该命令依次执行：

1. 选择已经安装项目依赖的 Python；
2. 启动隔离的 pgvector/PostgreSQL；
3. 等待数据库健康；
4. 执行 Alembic `upgrade head`；
5. 确认 head 为 `0012_study_plan_archival`；
6. 默认以 Web 开发模式在前台启动 DeepTutor 后端和前端，不更新已跟踪的 production
   build 缓存。

如果当前 `system.json` 中的后端或前端端口已经被占用，脚本会明确退出，不会调用启动器
自动修改端口配置。

打开：<http://127.0.0.1:3782/exam-mem/practice>

只准备数据库、不启动应用：

```bash
./scripts/exam_mem_demo/start-demo.sh --prepare-only
```

显式使用 Web 开发模式（与默认行为相同）：

```bash
./scripts/exam_mem_demo/start-demo.sh --dev
```

需要同时验证 production build 时使用：

```bash
./scripts/exam_mem_demo/start-demo.sh --production
```

production 模式会更新 `web/.next-deeptutor` 构建缓存，因此更适合作为提交前构建门禁，
不是日常演示默认值。

## 状态和停止

```bash
./scripts/exam_mem_demo/status-demo.sh
./scripts/exam_mem_demo/stop-demo.sh
```

DeepTutor 本身是前台进程，请在启动终端按 `Ctrl+C` 停止。`stop-demo.sh` 只停止
PostgreSQL，并保留演示数据。

## 重新创建空演示库

这是破坏性操作，只删除明确命名的演示容器和 volume，并要求输入确认：

```bash
./scripts/exam_mem_demo/reset-demo.sh
```

## LLM 配置说明

脚本不会生成测试模型、伪造评分或写入模型凭据。当前 workspace 若没有活动 LLM：

- 可以通过单一「智能备考」入口查看五个内部工作区；
- 可以验证插件、migration、PostgreSQL 和配置装配；
- 提交真实答案前，需要在 DeepTutor 的 **Settings → Models** 配置一个可用模型。

测试完成后，按中文 Runbook 使用专用账号、强密码、正式数据库和秘密管理配置：

```text
docs/exam_mem/runbook.zh-CN.md
```
