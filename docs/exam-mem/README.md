# ExamMem 技术文档

这里仅保留解释架构、运行边界和可复现实验所需的公开材料。

## 架构与产品边界

- [插件迁移报告](./plugin-migration.md)：Fork 到第一方全栈插件的边界、调用链和验收。
- [学习计划、复盘与学习档案边界](./product-boundaries.zh-CN.md)：Scope 隔离与 UI 职责。
- [Grade Review ADR](./adr/0007-grade-reviews.md)：append-only 复核事件及迁移约束。
- [延期清单](./deferred-items.md)：尚未实现的多源学习和效果评测边界。

## 运行与发布

- [中文 Runbook](./runbook.zh-CN.md)：隔离 PostgreSQL、迁移、启动、恢复和检查。
- [开源发布审计快照](./open-source-audit.zh-CN.md)：依赖、构建、权限和敏感内容门禁。

## 评测

- [评测方法](./evaluation-methodology.md)：数据契约、五种 Backend、指标和防泄漏方法。
- [Stage08 开发集失败/混合基线](../../results/stage08-dev.md)。
- [Stage09 一次性冻结测试](../../results/stage09-frozen-test.md)。

求职话术、逐 checkpoint 工作记录、完整 trace dump 和原始运行目录不属于公开技术文档。
