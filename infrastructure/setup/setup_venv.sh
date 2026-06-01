#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
VENV_DIR="${REPO_ROOT}/.venv"
VENV_PYTHON="${VENV_DIR}/bin/python"
SERVICE_REQUIREMENTS="${REPO_ROOT}/src/backend/app/services/requirements.txt"
UV_CACHE_DIR="${UV_CACHE_DIR:-${REPO_ROOT}/.uv_cache}"
export UV_CACHE_DIR

PYTHON_CMD=""
UV_CMD=""

is_python_usable() {
  local candidate="$1"
  [[ -n "${candidate}" ]] || return 1
  command -v "${candidate}" >/dev/null 2>&1 || [[ -x "${candidate}" ]] || return 1
  "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' >/dev/null 2>&1
}

for candidate in "${KERNELONE_BOOTSTRAP_PYTHON:-}" "${PYTHON:-}" python3 python; do
  if is_python_usable "${candidate}"; then
    PYTHON_CMD="${candidate}"
    break
  fi
done

if [[ -z "${PYTHON_CMD}" ]]; then
  echo "[setup_venv] ERROR: Python 3.10+ is required but was not found in PATH." >&2
  exit 1
fi

if [[ -e "${VENV_PYTHON}" ]] && ! "${VENV_PYTHON}" -c 'import sys; print(sys.executable)' >/dev/null 2>&1; then
  echo "[setup_venv] Existing virtual environment is invalid; rebuilding: ${VENV_DIR}"
  rm -rf "${VENV_DIR}"
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "[setup_venv] Creating virtual environment: ${VENV_DIR}"
  "${PYTHON_CMD}" -m venv "${VENV_DIR}"
fi

if command -v uv >/dev/null 2>&1; then
  UV_CMD="$(command -v uv)"
  echo "[setup_venv] Using uv installer: ${UV_CMD}"
fi

pip_install() {
  if [[ -n "${UV_CMD}" ]]; then
    "${UV_CMD}" pip install --python "${VENV_PYTHON}" "$@"
  else
    "${VENV_PYTHON}" -m pip install "$@"
  fi
}

pip_check() {
  if [[ -n "${UV_CMD}" ]]; then
    "${UV_CMD}" pip check --python "${VENV_PYTHON}"
  else
    "${VENV_PYTHON}" -m pip check
  fi
}

echo "[setup_venv] Upgrading pip/setuptools/wheel"
pip_install --upgrade pip setuptools wheel

echo "[setup_venv] Installing Polaris package (editable with dev extras)"
pip_install -e "${REPO_ROOT}[dev]"

if [[ -f "${SERVICE_REQUIREMENTS}" ]]; then
  echo "[setup_venv] Installing service requirements"
  pip_install -r "${SERVICE_REQUIREMENTS}"
fi

echo "[setup_venv] Running pip check"
pip_check

echo "[setup_venv] DONE"
echo "[setup_venv] Python: ${VENV_PYTHON}"
