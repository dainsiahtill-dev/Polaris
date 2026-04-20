"""Tool argument normalizers.

DESIGN PRINCIPLE (2026-04-05):
This package provides per-tool complex transformations that CANNOT be expressed
as simple arg_aliases mappings. Parameter alias mapping is handled by
SchemaDrivenNormalizer via contracts.py arg_aliases.

Two-stage normalization flow:
1. Stage 1 (SchemaDrivenNormalizer): Handle all arg_aliases mappings
2. Stage 2 (TOOL_NORMALIZERS): Handle complex transformations only

Complex transformations that require per-tool normalizers:
- Range parameter conversion: offset/limit -> start_line/end_line (read_file)
- Value range clamping: max_results [1, 10000], context_lines [0, 100] (repo_rg)
- Scope pattern normalization: directory -> directory/**/* (search_code)
- Boolean coercion for options (recursive, case_sensitive)
- Workspace path alias resolution (file path tools)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ._background import normalize_background_check_args, normalize_background_run_args
from ._file_path import normalize_file_path_args
from ._glob import normalize_glob_args
from ._list_directory import normalize_list_directory_args
from ._noop import normalize_noop_args
from ._precision_edit import normalize_precision_edit_args
from ._read_file import normalize_read_file_args
from ._repo_apply_diff import normalize_repo_apply_diff_args
from ._repo_read_around import normalize_repo_read_around_args
from ._repo_read_head_tail import normalize_repo_read_head_tail_args
from ._repo_rg import normalize_repo_rg_args
from ._search_code import normalize_search_code_args
from ._shared import (
    WriteContentNormalization,
    looks_like_patch_like_write_content,
    normalize_patch_like_write_content,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# Registry of normalizers keyed by tool name
# NOTE: grep is aliased to repo_rg and uses normalize_repo_rg_args
# NOTE: list_directory is now a pure alias for repo_tree (via contracts.py aliases)
#       Both use normalize_list_directory_args for path/boolean normalization
TOOL_NORMALIZERS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "read_file": normalize_read_file_args,
    "write_file": normalize_file_path_args,
    "edit_file": normalize_file_path_args,
    "append_to_file": normalize_file_path_args,
    "precision_edit": normalize_precision_edit_args,
    "repo_rg": normalize_repo_rg_args,
    "search_code": normalize_search_code_args,
    "ripgrep": normalize_search_code_args,
    "glob": normalize_glob_args,
    "list_directory": normalize_list_directory_args,  # Legacy: kept for backward compat during migration
    "repo_tree": normalize_list_directory_args,
    "repo_read_head": normalize_repo_read_head_tail_args,
    "repo_read_tail": normalize_repo_read_head_tail_args,
    "repo_read_around": normalize_repo_read_around_args,
    "repo_read_slice": normalize_noop_args,
    "background_run": normalize_background_run_args,
    "background_check": normalize_background_check_args,
    "background_cancel": normalize_noop_args,
    "background_list": normalize_noop_args,
    "background_wait": normalize_noop_args,
    "repo_apply_diff": normalize_repo_apply_diff_args,
    "compact_context": normalize_noop_args,
    "execute_command": normalize_noop_args,
    "file_exists": normalize_noop_args,
    "get_state": normalize_noop_args,
    "load_skill": normalize_noop_args,
    "read_artifact": normalize_noop_args,
    "read_episode": normalize_noop_args,
    "repo_diff": normalize_noop_args,
    "repo_map": normalize_noop_args,
    "repo_symbols_index": normalize_noop_args,
    "search_memory": normalize_noop_args,
    "search_replace": normalize_noop_args,
    "skill_manifest": normalize_noop_args,
    "task_create": normalize_noop_args,
    "task_ready": normalize_noop_args,
    "task_update": normalize_noop_args,
    "todo_read": normalize_noop_args,
    "todo_write": normalize_noop_args,
    "treesitter_find_symbol": normalize_noop_args,
    "treesitter_insert_method": normalize_noop_args,
    "treesitter_rename_symbol": normalize_noop_args,
    "treesitter_replace_node": normalize_noop_args,
}

__all__ = [
    "TOOL_NORMALIZERS",
    "normalize_background_check_args",
    "normalize_background_run_args",
    "normalize_file_path_args",
    "normalize_glob_args",
    "normalize_list_directory_args",
    "normalize_noop_args",
    "normalize_precision_edit_args",
    "normalize_read_file_args",
    "normalize_repo_read_around_args",
    "normalize_repo_read_head_tail_args",
    "normalize_repo_rg_args",
    "normalize_search_code_args",
]


# Alias for write_file (uses same normalizer as file_path)
def normalize_write_file_args(tool_args: dict[str, Any]) -> dict[str, Any]:
    """Normalize write_file arguments (same as file_path normalizer)."""
    return normalize_file_path_args(tool_args)


# === 自动同步检查（Phase 1 CI 门禁基础）===
def _assert_contracts_sync() -> None:
    """验证每个 TOOL_NORMALIZERS 条目在 contracts.py 中有对应声明。"""
    from polaris.kernelone.tool_execution.tool_spec_registry import ToolSpecRegistry

    registered = set(TOOL_NORMALIZERS.keys())
    declared = set(ToolSpecRegistry.list_names())
    missing = declared - registered
    if missing:
        raise AssertionError(f"TOOL_NORMALIZERS 缺少以下工具的注册: {sorted(missing)}")
    orphaned = registered - declared
    if orphaned:
        import warnings

        warnings.warn(
            f"TOOL_NORMALIZERS 有多余注册（contracts.py 中无声明）: {sorted(orphaned)}",
            stacklevel=2,
        )


# 门禁在 Phase 3 的 CI 脚本中执行，不在 import 时阻塞开发
# _assert_contracts_sync()  # 取消 import 时执行，改为 CI gate 调用
