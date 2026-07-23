#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
USER_DOCKER_COMPOSE_SET="${DOCKER_COMPOSE+x}"

load_env_file() {
  local path="$1"
  local line key
  if [[ ! -f "$path" ]]; then
    return
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*$ || "$line" =~ ^[[:space:]]*# ]] && continue
    key="${line%%=*}"
    key="${key#export }"
    key="${key//[[:space:]]/}"
    if [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ && -z "${!key+x}" ]]; then
      eval "export ${line}"
    fi
  done < "$path"
}

load_env_file_override() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    return
  fi

  set -a
  # shellcheck disable=SC1090
  source "$path"
  set +a
}

load_env_file "${ROOT_DIR}/.env"
load_env_file_override "${ROOT_DIR}/backend/.env"

if [[ -z "$USER_DOCKER_COMPOSE_SET" ]]; then
  DOCKER_COMPOSE="sudo docker compose"
else
  DOCKER_COMPOSE="${DOCKER_COMPOSE:-sudo docker compose}"
fi

START_DB="${START_DB:-1}"
KILL_PORTS="${KILL_PORTS:-1}"
DATABASE_URL="${DATABASE_URL:-postgresql://scicommons:scicommons@localhost:5432/scicommons}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_URL="${FRONTEND_URL:-http://134.87.8.193:5173}"
ARTICLE_SERVICE_BASE_URL="${ARTICLE_SERVICE_BASE_URL:-http://134.87.9.167:8100}"
CORS_ORIGINS="${CORS_ORIGINS:-${FRONTEND_URL},http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174}"
SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-}"

PIDS=()

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "Missing $1. Run ./setup.sh first." >&2
    exit 1
  fi
}

start_postgres() {
  # DOCKER_COMPOSE may intentionally contain spaces, for example: "sudo docker compose".
  # shellcheck disable=SC2086
  $DOCKER_COMPOSE up -d db
}

port_pids() {
  local port="$1"
  if command -v lsof >/dev/null 2>&1; then
    lsof -tiTCP:"$port" -sTCP:LISTEN 2>/dev/null || true
    return
  fi
  if command -v fuser >/dev/null 2>&1; then
    fuser -n tcp "$port" 2>/dev/null || true
    return
  fi
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :$port" 2>/dev/null \
      | sed -n 's/.*pid=\([0-9][0-9]*\).*/\1/p' \
      | sort -u
  fi
}

kill_port_processes() {
  local port="$1"
  local label="$2"
  local pids
  pids="$(port_pids "$port" | tr '\n' ' ' | xargs 2>/dev/null || true)"

  if [[ -z "$pids" ]]; then
    echo "==> ${label} port ${port} is free"
    return
  fi

  echo "==> ${label} port ${port} is in use by PID(s): ${pids}"
  echo "==> Stopping process(es) on port ${port}"
  # shellcheck disable=SC2086
  kill $pids 2>/dev/null || true
  sleep 2

  pids="$(port_pids "$port" | tr '\n' ' ' | xargs 2>/dev/null || true)"
  if [[ -n "$pids" ]]; then
    echo "==> Force stopping process(es) still on port ${port}: ${pids}"
    # shellcheck disable=SC2086
    kill -9 $pids 2>/dev/null || true
  fi
}

cleanup() {
  local status=$?
  trap - EXIT INT TERM
  for pid in "${PIDS[@]}"; do
    kill "$pid" >/dev/null 2>&1 || true
  done
  for pid in "${PIDS[@]}"; do
    wait "$pid" >/dev/null 2>&1 || true
  done
  exit "$status"
}

trap cleanup EXIT INT TERM

require_file "${ROOT_DIR}/backend/.venv/bin/activate"

if [[ "$KILL_PORTS" == "1" ]]; then
  kill_port_processes "$BACKEND_PORT" "Backend"
else
  echo "==> Skipping port cleanup. Set KILL_PORTS=1 to enable it."
fi

if [[ "$START_DB" == "1" ]]; then
  echo "==> Starting Postgres"
  start_postgres
fi

if [[ -z "$INTERNAL_API_TOKEN" ]]; then
  echo "==> Warning: INTERNAL_API_TOKEN is empty; /internal/feed-refresh will return 503."
fi

echo "==> Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${ROOT_DIR}/backend"
  source .venv/bin/activate
  export DATABASE_URL ARTICLE_SERVICE_BASE_URL CORS_ORIGINS SESSION_COOKIE_SECURE INTERNAL_API_TOKEN
  exec uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
PIDS+=("$!")

echo
echo "Services started:"
echo "  Backend:             http://localhost:${BACKEND_PORT}"
echo "  Allowed frontend:    ${FRONTEND_URL}"
echo "  GPU article service: ${ARTICLE_SERVICE_BASE_URL}"
echo
echo "Press Ctrl+C to stop the backend."

while true; do
  for pid in "${PIDS[@]}"; do
    if ! kill -0 "$pid" >/dev/null 2>&1; then
      set +e
      wait "$pid"
      status=$?
      set -e
      exit "$status"
    fi
  done
  sleep 2
done
