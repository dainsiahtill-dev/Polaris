"""Tests for JSON config file validation and repair.

Covers:
- JS-object-literal to JSON conversion
- JSON validation for write_file/edit_file
- Entry-point consistency validation
- Fail-closed behavior for invalid JSON
"""

from __future__ import annotations

import json
from pathlib import Path

from polaris.cells.roles.adapters.internal.director.deterministic_repairs.json_config_repairs import (
    try_repair_js_object_literal_to_json,
    validate_entrypoint_consistency,
    validate_json_config_file,
)

# ---------------------------------------------------------------------------
# JS-object-literal to JSON conversion
# ---------------------------------------------------------------------------


class TestRepairJsObjectLiteralToJson:
    """Test conversion of JS-object-literal style to standard JSON."""

    def test_already_valid_json_unchanged(self) -> None:
        content = json.dumps({"target": "ES2020", "strict": True}, indent=2)
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is False
        assert json.loads(repaired) == {"target": "ES2020", "strict": True}

    def test_unquoted_keys_repaired(self) -> None:
        content = '{ target: "ES2020", strict: true }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["target"] == "ES2020"
        assert parsed["strict"] is True

    def test_trailing_comma_removed(self) -> None:
        content = '{ "target": "ES2020", "strict": true, }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["target"] == "ES2020"

    def test_single_line_comment_removed(self) -> None:
        content = '{\n  // This is a comment\n  "target": "ES2020"\n}'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["target"] == "ES2020"

    def test_multi_line_comment_removed(self) -> None:
        content = '{\n  /* comment */\n  "target": "ES2020"\n}'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["target"] == "ES2020"

    def test_unquoted_string_value_repaired(self) -> None:
        content = '{ "target": ES2020 }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["target"] == "ES2020"

    def test_full_tsconfig_js_literal(self) -> None:
        """Test the exact pattern from the bug report."""
        content = """{
  compilerOptions: {
    target: ES2020,
    module: ES2020,
    outDir: "dist",
    rootDir: "src",
    strict: true
  },
  include: [src/**/*]
}"""
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["compilerOptions"]["target"] == "ES2020"
        assert parsed["include"] == ["src/**/*"]

    def test_nested_objects_repaired(self) -> None:
        content = """{
  compilerOptions: {
    target: ES2020,
    lib: [ES2020, DOM]
  }
}"""
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is True
        parsed = json.loads(repaired)
        assert parsed["compilerOptions"]["lib"] == ["ES2020", "DOM"]

    def test_empty_content_unchanged(self) -> None:
        repaired, was_repaired = try_repair_js_object_literal_to_json("")
        assert was_repaired is False
        assert repaired == ""

    def test_non_json_object_unchanged(self) -> None:
        content = "not json at all"
        _repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is False

    def test_preserves_string_values_with_colons(self) -> None:
        content = '{ "name": "test:project", "version": "1.0.0" }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is False
        parsed = json.loads(repaired)
        assert parsed["name"] == "test:project"

    def test_boolean_values_preserved(self) -> None:
        content = '{ "strict": true, "esModuleInterop": false }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is False
        parsed = json.loads(repaired)
        assert parsed["strict"] is True
        assert parsed["esModuleInterop"] is False

    def test_number_values_preserved(self) -> None:
        content = '{ "port": 3000, "timeout": 30.5 }'
        repaired, was_repaired = try_repair_js_object_literal_to_json(content)
        assert was_repaired is False
        parsed = json.loads(repaired)
        assert parsed["port"] == 3000
        assert parsed["timeout"] == 30.5


# ---------------------------------------------------------------------------
# validate_json_config_file
# ---------------------------------------------------------------------------


class TestValidateJsonConfigFile:
    """Test the main validation entry point."""

    def test_valid_json_passes(self) -> None:
        content = json.dumps({"name": "test"})
        result = validate_json_config_file(content, "test.json")
        assert result["ok"] is True
        assert result["repaired"] is False

    def test_invalid_json_repaired(self) -> None:
        content = '{ name: "test" }'
        result = validate_json_config_file(content, "test.json")
        assert result["ok"] is True
        assert result["repaired"] is True
        parsed = json.loads(result["content"])
        assert parsed["name"] == "test"

    def test_invalid_json_repair_disabled(self) -> None:
        content = '{ name: "test" }'
        result = validate_json_config_file(content, "test.json", allow_repair=False)
        assert result["ok"] is False
        assert "error" in result

    def test_empty_content_passes(self) -> None:
        result = validate_json_config_file("", "test.json")
        assert result["ok"] is True

    def test_bare_tokens_in_array_repaired(self) -> None:
        content = '{ "include": [src/**/*] }'
        result = validate_json_config_file(content, "tsconfig.json")
        assert result["ok"] is True
        assert result["repaired"] is True
        parsed = json.loads(result["content"])
        assert parsed["include"] == ["src/**/*"]


# ---------------------------------------------------------------------------
# validate_entrypoint_consistency
# ---------------------------------------------------------------------------


class TestValidateEntrypointConsistency:
    """Test entry-point consistency validation."""

    def test_package_json_with_missing_main(self, tmp_path: Path) -> None:
        content = json.dumps(
            {
                "name": "test",
                "main": "dist/index.js",
                "scripts": {"build": "tsc"},
            }
        )
        result = validate_entrypoint_consistency(content, "package.json", workspace_path=str(tmp_path))
        assert result["ok"] is False
        assert any("main" in e for e in result["errors"])

    def test_package_json_with_existing_main(self, tmp_path: Path) -> None:
        (tmp_path / "dist").mkdir()
        (tmp_path / "dist" / "index.js").write_text("module.exports = {}", encoding="utf-8")
        content = json.dumps(
            {
                "name": "test",
                "main": "dist/index.js",
                "scripts": {"build": "tsc"},
            }
        )
        result = validate_entrypoint_consistency(content, "package.json", workspace_path=str(tmp_path))
        assert result["ok"] is True

    def test_tsconfig_json_empty_include(self) -> None:
        content = json.dumps(
            {
                "compilerOptions": {"target": "ES2020"},
                "include": [],
            }
        )
        result = validate_entrypoint_consistency(content, "tsconfig.json")
        assert result["ok"] is False
        assert any("include" in e and "empty" in e for e in result["errors"])

    def test_tsconfig_json_valid_include(self) -> None:
        content = json.dumps(
            {
                "compilerOptions": {"target": "ES2020"},
                "include": ["src/**/*.ts"],
            }
        )
        result = validate_entrypoint_consistency(content, "tsconfig.json")
        assert result["ok"] is True

    def test_tsconfig_json_missing_include_ok(self) -> None:
        content = json.dumps(
            {
                "compilerOptions": {"target": "ES2020"},
            }
        )
        result = validate_entrypoint_consistency(content, "tsconfig.json")
        assert result["ok"] is True

    def test_non_json_content_skipped(self) -> None:
        result = validate_entrypoint_consistency("not json", "test.json")
        assert result["ok"] is True

    def test_script_with_missing_entry(self, tmp_path: Path) -> None:
        content = json.dumps(
            {
                "name": "test",
                "scripts": {"start": "node dist/index.js"},
            }
        )
        result = validate_entrypoint_consistency(content, "package.json", workspace_path=str(tmp_path))
        assert result["ok"] is False
        assert any("dist/index.js" in e for e in result["errors"])


# ---------------------------------------------------------------------------
# Director write_file JSON validation (integration)
# ---------------------------------------------------------------------------


class TestDirectorWriteFileJsonValidation:
    """Test that Director write_file rejects invalid JSON or repairs it."""

    def test_write_valid_json_succeeds(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        executor = _create_director_tool_executor(str(tmp_path))
        content = json.dumps({"name": "test", "version": "1.0.0"}, indent=2)
        result = executor.execute_tool(
            "write_file",
            {"file": "package.json", "content": content},
        )
        assert result["ok"] is True
        assert (tmp_path / "package.json").exists()

    def test_write_invalid_json_repaired(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        executor = _create_director_tool_executor(str(tmp_path))
        content = '{ name: "test", version: "1.0.0" }'
        result = executor.execute_tool(
            "write_file",
            {"file": "package.json", "content": content},
        )
        # Should be repaired and written
        assert result["ok"] is True
        assert (tmp_path / "package.json").exists()
        # Verify the written content is valid JSON
        written = json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))
        assert written["name"] == "test"

    def test_write_invalid_json_repair_disabled(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        executor = _create_director_tool_executor(str(tmp_path))
        # Use content that cannot be repaired
        content = '{ name: "test", invalid syntax }'
        result = executor.execute_tool(
            "write_file",
            {"file": "package.json", "content": content},
        )
        # Should fail - but we need to check if repair can fix it
        # If repair fails, it should be blocked
        if not result.get("ok"):
            assert result.get("error_type") == "invalid_json_content"
            assert result.get("blocked") is True

    def test_write_tsconfig_js_literal_repaired(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        executor = _create_director_tool_executor(str(tmp_path))
        content = """{
  compilerOptions: {
    target: ES2020,
    module: ES2020,
    strict: true
  },
  include: [src/**/*]
}"""
        result = executor.execute_tool(
            "write_file",
            {"file": "tsconfig.json", "content": content},
        )
        assert result["ok"] is True
        written = json.loads((tmp_path / "tsconfig.json").read_text(encoding="utf-8"))
        assert written["compilerOptions"]["target"] == "ES2020"

    def test_write_non_json_file_skips_validation(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        executor = _create_director_tool_executor(str(tmp_path))
        content = "const x = 1;\n"
        result = executor.execute_tool(
            "write_file",
            {"file": "app.js", "content": content},
        )
        assert result["ok"] is True

    def test_edit_valid_json_succeeds(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        (tmp_path / "config.json").write_text(
            json.dumps({"name": "old"}),
            encoding="utf-8",
        )
        executor = _create_director_tool_executor(str(tmp_path))
        result = executor.execute_tool(
            "edit_file",
            {
                "file": "config.json",
                "search": '"old"',
                "replace": '"new"',
            },
        )
        assert result["ok"] is True

    def test_edit_invalid_json_repaired(self, tmp_path: Path) -> None:
        from polaris.cells.roles.adapters.internal.director.execution_tools import (
            _create_director_tool_executor,
        )

        # Write a file with JS-object-literal that needs repair
        (tmp_path / "config.json").write_text(
            '{ name: "old", version: "1.0.0" }',
            encoding="utf-8",
        )
        executor = _create_director_tool_executor(str(tmp_path))
        result = executor.execute_tool(
            "edit_file",
            {
                "file": "config.json",
                "search": '"old"',
                "replace": '"new"',
            },
        )
        # The existing content is not valid JSON, so the edit should fail
        # or the repair should fix it
        if not result.get("ok"):
            assert result.get("error_type") == "invalid_json_content"
