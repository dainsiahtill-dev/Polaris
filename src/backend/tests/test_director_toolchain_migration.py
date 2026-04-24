"""Tests for the canonical director tooling contract.

This suite verifies the only supported import surface:
`polaris.cells.director.execution.public.tools`.

It intentionally does not preserve or validate legacy shim paths.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]


class TestCanonicalImports:
    """Verify canonical imports work and expose expected symbols."""

    def test_canonical_tools_package_has_init(self) -> None:
        from polaris.cells.director.execution.internal import tools

        assert tools.__file__ is not None

    def test_canonical_exports_all_expected_symbols(self) -> None:
        from polaris.cells.director.execution.internal import tools

        expected = [
            "ALLOWED_EXECUTION_COMMANDS",
            "ALLOWED_TOOLS",
            "BLOCKED_COMMAND_PATTERNS",
            "DEFAULT_MAX_RETRIES",
            "DEFAULT_READ_RADIUS",
            "KV_ALLOWED_KEYS",
            "MAX_EVENT_CONTENT_LINES",
            "MAX_TOOL_READ_LINES",
            "READ_ONLY_TOOLS",
            "WRITE_TOOLS",
            "ToolChainStep",
            "ToolChainResult",
            "append_log",
            "as_list",
            "sanitize_tool_name",
            "safe_int",
            "split_list_value",
            "split_tool_step",
            "is_command_allowed",
            "is_command_blocked",
            "extract_tool_budget",
            "extract_tool_plan",
            "parse_tool_plan_item",
            "_parse_key_value_token",
            "_normalize_tool_plan_dict_step",
            "build_tool_cli_args",
            "analyze_slice_content",
            "annotate_rg_output",
            "build_refs",
            "compact_tool_output",
            "count_tool_output_lines",
            "persist_tool_raw_output",
            "score_hit",
            "suggest_radius",
            "normalize_tool_plan",
            "parse_tool_chain_step",
            "run_tool_chain",
            "run_tool_plan",
        ]
        missing = [symbol for symbol in expected if not hasattr(tools, symbol)]
        assert not missing, f"Missing symbols: {missing}"

    def test_public_tools_port_exposes_security_and_cli_builder(self) -> None:
        from polaris.cells.director.execution.public.tools import (
            ALLOWED_EXECUTION_COMMANDS,
            build_tool_cli_args,
            is_command_allowed,
            is_command_blocked,
        )

        assert "pytest" in ALLOWED_EXECUTION_COMMANDS
        assert callable(build_tool_cli_args)
        assert is_command_blocked("pytest || echo pwned") is True
        assert is_command_allowed("pytest -q") is True
        assert build_tool_cli_args("repo_rg", {"pattern": "test", "paths": ["."]}) == [
            "test",
            ".",
        ]


class TestCallerImports:
    """Verify callers use the canonical public port."""

    def test_director_adapter_imports_public_tools(self) -> None:
        director_adapter_path = (
            _BACKEND_ROOT / "polaris" / "cells" / "roles" / "adapters" / "internal" / "director_adapter.py"
        )
        source = director_adapter_path.read_text(encoding="utf-8")

        assert "from polaris.cells.director.execution.public.tools import" in source
        assert "polaris.kernelone.tools.director" not in source

    def test_runtime_executor_imports_public_tools(self) -> None:
        runtime_executor_path = _BACKEND_ROOT / "polaris" / "kernelone" / "tools" / "runtime_executor.py"
        source = runtime_executor_path.read_text(encoding="utf-8")

        assert "from polaris.cells.director.execution.public.tools import" in source


class TestGraphOwnership:
    """Verify graph catalog reflects the canonical ownership."""

    def test_cells_yaml_has_canonical_ownership_path(self) -> None:
        cells_yaml_path = _BACKEND_ROOT / "docs" / "graph" / "catalog" / "cells.yaml"
        content = cells_yaml_path.read_text(encoding="utf-8")

        assert "polaris/cells/director/execution/internal/tools/**" in content

    def test_public_tools_port_is_supported_boundary(self) -> None:
        from polaris.cells.director.execution import public as public_pkg

        assert hasattr(public_pkg, "build_tool_cli_args")
        assert hasattr(public_pkg, "is_command_allowed")
        assert hasattr(public_pkg, "is_command_blocked")
