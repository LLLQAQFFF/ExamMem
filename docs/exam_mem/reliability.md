# ExamMem 可靠性设计

ExamMem 的可靠性来自显式契约和可审计状态迁移，而不是对模型输出做无限 fallback。

## 冻结不变量

| 不变量 | 约束 |
| --- | --- |
| Taxonomy 与 Scope | 练习、事实和推荐只能引用当前已发布大纲中的知识点 |
| L1 | 学习事件 append-only，保留原始证据和追踪标识 |
| L2 | 使用 CAS、事务和 provenance 更新，冲突不能静默覆盖 |
| L3 | 只由 L1/L2 重建，不成为不可追溯的独立真相源 |
| Lifecycle | 状态变化遵守固定策略，Decision Journal 与 Change Log 可审计 |
| 幂等与恢复 | checkpoint、幂等键和 trace 使重试不会重复写入 |
| 存储隔离 | ExamMem 只写自己的 PostgreSQL，不混入 DeepTutor Native Memory |

Taxonomy 版本、grader contract 或 Scope 不匹配属于业务契约失败，应以明确错误停止写入。网络中断和代理重置属于传输失败；二者必须分开诊断，不能把 HTTP 409 包装成模糊的 JSON 解析错误。

## 恢复与纠错

- Practice workflow 在服务端 checkpoint 后恢复，不依赖浏览器重复提交整条链路。
- 同一请求的幂等键阻止重复答题、重复评分和重复记忆写入。
- Review 展示题目、作答、题解、评分及考试后总结；纠错以追加的复核事件修正历史，不改写原始证据。
- 无效或误触的 assessment 使用归档语义隐藏，保留审计链和关联记忆的一致性。

## 验证证据

- `tests/exam_mem/` 覆盖 Taxonomy、练习、Lifecycle、Repository 和隔离边界。
- `tests/runtime/` 与 `tests/api/` 覆盖中性插件装配和 Host 调用。
- `evaluation/` 用相同数据契约比较 none、native、append-only、vector、lifecycle 五个 Backend。
- [Stage08](./evaluation/stage08-dev.md)公开失败基线；[Stage09](./evaluation/stage09-frozen-test.md)记录一次性冻结测试及仍未解决的限制。

本项目尚未用真实用户长期学习增益验证出题、评分或聊天抽取效果；这些限制不能由结构化 Memory benchmark 的高分替代。
