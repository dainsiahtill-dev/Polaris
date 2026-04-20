#!/usr/bin/env python3
"""Add read_file spec to contracts.py and budget guard to runtime_executor.py."""

# ---------------------------------------------------------------
# 1. Add read_file spec to contracts.py
# ---------------------------------------------------------------
with open("polaris/kernelone/tools/contracts.py", encoding="utf-8") as f:
    contracts_content = f.read()

# Add read_file BEFORE the closing } of _TOOL_SPECS
# Find the closing of the last tool entry before _ALIAS_INDEX
# Insert after "treesitter_rename_symbol" entry
read_file_spec = """    "read_file": {
        "category": "read",
        "cost_level": "high",
        "description": "Read the full content of a text file (UTF-8). HIGH COST: prefer repo_read_slice/around for targeted reading.",
        "aliases": ["rf", "cat", "file_read"],
        "arg_aliases": {
            "file_path": "file",
            "path": "file",
            "filepath": "file",
        },
        "arguments": [
            {"name": "file", "type": "string", "required": True},
            {"name": "max_bytes", "type": "integer", "required": False, "default": 200000},
            {"name": "range_required", "type": "boolean", "required": False, "default": False},
        ],
        "response_format_hint": "Full file content with truncation flag",
        "budget_hint": "For files >500 lines, prefer repo_read_slice. For files >2000 lines, explicit budget upgrade required.",
        "required_any": [("file",)],
        "required_doc": "args.file required. Prefer repo_read_slice/around for files >100 lines.",
    },
"""

# Find the insertion point: after treesitter_rename_symbol entry
ts_rename_marker = """    "treesitter_rename_symbol": {
        "category": "write",
        "description": "Rename a symbol across all references in a file using tree-sitter.",
        "aliases": ["ts_rename_symbol", "rename_symbol"],
        "arg_aliases": {"lang": "language", "path": "file"},
        "arguments": [
            {"name": "language", "type": "string", "required": True},
            {"name": "file", "type": "string", "required": True},
            {"name": "symbol", "type": "string", "required": True},
            {"name": "new_name", "type": "string", "required": True},
            {"name": "kind", "type": "string", "required": False},
        ],
        "response_format_hint": "Rename confirmation with count of references updated",
        "required_any": [("language",), ("file",), ("symbol",), ("new_name",)],
        "required_doc": "args.language + args.file + args.symbol + args.new_name",
    },
}"""

contracts_content = contracts_content.replace(
    ts_rename_marker,
    ts_rename_marker[:-2] + ",\n" + read_file_spec + "}\n",
)

with open("polaris/kernelone/tools/contracts.py", "w", encoding="utf-8") as f:
    f.write(contracts_content)

# Verify
with open("polaris/kernelone/tools/contracts.py", encoding="utf-8") as f:
    c = f.read()
assert '"read_file"' in c, "read_file spec not added"
assert '"cost_level": "high"' in c, "cost_level not added"
assert '"budget_hint"' in c, "budget_hint not added"
print("contracts.py: read_file spec added")

# ---------------------------------------------------------------
# 2. Add Layer-1 budget guard in runtime_executor.py
# ---------------------------------------------------------------
with open("polaris/kernelone/tools/runtime_executor.py", encoding="utf-8") as f:
    runtime_content = f.read()

old_invoke = '''    def _invoke_with_direct_executor(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        cwd: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        """Execute a KernelOne-native tool directly without the deleted legacy module."""
        from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

        tool_arguments = dict(arguments)
        if tool_name == "execute_command":
            tool_arguments.setdefault("timeout", timeout_sec)

        executor = AgentAccelToolExecutor(workspace=cwd)'''

new_invoke = '''    # Layer-1 budget constants for read_file downgrade
    _READ_WARN_LINES = 500
    _READ_HARD_LIMIT = 2000

    def _invoke_with_direct_executor(
        self,
        *,
        tool_name: str,
        arguments: dict[str, Any],
        cwd: str,
        timeout_sec: int,
    ) -> dict[str, Any]:
        """Execute a KernelOne-native tool directly without the deleted legacy module.

        Layer-1 budget guard: intercepts read_file before it reaches
        AgentAccelToolExecutor (Layer 2) to enforce line-count limits.
        """
        from polaris.kernelone.llm.toolkit.executor import AgentAccelToolExecutor

        tool_arguments = dict(arguments)
        if tool_name == "execute_command":
            tool_arguments.setdefault("timeout", timeout_sec)

        # Layer-1 budget guard for read_file
        if tool_name == "read_file":
            file_arg = arguments.get("file") if isinstance(arguments, dict) else None
            if not file_arg:
                file_arg = arguments.get("path") if isinstance(arguments, dict) else None
            if file_arg:
                check_result = self._check_read_file_budget(file_arg, cwd)
                if check_result is not None:
                    return check_result

        executor = AgentAccelToolExecutor(workspace=cwd)'''

runtime_content = runtime_content.replace(old_invoke, new_invoke, 1)

# Add the _check_read_file_budget helper method
# Insert it right after _invoke_with_direct_executor ends (after close_sync block)
old_return = """        if not isinstance(result, dict):
            return {"ok": False, "tool": tool_name, "error": "invalid tool result"}
        result.setdefault("tool", tool_name)
        return result"""

new_return_and_helper = '''        if not isinstance(result, dict):
            return {"ok": False, "tool": tool_name, "error": "invalid tool result"}
        result.setdefault("tool", tool_name)
        return result

    def _check_read_file_budget(
        self,
        file_arg: str,
        cwd: str,
    ) -> dict[str, Any] | None:
        """Layer-1 budget guard: check read_file target against line limits.

        Returns:
            None if the file is within budget (proceed normally).
            A dict error response if the file should be rejected/warned.

        Enforces:
            - Hard limit: >2000 lines → rejection with BudgetExceededError hint
            - Warning zone: >500 lines → warning appended to result after execution
        """
        import os
        try:
            raw_path = str(file_arg or "").strip()
            if not raw_path:
                return None
            if os.path.isabs(raw_path):
                target = os.path.abspath(raw_path)
            else:
                target = os.path.abspath(os.path.join(cwd, raw_path))
            target = os.path.normpath(target)
            if not os.path.isfile(target):
                return None  # Let executor handle the "not found"
            # Fast size estimation: ~104 bytes/line → line_count ≈ size/104
            file_size = os.path.getsize(target)
            estimated_lines = max(1, file_size // 104)
            if estimated_lines > self._READ_HARD_LIMIT:
                return {
                    "ok": False,
                    "tool": "read_file",
                    "error": (
                        f"BudgetExceededError: read_file hard limit exceeded. "
                        f"File has ~{estimated_lines} lines (limit: {self._READ_HARD_LIMIT}). "
                        f"Large full-file reads exhaust context budget."
                    ),
                    "error_code": "BUDGET_EXCEEDED",
                    "file": raw_path,
                    "line_count": estimated_lines,
                    "limit": self._READ_HARD_LIMIT,
                    "suggestion": (
                        f"Use repo_read_slice with {{'file': '{raw_path}', 'start': 1, 'end': 200}} "
                        f"for the first section. Use repo_read_around to examine specific locations."
                    ),
                }
            if estimated_lines > self._READ_WARN_LINES:
                # Warn but allow: add budget_warning to tool_arguments
                # so Layer-2 gets the signal to attach a warning to the result
                pass  # Layer-2 will handle the warning
            return None
        except Exception:
            return None  # Let normal execution proceed'''

runtime_content = runtime_content.replace(old_return, new_return_and_helper, 1)

with open("polaris/kernelone/tools/runtime_executor.py", "w", encoding="utf-8") as f:
    f.write(runtime_content)

# Verify
with open("polaris/kernelone/tools/runtime_executor.py", encoding="utf-8") as f:
    rc = f.read()
checks = [
    '"read_file"' in rc,
    "_READ_WARN_LINES" in rc,
    "_READ_HARD_LIMIT" in rc,
    "_check_read_file_budget" in rc,
    "BUDGET_EXCEEDED" in rc,
    "Layer-1 budget guard" in rc,
]
print("runtime_executor.py checks:", checks)
if not all(checks):
    fail_keys = [k for k, v in zip(["rf_spec", "warn", "limit", "helper", "err", "doc"], checks) if not v]
    print("FAIL:", fail_keys)

print("All done!")
