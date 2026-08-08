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
