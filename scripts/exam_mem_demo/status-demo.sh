#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

demo_require_docker
PYTHON_BIN="$(demo_python)"

if ! demo_container_exists; then
  demo_log "演示 PostgreSQL 尚未创建。"
  exit 1
fi

CONTAINER_STATE="$(docker inspect -f '{{.State.Status}}' "${DEMO_CONTAINER_NAME}")"
CONTAINER_HEALTH="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "${DEMO_CONTAINER_NAME}")"
demo_log "PostgreSQL container=${CONTAINER_STATE} health=${CONTAINER_HEALTH} port=127.0.0.1:${DEMO_DB_PORT}"

if ! demo_container_running; then
  demo_log "数据库已停止；volume ${DEMO_VOLUME_NAME} 仍保留。"
  exit 0
fi

DEMO_HEAD="$(
  docker exec "${DEMO_CONTAINER_NAME}" \
    psql -U "${DEMO_DB_USER}" -d "${DEMO_DB_NAME}" -Atc \
    'SELECT version_num FROM alembic_version' 2>/dev/null || true
)"
demo_log "Migration head=${DEMO_HEAD:-<not migrated>}（预期 0009_assessments）"

readarray -t DEMO_PORTS < <(demo_ports "${PYTHON_BIN}")
BACKEND_PORT="${DEMO_PORTS[0]}"
FRONTEND_PORT="${DEMO_PORTS[1]}"

EXAM_MEM_DATABASE_URL="${DEMO_DATABASE_URL}" \
  "${PYTHON_BIN}" - "${BACKEND_PORT}" "${FRONTEND_PORT}" <<'PY'
import json
import sys
from urllib.error import HTTPError, URLError
from urllib.request import ProxyHandler, build_opener

backend_port, frontend_port = sys.argv[1:]


opener = build_opener(ProxyHandler({}))


def fetch(url: str, *, timeout: float = 2):
    try:
        with opener.open(url, timeout=timeout) as response:  # noqa: S310 - fixed localhost URL
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, exc.read()
    except URLError:
        return None, b""
    except TimeoutError:
        return "starting", b""


frontend_url = f"http://127.0.0.1:{frontend_port}/exam-mem/practice"
frontend_status, _ = fetch(frontend_url, timeout=15)
print(f"[ExamMem Demo] Frontend={frontend_status or 'stopped'} {frontend_url}")

backend_url = f"http://127.0.0.1:{backend_port}"
auth_status, _ = fetch(f"{backend_url}/api/v1/auth/status")
print(f"[ExamMem Demo] Backend={auth_status or 'stopped'} {backend_url}")

plugin_status, body = fetch(f"{backend_url}/api/v1/plugins/list")
if plugin_status == 200:
    payload = json.loads(body)
    plugins = {item.get("name"): item for item in payload.get("plugins", [])}
    exam_mem = plugins.get("exam_mem")
    if exam_mem is None:
        print("[ExamMem Demo] Plugin=missing（检查 plugins.json 是否禁用了 exam_mem）")
    else:
        migration = exam_mem.get("migration") or {}
        print(
            "[ExamMem Demo] Plugin=exam_mem "
            f"capabilities={exam_mem.get('capabilities', [])} "
            f"expected_head={migration.get('expected_head')}"
        )
elif plugin_status in {401, 403}:
    print("[ExamMem Demo] Plugin API=需要登录后检查")
else:
    print(f"[ExamMem Demo] Plugin API={plugin_status or 'stopped'}")
PY
