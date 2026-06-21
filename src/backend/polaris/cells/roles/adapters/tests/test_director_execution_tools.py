"""Tests for Director runtime execution tools."""

from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor


def test_write_file_rejects_collapsed_newline_source_payload(tmp_path) -> None:
    executor = DirectorToolExecutor(str(tmp_path))
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


def test_write_file_allows_normal_single_line_typescript(tmp_path) -> None:
    executor = DirectorToolExecutor(str(tmp_path))

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
