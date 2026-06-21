#!/usr/bin/env bash
# Polaris 一键复测清单
# 用法: bash infrastructure/scripts/retest-checklist.sh [--dry-run]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PASS=0
FAIL=0
GREEN='\033[32m'
RED='\033[31m'
CYAN='\033[36m'
NC='\033[0m'

pass() { PASS=$((PASS + 1)); echo -e "  ${GREEN}✓${NC} $1"; }
fail() { FAIL=$((FAIL + 1)); echo -e "  ${RED}✗${NC} $1"; }

echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  Polaris 一键复测清单${NC}"
echo -e "${CYAN}  环境预检 + 端到端联通核验${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo ""

cd "$REPO_ROOT"

DRY_RUN=false
[[ "${1:-}" == "--dry-run" ]] && DRY_RUN=true

# ── 1. 环境预检 ──
echo -e "${CYAN}[1/5] 环境预检${NC}"

NODE_OK=$(node --version 2>/dev/null || true)
if [[ -n "$NODE_OK" ]]; then pass "Node.js $NODE_OK"; else fail "Node.js 未安装"; fi

NPX_OK=$(npx --version 2>/dev/null || true)
if [[ -n "$NPX_OK" ]]; then pass "npx $NPX_OK"; else fail "npx 未安装"; fi

PLAYWRIGHT_OK=$(npx playwright --version 2>/dev/null || true)
if [[ -n "$PLAYWRIGHT_OK" ]]; then pass "Playwright $PLAYWRIGHT_OK"; else fail "Playwright 未安装"; fi

VENV_PYTHON="$REPO_ROOT/.venv/bin/python"
if [[ -f "$VENV_PYTHON" ]]; then pass "venv Python 可用"; else fail "venv Python 未找到 ($VENV_PYTHON)"; fi

PACKAGE_OK=$(test -f "$REPO_ROOT/package.json" && echo "yes" || echo "")
if [[ -n "$PACKAGE_OK" ]]; then pass "package.json 存在"; else fail "package.json 缺失"; fi

NODE_MODULES_OK=$(test -d "$REPO_ROOT/node_modules" && echo "yes" || echo "")
if [[ -n "$NODE_MODULES_OK" ]]; then pass "node_modules 存在"; else fail "node_modules 缺失 (请运行 npm install)"; fi

# ── 2. 端口可用性 ──
echo ""
echo -e "${CYAN}[2/5] 端口可用性检查${NC}"

BACKEND_PORT=${KERNELONE_BACKEND_PORT:-49977}
RENDERER_PORT=${KERNELONE_RENDERER_PORT:-5173}

check_port_free() {
  local port=$1 name=$2
  if command -v ss &>/dev/null; then
    ! ss -tlnp "sport = :$port" 2>/dev/null | grep -q . && return 0
    return 1
  fi
  if command -v lsof &>/dev/null; then
    ! lsof -iTCP:"$port" -sTCP:LISTEN -t 2>/dev/null | grep -q . && return 0
    return 1
  fi
  return 0
}

# Ports should be free before the test (the e2e test launches its own backend)
if check_port_free "$BACKEND_PORT"; then pass "后端端口 $BACKEND_PORT 空闲"; else fail "后端端口 $BACKEND_PORT 被占用"; fi
if check_port_free "$RENDERER_PORT"; then pass "渲染端口 $RENDERER_PORT 空闲"; else fail "渲染端口 $RENDERER_PORT 被占用"; fi

# ── 3. 前端构建 ──
echo ""
echo -e "${CYAN}[3/5] 前端构建检查${NC}"

DIST_DIR="$REPO_ROOT/src/frontend/dist"
if [[ -d "$DIST_DIR" ]] && [[ -f "$DIST_DIR/index.html" ]]; then
  pass "前端构建产物存在 (dist/index.html)"
else
  echo "  → 构建中..."
  if $DRY_RUN; then
    echo "  (dry-run 跳过构建)"
  else
    npm run build:renderer 2>&1 | tail -1
    if [[ -f "$DIST_DIR/index.html" ]]; then pass "前端构建成功"; else fail "前端构建失败"; fi
  fi
fi

# ── 4. 运行端到端联通核验 ──
echo ""
echo -e "${CYAN}[4/5] 端到端联通核验${NC}"

if $DRY_RUN; then
  echo "  (dry-run 跳过测试)"
else
  node "$SCRIPT_DIR/run-connectivity-verification.mjs" 2>&1
  E2E_EXIT=$?
  if [[ $E2E_EXIT -eq 0 ]]; then
    pass "端到端联通核验通过"
  else
    fail "端到端联通核验失败 (exit=$E2E_EXIT)"
  fi
fi

# ── 5. 结果汇总 ──
echo ""
echo -e "${CYAN}══════════════════════════════════════════════${NC}"
echo -e "${CYAN}  复测结果: ${PASS} 通过 / ${FAIL} 失败${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}"

if $DRY_RUN; then
  echo ""
  echo "一键运行:"
  echo "  bash infrastructure/scripts/retest-checklist.sh"
  echo ""
  echo "直接跑 Playwright spec:"
  echo "  npm run test:e2e:connectivity"
  echo ""
  echo "查看 Playwright 报告:"
  echo "  npx playwright show-report"
fi

if [[ $FAIL -gt 0 ]]; then exit 1; fi
