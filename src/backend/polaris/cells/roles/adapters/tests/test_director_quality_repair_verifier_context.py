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


def test_quality_repair_includes_named_type_impl_when_rustc_omits_definition_path(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "treasure_rules.rs").write_text(
        "fn check(budget: &Budget) { let _ = budget.remaining(); }\n",
        encoding="utf-8",
    )
    (models / "budget.rs").write_text(
        "pub struct Budget {\n    total: f64,\n}\n\nimpl Budget {\n    pub fn spendable(&self) -> f64 { self.total }\n    pub fn classify(&self, cost: f64) -> i32 { 0 }\n}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the rustc failure.",
        artifact_quality_errors=[
            "error[E0599]: no method named `remaining` found for reference `&Budget` in the current scope\n"
            "   --> src/engine/treasure_rules.rs:1:45\n"
            "    |\n"
            "1  | fn check(budget: &Budget) { let _ = budget.remaining(); }\n"
            "    |                                             ^^^^^^^^^ method not found in `&Budget`\n"
        ],
        changed_files=["src/engine/treasure_rules.rs"],
        repair_target_files=["src/engine/treasure_rules.rs"],
        workspace_full=str(tmp_path),
    )

    assert "src/models/budget.rs" in message
    assert "spendable" in message
    assert "classify" in message
    assert "READ-ONLY DEFINITION" in message


def test_quality_repair_includes_named_cpp_class_when_gxx_omits_header_path(
    tmp_path: Path,
) -> None:
    engine = tmp_path / "src" / "engine"
    models = tmp_path / "src" / "models"
    engine.mkdir(parents=True)
    models.mkdir(parents=True)
    (engine / "generator.cpp").write_text(
        "bool can_step(const patrol_chess::models::Robot& robot) { return robot.energy(); }\n",
        encoding="utf-8",
    )
    (models / "robot.hpp").write_text(
        "namespace patrol_chess::models {\n"
        "class Robot {\n"
        "public:\n"
        "    bool begin_move();\n"
        "    Position position() const;\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )
    (models / "energy.hpp").write_text(
        "namespace patrol_chess::models {\n"
        "class Energy {\n"
        "public:\n"
        "    bool can_spend(unsigned delta) const;\n"
        "    unsigned current() const;\n"
        "};\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair the g++ failure.",
        artifact_quality_errors=[
            "src/engine/generator.cpp:49:42: error: ‘const class patrol_chess::models::Robot’ "
            "has no member named ‘energy’\n"
        ],
        changed_files=["src/engine/generator.cpp"],
        repair_target_files=["src/engine/generator.cpp"],
        workspace_full=str(tmp_path),
    )

    assert "src/models/robot.hpp" in message
    assert "src/models/energy.hpp" in message
    assert "begin_move" in message
    assert "can_spend" in message
    assert "READ-ONLY DEFINITION" in message
    assert "Never invent members" in message
    assert "qualify the use-site as ::NS::models" in message
    assert "An unclosed A swallows later includes into A::std" in message
