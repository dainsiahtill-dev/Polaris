# Tool Normalization 收敛 — 3人团队执行计划

**版本**: v1.0
**日期**: 2026-03-30
**执行团队**: 3 名高级 Python 工程师
**蓝图文档**: `docs/blueprints/TOOL_NORMALIZATION_CONVERGENCE_BLUEPRINT_20260330.md`

---

## 执行摘要

| 工程师 | Phase | 职责 | 核心文件 | 预估规模 |
|--------|-------|------|---------|---------|
| Engineer A | Phase 0 + Phase 3 | 死代码清理 + CI门禁 | `tool_normalization.py`, `normalizer.py`, `gate.py` | ~100行删除 + ~80行新增 |
| Engineer B | Phase 1 | 自动生成机制 | `generator.py`, `normalizers/__init__.py` | ~120行新增 |
| Engineer C | Phase 2 | 补齐所有缺失 normalizer | `_*.py` × 15+ | ~400行新增 + ~200行测试 |

---

## Phase 0: 死代码清理

**Engineer A 专属**，可与 Phase 1/2 并行（但建议先完成，避免冲突）

### 步骤 0.1：确认引用关系

```bash
# 统计所有 import 旧文件的代码
grep -r "from polaris.kernelone.llm.toolkit import tool_normalization" polaris/ --include="*.py"
grep -r "from polaris.kernelone.llm.toolkit.tool_normalization import" polaris/ --include="*.py"
grep -r "from polaris.kernelone.llm.tools import normalizer" polaris/ --include="*.py"
grep -r "from polaris.kernelone.llm.tools.normalizer import" polaris/ --include="*.py"
```

### 步骤 0.2：删除 `polaris/kernelone/llm/toolkit/tool_normalization.py`

该文件是旧单体（1089行），已被 `normalizers/` 包替代。确认无任何 import 后删除。

### 步骤 0.3：删除 `polaris/kernelone/llm/tools/normalizer.py`

该文件（98行）只是转发到 `polaris.kernelone.llm.toolkit.tool_normalization`，无独立逻辑。确认无 import 后删除。

### 步骤 0.4：更新 `polaris/kernelone/llm/tools/contracts.py` 废弃注释

确保所有 re-export 有明确的 "DEPRECATED - use polaris.kernelone.llm.contracts.tool" 注释。

### 步骤 0.5：运行回归

```bash
python -c "from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments; print('OK')"
python -c "from polaris.kernelone.llm.tools.contracts import ToolCall, ToolPolicy; print('OK')"
```

---

## Phase 1: 自动生成机制

**Engineer B 专属**

### 步骤 1.1：创建 `polaris/kernelone/llm/toolkit/tool_normalization/generator.py`

**文件**: `polaris/kernelone/llm/toolkit/tool_normalization/generator.py`

**代码骨架**：

```python
"""从 contracts.py 自动生成 normalizer 函数的代码生成器。"""

from __future__ import annotations

from typing import Any


def _to_python_type(t: str) -> str:
    return {"string": "str", "integer": "int", "boolean": "bool"}.get(t, "Any")


def generate_normalizer_signature(tool_name: str, spec: dict[str, Any]) -> str:
    """生成 normalizer 函数签名。"""
    args_def = ", ".join(
        f"{a['name']}: {a.get('type', 'str')}" for a in spec.get("arguments", [])
    )
    return f"def normalize_{tool_name}_args({args_def}) -> dict[str, Any]:"


def generate_arg_normalization(spec: dict[str, Any]) -> list[str]:
    """从 arg_aliases 生成参数归一化代码。"""
    lines = []
    for param in spec.get("arguments", []):
        name = param["name"]
        aliases = [
            k for k, v in spec.get("arg_aliases", {}).items()
            if v == name and k != name
        ]
        if not aliases:
            continue
        lines.append(
            f"    if not normalized.get('{name}'):"
            f"\n        for alias in ({', '.join(repr(a) for a in aliases)}):"
            f"\n            if alias in normalized:"
            f"\n                normalized['{name}'] = normalized.pop(alias)"
            f"\n                break"
        )
    return lines


def generate_file_path_normalization(spec: dict[str, Any]) -> list[str]:
    """生成文件路径归一化代码（所有含 path/file 参数的工具）。"""
    file_params = [a["name"] for a in spec.get("arguments", [])
                   if a.get("type") == "string" and any(
                       k in a.get("name", "") for k in ["file", "path"]
                   )]
    if not file_params:
        return []
    # ... 实现路径归一化逻辑
    return []


def generate_normalizer_code(tool_name: str, spec: dict[str, Any]) -> str:
    """为单个工具生成完整 normalizer 代码。"""
    lines = [
        f'"""Normalizer for {tool_name} tool."""',
        f"",
        f"from __future__ import annotations",
        f"",
        f"from typing import Any",
        f"",
        f"",
        f"def normalize_{tool_name}_args(tool_args: dict[str, Any]) -> dict[str, Any]:",
        f'    """Normalize {tool_name} arguments."""',
        f"    normalized = dict(tool_args)",
        f"",
    ]
    lines.extend(generate_file_path_normalization(spec))
    lines.extend(generate_arg_normalization(spec))
    lines.append("    return normalized")
    return "\n".join(lines)


def generate_all_normalizers() -> dict[str, str]:
    """为所有 contracts.py 中的工具生成 normalizer 代码。"""
    from polaris.kernelone.tools.contracts import _TOOL_SPECS
    return {
        name: generate_normalizer_code(name, spec)
        for name, spec in _TOOL_SPECS.items()
        if spec.get("category") != "internal"
    }


# CLI 入口
if __name__ == "__main__":
    import sys
    generated = generate_all_normalizers()
    for tool, code in generated.items():
        print(f"# === {tool} ===")
        print(code)
        print()
```

### 步骤 1.2：更新 `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/__init__.py`

**在 `TOOL_NORMALIZERS` 定义后添加断言检查**：

```python
# 自动验证：每个 TOOL_NORMALIZERS 条目的参数签名与 contracts.py 一致
def _assert_contracts_sync():
    from polaris.kernelone.tools.contracts import _TOOL_SPECS
    for tool_name, normalizer in TOOL_NORMALIZERS.items():
        if tool_name not in _TOOL_SPECS:
            raise AssertionError(
                f"TOOL_NORMALIZERS has '{tool_name}' but contracts.py has no such tool"
            )
        spec = _TOOL_SPECS[tool_name]
        required = {a["name"] for a in spec.get("arguments", []) if a.get("required")}
        # 可选：验证 normalizer 是否覆盖了所有 required 参数的别名
_assert_contracts_sync()
```

### 步骤 1.3：验证 Phase 1

```bash
python -c "
from polaris.kernelone.llm.toolkit.tool_normalization.generator import generate_all_normalizers, generate_normalizer_code
from polaris.kernelone.tools.contracts import _TOOL_SPECS

# 验证 precision_edit 生成的代码
code = generate_normalizer_code('precision_edit', _TOOL_SPECS['precision_edit'])
print(code[:500])
"
```

---

## Phase 2: 补齐所有缺失 normalizer

**Engineer C 专属**

### 批次分配

#### Batch A（最高优先，LLM 高频使用）

| 工具 | 文件 | 核心别名 |
|------|------|---------|
| `precision_edit` | `_precision_edit.py` | `query`/`find`/`text` → `search`; `replacement`/`with` → `replace` |
| `repo_apply_diff` | `_repo_apply_diff.py` | `patch` → `diff` |
| `repo_tree` | `_repo_tree.py` | `root` → `path`; `depth` → `max_entries` |
| `repo_read_around` | `_repo_read_around.py` | `target` → `file`; `before_lines`/`after_lines` |
| `repo_read_slice` | `_repo_read_slice.py` | `target` → `file`; `start`/`end` |

#### Batch B（次高优先，repo read 系列）

| 工具 | 文件 |
|------|------|
| `repo_read_head` | `_repo_read_head.py` |
| `repo_read_tail` | `_repo_read_tail.py` |
| `repo_diff` | `_repo_diff.py` |
| `repo_map` | `_repo_map.py` |
| `repo_symbols_index` | `_repo_symbols_index.py` |

#### Batch C（低优先，background/todo/task/treesitter）

| 工具 | 文件 |
|------|------|
| `background_run` | `_background_run.py` |
| `background_check` | `_background_check.py` |
| `background_list` | `_background_list.py` |
| `background_cancel` | `_background_cancel.py` |
| `background_wait` | `_background_wait.py` |
| `todo_read` | `_todo_read.py` |
| `todo_write` | `_todo_write.py` |
| `task_create` | `_task_create.py` |
| `task_update` | `_task_update.py` |
| `task_ready` | `_task_ready.py` |
| `compact_context` | `_compact_context.py` |
| `treesitter_find_symbol` | `_treesitter_find_symbol.py` |
| `treesitter_replace_node` | `_treesitter_replace_node.py` |
| `treesitter_insert_method` | `_treesitter_insert_method.py` |
| `treesitter_rename_symbol` | `_treesitter_rename_symbol.py` |

### 步骤 2.1：实现 Batch A（每个文件 ~40-60 行）

每个 normalizer 参考 `_search_replace.py` 的结构：

```python
"""Normalizer for precision_edit tool."""

from __future__ import annotations

from typing import Any

from ._shared import _normalize_workspace_alias_path


def normalize_precision_edit_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize precision_edit arguments."""
    normalized = dict(tool_args)

    # file path normalization
    if not normalized.get("file"):
        for alias in ("path", "filepath", "file_path", "target"):
            candidate = normalized.get(alias)
            if isinstance(candidate, str) and candidate.strip():
                normalized["file"] = _normalize_workspace_alias_path(candidate.strip())
                break

    # search parameter alias
    if not normalized.get("search"):
        for alias in ("query", "find", "text", "pattern"):
            candidate = normalized.get(alias)
            if isinstance(candidate, str):
                normalized["search"] = candidate
                normalized.pop(alias, None)
                break

    # replace parameter alias
    if not normalized.get("replace"):
        for alias in ("replacement", "with", "to"):
            if alias in normalized:
                normalized["replace"] = normalized.pop(alias)
                break

    # cleanup path aliases
    for alias in ("path", "filepath", "file_path", "target"):
        normalized.pop(alias, None)

    return normalized
```

### 步骤 2.2：实现 Batch B 和 Batch C

使用 Phase 1 的 generator.py 生成骨架，人工补充业务逻辑：

```bash
# 生成所有 normalizer 骨架
python polaris/kernelone/llm/toolkit/tool_normalization/generator.py > /tmp/generated_normalizers.txt
```

### 步骤 2.3：更新 `polaris/kernelone/llm/toolkit/tool_normalization/normalizers/__init__.py`

注册所有新增 normalizer：

```python
from ._precision_edit import normalize_precision_edit_args
from ._repo_apply_diff import normalize_repo_apply_diff_args
# ... 其他导入

TOOL_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    # ... 原有 14 个 ...
    "precision_edit": normalize_precision_edit_args,
    "repo_apply_diff": normalize_repo_apply_diff_args,
    # ... Batch B 和 C ...
}
```

### 步骤 2.4：单元测试

每个 normalizer 需要至少一个测试文件：

```python
# polaris/kernelone/llm/toolkit/tool_normalization/normalizers/tests/test_precision_edit.py
import pytest
from polaris.kernelone.llm.toolkit.tool_normalization.normalizers._precision_edit import (
    normalize_precision_edit_args,
)

def test_query_alias_maps_to_search():
    result = normalize_precision_edit_args({
        "file": "test.py",
        "query": "old text",
        "replace": "new text",
    })
    assert result["search"] == "old text"
    assert result["query"] not in result

def test_find_alias_maps_to_search():
    result = normalize_precision_edit_args({
        "file": "test.py",
        "find": "old text",
        "replace": "new text",
    })
    assert result["search"] == "old text"

def test_replacement_alias_maps_to_replace():
    result = normalize_precision_edit_args({
        "file": "test.py",
        "search": "old",
        "replacement": "new",
    })
    assert result["replace"] == "new"

def test_path_alias_maps_to_file():
    result = normalize_precision_edit_args({
        "path": "/workspace/test.py",
        "search": "old",
        "replace": "new",
    })
    assert result["file"] == "test.py"
```

### 步骤 2.5：验证 Phase 2

```bash
python -c "
from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments
from polaris.kernelone.tools.contracts import _TOOL_SPECS

missing = []
for tool_name in _TOOL_SPECS:
    result = normalize_tool_arguments(tool_name, {'test': 'value'})
    # 验证不报错即可

print(f'normalize_tool_arguments covers {len(_TOOL_SPECS)} tools without errors')

# 重点验证 precision_edit
result = normalize_tool_arguments('precision_edit', {'file': 'a.py', 'query': 'x', 'replace': 'y'})
assert result.get('search') == 'x', f'query not normalized: {result}'
print('precision_edit: query alias → search ✓')

result = normalize_tool_arguments('precision_edit', {'file': 'a.py', 'find': 'x', 'replacement': 'y'})
assert result.get('search') == 'x', f'find not normalized: {result}'
assert result.get('replace') == 'y', f'replacement not normalized: {result}'
print('precision_edit: find + replacement alias ✓')
"
```

---

## Phase 3: CI 门禁

**Engineer A + Engineer B 协作**

### 步骤 3.1：创建门禁脚本

**文件**: `docs/governance/ci/scripts/run_tool_normalization_gate.py`

```python
#!/usr/bin/env python3
"""Tool Normalization 同步门禁。

检查 contracts.py 与 TOOL_NORMALIZERS 是否同步。
用法:
    python run_tool_normalization_gate.py --workspace . --mode sync-check
"""

import argparse
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", default=".", help="仓库根目录")
    parser.add_argument("--mode", default="sync-check", choices=["sync-check", "generate"])
    parser.add_argument("--report", help="输出报告路径")
    args = parser.parse_args()

    sys.path.insert(0, str(Path(args.workspace) / "src" / "backend"))

    from polaris.kernelone.tools.contracts import _TOOL_SPECS
    from polaris.kernelone.llm.toolkit.tool_normalization.normalizers import TOOL_NORMALIZERS

    # 1. 检查 contracts 中每个工具是否在 TOOL_NORMALIZERS 中注册
    registered = set(TOOL_NORMALIZERS.keys())
    declared = set(_TOOL_SPECS.keys())

    missing = declared - registered
    orphaned = registered - declared

    print(f"contracts.py 声明工具数: {len(declared)}")
    print(f"TOOL_NORMALIZERS 注册数: {len(registered)}")

    if missing:
        print(f"\n[FAIL] 工具在 contracts.py 中声明但未注册 normalizer:")
        for t in sorted(missing):
            print(f"  - {t}")
    else:
        print("\n[PASS] 所有 contracts.py 工具均有 normalizer 注册")

    if orphaned:
        print(f"\n[WARN] normalizer 已注册但 contracts.py 中无声明:")
        for t in sorted(orphaned):
            print(f"  - {t}")

    # 2. 检查每个 normalizer 的 arg_aliases 是否与 contracts.py 一致
    print("\n正在检查 arg_aliases 同步...")
    issues = []
    for tool_name, spec in _TOOL_SPECS.items():
        if tool_name not in TOOL_NORMALIZERS:
            continue
        normalizer = TOOL_NORMALIZERS[tool_name]
        # 验证 normalizer 函数是否处理了 contracts 中声明的别名
        # (通过调用并检查输出是否包含规范参数名)
        test_args = {
            alias: "test_value"
            for alias, canon in spec.get("arg_aliases", {}).items()
            if alias != canon
        }
        if not test_args:
            continue
        try:
            result = normalizer(test_args)
            for alias, canon in spec.get("arg_aliases", {}).items():
                if alias == canon:
                    continue
                if alias in result:
                    issues.append(f"  {tool_name}: alias '{alias}' not consumed, still in result")
        except Exception as e:
            issues.append(f"  {tool_name}: normalizer raised {e}")

    if issues:
        print(f"\n[FAIL] arg_aliases 处理异常:")
        for issue in issues:
            print(issue)
    else:
        print("[PASS] 所有 arg_aliases 均被正常消费")

    # 返回码：0=通过, 1=失败
    has_fail = bool(missing) or bool(issues)
    sys.exit(1 if has_fail else 0)

if __name__ == "__main__":
    main()
```

### 步骤 3.2：注册 fitness rule

**文件**: `docs/governance/ci/fitness-rules.yaml`

新增规则：

```yaml
- id: tool-normalization-sync
  description: "所有 contracts.py 声明的工具必须在 TOOL_NORMALIZERS 中注册"
  severity: blocker
  phase: pre-commit
  command: python docs/governance/ci/scripts/run_tool_normalization_gate.py --workspace . --mode sync-check
```

### 步骤 3.3：验证 Phase 3

```bash
python docs/governance/ci/scripts/run_tool_normalization_gate.py --workspace . --mode sync-check
# 期望输出: [PASS] 所有 contracts.py 工具均有 normalizer 注册
```

---

## 团队协作约定

### PR 提交流程

1. **Engineer A**: 先提 Phase 0（死代码清理），不依赖其他 Phase
2. **Engineer B**: Phase 1（generator），依赖 Phase 0 merge 后
3. **Engineer C**: Phase 2（Batch A → B → C），依赖 Phase 0 merge 后，可与 Phase 1 并行
4. **Engineer A + B**: Phase 3（gate），依赖 Phase 1 merge 后

### 代码风格

- 所有新增文件通过 `ruff check . --fix` 和 `ruff format .`
- 每个 normalizer 必须有对应测试（`pytest -v` 100% 通过）
- 禁止 `# type: ignore` 掩盖类型冲突

### 每日同步

每天 10:00 UTC+8 通过内部频道同步进度，blocker 立即升级。

---

## 预估工作量

| Phase | 工程师 | 规模 | 预估时间 |
|-------|--------|------|---------|
| Phase 0 | Engineer A | ~100行删除 + 确认 | 0.5 人天 |
| Phase 1 | Engineer B | ~120行新增 | 1 人天 |
| Phase 2 | Engineer C | ~600行新增 + ~200行测试 | 2 人天 |
| Phase 3 | Engineer A+B | ~80行新增 + CI配置 | 0.5 人天 |
| **合计** | | **~1100行** | **4 人天** |
