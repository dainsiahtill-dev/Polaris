#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
SERVICE_REQUIREMENTS="${REPO_ROOT}/src/backend/app/services/requirements.txt"

if command -v python3 >/dev/null 2>&1; then
  PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_CMD="python"
else
  echo "[setup_venv] ERROR: Python 3.10+ is required but was not found in PATH." >&2
  exit 1
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[setup_venv] Creating virtual environment: ${VENV_DIR}"
  "${PYTHON_CMD}" -m venv "${VENV_DIR}"
fi

echo "[setup_venv] Upgrading pip/setuptools/wheel"
"${VENV_PYTHON}" -m pip install --upgrade pip setuptools wheel

echo "[setup_venv] Installing Polaris package (editable)"
"${VENV_PYTHON}" -m pip install -e "${REPO_ROOT}"

if [[ -f "${SERVICE_REQUIREMENTS}" ]]; then
  echo "[setup_venv] Installing service requirements"
  "${VENV_PYTHON}" -m pip install -r "${SERVICE_REQUIREMENTS}"
fi

echo "[setup_venv] Running pip check"
"${VENV_PYTHON}" -m pip check

echo "[setup_venv] DONE"
echo "[setup_venv] Python: ${VENV_PYTHON}"
