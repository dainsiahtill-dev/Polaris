"""Regression tests for bounded diagnostic-centered Director repair context."""

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
)


def test_quality_repair_message_includes_head_and_diagnostic_line(tmp_path: Path) -> None:
    source = tmp_path / "main_test.go"
    lines = [f"// line {index}: ordinary source" for index in range(1, 701)]
    lines[617] = "}cceeded; want error wrapping"
    source.write_text("\n".join(lines), encoding="utf-8")

    message = _build_materialization_quality_repair_message(
        original_message="Repair Go tests.",
        artifact_quality_errors=["go test ./...: main_test.go:618:2: expected ';', found cceeded"],
        changed_files=["main_test.go"],
        repair_target_files=["main_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "[diagnostic excerpt lines" in message
    assert "[file head lines" in message
    assert "}cceeded; want error wrapping" in message
    assert "// line 1: ordinary source" in message


def test_quality_repair_message_keeps_head_fallback_without_matching_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "main_test.go"
    source.write_text("head contract\n" + ("x" * 6000), encoding="utf-8")

    message = _build_materialization_quality_repair_message(
        original_message="Repair Go tests.",
        artifact_quality_errors=["go test ./... failed without a source location"],
        changed_files=["main_test.go"],
        repair_target_files=["main_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "head contract" in message
    assert "[truncated]" in message
    assert "[diagnostic excerpt" not in message
