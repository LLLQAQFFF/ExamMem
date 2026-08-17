# ExamMem 阶段 08 评测 Runbook

## 1. 目标与边界

本评测比较 `none`、`native`、`append_only`、`vector`、`lifecycle` 五个 Memory Backend。输入固定为 120 条受控学习轨迹（dev 40、冻结 test 80），随机种子为 `20260806`。

- `protocol_check` 只验证协议、错误分层和调用链，不进入正式得分。
- `dev` 可运行、分析和修正评测实现。
- `test` 在本阶段只允许校验 schema、数量和冻结哈希，CLI 会拒绝 rollout，防止针对 holdout 调参。
- 输入从结构化 `LearningEvent` 开始；原始对话抽取不在本基准内。
- 下游 Memory 对比使用 Gold-normalized slot，避免抽取错误污染 Lifecycle 分数；slot normalizer 另行独立评分。

## 2. 前置检查

```bash
cd /home/lh/DeepTutor
git status --short --branch
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli protocol validate
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli dataset verify \
  --dataset-version exam_mem_controlled_v1 --no-content-output
```

PostgreSQL 必须是隔离测试库或明确的本地开发库，并已迁移到当前 head：

```bash
export EXAM_MEM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@127.0.0.1:PORT/DB'
EXAM_MEM_DATABASE_URL="$EXAM_MEM_DATABASE_URL" \
  /home/lh/miniconda3/envs/exammem/bin/alembic \
  -c exam_mem/storage/alembic.ini current
```

期望 migration head：`0011_assessment_archival`。0001～0006 不允许修改。

## 3. 运行 protocol-check

五臂运行会调用当前配置的 Host LLM：Lifecycle 用于语义关系判定，Native 用于 L1→L2→L3 consolidation。运行前确认数据发送范围和模型费用已获授权。

```bash
EXAM_MEM_DATABASE_URL="$EXAM_MEM_DATABASE_URL" \
  /home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli evaluate run \
  --run-id protocol-check-YYYYMMDD-SHA \
  --split protocol_check \
  --backend none \
  --backend native \
  --backend append_only \
  --backend vector \
  --backend lifecycle \
  --output-root artifacts/stage08/runs \
  --concurrency 1 \
  --timeout-seconds 300 \
  --top-k 5
```

中断后用完全相同的参数追加 `--resume`。缓存的 config hash 或 code SHA 不一致时会拒绝恢复。

## 4. 运行 dev

protocol-check 的五臂均能完成或给出可解释的首层失败后，运行 40-case dev：

```bash
EXAM_MEM_DATABASE_URL="$EXAM_MEM_DATABASE_URL" \
  /home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli evaluate run \
  --run-id dev-YYYYMMDD-SHA \
  --split dev \
  --backend none \
  --backend native \
  --backend append_only \
  --backend vector \
  --backend lifecycle \
  --output-root artifacts/stage08/runs \
  --concurrency 1 \
  --timeout-seconds 300 \
  --top-k 5
```

同一 user 的 case 始终串行；不同 user 才可受 `--concurrency` 控制并发。

## 5. 产物与判读

每个不可变 run 目录包含：

- `manifest.json`：协议、数据哈希、代码 SHA、seed 和后端集合；
- `config.json`：不含凭据的模型、公平和 backend 配置；
- `cases.jsonl`、`traces.jsonl`：输入与逐步观测；
- `snapshots/`：每 case 的前后快照；
- `metrics.json`、`metrics.csv`：25 个注册指标；
- `scenario_metrics.json`：12 类场景分解；
- `bad_cases.jsonl`：首个失败层、类型和步骤；
- `report.json`、`report.md`：仅五臂齐全时生成正式对比报告。

数值为空必须同时出现以下状态之一：

- `undefined`：指标适用，但当前分母为 0；
- `not_applicable`：该后端或本基准层不提供所需语义。

禁止把 N/A 写成 0。Host 未返回 token usage 时，token 和美元成本不得估算。

## 6. 数据库副作用与清理

PostgreSQL arm 只新增带 `eval:<run_id>:<backend>:` 前缀的事件、baseline facts、Learning Memory、Lifecycle Decision、Change Log 和 projection。Native 每个 case 写到 run 目录下独立文件树，不读取用户 Native Memory。

清理属于破坏性操作，必须先按 run 前缀只读核对行数并备份审计产物；不要对共享库执行。推荐保留专用容器/volume 到报告验收完成，再整体删除专用容器和专用 volume。

## 7. 验收命令

```bash
/home/lh/miniconda3/envs/exammem/bin/python -m ruff check evaluation tests/evaluation
/home/lh/miniconda3/envs/exammem/bin/python -m pytest -q tests/evaluation
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli dataset verify \
  --dataset-version exam_mem_controlled_v1 --no-content-output
git diff --check
git -C /home/lh/code/ExamMem status --short --branch
```

