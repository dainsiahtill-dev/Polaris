from __future__ import annotations

from polaris.cells.roles.adapters.internal.director.quality_gate import (
    _build_materialization_quality_repair_message,
)


def test_missing_file_repair_pins_workspace_relative_tool_path() -> None:
    message = _build_materialization_quality_repair_message(
        original_message="Implement the project stylesheet.",
        artifact_quality_errors=["declared target file missing 'src/styles.css'"],
        changed_files=["src/main.js"],
        missing_target_files=["src/styles.css"],
    )

    assert "DIRECT TOOL PATH CONTRACT (fail-closed)" in message
    assert "Never use an absolute path, /tmp path" in message
    assert "Authorized tool target paths:\n- src/styles.css" in message


def test_existing_file_repair_forbids_temporary_staging_paths() -> None:
    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go rule engine.",
        artifact_quality_errors=["go test failed in engine/rules.go"],
        changed_files=["engine/rules.go", "engine/service.go"],
        repair_target_files=["engine/rules.go", "engine/service.go"],
    )

    assert "Authorized tool target paths:" in message
    assert "- engine/rules.go" in message
    assert "- engine/service.go" in message
    assert "repair_* staging path" in message
    assert "Apply SEARCH/REPLACE directly to the authorized project file" in message
