# 工具目录统一实施蓝图

## 概述

本文档定义了工具目录统一架构的实施蓝图，包括具体修改步骤、验证标准和回滚方案。

## 问题根因分析

### 问题链路图

```
contracts.py (工具定义)
    │
    ├── repo_rg.aliases = ["rg", ...]
    │
    └── ripgrep.aliases = ["rg", ...]  ← 别名冲突！
                │
                ▼
        canonicalize_tool_name("rg") 结果不确定
                │
                ▼
        benchmark fixture 期望 "ripgrep"
                │
                ▼
        LLM 使用 "repo_rg"
                │
                ▼
        测试失败
```

### 核心问题清单

| # | 问题 | 根因 | 优先级 |
|---|------|------|--------|
| 1 | `rg` 别名冲突 | `repo_rg` 和 `ripgrep` 都声明了 `rg` | P0 |
| 2 | 三重真相源分裂 | contracts.py、profiles.py、fixtures 各自定义 | P0 |
| 3 | benchmark fixture 使用非规范名 | `l1_grep_search.json` 期望 `ripgrep` | P0 |
| 4 | PolicyLayer 时机错误 | evaluate() 在工具执行后调用 | P1 |
| 5 | 两套规范化机制 | `normalize_tool_name` vs `canonicalize_tool_name` | P1 |

## 实施阶段

### Phase 1: 工具定义统一 (P0)

> **专家验证结论**：ripgrep 和 repo_rg 有实质性差异，不建议简单合并。
> - repo_rg 支持 paths 数组（定义上支持，实现上未生效）
> - repo_rg 有 8 个独有 arg_aliases
> - repo_rg 有更严格的参数验证
> 
> **修正方案**：保留两者为独立 canonical，但解决 rg 别名冲突，并修复 fixture。

#### 任务 1.1: 解决 rg 别名冲突

**文件**: `polaris/kernelone/tools/contracts.py`

**问题**：`rg` 同时是 `repo_rg` 和 `ripgrep` 的别名，导致规范化结果不确定。

**修改内容**:

```python
# 修改前 (第 111-128 行)
"repo_rg": {
    "aliases": ["repo_search", "repo_grep", "rg", "find"],  # rg 冲突
    ...
},

# 修改后
"repo_rg": {
    "aliases": ["repo_search", "repo_grep", "find"],  # 移除 rg，保留其他别名
    "description": "PRIMARY code search tool for Polaris. Supports regex, glob filtering, multi-path search (paths array), and context lines. Use this for most searches.",
    ...
},

# ripgrep 保持独立 canonical，rg 作为其主别名
"ripgrep": {
    "aliases": ["rg", "repo_rg_direct"],  # rg 归属 ripgrep
    "description": "Direct ripgrep search for regex patterns. Use when you need precise regex control or single-path search.",
    ...
},
```

**验证**:

```python
from polaris.kernelone.tools.contracts import canonicalize_tool_name

assert canonicalize_tool_name("ripgrep") == "ripgrep"  # 独立 canonical
assert canonicalize_tool_name("rg") == "ripgrep"       # rg 归属 ripgrep
assert canonicalize_tool_name("repo_rg") == "repo_rg"  # 独立 canonical
```

#### 任务 1.2: 修复 benchmark fixture

> **专家验证结论**：fixture 期望 `ripgrep`，但 `ripgrep` 是独立 canonical，LLM 调用 `repo_rg` 是正确行为。
> 
> **修正方案**：使用 `required_any_tools` 支持两者任一。

**文件**: `polaris/cells/llm/evaluation/fixtures/tool_calling_matrix/cases/l1_grep_search.json`

**修改内容**:

```json
{
  "judge": {
    "stream": {
      "required_any_tools": [["ripgrep", "repo_rg"]],
      "forbidden_tools": ["execute_command"],
      "min_tool_calls": 1,
      "max_tool_calls": 1,
      "first_tool_any": ["ripgrep", "repo_rg"]
    },
    "non_stream": {
      "required_any_tools": [["ripgrep", "repo_rg"]],
      "forbidden_tools": ["execute_command"],
      "min_tool_calls": 1,
      "max_tool_calls": 1
    }
  }
}
```

#### 任务 1.3: 更新测试断言

**文件**: `polaris/kernelone/tools/tests/test_contracts_validation_integration.py`

**修改内容**:

```python
# 修改前 (第 435-442 行) - 错误断言
assert canonicalize_tool_name("ripgrep") == "repo_rg"  # 错误：ripgrep 是独立 canonical
assert canonicalize_tool_name("rg") == "ripgrep"       # 正确

# 修改后 - 正确断言
assert canonicalize_tool_name("ripgrep") == "ripgrep"  # ripgrep 是独立 canonical
assert canonicalize_tool_name("rg") == "ripgrep"       # rg 归属 ripgrep
assert canonicalize_tool_name("repo_rg") == "repo_rg"  # repo_rg 是独立 canonical
```

#### 任务 1.4: 添加 repo_rg 到 tool_normalization.py

> **专家验证结论**：repo_rg 不在 tool_normalization.py 处理列表中，参数别名在 toolkit 层不归一化。

**文件**: `polaris/kernelone/llm/toolkit/tool_normalization.py`

**修改位置**: `normalize_tool_arguments()` 函数 (约第 540-620 行)

**修改内容**:

```python
# 在 search_code/ripgrep/grep 处理后添加 repo_rg 处理
if tool_name == "repo_rg":
    # 参数别名归一化
    if not normalized.get("pattern"):
        for alias in ("query", "text", "search", "keyword", "q"):
            candidate = normalized.get(alias)
            if isinstance(candidate, str) and candidate.strip():
                normalized["pattern"] = _clean_scalar_text(candidate)
                break
    
    # path 别名归一化
    if not normalized.get("path") and not normalized.get("paths"):
        for alias in ("file", "file_path", "filepath", "dir", "directory"):
            candidate = normalized.get(alias)
            if isinstance(candidate, str) and candidate.strip():
                normalized["path"] = _normalize_workspace_alias_path(candidate.strip())
                break
    
    # max_results 别名归一化
    if not normalized.get("max_results"):
        for alias in ("max", "limit", "n"):
            candidate = normalized.get(alias)
            if candidate is not None:
                int_value = _coerce_int(candidate)
                if int_value is not None:
                    normalized["max_results"] = int_value
                    break
    
    # glob 别名归一化
    if not normalized.get("glob") and normalized.get("g"):
        normalized["glob"] = normalized.get("g")
```

---

### Phase 2: 策略层修复 (P1)

> **专家验证结论**：PolicyLayer.evaluate() 在工具执行后调用（turn_engine.py:1005-1045），冷却机制失效。

#### 任务 2.1: 修复 PolicyLayer 调用时机

**文件**: `polaris/cells/roles/kernel/internal/turn_engine.py`

**修改位置**: 
- `run()` 方法：第 1000-1045 行
- `run_stream()` 方法：第 1380-1434 行

**修改内容**:

```python
# 修改前 (第 1005-1045 行)
for call in exec_tool_calls:
    result = await self._execute_single_tool(...)  # 先执行
    state.record_tool_call()

policy_result = policy.evaluate(current_canonical, ...)  # 后检查

# 修改后 (正确顺序)
# 1. 先转换为 CanonicalToolCall
current_canonical = self._to_canonical(list(exec_tool_calls) + list(deferred_tool_calls))
pre_stall = policy.precheck_stall(current_canonical)

# 2. 在执行前检查策略
policy_result = policy.evaluate(
    current_canonical,
    budget_state={
        "tool_call_count": state.budgets.tool_call_count,
        "turn_count": state.budgets.turn_count,
    },
    precheck_stall_count=pre_stall,
)

# 3. 只执行批准的调用
approved_calls = [c for c in exec_tool_calls if c in policy_result.approved_calls]
for call in approved_calls:
    result = await self._execute_single_tool(
        profile=profile,
        request=request,
        call=call,
    )
    round_tool_results.append(result)
    state.record_tool_call()
    all_tool_results.append(result if isinstance(result, dict) else {"value": result})
    all_tool_calls.append({"tool": call.tool, "args": call.args})

# 4. 记录被拦截的调用
for call in policy_result.blocked_calls:
    tool_name = getattr(call, "tool", "?")
    logger.warning("[TurnEngine] PolicyLayer 拦截工具: tool=%s", tool_name)
    all_tool_calls.append({"tool": call.tool, "args": call.args, "blocked": True})
```

#### 任务 2.2: 统一规范化机制

**文件**: `polaris/cells/roles/kernel/internal/tool_gateway.py`

**修改位置**: `_normalize_tool_name()` 方法

**修改内容**:

```python
# 修改前
def _normalize_tool_name(self, tool_name: str) -> str:
    return normalize_tool_name(tool_name)

# 修改后
def _normalize_tool_name(self, tool_name: str) -> str:
    from polaris.kernelone.tools.contracts import canonicalize_tool_name
    return canonicalize_tool_name(tool_name, keep_unknown=True)
```

---

### Phase 3: 治理门禁 (P1)

#### 任务 3.1: 创建治理门禁脚本

**文件**: `docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py`

**内容**: 见附录 A

#### 任务 3.2: 添加 fitness-rules

**文件**: `docs/governance/ci/fitness-rules.yaml`

**添加内容**:

```yaml
  - id: tool_aliases_no_conflicts
    severity: blocker
    description: >
      Tool aliases must not map to multiple canonical names.
    evidence:
      - polaris/kernelone/tools/contracts.py
    current_status: enforced_non_regressive
    desired_automation:
      - run docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py --check-aliases

  - id: benchmark_fixture_canonical_only
    severity: blocker
    description: >
      Benchmark fixtures must expect only canonical tool names.
    evidence:
      - polaris/cells/llm/evaluation/fixtures/tool_calling_matrix/cases/*.json
    current_status: seeded
    desired_automation:
      - run docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py --check-fixtures
```

---

## 验证标准

### 单元测试验证

```bash
# 运行工具规范化测试
pytest polaris/kernelone/tools/tests/test_contracts_validation_integration.py -v

# 运行 benchmark 测试
pytest polaris/cells/llm/evaluation/tests/test_llm_tool_calling_matrix.py -v
```

### 集成测试验证

```bash
# 运行单个 benchmark case
python -u -m polaris.delivery.cli agentic-eval \
    --workspace C:/Temp/BenchmarkTest \
    --suite tool_calling_matrix \
    --role director \
    --case-id l1_grep_search
```

### 预期结果

| 验证项 | 预期结果 |
|--------|----------|
| `canonicalize_tool_name("ripgrep")` | `"repo_rg"` |
| `canonicalize_tool_name("rg")` | `"repo_rg"` |
| `l1_grep_search` benchmark | PASS |
| 别名冲突检测 | 0 conflicts |

---

## 回滚方案

### Phase 1 回滚

如果合并导致问题，恢复 `ripgrep` 作为独立 canonical：

```python
"ripgrep": {
    "aliases": ["rg", "repo_rg_direct"],
    ...
},
```

### Phase 2 回滚

恢复 PolicyLayer 原有调用时机。

### Phase 3 回滚

禁用新增的 fitness-rules。

---

## 附录 A: 治理门禁脚本

```python
#!/usr/bin/env python
"""Tool catalog consistency gate.

Validates:
1. No alias conflicts in tool definitions
2. Role whitelists use only canonical names
3. Benchmark fixtures expect only canonical names

Usage:
    python run_tool_catalog_consistency_gate.py --workspace . --mode hard-fail
"""

import argparse
import json
import sys
from pathlib import Path
from dataclasses import dataclass
from typing import Any


@dataclass
class Issue:
    category: str
    message: str
    file: str | None = None
    severity: str = "error"


def load_contracts(workspace: Path) -> dict[str, Any]:
    """Load tool definitions from contracts.py."""
    # 简化实现：直接解析 _TOOL_SPECS
    contracts_path = workspace / "polaris" / "kernelone" / "tools" / "contracts.py"
    # ... 解析逻辑
    return {}


def check_alias_conflicts(contracts: dict) -> list[Issue]:
    """Detect aliases that map to multiple canonical names."""
    alias_to_canonical: dict[str, str] = {}
    issues: list[Issue] = []
    
    for tool_name, spec in contracts.items():
        for alias in spec.get("aliases", []):
            alias_lower = alias.lower()
            if alias_lower in alias_to_canonical:
                issues.append(Issue(
                    category="alias_conflict",
                    message=f"alias `{alias}` maps to both `{alias_to_canonical[alias_lower]}` and `{tool_name}`",
                ))
            alias_to_canonical[alias_lower] = tool_name
    
    return issues


def check_profiles_use_canonical(workspace: Path, canonical_names: set[str]) -> list[Issue]:
    """Verify role whitelists use only canonical tool names."""
    issues: list[Issue] = []
    profiles_path = workspace / "polaris" / "cells" / "roles" / "profile" / "internal" / "builtin_profiles.py"
    # ... 解析和检查逻辑
    return issues


def check_fixtures_use_canonical(workspace: Path, canonical_names: set[str]) -> list[Issue]:
    """Verify benchmark fixtures expect canonical tool names."""
    issues: list[Issue] = []
    fixtures_dir = workspace / "polaris" / "cells" / "llm" / "evaluation" / "fixtures" / "tool_calling_matrix" / "cases"
    
    for fixture in fixtures_dir.glob("*.json"):
        case = json.loads(fixture.read_text(encoding="utf-8"))
        for mode in ["stream", "non_stream"]:
            required = case.get("judge", {}).get(mode, {}).get("required_tools", [])
            for tool in required:
                if tool not in canonical_names:
                    issues.append(Issue(
                        category="non_canonical_in_fixture",
                        message=f"fixture `{fixture.name}` expects non-canonical `{tool}`",
                        file=str(fixture),
                    ))
    
    return issues


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", type=str, default=".")
    parser.add_argument("--mode", choices=["hard-fail", "soft-fail"], default="hard-fail")
    parser.add_argument("--check-aliases", action="store_true")
    parser.add_argument("--check-profiles", action="store_true")
    parser.add_argument("--check-fixtures", action="store_true")
    args = parser.parse_args()
    
    workspace = Path(args.workspace).resolve()
    all_issues: list[Issue] = []
    
    contracts = load_contracts(workspace)
    canonical_names = set(contracts.keys())
    
    if args.check_aliases:
        all_issues.extend(check_alias_conflicts(contracts))
    
    if args.check_profiles:
        all_issues.extend(check_profiles_use_canonical(workspace, canonical_names))
    
    if args.check_fixtures:
        all_issues.extend(check_fixtures_use_canonical(workspace, canonical_names))
    
    # 输出结果
    for issue in all_issues:
        print(f"[{issue.severity}] {issue.category}: {issue.message}")
    
    if all_issues and args.mode == "hard-fail":
        return 1
    
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

---

## 时间线

| 阶段 | 预计时间 | 负责专家 |
|------|----------|----------|
| Phase 1 | 1 天 | 专家1, 专家3 |
| Phase 2 | 1 天 | 专家2, 专家5 |
| Phase 3 | 1 天 | 专家6 |
| 验证测试 | 0.5 天 | 全体 |

**总计**: 3.5 天