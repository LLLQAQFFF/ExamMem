# ExamMem evaluation datasets

本目录保留公开结果所需的数据契约、受控合成样本和冻结 split。

## `exam_mem_controlled_v1`

- 用途：评估结构化 `LearningEvent` 之后的 Memory lifecycle、当前状态、Scope 隔离、
  检索和推荐；
- 内容：项目内构造的数学一线性代数与概率论多轮学习轨迹；
- 隐私：不包含真实用户、真实聊天、邮箱、姓名、凭据或数据库 dump；
- 版权：不是教材、题库或历年真题的复制，不用于证明考研出题或判题质量；
- 切分：40 个 dev case、80 个 frozen-test case，另有 24 个协议检查模板；
- 完整性：文件列表、逐文件 hash 和 split aggregate hash 位于
  `exam_mem_controlled_v1.manifest.json`。

Stage09 的 frozen test 已完成一次性 release，结果见
[`docs/exam_mem/evaluation/stage09-frozen-test.md`](../../docs/exam_mem/evaluation/stage09-frozen-test.md)。公开后的 test 可以
用于复核已发布结果，但不能继续当作未来调参的未见 holdout；新实验需要创建并冻结新的
数据版本。

这些样本作为本项目的合成测试 fixture 随仓库发布，使用时同时遵守仓库根目录的
[`LICENSE`](../../LICENSE)。
