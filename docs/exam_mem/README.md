# ExamMem 技术文档

这里仅保留解释架构、运行边界和可复现实验所需的公开材料。

## 架构与产品边界

- [DeepTutor × ExamMem 技术全景](./TECHNICAL_OVERVIEW.zh-CN.md)：框架、Memory、教材链路、关键算法、当前默认检索策略、评估结果与改进方向的精简总览。
- [系统架构](./architecture.md)：Core、Host Hook、插件与领域存储的所有权和调用链。
- [可靠性设计](./reliability.md)：生命周期不变量、失败分类、恢复和验证证据。
- [插件迁移报告](./plugin-migration.md)：Fork 到第一方全栈插件的边界、调用链和验收。
- [学习计划、复盘与学习档案边界](./product-boundaries.zh-CN.md)：Scope 隔离与 UI 职责。
- [教材库与基于教材学习需求](./textbook-grounded-learning-requirements.zh-CN.md)：结构化摄取、教材绑定、RAG 教学与多源冲突治理。
- [Grade Review ADR](./adr/0007-grade-reviews.md)：append-only 复核事件及迁移约束。
- [延期清单](./deferred-items.md)：尚未实现的多源学习和效果评测边界。

## 运行与发布

- [中文 Runbook](./runbook.zh-CN.md)：隔离 PostgreSQL、迁移、启动、恢复和检查。
- [开源发布审计快照](./open-source-audit.zh-CN.md)：依赖、构建、权限和敏感内容门禁。

## 评测

- [评测方法](./evaluation/methodology.md)：数据契约、五种 Backend、指标和防泄漏方法。
- [Stage08 开发集失败/混合基线](./evaluation/stage08-dev.md)。
- [Stage09 一次性冻结测试](./evaluation/stage09-frozen-test.md)。
- [Memory 语义检索 v2 修复方案与最终结果](./evaluation/semantic-retrieval-v2-remediation.zh-CN.md)：故障阶段、修复调用链、Top-1 相对分差策略和冻结集验收结果。
- [ExamMem 面试深读](./INTERVIEW_DEEP_DIVE.zh-CN.md)：按调用链解释 Memory、教材 RAG、评估指标和已知限制。

求职话术、逐 checkpoint 工作记录、完整 trace dump 和原始运行目录不属于公开技术文档。
