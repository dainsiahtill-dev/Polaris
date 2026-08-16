from __future__ import annotations

from pathlib import Path

from polaris.cells.roles.adapters.internal.director.quality_gate._prompt_and_targets import (
    _build_materialization_quality_repair_message,
)


def test_quality_repair_includes_complete_go_verifier_when_helper_is_far_from_failure(tmp_path: Path) -> None:
    test_path = tmp_path / "main_test.go"
    lines = ["package main", "", 'import "testing"', ""]
    lines.extend(f"// fixture line {index}" for index in range(1, 180))
    lines.extend(
        [
            "func TestMainSmoke(t *testing.T) {",
            '    if got := readCaptured(); got == "" { t.Fatal("empty output") }',
            "}",
        ]
    )
    lines.extend(f"// spacer line {index}" for index in range(180, 360))
    lines.append('func readCaptured() string { return "" } // ROOT_CAUSE_CAPTURE_HELPER')
    test_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    failure_line = lines.index("func TestMainSmoke(t *testing.T) {") + 2
    message = _build_materialization_quality_repair_message(
        original_message="Repair the Go verifier failure.",
        artifact_quality_errors=[f"./main_test.go:{failure_line}: smoke output missing expected content"],
        changed_files=["main_test.go"],
        repair_target_files=["main_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "CURRENT UTF-8 CONTENT OF REPAIR TARGETS" in message
    assert "ROOT_CAUSE_CAPTURE_HELPER" in message
    assert "[diagnostic excerpt" not in message


def test_quality_repair_includes_read_only_referenced_rust_definitions(tmp_path: Path) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "treasure_rules.rs").write_text(
        "fn evaluate_reef(reef: &Reef) {\n    match reef.hazard() {\n        ReefHazard::Shoal => {}\n    }\n}\n",
        encoding="utf-8",
    )
    (models / "reef.rs").write_text(
        "pub enum ReefHazard {\n    Calm,\n    Shallow,\n    Treacherous,\n}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the rustc failure.",
        artifact_quality_errors=[
            "error[E0599]: no variant named `Shoal` found for enum `reef::ReefHazard`\n"
            "   --> src/engine/treasure_rules.rs:3:21\n"
            "    |\n"
            "   ::: src/models/reef.rs:1:1\n"
            "    |\n"
            "1  | pub enum ReefHazard {\n"
        ],
        changed_files=["src/engine/treasure_rules.rs"],
        repair_target_files=["src/engine/treasure_rules.rs"],
        workspace_full=str(tmp_path),
    )

    assert "REFERENCED TYPE DEFINITIONS (READ-ONLY EVIDENCE; NEVER EDIT THESE FILES)" in message
    assert "src/models/reef.rs" in message
    assert "Shallow" in message
    assert "Never invent members" in message
