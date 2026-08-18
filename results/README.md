# ExamMem 可复现实验结果

本目录只保留足以核对公开结论的最终报告、复现命令和机器可读摘要。

| 阶段 | 目的 | 报告 | 复现 | 摘要 |
| --- | --- | --- | --- | --- |
| Stage08 | 公开 dev 上的失败/混合 baseline，记录污染和完成率问题 | [报告](./stage08-dev.md) | [Runbook](./stage08-runbook.zh-CN.md) | [JSON](./stage08-dev-summary.json) |
| Stage09 | 只用 dev 修正后的一次性 80-case frozen test | [报告](./stage09-frozen-test.md) | [Runbook](./stage09-runbook.zh-CN.md) | [JSON](./stage09-frozen-test-summary.json) |

冻结 benchmark、manifest 和 split 位于 [`evaluation/datasets/`](../evaluation/datasets/)。
原始 trace、snapshot、Native Memory 输出和完整 run 目录默认写入
`artifacts/stage08/runs/`，受 `.gitignore` 保护，只在本地审计，不提交 GitHub。

Stage09 的 95.73% 是结构化 LearningEvent 之后的 Lifecycle operation accuracy；它不是
整个产品、出题、判题、聊天抽取或真实学习增益准确率。
