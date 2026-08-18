# 学习计划隔离、考试复盘与学习档案需求基线

状态：当前闭环产品化基线（2026-08-17）

## 1. 产品边界

- 学习计划是智能备考的第一层隔离边界。一个导入并发布的大纲对应一个 `plan_id`，ExamMem 使用 `exam_id=plan:{plan_id}` 进入既有四维 Scope。
- 科目、知识点与大纲版本来自已发布学习计划，不允许在练习或记忆页面临时创造另一套范围。
- 不同学习计划默认不同时展示或合并分析。切换计划后，科目、章节、知识点、考试和记忆选择必须重新确定。
- 本阶段的“隔离”是 ExamMem 独立 PostgreSQL 内的逻辑 Scope 隔离，不是每个计划建立一个物理数据库。
- Practice 是正式刷题闭环；普通 Chat 和学习路径 Agent 侧记不是正式 L1，不能改变判题或掌握度。

## 2. 考试复盘职责

考试复盘围绕“一份考试 → 一个试卷版本 → 一次作答”展示：

1. 学习计划、科目、大纲版本与考试版本。
2. 历次作答状态、分数和完成时间。
3. 每道已作答题的题干、用户答案、参考答案、题解/评分规则。
4. Grade 结果与证据、错误诊断、下一步推荐。
5. 由已落库 Grade、Diagnosis 和 Recommendation 确定性生成的本次考试总结。
6. Grade Review 异议链、Trace 和 Lifecycle 审计链。
7. 跳转到该题正式 L1 event 的学习档案链接。

安全约束：未作答题、考试历史列表、出题响应和答题响应不得返回参考答案或评分规则；完整答案只在该题已有提交记录的复盘详情中返回。

## 3. 学习档案职责

- L1 是只追加的正式证据索引。卡片可展开查看原题、作答、参考答案、判题证据、诊断和推荐，但不能原地编辑。
- L1 展示由该 event 产生或支持的 L2 记忆、版本和生命周期状态，并能跳到准确考试复盘。
- L2 按 namespace + slot_key 展示完整版本链、provenance 和 lifecycle；不准确内容通过现有显式确认的追加纠正流程修改，历史版本保持只读。
- L2 来源可以回到对应考试会话；从 L1 也可以直接定位关联 L2 版本。
- L3 是可重建投影。当前存储契约是“学习计划 + 科目”级跨大纲版本综合画像，UI 必须明确标识；大纲版本筛选只约束 L1/L2，不能把 L3 冒充成单一大纲版本。
- 已确认的学习路径 Agent 侧记单独展示为“非 L1”；普通聊天线索继续独立，不能与正式刷题证据混合。

## 4. 数据与调用链

```text
已发布学习计划
  → exam_id=plan:{plan_id} + subject_id + taxonomy_version
  → versioned assessment + finite attempt
  → practice checkpoint（完整题目/作答/Grade/Diagnosis/Recommendation）
  → append-only L1 LearningEvent
  → L2 CAS + provenance + lifecycle
  → post-commit L3 rebuild

考试复盘 ← checkpoint + assessment/attempt + audit
学习档案 ← L1 + checkpoint detail + memory_provenance + L2/L3
```

没有新增第二套考试事实或记忆事实；两个页面只是针对不同任务组织同一批持久化事实。

## 5. 本次最小实现与延期

本次实现不新增 migration，不修改冻结的 0001～0006，也不改变 L1/L2/L3 合同。考试总结为读模型的确定性派生结果，不写回 L1/L2。

以下仍延期：

- 用户自由填写的考试后反思及其确认/进入 L2 的治理流程。
- L3 按单一 taxonomy version 重建或查看历史投影。
- 跨学习计划只读对比视图。
- 多源文件长期引用、课程问答、来源驱动出题和 Learning Journey Memory。

若以后实现用户反思，必须使用追加式记录、幂等键和明确确认状态；不得修改原 L1，也不得让未确认文本影响 L2。

## 6. 验收点

- 学习计划选择没有“全部计划”默认混合入口。
- 同一考试 ID 的不同试卷版本与多次作答可分别查看。
- 已作答题详情完整，未作答题答案不泄露。
- Review 与 Archive 能通过 session/event/memory 标识双向定位。
- L1 只读；错误信息通过 L2 追加纠正，版本链与来源可见。
- PostgreSQL 隔离测试覆盖 assessment/taxonomy/event/memory 关联。
- DeepTutor Core 不 import `exam_mem`，ExamMem 仍通过中性 Plugin API 装配。
