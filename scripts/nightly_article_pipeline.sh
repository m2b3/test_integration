#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

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
load_env_file "${ROOT_DIR}/scicomm_embedding/.env"

LOG_DIR="${ROOT_DIR}/scicomm_embedding/logs"
mkdir -p "$LOG_DIR"

{
  echo "==> $(date -Is) Starting nightly article pipeline"
  cd "${ROOT_DIR}/scicomm_embedding"
  source .venv/bin/activate
  python pipeline.py
  echo "==> $(date -Is) Finished nightly article pipeline"
} >> "${LOG_DIR}/nightly_pipeline.log" 2>&1
