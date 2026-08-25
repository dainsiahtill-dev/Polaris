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


def test_quality_repair_message_embeds_full_product_source_with_inline_unit_test(
    tmp_path: Path,
) -> None:
    """A product file whose inline unit test fails must show its implementation.

    Live L1-05 (factory_d842dba2e017): ``src/engine/flavor_rules.rs`` carries
    ``#[cfg(test)]`` tests; the failing assertion points at line 452 while the
    legal fix lives in ``compatibility_report`` around line 300.  The old
    verifier-source-only full-body gate handed the model a 40-line head plus
    the assertion window — never the aggregation logic — so three real edits
    circled the fix site without ever seeing it.  Any budget-fitting target
    with diagnostic anchors gets its complete body.
    """

    source = tmp_path / "src" / "engine" / "flavor_rules.rs"
    source.parent.mkdir(parents=True)
    lines = [f"// ordinary implementation line {index}" for index in range(1, 474)]
    lines[300 - 1] = "pub fn compatibility_report(target: FlavorProfile, ingredients: &[Ingredient]) -> Report {"
    lines[315 - 1] = "let mean_deviation: f32 = diffs.iter().sum::<f32>() / 6.0;"
    lines[452 - 1] = "assert_eq!(report.verdict, Verdict::Conflicted);"
    source.write_text("\n".join(lines), encoding="utf-8")

    message = _build_materialization_quality_repair_message(
        original_message="Repair the failing compatibility verdict.",
        artifact_quality_errors=[
            "cargo test --quiet: running 13 tests\n"
            "engine::flavor_rules::tests::conflicted_ingredients_score_low --- FAILED\n"
            "thread panicked at src/engine/flavor_rules.rs:452:9:\n"
            "assertion `left == right` failed\n"
            "  left: Balanced\n"
            " right: Conflicted"
        ],
        changed_files=["src/engine/flavor_rules.rs"],
        repair_target_files=["src/engine/flavor_rules.rs"],
        workspace_full=str(tmp_path),
    )

    assert "pub fn compatibility_report" in message
    assert "let mean_deviation: f32 = diffs.iter().sum::<f32>() / 6.0;" in message
    assert "assert_eq!(report.verdict, Verdict::Conflicted);" in message


def test_quality_repair_message_prioritizes_exact_compile_site_before_full_verifier(
    tmp_path: Path,
) -> None:
    """A small verifier with repeated calls must lead with the failing line.

    Live L3-22 exposed two ``Assign`` calls in one Go test file.  Full-file
    context alone made the Director repeatedly edit the later, already-correct
    occurrence while the compiler diagnostic at line 6 remained unchanged.
    """

    source = tmp_path / "internal" / "bubbletea" / "note_test.go"
    source.parent.mkdir(parents=True)
    source.write_text(
        "package bubbletea\n\n"
        "func TestEmpty(t *testing.T) {\n"
        "    b := NewBoard()\n"
        "    // exact failing occurrence follows\n"
        "    err := b.Assign(Note{})\n"
        "    _ = err\n"
        "}\n\n"
        "func TestValid(t *testing.T) {\n"
        "    b := NewBoard()\n"
        "    if _, err := b.Assign(validNote()); err != nil { t.Fatal(err) }\n"
        "}\n",
        encoding="utf-8",
    )

    message = _build_materialization_quality_repair_message(
        original_message="Repair Go tests.",
        artifact_quality_errors=[
            "internal/bubbletea/note_test.go:6:9: assignment mismatch: "
            "1 variable but b.Assign returns 2 values"
        ],
        changed_files=["internal/bubbletea/note_test.go"],
        repair_target_files=["internal/bubbletea/note_test.go"],
        workspace_full=str(tmp_path),
    )

    assert "PRIMARY DIAGNOSTIC SITE" in message
    assert "Resolve this exact path:line compiler/verifier diagnostic first" in message
    assert "6:     err := b.Assign(Note{})" in message
    assert message.index("PRIMARY DIAGNOSTIC SITE") < message.index("CURRENT UTF-8 CONTENT OF REPAIR TARGETS")
    assert "if _, err := b.Assign(validNote())" in message


def test_quality_repair_message_keeps_head_fallback_without_matching_diagnostic(tmp_path: Path) -> None:
    source = tmp_path / "main_test.go"
    source.write_text("head contract\n" + ("x" * 26000), encoding="utf-8")

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
