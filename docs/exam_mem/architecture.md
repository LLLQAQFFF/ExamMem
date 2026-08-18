# ExamMem 系统架构

ExamMem 是 DeepTutor 的第一方全栈插件，不是 DeepTutor Core 内的考试特判。仓库按所有权分为四层：

| 层 | 目录 | 职责 |
| --- | --- | --- |
| DeepTutor Host | `deeptutor/`、`web/` | 提供中性的插件发现、能力调用、路由和 UI 宿主 |
| ExamMem 集成 | `deeptutor_plugins/exam_mem/` | 将 Host Hook 装配为 ExamMem API、页面和运行时能力 |
| ExamMem 领域 | `exam_mem/` | Taxonomy、练习、评分、Lifecycle、推荐和 PostgreSQL Repository |
| 独立评测 | `evaluation/` | 数据契约、五种 Backend、runner、指标与冻结测试协议 |

DeepTutor Core 不直接导入 `exam_mem`。插件未启用时，原生测试和构建仍可独立工作；插件启用后，由 Host 的中性能力协议连接 ExamMem。

## 真实调用链

```text
Browser / HTTP / SDK
  → DeepTutor Plugin Host
  → exam_practice capability
  → ExamMem practice workflow
  → question generation / grading / diagnosis
  → Learning Memory lifecycle
  → recommendation / checkpoint / trace
  → ExamMem PostgreSQL
```

ExamMem PostgreSQL 是考试学习事实的唯一业务真相源，不读写 DeepTutor 内部数据库或 Native Memory。DeepTutor 原生聊天记忆与 ExamMem Learning Memory 在产品语义和存储上保持分离。

学习会话通过一条只读、显式绑定的中性链路使用这些事实：

```text
ExamMem PostgreSQL L1/L2/L3
  → ExamMem session-context contributor
  → DeepTutor neutral Context Hook
  → linked Mastery Path / Chat prompt
```

正式作答和 Lifecycle Memory 是强证据；已确认的学习路径记录只用于延续讲解，是弱证据，
不能直接提升掌握度。普通聊天没有显式绑定，不会自动读取 ExamMem 学习记忆。

## 核心数据边界

- 学习计划固定一版 Taxonomy；模块和知识点形成层级化考试范围。
- `slot_key` 与四维 Scope 标识事实归属，防止专业、计划、版本或知识点串线。
- 同一 assessment 可以有多个不可变版本；每次 attempt 归属明确版本。
- L1 保存事件证据，L2 保存带 provenance 的当前学习状态，L3 是可重建的跨范围综合。
- 学习画像和复习队列只按所选计划、科目与 Taxonomy 读取 L1/L2/L3，是无独立存储的
  可重建视图；归档、失效和争议状态按 Lifecycle 语义处理。
- Practice、Review、Issues、Configuration 分别承担作答闭环、复盘、纠错和配置职责。

迁移过程和 Host/Plugin 责任矩阵见[插件迁移报告](./plugin-migration.md)，产品页面边界见[学习计划、复盘与学习档案边界](./product-boundaries.zh-CN.md)。
