"""Tests for Director runtime execution tools."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.execution_tools import _create_director_tool_executor


def test_write_file_rejects_collapsed_newline_source_payload(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))
    collapsed_source = (
        "export class HumidityModel {n  public value: number;n  public unit: string;"
        "n  public location: string;n  public timestamp: Date;n  public isHigh: boolean;"
        "n  constructor(value: number) {n    this.value = value;n  }n}"
    )

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/models/humidity.ts",
            "content": collapsed_source,
            "target_files": ["src/models/humidity.ts"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "invalid_source_content"
    assert "real UTF-8 line breaks" in result["error"]
    assert not (tmp_path / "src" / "models" / "humidity.ts").exists()


def test_write_file_rejects_source_narration_payload(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/main.ts",
            "content": "I'll address the issues now.\nexport const ready = true;\n",
            "target_files": ["src/main.ts"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_narration_contamination"
    assert result["retryable"] is True
    assert not (tmp_path / "src" / "main.ts").exists()


def test_write_file_rejects_repair_directive_narration_payload(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/models/moonphase.ts",
            "content": (
                "The repair directive is clear: create the missing module imported by src/index.ts.\n"
                "I also need to create src/engine.ts to resolve the other unresolved import.\n"
            ),
            "target_files": ["src/models/moonphase.ts"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_narration_contamination"
    assert result["retryable"] is True
    assert not (tmp_path / "src" / "models" / "moonphase.ts").exists()


def test_write_file_rejects_quality_repair_mode_narration_payload(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/main.ts",
            "content": (
                "The quality repair mode requires me to create the missing files. Let me analyze what's needed:\n"
                "1. `src/main.ts` - Missing target file\n"
            ),
            "target_files": ["src/main.ts"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_narration_contamination"
    assert result["retryable"] is True
    assert not (tmp_path / "src" / "main.ts").exists()


def test_write_file_rejects_destructive_shrink(tmp_path) -> None:
    target = tmp_path / "src" / "big.ts"
    target.parent.mkdir(parents=True)
    target.write_text("".join(f"export const value{i} = {i};\n" for i in range(200)), encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/big.ts",
            "content": "export const fixed = true;\n",
            "target_files": ["src/big.ts"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "destructive_shrink"
    assert result["retryable"] is True
    assert "partial edit" in result["suggestion"]
    assert "value199" in target.read_text(encoding="utf-8")


def test_write_file_allows_normal_single_line_typescript(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/index.ts",
            "content": "export const n = 1; export const next = n + 1;",
            "target_files": ["src/index.ts"],
        },
    )

    assert result["ok"] is True
    assert (tmp_path / "src" / "index.ts").read_text(encoding="utf-8") == (
        "export const n = 1; export const next = n + 1;"
    )


def test_delete_file_is_advertised_and_deletes_single_workspace_file(tmp_path) -> None:
    target = tmp_path / "src" / "stale.ts"
    target.parent.mkdir(parents=True)
    target.write_text("export const stale = true;\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "delete_file",
        {
            "path": "src/stale.ts",
            "target_files": ["src/stale.ts"],
        },
    )

    assert executor.supports_tool("delete_file") is True
    assert "delete_file" in executor.available_tools
    assert result["ok"] is True
    assert result["file"] == "src/stale.ts"
    assert result["path"] == "src/stale.ts"
    assert result["deleted"] is True
    assert result["bytes_written"] == 0
    assert result["operation"] == "delete_file"
    assert result["director_policy"]["allowed"] is True
    assert not target.exists()


def test_delete_file_rejects_missing_file(tmp_path) -> None:
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool("delete_file", {"path": "src/missing.ts"})

    assert result["ok"] is False
    assert result["file"] == "src/missing.ts"
    assert "File not found" in result["error"]


def test_delete_file_rejects_directory(tmp_path) -> None:
    target = tmp_path / "src" / "dir.ts"
    target.mkdir(parents=True)
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool("delete_file", {"path": "src/dir.ts"})

    assert result["ok"] is False
    assert result["file"] == "src/dir.ts"
    assert "directory" in result["error"]
    assert target.is_dir()


def test_delete_file_rejects_path_outside_workspace(tmp_path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside.ts"
    outside.write_text("export const outside = true;\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    try:
        result = executor.execute_tool("delete_file", {"path": str(outside)})
        assert outside.exists()
    finally:
        outside.unlink(missing_ok=True)

    assert result["ok"] is False
    assert "UNSUPPORTED_PATH_PREFIX" in result["error"]


def test_search_code_remains_bound_to_authorized_executor(tmp_path) -> None:
    source = tmp_path / "src" / "main.py"
    source.parent.mkdir(parents=True)
    source.write_text("def sentinel_value():\n    return 42\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "search_code",
        {"query": "sentinel_value"},
    )

    assert result["ok"] is True
    assert "sentinel_value" in result["results"]


def test_r146_director_write_file_sanitizes_jsdoc_glob_before_disk(tmp_path) -> None:
    """R146: Director DEO write path must apply block-comment glob hygiene.

    AgentAccel already sanitized src/**/*.ts inside JSDoc; Director execution_tools
    historically bypassed that path, shipping unparseable TypeScript that failed
    real_run_gate npm run build (r145 L1-01 verify.ts TS1109).
    """

    executor = _create_director_tool_executor(str(tmp_path))
    content = """/**
 * Verifies source_target_coverage: src/**/*.ts is covered.
 */

export function main(): string {
  return "flight";
}
"""

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/verify.ts",
            "content": content,
            "target_files": ["src/verify.ts"],
        },
    )

    assert result["ok"] is True, result
    assert result.get("block_comment_glob_sanitized") is True
    written = (tmp_path / "src" / "verify.ts").read_text(encoding="utf-8")
    assert "src/** /*.ts" in written
    assert "src/**/*.ts" not in written


def test_r147_director_write_file_sanitizes_control_flow_comma_and_reports_ts_syntax(
    tmp_path,
) -> None:
    """R147: Director writes must rewrite ``return,`` and surface TS syntax checks.

    Live r146 shipped src/web.ts with ``return,`` (TS1109) because:
    1) Director write hygiene did not normalize control-flow commas
    2) check_source_file_syntax ignored TypeScript entirely
    """

    executor = _create_director_tool_executor(str(tmp_path))
    content = """export function makeLoop(): { start(): void } {
  let handle: number | null = null;
  return {
    start(): void {
      if (handle !== null) {
        return,
      }
      handle = 1;
    },
  };
}
"""

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/web.ts",
            "content": content,
            "target_files": ["src/web.ts"],
        },
    )

    assert result["ok"] is True, result
    assert result.get("control_flow_comma_sanitized") is True
    written = (tmp_path / "src" / "web.ts").read_text(encoding="utf-8")
    assert "return;" in written
    assert "return," not in written
    # After hygiene the file should parse as syntax-ok under tsc gate.
    assert result.get("syntax_check") == "passed"


def test_r179_edit_blocks_is_available_and_applies_search_replace(tmp_path) -> None:
    """R179/M03: Director physical surface must execute preferred edit_blocks tool."""

    (tmp_path / "src").mkdir()
    target = tmp_path / "src" / "a.py"
    target.write_text("def hello():\n    return 1\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("# ok\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    assert "edit_blocks" in executor.available_tools

    blocks = "<<<<<<< SEARCH\ndef hello():\n    return 1\n=======\ndef hello():\n    return 2\n>>>>>>> REPLACE\n"
    result = executor.execute_tool(
        "edit_blocks",
        {
            "file": "src/a.py",
            "blocks": blocks,
            "target_files": ["src/a.py"],
            "allowed_scope": ["src/a.py"],
        },
    )
    assert result.get("ok") is True, result
    assert "return 2" in target.read_text(encoding="utf-8")


def test_r195_write_file_recovers_structured_content_no_dict_leak(tmp_path) -> None:
    """R195/M03: write_file must not str() a non-string content body into the file.

    Weak Directors (e.g. MiniMax-M3) emit ``content`` as a structured body (a
    ``$text`` continuation map or a list of fragments) instead of a plain string.
    The physical writer used ``str(args.get("content", ""))`` which serialized the
    Python repr into the source file (L1-01 m03-r17 ``src/main.ts:111`` leaked
    ``{'$text': ...}`` -> TS1005). The writer must recover the structured body to a
    plain UTF-8 string (or fail-closed); it must NEVER silently stringify a
    mapping/list into a file.
    """
    executor = _create_director_tool_executor(str(tmp_path))
    structured = ["export const firefly = 1;", "export const flower = 2;"]

    result = executor.execute_tool(
        "write_file",
        {
            "path": "src/index.ts",
            "content": structured,
            "target_files": ["src/index.ts"],
        },
    )

    assert result["ok"] is True, result
    written = (tmp_path / "src" / "index.ts").read_text(encoding="utf-8")
    # No Python repr leak:
    assert "[" not in written
    assert "'" not in written
    # Recovered plain-string body, one statement per line:
    assert written == "export const firefly = 1;\nexport const flower = 2;"


def test_write_file_rejects_nested_cpp_body_before_physical_effect(tmp_path) -> None:
    """L3-24 r82: XML-shaped native arguments must not poison C++ source."""

    executor = _create_director_tool_executor(str(tmp_path))
    structured = {
        "$text": "#pragma once\n#include ",
        "vector": {
            "$text": "\nstruct Box { std::vector",
            "int": {"$text": " values; };\n"},
        },
    }

    result = executor.execute_tool(
        "write_file",
        {
            "path": "include/example.hpp",
            "content": structured,
            "target_files": ["include/example.hpp"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "invalid_source_content"
    assert not (tmp_path / "include" / "example.hpp").exists()


def test_r195_edit_file_empty_search_is_non_fatal_no_op(tmp_path) -> None:
    """R195/M03: edit_file with an empty search must not become a control-plane failure.

    A single Director tool call whose only defect is a missing/empty search string
    is a recoverable arg-shape error, not a run-ledger integrity break. Previously
    the physical editor returned ``{"ok": False, "error": "Search text must not be
    empty"}`` which the mutation port projected as ``deo_physical_execution_failed``
    -> ``TOOL_RESULT_FAILED`` -> ``canonical_execution=run_ledger_integrity_failed``
    -> ``DELIVERY_FAILED`` (L1-01 m03-r17, 2 such calls killed the whole delivery).
    This must instead be an allowed no-op (file preserved, stays out of the ledger);
    the product-quality plane catches any genuine downstream defect separately.
    """
    (tmp_path / "src").mkdir(parents=True)
    target = tmp_path / "src" / "main.ts"
    body = "export const firefly = 1;\n"
    target.write_text(body, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "edit_file",
        {"file": "src/main.ts", "replace": "export const flower = 2;"},
    )

    assert result["ok"] is True, result
    # R193/R194 no-wipe guarantee: file is preserved exactly.
    assert target.read_text(encoding="utf-8") == body
    # Must NOT surface as a control-plane failure class.
    assert result.get("error_code") != "deo_physical_execution_failed"
    assert result.get("error") is None


def test_edit_file_rejects_new_go_syntax_failure_before_commit(tmp_path) -> None:
    """L1-04 r51: partial exact replacement must not corrupt a parseable file."""

    target = tmp_path / "engine" / "rules.go"
    target.parent.mkdir(parents=True)
    original = "package engine\n\nfunc Cast() int {\n\treturn 1\n}\n"
    target.write_text(original, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "engine/rules.go",
            "search": "\treturn 1\n}",
            "replace": "\treturn 2\n}na < spellCost {",
            "target_files": ["engine/rules.go"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_syntax_regression"
    assert result["retryable"] is True
    assert result["syntax_check"] == "failed_precommit"
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_rejects_new_go_compile_failure_before_commit(tmp_path) -> None:
    """L3-22: gofmt-valid undefined identifiers must not reach the workspace."""

    (tmp_path / "go.mod").write_text("module example.com/candidate\n\ngo 1.21\n", encoding="utf-8")
    target = tmp_path / "engine" / "engine.go"
    target.parent.mkdir(parents=True)
    original = "package engine\n\nfunc Value() int {\n\tvalue := 1\n\treturn value\n}\n"
    target.write_text(original, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "engine/engine.go",
            "search": "\tvalue := 1\n",
            "replace": "",
            "target_files": ["engine/engine.go"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_compile_regression"
    assert result["retryable"] is True
    assert result["compile_check"] == "failed_precommit"
    assert result["verifier_command"] == "go test -run ^$ ./..."
    assert "undefined: value" in result["error"]
    assert target.read_text(encoding="utf-8") == original


def test_write_file_rejects_undeclared_missing_cpp_local_include_before_commit(tmp_path) -> None:
    """L3-24 r14: sources may not invent an out-of-scope project header."""

    include_dir = tmp_path / "include" / "invisible_ink_diary"
    include_dir.mkdir(parents=True)
    (include_dir / "moon_cipher.hpp").write_text("#pragma once\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "write_file",
        {
            "file": "src/cipher_engine.cpp",
            "content": (
                '#include "invisible_ink_diary/cipher_engine.hpp"\n\n'
                "int cipher_engine_value() { return 1; }\n"
            ),
            "target_files": [
                "src/cipher_engine.cpp",
                "include/invisible_ink_diary/moon_cipher.hpp",
            ],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "undeclared_local_include_dependency"
    assert result["retryable"] is True
    assert result["missing_local_includes"] == ["invisible_ink_diary/cipher_engine.hpp"]
    assert not (tmp_path / "src" / "cipher_engine.cpp").exists()


def test_write_file_allows_declared_cpp_local_include_pending_same_batch(tmp_path) -> None:
    """A header already authorized for this task may be materialized later in the batch."""

    include_dir = tmp_path / "include" / "invisible_ink_diary"
    include_dir.mkdir(parents=True)
    executor = _create_director_tool_executor(str(tmp_path))
    body = (
        '#include "invisible_ink_diary/cipher_engine.hpp"\n\n'
        "int cipher_engine_value() { return 1; }\n"
    )

    result = executor.execute_tool(
        "write_file",
        {
            "file": "src/cipher_engine.cpp",
            "content": body,
            "target_files": [
                "src/cipher_engine.cpp",
                "include/invisible_ink_diary/cipher_engine.hpp",
            ],
        },
    )

    assert result["ok"] is True, result
    assert (tmp_path / "src" / "cipher_engine.cpp").read_text(encoding="utf-8") == body


def test_write_file_uses_authoritative_scope_for_include_root_sibling(tmp_path) -> None:
    """L3-24 r16: native tool args need not repeat JobToken path authority."""

    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(
        (
            "include/invisible_diary/diary.hpp",
            "include/invisible_diary/moon.hpp",
        )
    )
    body = '#pragma once\n#include "invisible_diary/moon.hpp"\n'

    result = executor.execute_tool(
        "write_file",
        {
            "file": "include/invisible_diary/diary.hpp",
            "content": body,
        },
    )

    assert result["ok"] is True, result
    assert (tmp_path / "include" / "invisible_diary" / "diary.hpp").read_text(encoding="utf-8") == body


def test_authoritative_scope_still_rejects_undeclared_cpp_local_include(tmp_path) -> None:
    """Trusted scope fixes aliasing only; it must not broaden undeclared writes."""

    include_dir = tmp_path / "include" / "invisible_diary"
    include_dir.mkdir(parents=True)
    (include_dir / "cipher.hpp").write_text("#pragma once\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    executor._bind_authorized_scope(("include/invisible_diary/diary.hpp",))

    result = executor.execute_tool(
        "write_file",
        {
            "file": "include/invisible_diary/diary.hpp",
            "content": '#pragma once\n#include "invisible_diary/undeclared.hpp"\n',
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "undeclared_local_include_dependency"
    assert not (include_dir / "diary.hpp").exists()


def test_write_file_defers_go_test_compile_failure_to_workspace_quality(tmp_path) -> None:
    """L3-22 r42: test API mismatch must land for same-Director repair."""

    (tmp_path / "go.mod").write_text("module example.com/candidate\n\ngo 1.21\n", encoding="utf-8")
    (tmp_path / "main.go").write_text("package main\n\nfunc main() {}\n", encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    test_body = (
        "package main\n\n"
        'import "testing"\n\n'
        "func TestRunDemo(t *testing.T) {\n"
        "\tRunDemo()\n"
        "}\n"
    )

    result = executor.execute_tool(
        "write_file",
        {
            "file": "main_test.go",
            "content": test_body,
            "target_files": ["main_test.go"],
        },
    )

    assert result["ok"] is True, result
    assert (tmp_path / "main_test.go").read_text(encoding="utf-8") == test_body


def test_edit_file_rejects_changed_but_still_invalid_go_candidate(tmp_path) -> None:
    """L1-04 r51: an existing parse error must be fixed atomically, not moved."""

    target = tmp_path / "engine" / "rules.go"
    target.parent.mkdir(parents=True)
    original = "package engine\n\nfunc Cast() int {\n\treturn 1\n}na < spellCost {\n"
    target.write_text(original, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "engine/rules.go",
            "search": "}na < spellCost {",
            "replace": "}\n\treturn 2",
            "target_files": ["engine/rules.go"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "source_syntax_not_repaired"
    assert result["preexisting_syntax_failure"] is True
    assert result["retryable"] is True
    assert result["syntax_check"] == "failed_precommit"
    assert target.read_text(encoding="utf-8") == original


def test_edit_file_rejects_destructive_full_file_search_replace_before_commit(tmp_path) -> None:
    """L3-21: edit_file must not bypass write_file's destructive-shrink guard.

    The live quality-repair turn copied the complete 15KB Python test file into
    ``search`` but returned only its imports in ``replace``.  The candidate was
    valid Python, so the syntax-only guard committed it and unittest discovery
    collapsed from 34 tests to 0.  Large destructive replacement must be
    rejected before disk mutation just like an equivalent write_file call.
    """

    target = tmp_path / "tests" / "test_product.py"
    target.parent.mkdir(parents=True)
    original = "".join(
        f"def test_case_{index}():\n    assert {index} == {index}\n\n"
        for index in range(80)
    )
    target.write_text(original, encoding="utf-8")
    executor = _create_director_tool_executor(str(tmp_path))
    shortened = '"""Product tests."""\n\nfrom unittest import TestCase\n'

    result = executor.execute_tool(
        "edit_file",
        {
            "file": "tests/test_product.py",
            "search": original,
            "replace": shortened,
            "target_files": ["tests/test_product.py"],
        },
    )

    assert result["ok"] is False
    assert result["error_type"] == "destructive_shrink"
    assert result["retryable"] is True
    assert target.read_text(encoding="utf-8") == original


def test_r195_compound_command_block_is_non_fatal_no_op(tmp_path) -> None:
    """Layer 2 / R195-pattern: a blocked compound/restricted command must not break
    canonical_execution.

    L1-01 r15 and r22 both DELIVERY_FAILED because the Director's verification
    command (e.g. ``npm run build && npm test``) was blocked by ``_SHELL_META_RE``
    -> ``deo_physical_execution_failed`` -> ``TOOL_RESULT_FAILED`` ->
    ``run_ledger_integrity_failed``. A blocked command is a recoverable denial
    (the model can re-issue as single commands), not a control-plane integrity
    break. The security guard is PRESERVED (the command is never executed); it
    simply returns a non-fatal no-op so the ledger stays clean and the model
    gets corrective feedback. Product-quality gates catch any unverified build
    on a separate plane.
    """
    executor = _create_director_tool_executor(str(tmp_path))
    result = executor.execute_tool(
        "execute_command",
        {"command": "npm run build && npm test"},
    )
    assert result["ok"] is True, result
    assert result.get("blocked") is True
    assert result.get("no_op") is True
    # Security guard preserved: command was NOT executed.
    assert "not executed" in str(result.get("output", "")).lower()
    # Must NOT surface as a control-plane failure.
    assert result.get("error") is None or result.get("error_code") != "deo_physical_execution_failed"
