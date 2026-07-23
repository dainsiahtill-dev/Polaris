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
