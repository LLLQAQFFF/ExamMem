#!/usr/bin/env bash

set -Eeuo pipefail

readonly REPO_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
readonly DB_CONTAINER="exammem-postgres"
readonly DB_HOST="127.0.0.1"
readonly DB_PORT="55432"
readonly DB_USER="exammem"
readonly DB_NAME="exammem"
readonly FRONTEND_URL="http://127.0.0.1:3782/"
readonly PUBLIC_URL="https://app.exammem.com"
readonly TUNNEL_NAME="deeptutor-exammem"

app_pid=""
tunnel_pid=""

log() {
  printf '[ExamMem] %s\n' "$*"
}

fail() {
  printf '[ExamMem] 错误：%s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'EOF'
用法：
  ./scripts/start-public-server.sh

作用：
  启动已有 PostgreSQL 容器，校验数据库 migration，随后启动 DeepTutor
  和 app.exammem.com 对应的 Cloudflare Tunnel。

说明：
  - 未激活 exammem 时，会通过 conda run 自动进入该环境。
  - 数据库密码采用隐藏输入，不会写入文件。
  - 脚本不会创建、删除或升级数据库。
  - 在前台运行；按 Ctrl+C 会同时停止 DeepTutor 和 Tunnel。
  - PostgreSQL 容器及其持久化 volume 会继续保留。
EOF
}

cleanup() {
  local pid

  trap - EXIT INT TERM
  for pid in "${tunnel_pid}" "${app_pid}"; do
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
  for pid in "${tunnel_pid}" "${app_pid}"; do
    if [[ -n "${pid}" ]]; then
      wait "${pid}" 2>/dev/null || true
    fi
  done
}

on_signal() {
  log "收到停止信号，正在关闭 DeepTutor 和 Tunnel。"
  exit 130
}

wait_for_database() {
  local status=""

  for _ in {1..30}; do
    status="$(
      docker inspect --format \
        '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' \
        "${DB_CONTAINER}" 2>/dev/null || true
    )"
    if [[ "${status}" == "healthy" || "${status}" == "running" ]]; then
      return 0
    fi
    sleep 2
  done
  fail "PostgreSQL 未在 60 秒内就绪，当前状态：${status:-unknown}。"
}

wait_for_frontend() {
  for _ in {1..180}; do
    if curl --noproxy '*' --silent --output /dev/null \
      --connect-timeout 1 --max-time 3 "${FRONTEND_URL}"; then
      return 0
    fi
    if ! kill -0 "${app_pid}" 2>/dev/null; then
      wait "${app_pid}" || true
      fail "DeepTutor 在前端就绪前退出，请查看上方日志。"
    fi
    sleep 2
  done
  fail "DeepTutor 前端未在 6 分钟内就绪。"
}

if (($#)); then
  case "$1" in
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage >&2
      fail "未知参数：$1"
      ;;
  esac
fi

if [[ "${CONDA_DEFAULT_ENV:-}" != "exammem" ]]; then
  command -v conda >/dev/null 2>&1 || \
    fail "当前未激活 exammem，且找不到 conda 命令。"
  log "正在通过 conda run 进入 exammem 环境。"
  exec conda run --no-capture-output -n exammem "${BASH_SOURCE[0]}" "$@"
fi

trap cleanup EXIT
trap on_signal INT TERM

for command_name in docker cloudflared curl; do
  command -v "${command_name}" >/dev/null 2>&1 || \
    fail "找不到 ${command_name}，请先确认它已安装并在 PATH 中。"
done

readonly PYTHON_BIN="$(command -v python)"
"${PYTHON_BIN}" -c 'import alembic, asyncpg, deeptutor_cli' 2>/dev/null || \
  fail "当前 Python 环境缺少运行依赖，请确认已激活 exammem。"

docker info >/dev/null 2>&1 || \
  fail "Docker Engine 尚未运行；请先启动 Docker Desktop。"

if curl --noproxy '*' --silent --output /dev/null \
  --connect-timeout 1 --max-time 3 "${FRONTEND_URL}"; then
  fail "${FRONTEND_URL} 已有服务运行；请先停止旧的 DeepTutor。"
fi

docker inspect "${DB_CONTAINER}" >/dev/null 2>&1 || \
  fail "找不到 ${DB_CONTAINER}；本脚本只启动已有容器，不会自动创建数据库。"

if [[ "$(docker inspect --format '{{.State.Running}}' "${DB_CONTAINER}")" != "true" ]]; then
  log "正在启动已有 PostgreSQL 容器 ${DB_CONTAINER}。"
  docker start "${DB_CONTAINER}" >/dev/null
else
  log "PostgreSQL 容器已经运行。"
fi
wait_for_database

if [[ -z "${EXAM_MEM_DATABASE_URL:-}" ]]; then
  read -r -s -p '请输入 ExamMem 数据库密码：' db_password
  printf '\n'
  [[ -n "${db_password}" ]] || fail "数据库密码不能为空。"
  encoded_password="$(
    printf '%s' "${db_password}" | "${PYTHON_BIN}" -c \
      'import sys, urllib.parse as u; print(u.quote(sys.stdin.read(), safe=""))'
  )"
  unset db_password
  export EXAM_MEM_DATABASE_URL="postgresql+asyncpg://${DB_USER}:${encoded_password}@${DB_HOST}:${DB_PORT}/${DB_NAME}"
  unset encoded_password
else
  log "使用当前终端已有的 EXAM_MEM_DATABASE_URL。"
fi

code_head="$(
  cd "${REPO_ROOT}"
  "${PYTHON_BIN}" -c '
from alembic.config import Config
from alembic.script import ScriptDirectory

heads = ScriptDirectory.from_config(Config("alembic.ini")).get_heads()
if len(heads) != 1:
    raise SystemExit(f"expected exactly one migration head, got: {heads}")
print(heads[0])
'
)"

current_output="$(
  cd "${REPO_ROOT}"
  "${PYTHON_BIN}" -m alembic -c alembic.ini current
)" || fail "无法连接 ExamMem 数据库；请确认刚才输入的密码正确。"

if [[ "${current_output}" != *"${code_head} (head)"* ]]; then
  printf '%s\n' "${current_output}" >&2
  fail "数据库版本与代码 head ${code_head} 不一致；请先审查并执行 migration。"
fi
log "数据库连接正常，migration head：${code_head}。"

cloudflared tunnel ingress validate >/dev/null || \
  fail "Cloudflare ingress 配置校验失败，请检查 ~/.cloudflared/config.yml。"

log "正在启动 DeepTutor；首次构建前端可能需要几分钟。"
(
  cd "${REPO_ROOT}"
  exec "${PYTHON_BIN}" -m deeptutor_cli start --home "${REPO_ROOT}"
) &
app_pid=$!
wait_for_frontend

log "本地前端已就绪，正在使用 HTTP/2 启动 Cloudflare Tunnel。"
env -u EXAM_MEM_DATABASE_URL TUNNEL_TRANSPORT_PROTOCOL=http2 \
  cloudflared tunnel run "${TUNNEL_NAME}" &
tunnel_pid=$!
sleep 2
kill -0 "${tunnel_pid}" 2>/dev/null || \
  fail "Cloudflare Tunnel 启动失败，请查看上方日志。"

cat <<EOF

[ExamMem] 服务已启动：
  本地地址：${FRONTEND_URL}
  公网地址：${PUBLIC_URL}
  停止服务：在本终端按 Ctrl+C

EOF

set +e
wait -n "${app_pid}" "${tunnel_pid}"
exit_code=$?
set -e

if ! kill -0 "${app_pid}" 2>/dev/null; then
  log "DeepTutor 已退出。"
else
  log "Cloudflare Tunnel 已退出。"
fi
exit "${exit_code}"
