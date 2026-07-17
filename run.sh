#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

load_env_file "${ROOT_DIR}/.env"

DOCKER_COMPOSE="${DOCKER_COMPOSE:-docker compose}"
TRY_SUDO_DOCKER="${TRY_SUDO_DOCKER:-1}"
START_DB="${START_DB:-1}"
DATABASE_URL="${DATABASE_URL:-postgresql://scicommons:scicommons@localhost:5432/scicommons}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
ARTICLE_HOST="${ARTICLE_HOST:-0.0.0.0}"
ARTICLE_PORT="${ARTICLE_PORT:-8100}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
SCICOMM_ARTIFACT_DIR="${SCICOMM_ARTIFACT_DIR:-${ROOT_DIR}/scicomm_embedding}"
ARTICLE_SERVICE_BASE_URL="${ARTICLE_SERVICE_BASE_URL:-http://localhost:${ARTICLE_PORT}}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${BACKEND_PORT}}"

PIDS=()

require_file() {
  if [[ ! -e "$1" ]]; then
    echo "Missing $1. Run ./setup.sh first." >&2
    exit 1
  fi
}

start_postgres() {
  set +e
  # DOCKER_COMPOSE may intentionally contain spaces, for example: "sudo docker compose".
  # shellcheck disable=SC2086
  $DOCKER_COMPOSE up -d db
  local status=$?
  set -e

  if [[ "$status" -eq 0 ]]; then
    return 0
  fi
  if [[ "$TRY_SUDO_DOCKER" == "1" && "$DOCKER_COMPOSE" != sudo* ]] && command -v sudo >/dev/null 2>&1; then
    echo "==> Docker Compose failed; retrying with sudo docker compose"
    sudo docker compose up -d db
    return 0
  fi
  return "$status"
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
require_file "${ROOT_DIR}/scicomm_embedding/.venv/bin/activate"
require_file "${ROOT_DIR}/frontend/node_modules"

if [[ "$START_DB" == "1" ]]; then
  echo "==> Starting Postgres"
  start_postgres
fi

missing_artifacts=0
for artifact in all.sqlite all_specter.index all_metadata.json all_manifest.json; do
  if [[ ! -f "${SCICOMM_ARTIFACT_DIR}/${artifact}" ]]; then
    missing_artifacts=1
  fi
done
if [[ "$missing_artifacts" == "1" ]]; then
  echo "==> Warning: article artifacts are missing in ${SCICOMM_ARTIFACT_DIR}."
  echo "    Article endpoints will return 503 until you run: cd scicomm_embedding && source .venv/bin/activate && python pipeline.py"
fi

echo "==> Starting article service on ${ARTICLE_HOST}:${ARTICLE_PORT}"
(
  cd "${ROOT_DIR}/scicomm_embedding"
  source .venv/bin/activate
  export SCICOMM_ARTIFACT_DIR
  exec uvicorn article_service.main:app --host "$ARTICLE_HOST" --port "$ARTICLE_PORT"
) &
PIDS+=("$!")

echo "==> Starting backend on ${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${ROOT_DIR}/backend"
  source .venv/bin/activate
  export DATABASE_URL ARTICLE_SERVICE_BASE_URL
  exec uvicorn app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
) &
PIDS+=("$!")

echo "==> Starting frontend on ${FRONTEND_HOST}:${FRONTEND_PORT}"
(
  cd "${ROOT_DIR}/frontend"
  export VITE_API_BASE_URL
  exec npm run dev -- --host "$FRONTEND_HOST" --port "$FRONTEND_PORT"
) &
PIDS+=("$!")

echo
echo "Services started:"
echo "  Frontend:        http://localhost:${FRONTEND_PORT}"
echo "  Backend:         http://localhost:${BACKEND_PORT}"
echo "  Article service: http://localhost:${ARTICLE_PORT}"
echo
echo "Press Ctrl+C to stop frontend, backend, and article service."

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
