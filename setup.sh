#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
DOCKER_COMPOSE="${DOCKER_COMPOSE:-docker compose}"
DATABASE_URL="${DATABASE_URL:-postgresql://scicommons:scicommons@localhost:5432/scicommons}"
VITE_API_BASE_URL="${VITE_API_BASE_URL:-http://localhost:8000}"
START_DB="${START_DB:-1}"
RESET_USER_DB="${RESET_USER_DB:-1}"
RUN_PIPELINE="${RUN_PIPELINE:-0}"

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

require_command "$PYTHON"
require_command npm

install_python_requirements "backend" "${ROOT_DIR}/backend" "${ROOT_DIR}/backend/requirements.txt"
install_python_requirements "article pipeline/search" "${ROOT_DIR}/scicomm_embedding" "${ROOT_DIR}/scicomm_embedding/requirements.txt"

echo "==> Setting up igather2 Python environment"
"$PYTHON" -m venv "${ROOT_DIR}/igather2/.venv"
"${ROOT_DIR}/igather2/.venv/bin/python" -m pip install --upgrade pip
"${ROOT_DIR}/igather2/.venv/bin/python" -m pip install -e "${ROOT_DIR}/igather2"

echo "==> Installing frontend dependencies"
(cd "${ROOT_DIR}/frontend" && npm install)

if [[ ! -f "${ROOT_DIR}/frontend/.env" || "${OVERWRITE_FRONTEND_ENV:-0}" == "1" ]]; then
  echo "==> Writing frontend/.env"
  printf 'VITE_API_BASE_URL=%s\n' "$VITE_API_BASE_URL" > "${ROOT_DIR}/frontend/.env"
else
  echo "==> Keeping existing frontend/.env"
fi

if [[ "$START_DB" == "1" ]]; then
  echo "==> Starting Postgres"
  # DOCKER_COMPOSE may intentionally contain spaces, for example: "sudo docker compose".
  # shellcheck disable=SC2086
  $DOCKER_COMPOSE up -d db
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

echo "Setup complete."
