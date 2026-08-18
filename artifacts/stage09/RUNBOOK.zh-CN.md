# ExamMem 阶段 09 Runbook

## 1. 已冻结结果

Stage09 的 test release 已经消费，不应再次运行或用新代码覆盖：

- run：`stage09-test-258e4456-configured-final`；
- code：`258e4456018fdd00abf31ce090457a2c4f8d071a`；
- split：`test`，80 case；
- dataset SHA-256：
  `fd01c3a2eb910ad0476e82e54eacc593d44d867d37c812b69f1a99e8a8553011`；
- embedding：`ollama:qwen3-embedding:0.6b:1024`；
- 五臂：`none/native/append_only/vector/lifecycle`。

冻结结果只允许读取和归档。未来修正只能在 dev 上验证，并为下一轮创建新的数据版本和
新的 holdout。

## 2. 环境检查

```bash
cd /home/lh/DeepTutor
git status --short --branch
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Ports}}'
curl -fsS http://127.0.0.1:11434/api/tags
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli protocol validate
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli dataset verify \
  --dataset-version exam_mem_controlled_v1 --no-content-output
```

PostgreSQL 必须是隔离测试库或明确的本地开发库：

```bash
export EXAM_MEM_DATABASE_URL='postgresql+asyncpg://USER:PASSWORD@127.0.0.1:PORT/DB'
EXAM_MEM_DATABASE_URL="$EXAM_MEM_DATABASE_URL" \
  /home/lh/miniconda3/envs/exammem/bin/alembic \
  -c exam_mem/storage/alembic.ini current
```

期望 head：`0011_assessment_archival`。不得修改 migrations `0001`～`0006`。

## 3. 可重复运行 dev

创建新的 `run-id`，不要复用正式 test run：

```bash
EXAM_MEM_DATABASE_URL="$EXAM_MEM_DATABASE_URL" \
  /home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli evaluate run \
  --run-id dev-YYYYMMDD-GITSHA \
  --split dev \
  --backend none \
  --backend native \
  --backend append_only \
  --backend vector \
  --backend lifecycle \
  --output-root artifacts/stage08/runs \
  --concurrency 1 \
  --timeout-seconds 300 \
  --max-llm-calls-per-case 100 \
  --top-k 5 \
  --embedding-mode configured
```

完全相同的 run identity 中断后可加 `--resume`。如果 PostgreSQL case 已提交但 partial
尚未落盘，不能原地重放；应换新 `run-id` 和新隔离数据库从零运行。

`--case-id` 和 `--scenario` 只用于 dev 诊断。正式五臂报告不得带过滤器。

## 4. 冻结 test 审计（只读）

```bash
RUN_DIR=artifacts/stage08/runs/stage09-test-258e4456-configured-final
sed -n '1,220p' "$RUN_DIR/manifest.json"
sed -n '1,220p' "$RUN_DIR/report.md"
/home/lh/miniconda3/envs/exammem/bin/python -m evaluation.cli dataset verify \
  --dataset-version exam_mem_controlled_v1 --no-content-output
git show -s --format='%H %s' 258e4456
```

必须核对：`complete_five_arm_report=true`、`split=test`、80 case、五臂齐全、无 filters、
code SHA 和 dataset hash 与第 1 节一致，并确认 `report.json` 五个 backend outcome 总数
均为 80。CLI 本次以退出码 0 收口，但 manifest 不持久化一个容易与 case outcome 混淆的
全局 `status` 字段；Lifecycle outcome 是 79 completed、1 failed。

产物说明：

- `manifest.json`：不可变运行身份；
- `config.json`：不含凭据的模型和公平配置；
- `metrics.json/csv`：注册指标及 N/A/undefined 语义；
- `confusion_matrix.json`：Gold×预测 operation；
- `bad_cases.jsonl`：首层错误；
- `traces.jsonl`、`snapshots/`：逐步审计；
- `report.json/md`：五臂完整报告。

## 5. 代码门禁

```bash
/home/lh/miniconda3/envs/exammem/bin/python -m pytest -q tests/evaluation
/home/lh/miniconda3/envs/exammem/bin/python -m pytest -q \
  tests/exam_mem/lifecycle \
  tests/exam_mem/storage
/home/lh/miniconda3/envs/exammem/bin/python -m pytest -q \
  tests/runtime/test_exam_mem_plugin.py \
  tests/runtime/test_plugin_host_services.py \
  tests/runtime/test_plugin_settings.py
/home/lh/miniconda3/envs/exammem/bin/python -m ruff check .
git diff --check
git -C /home/lh/code/ExamMem status --short --branch
```

当前环境的 Starlette/httpx2/AnyIO `TestClient` 存在最小可复现的 ASGI portal 卡住问题，
因此 `tests/runtime/test_plugin_manager.py::test_mount_router_applies_declared_access_dependencies`
不能被记为通过。修复前先在独立环境验证兼容版本，不要为了让门禁变绿删除权限断言或加
超时特判；安装/变更依赖需单独授权并更新依赖声明。

还要检查：

- DeepTutor Core 不直接 `import exam_mem`；
- migrations 0001～0006 与冻结源逐字节一致；
- secret scan 不包含真实 DSN、token、cookie 或私钥；
- 不暂存 `web/.next-deeptutor/**` 等本地构建产物。

## 6. 数据副作用和清理

评测 PostgreSQL 只应写入 `eval:<run-id>:<backend>:` 前缀记录；Native 只写 run 目录。
清理是破坏性操作：先保存 manifest、report、metrics、confusion 和 bad cases，再只读核对
前缀行数。不要对共享库或 demo 业务库执行清理；推荐在报告验收后整体删除专用测试库或
专用 volume，且需另行确认。
