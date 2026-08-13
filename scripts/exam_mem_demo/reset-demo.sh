#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_common.sh
source "${SCRIPT_DIR}/_common.sh"

demo_require_docker

cat <<EOF
[ExamMem Demo] 该操作会永久删除：
  container: ${DEMO_CONTAINER_NAME}
  volume:    ${DEMO_VOLUME_NAME}
  database:  ${DEMO_DB_NAME}

不会删除 DeepTutor 配置、源码或其他 PostgreSQL 容器/volume。
EOF

if [[ "${EXAM_MEM_DEMO_RESET_CONFIRM:-}" != "DELETE ${DEMO_DB_NAME}" ]]; then
  if [[ ! -t 0 ]]; then
    demo_fail "非交互执行必须设置 EXAM_MEM_DEMO_RESET_CONFIRM='DELETE ${DEMO_DB_NAME}'。"
  fi
  read -r -p "输入 DELETE ${DEMO_DB_NAME} 继续：" confirmation
  [[ "${confirmation}" == "DELETE ${DEMO_DB_NAME}" ]] || demo_fail "确认不匹配，未删除任何内容。"
fi

demo_compose down --volumes --remove-orphans
demo_log "演示 PostgreSQL 容器和 volume 已删除。"
