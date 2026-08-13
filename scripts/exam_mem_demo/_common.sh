#!/usr/bin/env bash

set -euo pipefail

DEMO_SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DEMO_REPO_ROOT="$(cd -- "${DEMO_SCRIPT_DIR}/../.." && pwd)"
DEMO_COMPOSE_FILE="${DEMO_SCRIPT_DIR}/compose.demo.yaml"
DEMO_CONTAINER_NAME="exammem-demo-postgres"
DEMO_VOLUME_NAME="exammem-demo-postgres-data"
DEMO_DB_USER="exammem_demo"
DEMO_DB_PASSWORD="exammem-demo-only"
DEMO_DB_NAME="exammem_demo"
DEMO_DB_PORT="${EXAM_MEM_DEMO_POSTGRES_PORT:-55434}"
DEMO_DATABASE_URL="postgresql+asyncpg://${DEMO_DB_USER}:${DEMO_DB_PASSWORD}@127.0.0.1:${DEMO_DB_PORT}/${DEMO_DB_NAME}"

demo_log() {
  printf '[ExamMem Demo] %s\n' "$*"
}

demo_fail() {
  printf '[ExamMem Demo] ERROR: %s\n' "$*" >&2
  exit 1
}

demo_python_works() {
  local candidate="$1"
  "${candidate}" -c \
    'import alembic, asyncpg, deeptutor, exam_mem, sqlalchemy' \
    >/dev/null 2>&1
}

demo_python() {
  local candidate

  if [[ -n "${EXAM_MEM_DEMO_PYTHON:-}" ]]; then
    candidate="${EXAM_MEM_DEMO_PYTHON}"
    [[ -x "${candidate}" ]] || demo_fail "EXAM_MEM_DEMO_PYTHON 不可执行：${candidate}"
    demo_python_works "${candidate}" || demo_fail "指定 Python 缺少 DeepTutor/ExamMem 运行依赖：${candidate}"
    printf '%s\n' "${candidate}"
    return
  fi

  if command -v python >/dev/null 2>&1; then
    candidate="$(command -v python)"
    if demo_python_works "${candidate}"; then
      printf '%s\n' "${candidate}"
      return
    fi
  fi

  candidate="/home/lh/miniconda3/envs/exammem/bin/python"
  if [[ -x "${candidate}" ]] && demo_python_works "${candidate}"; then
    printf '%s\n' "${candidate}"
    return
  fi

  demo_fail "找不到已安装 DeepTutor/ExamMem 依赖的 Python；可用 EXAM_MEM_DEMO_PYTHON 指定。"
}

demo_require_docker() {
  command -v docker >/dev/null 2>&1 || demo_fail "未找到 docker 命令。"
  docker info >/dev/null 2>&1 || demo_fail "Docker 服务不可用或当前用户无访问权限。"
  docker compose version >/dev/null 2>&1 || demo_fail "当前 Docker 未提供 compose 子命令。"
}

demo_compose() {
  EXAM_MEM_DEMO_POSTGRES_PORT="${DEMO_DB_PORT}" \
    docker compose -f "${DEMO_COMPOSE_FILE}" "$@"
}

demo_container_exists() {
  docker inspect "${DEMO_CONTAINER_NAME}" >/dev/null 2>&1
}

demo_container_running() {
  [[ "$(docker inspect -f '{{.State.Running}}' "${DEMO_CONTAINER_NAME}" 2>/dev/null || true)" == "true" ]]
}

demo_wait_for_postgres() {
  local attempt
  for attempt in $(seq 1 30); do
    if docker exec "${DEMO_CONTAINER_NAME}" \
      pg_isready -U "${DEMO_DB_USER}" -d "${DEMO_DB_NAME}" \
      >/dev/null 2>&1; then
      demo_log "PostgreSQL 已就绪。"
      return
    fi
    sleep 1
  done
  demo_compose logs --tail=80 postgres >&2 || true
  demo_fail "PostgreSQL 在 30 秒内未通过健康检查。"
}

demo_model_summary() {
  local python_bin="$1"
  (
    cd "${DEMO_REPO_ROOT}"
    "${python_bin}" - <<'PY'
from deeptutor.services.config import get_model_catalog_service

catalog = get_model_catalog_service().load()
services = catalog.get("services", {}) if isinstance(catalog, dict) else {}
llm = services.get("llm", {}) if isinstance(services, dict) else {}
profiles = llm.get("profiles", []) if isinstance(llm, dict) else []
model_count = sum(
    len(profile.get("models", []))
    for profile in profiles
    if isinstance(profile, dict)
)
if profiles and model_count and llm.get("active_profile_id") and llm.get("active_model_id"):
    print("ready")
else:
    print("missing")
PY
  )
}

demo_ports() {
  local python_bin="$1"
  (
    cd "${DEMO_REPO_ROOT}"
    "${python_bin}" - <<'PY'
import json
from pathlib import Path

path = Path("data/user/settings/system.json")
payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
print(int(payload.get("backend_port", 8001)))
print(int(payload.get("frontend_port", 3782)))
PY
  )
}

demo_assert_ports_free() {
  local python_bin="$1"
  shift
  "${python_bin}" - "$@" <<'PY'
import socket
import sys

occupied = []
for raw_port in sys.argv[1:]:
    port = int(raw_port)
    with socket.socket() as sock:
        sock.settimeout(0.25)
        if sock.connect_ex(("127.0.0.1", port)) == 0:
            occupied.append(str(port))

if occupied:
    print(",".join(occupied))
    raise SystemExit(1)
PY
}
