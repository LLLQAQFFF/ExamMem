# ExamMem

ExamMem 是构建在 [DeepTutor](https://github.com/HKUDS/DeepTutor) 之上的垂类智能备考产品。
它将学习大纲、知识点辅导、版本化练习、判题诊断和长期学习记忆连接成可恢复、可审计的闭环。

## 当前能力

- 从 PDF、TXT、Markdown 或公开 URL 导入考试大纲，解析为“学习计划 → 科目 → 章节 → 知识点”。
- 从知识点进入 DeepTutor 对话学习，并保持学习计划和知识点上下文。
- 按已发布考试范围生成练习，支持同一考试的多个版本和多次作答。
- 完成 Grade → Diagnosis → Learning Memory → Recommendation → Recovery/Correction 闭环。
- 查看考试复盘、L1/L2/L3 学习档案、记忆证据、版本链、问题与配置。
- Browser、HTTP API 和 Python SDK 共用同一套插件能力与 PostgreSQL 领域存储。

## 与 DeepTutor 的关系

DeepTutor 提供通用聊天、模型配置、Agent 运行时和三层记忆交互框架；ExamMem 通过中性的
Plugin API 装配，不让 DeepTutor Core 直接依赖 `exam_mem`。

ExamMem 使用独立 PostgreSQL，不直接读写 DeepTutor 内部数据库或 Native Memory。
Practice 与通用 Chat、Learning Memory 与 Native Memory 保持清晰边界。

## 快速体验

前提：Docker 服务已启动，项目依赖已按 DeepTutor 开发环境安装。

```bash
cd /home/lh/DeepTutor
./scripts/exam_mem_demo/start-demo.sh --dev
```

脚本会启动隔离的本地演示 PostgreSQL、执行 migration，并以前台方式启动后端和前端。
随后打开终端显示的地址，默认入口为：

```text
http://127.0.0.1:3782/exam-mem/practice
```

停止应用请按 `Ctrl+C`。管理演示环境：

```bash
./scripts/exam_mem_demo/status-demo.sh
./scripts/exam_mem_demo/stop-demo.sh
```

首次进行真实出题或判题前，请在 DeepTutor 的“设置 → 模型”中配置可用模型。
演示脚本中的固定数据库口令仅用于本机测试，不应复用于正式环境。

## 产品入口

| 路径 | 功能 |
| --- | --- |
| `/exam-mem/learning` | 导入、确认并按知识点学习考试大纲 |
| `/exam-mem/practice` | 生成练习、版本化考试、作答与恢复 |
| `/exam-mem/review` | 成绩、诊断、证据和考试复盘 |
| `/exam-mem/memories` | L1/L2/L3 学习档案、版本与纠错 |
| `/exam-mem/configuration` | ExamMem 配置及生效状态 |

## 核心约束

- Taxonomy、`slot_key` 和四维 Scope 决定知识点与记忆归属。
- L1 append-only；L2 保留 CAS、provenance 和事务语义；L3 可重建。
- Lifecycle、Decision Journal、Change Log、补偿、Trace 和幂等语义均保留。
- migrations `0001`～`0006` 是冻结历史，不修改内容、revision、顺序或语义。
- 当前范围不包含文件/视频/图片/笔记/PPT 多源摄取、Learning Journey Memory 或课程问答。

## 文档

- [中文运行手册](artifacts/exam_mem_migration/RUNBOOK.zh-CN.md)
- [迁移报告](artifacts/exam_mem_migration/MIGRATION_REPORT.md)
- [Checkpoint 与验收记录](artifacts/exam_mem_migration/CHECKPOINTS.md)
- [延期清单](artifacts/exam_mem_migration/DEFERRED_ITEMS.md)
- [面试项目讲解指南](artifacts/exam_mem_migration/INTERVIEW_GUIDE.zh-CN.md)

## 开源说明

本项目保留 DeepTutor 上游的版权和许可证声明，详见 [LICENSE](LICENSE)。ExamMem 领域实现及
DeepTutor 集成改动位于本仓库当前开发分支；向上游同步时应继续保持插件边界和独立存储约束。
