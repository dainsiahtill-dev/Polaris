#!/bin/bash
# Polaris Unix/Linux/macOS 快捷入口
# 用法: ./hp.sh <command> [options]

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/src/backend/scripts/pm:${SCRIPT_DIR}/src/backend:${SCRIPT_DIR}:${PYTHONPATH}"

python3 "${SCRIPT_DIR}/polaris.py" "$@"
