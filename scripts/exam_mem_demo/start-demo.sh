#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

launch_mode="dev"
prepare_only=false

usage() {
  cat <<'EOF'
用法：
  ./scripts/exam_mem_demo/start-demo.sh [--dev|--production] [--prepare-only]

选项：
  --dev           使用 Next.js 开发服务器（默认，不改生产构建缓存）。
  --production    运行 production build 后启动；会更新 .next-deeptutor 缓存。
  --prepare-only  只启动 PostgreSQL 并执行 migration，不启动 DeepTutor。
  -h, --help      显示帮助。
EOF
}

while (($#)); do
  case "$1" in
    --dev)
      launch_mode="dev"
      ;;
    --production)
      launch_mode="production"
      ;;
    --prepare-only)
      prepare_only=true
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      demo_fail "未知参数：$1"
      ;;
  esac
  shift
done

demo_require_docker
PYTHON_BIN="$(demo_python)"

demo_log "演示数据库仅绑定 127.0.0.1:${DEMO_DB_PORT}。"
demo_log "容器：${DEMO_CONTAINER_NAME}；持久化 volume：${DEMO_VOLUME_NAME}。"
demo_log "固定口令只用于本机演示，不要复用于正式配置。"

if demo_container_exists && ! demo_container_running; then
  demo_log "启动已有演示容器。"
else
  demo_log "创建或确认演示 PostgreSQL。"
fi
demo_compose up -d postgres
demo_wait_for_postgres

demo_log "执行 Alembic upgrade head（只写演示数据库 ${DEMO_DB_NAME}）。"
(
  cd "${DEMO_REPO_ROOT}"
  EXAM_MEM_DATABASE_URL="${DEMO_DATABASE_URL}" \
    "${PYTHON_BIN}" -m alembic -c alembic.ini upgrade head
)

DEMO_HEAD="$(
  docker exec "${DEMO_CONTAINER_NAME}" \
    psql -U "${DEMO_DB_USER}" -d "${DEMO_DB_NAME}" -Atc \
    'SELECT version_num FROM alembic_version'
)"
[[ "${DEMO_HEAD}" == "0007_grade_reviews" ]] || \
  demo_fail "数据库 head 异常：${DEMO_HEAD:-<empty>}"
demo_log "Migration head：${DEMO_HEAD}。"

if [[ "$(demo_model_summary "${PYTHON_BIN}")" == "missing" ]]; then
  demo_log "提示：当前 DeepTutor 没有可用 LLM 模型配置。"
  demo_log "你可以查看全部 ExamMem 页面和数据库装配；提交真实答案前请在 Settings → Models 配置模型。"
else
  demo_log "检测到当前 workspace 已配置活动 LLM，可以尝试完整答题。"
fi

if [[ "${prepare_only}" == true ]]; then
  demo_log "准备完成。运行 status-demo.sh 查看状态，或再次运行本脚本启动 DeepTutor。"
  exit 0
fi

readarray -t DEMO_PORTS < <(demo_ports "${PYTHON_BIN}")
BACKEND_PORT="${DEMO_PORTS[0]}"
FRONTEND_PORT="${DEMO_PORTS[1]}"

OCCUPIED_PORTS="$(demo_assert_ports_free "${PYTHON_BIN}" "${BACKEND_PORT}" "${FRONTEND_PORT}" || true)"
if [[ -n "${OCCUPIED_PORTS}" ]]; then
  demo_fail "DeepTutor 端口已被占用：${OCCUPIED_PORTS}。请先停止已有服务；演示脚本不会改写 system.json 端口。"
fi

cat <<EOF

[ExamMem Demo] 即将前台启动 DeepTutor：
  前端：http://127.0.0.1:${FRONTEND_PORT}/exam-mem/practice
  后端：http://127.0.0.1:${BACKEND_PORT}
  停止 DeepTutor：在本终端按 Ctrl+C
  停止演示数据库：./scripts/exam_mem_demo/stop-demo.sh

EOF

cd "${DEMO_REPO_ROOT}"
if [[ "${launch_mode}" == "production" ]]; then
  exec env EXAM_MEM_DATABASE_URL="${DEMO_DATABASE_URL}" \
    "${PYTHON_BIN}" -m deeptutor_cli start --home "${DEMO_REPO_ROOT}"
fi
exec env EXAM_MEM_DATABASE_URL="${DEMO_DATABASE_URL}" \
  "${PYTHON_BIN}" -m deeptutor_cli start --home "${DEMO_REPO_ROOT}" --dev
