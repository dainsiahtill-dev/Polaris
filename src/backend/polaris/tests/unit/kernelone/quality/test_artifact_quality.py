from __future__ import annotations

from pathlib import Path

from polaris.kernelone.quality import scan_workspace_artifact_quality


def _write_trivial_test(path: Path, *, count: int = 4) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"test('case {idx}', () => expect({idx} + 1).toBe({idx + 1}));" for idx in range(count)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_scoped_scan_detects_placeholder_tests_in_target_file(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["tests/unit/card-rules.test.ts"],
    )

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scoped_scan_ignores_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")
    changed = tmp_path / "src" / "feature.ts"
    changed.parent.mkdir(parents=True, exist_ok=True)
    changed.write_text("export const feature = true;\n", encoding="utf-8")

    errors = scan_workspace_artifact_quality(
        str(tmp_path),
        relative_paths=["src/feature.ts"],
    )

    assert errors == []


def test_full_scan_detects_unrelated_placeholder_tests(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "legacy.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path))

    assert errors
    assert "tests/unit/legacy.test.ts" in errors[0]


def test_scoped_scan_expands_declared_directory_targets(tmp_path: Path) -> None:
    _write_trivial_test(tmp_path / "tests" / "unit" / "card-rules.test.ts")

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["tests"])

    assert errors
    assert "tests/unit/card-rules.test.ts" in errors[0]


def test_scan_detects_generated_structural_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "generated.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const note = 'structural build passed';\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/generated.ts"])

    assert errors
    assert "structural build passed" in errors[0]


def test_scan_detects_audit_seed_scenario_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "game" / "rules-engine.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export const cardRulesEngineScenario0 = {
  title: "card-rules-engine planning scenario 0",
  tags: ["planning", "draft", "audit-seed"],
};
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/game/rules-engine.ts"])

    assert errors
    assert "audit-seed" in errors[0] or "planning scenario" in errors[0]


def test_scan_detects_structural_verification_scripts(tmp_path: Path) -> None:
    target = tmp_path / "scripts" / "build.mjs"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "console.log(`build verification completed: ${required.length} files`);\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["scripts/build.mjs"])

    assert errors
    assert "build verification completed" in errors[0]


def test_scan_detects_patch_residue_marker(tmp_path: Path) -> None:
    target = tmp_path / "src" / "assets" / "card-assets.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "export const assetReady = true;\n>>>> REPLACE src/assets/card-assets.ts\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/assets/card-assets.ts"])

    assert errors
    assert "patch residue marker" in errors[0]


def test_scan_detects_repeated_numeric_helper_filler(tmp_path: Path) -> None:
    target = tmp_path / "src" / "client" / "feature.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "\n".join(
            f"export function featureHelper{index}(value: number): number {{ return value + {index}; }}"
            for index in range(6)
        )
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/client/feature.ts"])

    assert errors
    assert "numeric helper filler" in errors[0]


def test_scan_detects_generic_payload_store_scaffold(tmp_path: Path) -> None:
    target = tmp_path / "src" / "state" / "store.ts"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        """
export interface CardRecord {
  payload: string;
  index: number;
}

export class CardStore {
  private readonly items = new Map<string, CardRecord>();
}

export function cardHelper1(value: number): number { return value + 1; }
export function cardHelper2(value: number): number { return value + 2; }
export function cardHelper3(value: number): number { return value + 3; }
""".strip()
        + "\n",
        encoding="utf-8",
    )

    errors = scan_workspace_artifact_quality(str(tmp_path), relative_paths=["src/state/store.ts"])

    assert errors
    assert any("generic payload/index store scaffold" in error for error in errors)
