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

shell_quote() {
  printf '%q' "$1"
}

require_command() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
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

install_python_requirements() {
  local name="$1"
  local dir="$2"
  local requirements="$3"

  echo "==> Setting up ${name} Python environment"
  "$PYTHON" -m venv "${dir}/.venv"
  "${dir}/.venv/bin/python" -m pip install --upgrade pip
  "${dir}/.venv/bin/python" -m pip install -r "$requirements"
}

start_postgres() {
  # DOCKER_COMPOSE may intentionally contain spaces, for example: "sudo docker compose".
  # shellcheck disable=SC2086
  $DOCKER_COMPOSE up -d db
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
RESET_USER_DB="${RESET_USER_DB:-1}"
RUN_MIGRATIONS="${RUN_MIGRATIONS:-1}"
BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_URL="${FRONTEND_URL:-http://134.87.8.193:5173}"
ARTICLE_SERVICE_BASE_URL="${ARTICLE_SERVICE_BASE_URL:-http://134.87.9.167:8100}"
CORS_ORIGINS="${CORS_ORIGINS:-${FRONTEND_URL},http://localhost:5173,http://localhost:5174,http://127.0.0.1:5173,http://127.0.0.1:5174}"
SESSION_COOKIE_SECURE="${SESSION_COOKIE_SECURE:-false}"
INTERNAL_API_TOKEN="${INTERNAL_API_TOKEN:-}"

require_command "$PYTHON"

if [[ -z "$INTERNAL_API_TOKEN" ]]; then
  INTERNAL_API_TOKEN="$("$PYTHON" -c 'import secrets; print(secrets.token_urlsafe(32))')"
fi

write_env_file "${ROOT_DIR}/.env" "PYTHON=$(shell_quote "$PYTHON")
DOCKER_COMPOSE=$(shell_quote "$DOCKER_COMPOSE")
DATABASE_URL=$(shell_quote "$DATABASE_URL")
START_DB=$(shell_quote "$START_DB")
RESET_USER_DB=$(shell_quote "$RESET_USER_DB")
RUN_MIGRATIONS=$(shell_quote "$RUN_MIGRATIONS")
BACKEND_HOST=$(shell_quote "$BACKEND_HOST")
BACKEND_PORT=$(shell_quote "$BACKEND_PORT")
FRONTEND_URL=$(shell_quote "$FRONTEND_URL")
ARTICLE_SERVICE_BASE_URL=$(shell_quote "$ARTICLE_SERVICE_BASE_URL")
CORS_ORIGINS=$(shell_quote "$CORS_ORIGINS")
SESSION_COOKIE_SECURE=$(shell_quote "$SESSION_COOKIE_SECURE")
INTERNAL_API_TOKEN=$(shell_quote "$INTERNAL_API_TOKEN")"

write_env_file "${ROOT_DIR}/backend/.env" "DATABASE_URL=${DATABASE_URL}
ARTICLE_SERVICE_BASE_URL=${ARTICLE_SERVICE_BASE_URL}
CORS_ORIGINS=${CORS_ORIGINS}
SESSION_COOKIE_SECURE=${SESSION_COOKIE_SECURE}
INTERNAL_API_TOKEN=${INTERNAL_API_TOKEN}"

install_python_requirements "backend" "${ROOT_DIR}/backend" "${ROOT_DIR}/backend/requirements.txt"

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
elif [[ "$RUN_MIGRATIONS" == "1" ]]; then
  echo "==> Applying non-destructive user database migrations"
  (
    cd "${ROOT_DIR}/backend"
    DATABASE_URL="$DATABASE_URL" ./.venv/bin/python migrate_database.py
  )
fi

echo "Setup complete."
echo "Backend URL: http://localhost:${BACKEND_PORT}"
echo "Frontend origin allowed by default: ${FRONTEND_URL}"
echo "GPU article service: ${ARTICLE_SERVICE_BASE_URL}"
echo "Internal feed refresh token is in .env as INTERNAL_API_TOKEN."
