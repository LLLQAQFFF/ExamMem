#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

demo_require_docker

if ! demo_container_exists; then
  demo_log "演示 PostgreSQL 不存在，无需停止。"
  exit 0
fi

if demo_container_running; then
  demo_compose stop postgres
  demo_log "演示 PostgreSQL 已停止。"
else
  demo_log "演示 PostgreSQL 已经处于停止状态。"
fi

demo_log "数据 volume ${DEMO_VOLUME_NAME} 已保留；再次运行 start-demo.sh 会继续使用。"
demo_log "如果 DeepTutor 仍在运行，请在启动它的终端按 Ctrl+C。"
