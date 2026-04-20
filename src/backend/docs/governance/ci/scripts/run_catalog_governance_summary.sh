#!/usr/bin/env bash
set -euo pipefail

workspace="."
mode="audit-only"
top="10"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --workspace)
      workspace="$2"
      shift 2
      ;;
    --mode)
      mode="$2"
      shift 2
      ;;
    --top)
      top="$2"
      shift 2
      ;;
    -h|--help)
      cat <<'EOF'
Usage: run_catalog_governance_summary.sh [--workspace PATH] [--mode audit-only|fail-on-new|hard-fail] [--top N]
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
report_path=""

if command -v python.exe >/dev/null 2>&1; then
  py_cmd=(python.exe)
elif command -v py.exe >/dev/null 2>&1; then
  py_cmd=(py.exe -3)
elif command -v python3 >/dev/null 2>&1; then
  py_cmd=(python3)
elif command -v python >/dev/null 2>&1; then
  py_cmd=(python)
else
  echo "Python interpreter not found. Tried: python.exe, py.exe, python3, python" >&2
  exit 127
fi

cleanup() {
  rm -f "$report_path"
}
trap cleanup EXIT

gate_script="$script_dir/run_catalog_governance_gate.py"
summary_script="$script_dir/summarize_catalog_governance_gate.py"
workspace_arg="$workspace"

if [[ "${py_cmd[0]}" == *.exe ]] && command -v wslpath >/dev/null 2>&1; then
  report_path="$(mktemp "$script_dir/catalog_governance_report.XXXXXX.json")"
  gate_script="$(wslpath -w "$gate_script")"
  summary_script="$(wslpath -w "$summary_script")"
  if [[ "$workspace_arg" == /* ]]; then
    workspace_arg="$(wslpath -w "$workspace_arg")"
  elif [[ "$workspace_arg" == "." ]]; then
    workspace_arg="$(wslpath -w "$(pwd)")"
  fi
  report_arg="$(wslpath -w "$report_path")"
else
  report_path="$(mktemp "${TMPDIR:-/tmp}/catalog_governance_report.XXXXXX.json")"
  report_arg="$report_path"
fi

"${py_cmd[@]}" "$gate_script" --workspace "$workspace_arg" --mode "$mode" --report "$report_arg" >/dev/null
"${py_cmd[@]}" "$summary_script" --input "$report_arg" --top "$top"
