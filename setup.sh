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

load_env_file "${ROOT_DIR}/.env"

PYTHON="${PYTHON:-python3}"
if [[ -z "$USER_DOCKER_COMPOSE_SET" ]]; then
  DOCKER_COMPOSE="sudo docker compose"
else
  DOCKER_COMPOSE="${DOCKER_COMPOSE:-sudo docker compose}"
fi
DATABASE_URL="${DATABASE_URL:-postgresql://scicommons:scicommons@localhost:5432/scicommons}"
START_DB="${START_DB:-1}"
KILL_PORTS="${KILL_PORTS:-1}"
RESET_USER_DB="${RESET_USER_DB:-1}"
RUN_PIPELINE="${RUN_PIPELINE:-0}"
INSTALL_ARTICLE_CRON="${INSTALL_ARTICLE_CRON:-1}"
ARTICLE_CRON_SCHEDULE="${ARTICLE_CRON_SCHEDULE:-0 2 * * *}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
ARTICLE_HOST="${ARTICLE_HOST:-0.0.0.0}"
ARTICLE_PORT="${ARTICLE_PORT:-8100}"
FRONTEND_HOST="${FRONTEND_HOST:-0.0.0.0}"
FRONTEND_PORT="${FRONTEND_PORT:-5173}"
SCICOMM_ARTIFACT_DIR="${SCICOMM_ARTIFACT_DIR:-${ROOT_DIR}/scicomm_embedding}"
ARTICLE_SERVICE_BASE_URL="${ARTICLE_SERVICE_BASE_URL:-http://localhost:${ARTICLE_PORT}}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:${BACKEND_PORT}}"
SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
NCBI_EMAIL="${NCBI_EMAIL:-you@example.com}"
NCBI_TOOL="${NCBI_TOOL:-scicommons-monorepo}"
NCBI_API_KEY="${NCBI_API_KEY:-}"
EDIRECT_PREFIX="${EDIRECT_PREFIX:-}"
OPENREVIEW_USERNAME="${OPENREVIEW_USERNAME:-}"
OPENREVIEW_PASSWORD="${OPENREVIEW_PASSWORD:-}"
INSTALL_CPU_TORCH="${INSTALL_CPU_TORCH:-1}"
TORCH_CPU_INDEX_URL="${TORCH_CPU_INDEX_URL:-https://download.pytorch.org/whl/cpu}"

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

install_python_requirements() {
  local name="$1"
  local dir="$2"
  local requirements="$3"

  echo "==> Setting up ${name} Python environment"
  "$PYTHON" -m venv "${dir}/.venv"
  "${dir}/.venv/bin/python" -m pip install --upgrade pip
  "${dir}/.venv/bin/python" -m pip install -r "$requirements"
}

install_article_requirements() {
  local dir="$1"
  local requirements="$2"

  echo "==> Setting up article pipeline/search Python environment"
  "$PYTHON" -m venv "${dir}/.venv"
  "${dir}/.venv/bin/python" -m pip install --upgrade pip
  if [[ "$INSTALL_CPU_TORCH" == "1" ]]; then
    echo "==> Installing CPU-only torch before article dependencies"
    "${dir}/.venv/bin/python" -m pip install --index-url "$TORCH_CPU_INDEX_URL" torch
  fi
  "${dir}/.venv/bin/python" -m pip install -r "$requirements"
}

start_postgres() {
  # DOCKER_COMPOSE may intentionally contain spaces, for example: "sudo docker compose".
  # shellcheck disable=SC2086
  $DOCKER_COMPOSE up -d db
}

install_article_cron() {
  if [[ "$INSTALL_ARTICLE_CRON" != "1" ]]; then
    echo "==> Skipping nightly article cron. Set INSTALL_ARTICLE_CRON=1 to install it."
    return
  fi
  if ! command -v crontab >/dev/null 2>&1; then
    echo "==> crontab is not available; skipping nightly article cron." >&2
    return
  fi

  local marker_start="# scicommons nightly article pipeline start"
  local marker_end="# scicommons nightly article pipeline end"
  local command="cd $(shell_quote "$ROOT_DIR") && ./scripts/nightly_article_pipeline.sh"
  local tmp
  tmp="$(mktemp)"

  (crontab -l 2>/dev/null || true) | awk \
    -v start="$marker_start" \
    -v end="$marker_end" \
    'BEGIN {skip=0} $0 == start {skip=1; next} $0 == end {skip=0; next} skip == 0 {print}' > "$tmp"
  {
    echo "$marker_start"
    echo "${ARTICLE_CRON_SCHEDULE} ${command}"
    echo "$marker_end"
  } >> "$tmp"
  crontab "$tmp"
  rm -f "$tmp"
  echo "==> Installed nightly article cron: ${ARTICLE_CRON_SCHEDULE} ${command}"
}

write_env_file() {
  local path="$1"
  local content="$2"

  if [[ ! -f "$path" || "${OVERWRITE_ENV:-0}" == "1" ]]; then
    echo "==> Writing ${path#${ROOT_DIR}/}"
    printf '%s\n' "$content" > "$path"
  else
    echo "==> Keeping existing ${path#${ROOT_DIR}/}"
  fi
}

shell_quote() {
  printf '%q' "$1"
}

require_command "$PYTHON"
require_command npm

write_env_file "${ROOT_DIR}/.env" "PYTHON=$(shell_quote "$PYTHON")
DOCKER_COMPOSE=$(shell_quote "$DOCKER_COMPOSE")
DATABASE_URL=$(shell_quote "$DATABASE_URL")
START_DB=$(shell_quote "$START_DB")
KILL_PORTS=$(shell_quote "$KILL_PORTS")
RESET_USER_DB=$(shell_quote "$RESET_USER_DB")
RUN_PIPELINE=$(shell_quote "$RUN_PIPELINE")
INSTALL_ARTICLE_CRON=$(shell_quote "$INSTALL_ARTICLE_CRON")
ARTICLE_CRON_SCHEDULE=$(shell_quote "$ARTICLE_CRON_SCHEDULE")
BACKEND_HOST=$(shell_quote "$BACKEND_HOST")
BACKEND_PORT=$(shell_quote "$BACKEND_PORT")
ARTICLE_HOST=$(shell_quote "$ARTICLE_HOST")
ARTICLE_PORT=$(shell_quote "$ARTICLE_PORT")
FRONTEND_HOST=$(shell_quote "$FRONTEND_HOST")
FRONTEND_PORT=$(shell_quote "$FRONTEND_PORT")
SCICOMM_ARTIFACT_DIR=$(shell_quote "$SCICOMM_ARTIFACT_DIR")
ARTICLE_SERVICE_BASE_URL=$(shell_quote "$ARTICLE_SERVICE_BASE_URL")
VITE_API_BASE_URL=$(shell_quote "$VITE_API_BASE_URL")
SESSION_COOKIE_SECURE=$(shell_quote "$SESSION_COOKIE_SECURE")
NCBI_EMAIL=$(shell_quote "$NCBI_EMAIL")
NCBI_TOOL=$(shell_quote "$NCBI_TOOL")
NCBI_API_KEY=$(shell_quote "$NCBI_API_KEY")
EDIRECT_PREFIX=$(shell_quote "$EDIRECT_PREFIX")
OPENREVIEW_USERNAME=$(shell_quote "$OPENREVIEW_USERNAME")
OPENREVIEW_PASSWORD=$(shell_quote "$OPENREVIEW_PASSWORD")
INSTALL_CPU_TORCH=$(shell_quote "$INSTALL_CPU_TORCH")
TORCH_CPU_INDEX_URL=$(shell_quote "$TORCH_CPU_INDEX_URL")"

write_env_file "${ROOT_DIR}/backend/.env" "DATABASE_URL=${DATABASE_URL}
ARTICLE_SERVICE_BASE_URL=${ARTICLE_SERVICE_BASE_URL}
SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE}"

write_env_file "${ROOT_DIR}/scicomm_embedding/.env" "SCICOMM_ARTIFACT_DIR=${SCICOMM_ARTIFACT_DIR}
NCBI_EMAIL=${NCBI_EMAIL}
NCBI_TOOL=${NCBI_TOOL}
NCBI_API_KEY=${NCBI_API_KEY}
EDIRECT_PREFIX=${EDIRECT_PREFIX}
OPENREVIEW_USERNAME=${OPENREVIEW_USERNAME}
OPENREVIEW_PASSWORD=${OPENREVIEW_PASSWORD}"

write_env_file "${ROOT_DIR}/igather2/.env" "NCBI_EMAIL=${NCBI_EMAIL}
NCBI_TOOL=${NCBI_TOOL}
NCBI_API_KEY=${NCBI_API_KEY}
EDIRECT_PREFIX=${EDIRECT_PREFIX}"

install_python_requirements "backend" "${ROOT_DIR}/backend" "${ROOT_DIR}/backend/requirements.txt"
install_article_requirements "${ROOT_DIR}/scicomm_embedding" "${ROOT_DIR}/scicomm_embedding/requirements.txt"

echo "==> Setting up igather2 Python environment"
"$PYTHON" -m venv "${ROOT_DIR}/igather2/.venv"
"${ROOT_DIR}/igather2/.venv/bin/python" -m pip install --upgrade pip
"${ROOT_DIR}/igather2/.venv/bin/python" -m pip install -e "${ROOT_DIR}/igather2"

echo "==> Installing frontend dependencies"
(cd "${ROOT_DIR}/frontend" && npm install)

if [[ ! -f "${ROOT_DIR}/frontend/.env" || "${OVERWRITE_FRONTEND_ENV:-0}" == "1" || "${OVERWRITE_ENV:-0}" == "1" ]]; then
  echo "==> Writing frontend/.env"
  printf 'VITE_API_BASE_URL=%s\n' "$VITE_API_BASE_URL" > "${ROOT_DIR}/frontend/.env"
else
  echo "==> Keeping existing frontend/.env"
fi

if [[ "$START_DB" == "1" ]]; then
  echo "==> Starting Postgres"
  start_postgres
fi

if [[ "$RESET_USER_DB" == "1" ]]; then
  echo "==> Recreating and seeding user database"
  (
    cd "${ROOT_DIR}/backend"
    DATABASE_URL="$DATABASE_URL" ./.venv/bin/python setup_database.py
  )
fi

if [[ "$RUN_PIPELINE" == "1" ]]; then
  echo "==> Running article pipeline"
  (
    cd "${ROOT_DIR}/scicomm_embedding"
    source .venv/bin/activate
    python pipeline.py
  )
else
  echo "==> Skipping article pipeline. Set RUN_PIPELINE=1 to fetch articles and build search artifacts."
fi

install_article_cron

echo "Setup complete."
