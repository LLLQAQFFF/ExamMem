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

## `exam_mem_controlled_v2`

- 用途：保留第一次跨科目转换的失败审计记录，不作为跨科目效果证据；
- 问题：虽然题目和 canonical ID 使用 `cs_v1`，轨迹正文和部分 slot ID 仍残留
  数学语义；
- 规模：120 个合成多轮轨迹，40 个 dev case 和 80 个一次性 frozen-test case；
- 完整性：test aggregate SHA-256 为
  `c525191d1a6e7402c0fda41451b551f1c1c3e4eee485caf458c8350db6f31c9d`。

v2 的运行结果仅用于说明数据生成审计为什么必要，不能用于声称跨科目泛化。

## `exam_mem_controlled_v3`

- 用途：在推荐策略冻结后，验证 Memory lifecycle 和推荐能否跨科目泛化；
- 内容：计算机基础中的数据结构与算法，使用独立 `cs_v1` Taxonomy 和 12 个知识点；
- 规模：120 个合成多轮轨迹，40 个 dev case 和 80 个一次性 frozen-test case；
- 隔离：case ID、题目 ID、知识点 ID、考试/科目 scope、轨迹正文和查询均与 v1 分离；
- 语义门禁：构建测试会拒绝数学 Taxonomy ID、概念词和公式残留；
- 完整性：test aggregate SHA-256 为
  `9305f29c2110bb9f30438a765c71a6705bf78942f1b957b29cc346eae3352927`。

v3 沿用相同的 12 类生命周期形状以支持受控比较，但题目、答案、错误证据、记忆内容、
查询和 scope 均重写为计算机学科。正式测试结果一旦释放，只能用于报告，不能继续参与
调参。

这些样本作为本项目的合成测试 fixture 随仓库发布，使用时同时遵守仓库根目录的
[`LICENSE`](../../LICENSE)。
