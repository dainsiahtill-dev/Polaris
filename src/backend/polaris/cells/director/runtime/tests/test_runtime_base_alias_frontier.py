"""Pre-normalization ownership frontier using public repair services.

Effects below are private unit injections, not production artifact receipts.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    RunDirectorRepairCommandV1,
    normalize_director_repair_diagnostics,
    plan_director_repair,
    query_director_repair_coverage,
    query_director_repair_materialization_plan_probe,
    run_director_repair,
)

SOURCE_TOOL = "deterministic_typescript_tsconfig_lib_repair"


def _config(resolution: str = "NodeNext") -> str:
    return json.dumps({"compilerOptions": {"module": "ESNext", "moduleResolution": resolution, "strict": True}}) + "\n"


def _raw(path: str = "tsconfig.json", resolution: str = "NodeNext") -> str:
    return (
        f"{path}(3,5): error TS5110: Option 'module' must be set to '{resolution}' "
        f"when option 'moduleResolution' is set to '{resolution}'."
    )


def _inputs(errors: tuple[str, ...], typed: bool) -> dict[str, Any]:
    if typed:
        return {"diagnostics": normalize_director_repair_diagnostics(errors)}
    return {"artifact_quality_errors": errors}


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("path", "alias", "resolution"),
    [
        ("tsconfig.json", "./tsconfig.json", "NodeNext"),
        ("configs/tsconfig.build.json", ".\\configs\\tsconfig.build.json", "Node16"),
        ("tsconfig.json", " ././tsconfig.json ", "Node16"),
        ("configs/tsconfig.build.json", "configs\\tsconfig.build.json", "NodeNext"),
    ],
)
def test_conflicting_config_aliases_refuse_public_plan_probe_and_all_effects(
    tmp_path: Path, path: str, alias: str, resolution: str, typed: bool, reverse: bool
) -> None:
    """Catch either entrypoint choosing the last owner before the conflict guard."""
    original = _config(resolution)
    other = original.replace("true", "false")
    entries = [(path, original), (alias, other)]
    files = dict(reversed(entries) if reverse else entries)
    files["other/tsconfig.json"] = _config()
    before = deepcopy(files)
    errors = (_raw(path, resolution),)
    diagnostics = normalize_director_repair_diagnostics(errors)
    coverage = query_director_repair_coverage(QueryDirectorRepairCoverageV1(errors))
    assert coverage.items[0].known_rule_matched
    assert SOURCE_TOOL in coverage.items[0].matched_source_tools
    plan = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool=SOURCE_TOOL, base_files=files, **_inputs(errors, typed))
    )
    assert not plan.ok and not plan.planned
    assert not plan.composition_summary.patches and plan.effect_plan is None
    assert plan.diagnostics == diagnostics
    probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(base_files=files, artifact_quality_errors=errors)
    )
    assert probe.status == "coverage_matched_but_unplannable"
    assert SOURCE_TOOL not in probe.plannable_source_tools
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(original, encoding="utf-8")
    effects: list[str] = []

    def denied_effect(*args: object, **kwargs: object) -> dict[str, object]:
        effects.append("effect")
        raise AssertionError("Conflict must precede writer/editor/deleter/revalidator")

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-alias-conflict",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files=files,
            allowed_paths=(path,),
            **_inputs(errors, typed),
        ),
        writer=denied_effect,
        editor=denied_effect,
        deleter=denied_effect,
        revalidator=denied_effect,
    )
    assert not result.ok and not result.receipts
    assert result.error_code == "conflicting_base_file_alias"
    assert path in str(result.error_message) and alias in str(result.error_message)
    assert hashlib.sha256(original.encode("utf-8")).hexdigest() in str(result.error_message)
    assert hashlib.sha256(other.encode("utf-8")).hexdigest() in str(result.error_message)
    assert not effects
    assert target.read_text(encoding="utf-8") == original
    assert result.metadata["planning"]["diagnostics"] == plan.to_dict()["diagnostics"]
    assert files == before


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize(
    ("path", "alias"),
    [("src/main.ts", "./src/main.ts"), ("Cargo.toml", "cargo.toml"), ("nested/Cargo.lock", ".\\nested\\CARGO.LOCK")],
)
def test_conflicting_non_target_and_declared_manifest_aliases_cannot_hide_in_input(
    tmp_path: Path, path: str, alias: str, reverse: bool
) -> None:
    entries = [(path, "first\n"), (alias, "second\n")]
    files = {"tsconfig.json": _config(), **dict(reversed(entries) if reverse else entries)}
    plan = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool=SOURCE_TOOL, base_files=files, artifact_quality_errors=(_raw(),))
    )
    assert not plan.planned and plan.effect_plan is None
    effects: list[str] = []

    def denied_effect(*args: object, **kwargs: object) -> dict[str, object]:
        effects.append("effect")
        raise AssertionError("Ambiguous input cannot execute")

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-non-target-alias",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files=files,
            artifact_quality_errors=(_raw(),),
        ),
        writer=denied_effect,
        editor=denied_effect,
        deleter=denied_effect,
        revalidator=denied_effect,
    )
    assert not result.ok and not effects and not result.receipts


@pytest.mark.parametrize("typed", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_same_content_aliases_preserve_multi_config_hashes_and_unit_effects(
    tmp_path: Path, typed: bool, reverse: bool
) -> None:
    originals = {"tsconfig.json": _config(), "configs/tsconfig.build.json": _config("Node16")}
    entries = [
        ("tsconfig.json", originals["tsconfig.json"]),
        ("./tsconfig.json", originals["tsconfig.json"]),
        ("configs/tsconfig.build.json", originals["configs/tsconfig.build.json"]),
        (".\\configs\\tsconfig.build.json", originals["configs/tsconfig.build.json"]),
        ("Cargo.toml", "[package]\n"),
        ("cargo.toml", "[package]\n"),
        ("src/A.ts", "export const upper = 1;\n"),
        ("src/a.ts", "export const lower = 2;\n"),
    ]
    files = dict(reversed(entries) if reverse else entries)
    before = deepcopy(files)
    errors = (_raw(), _raw("configs/tsconfig.build.json", "Node16"))
    plan = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool=SOURCE_TOOL, base_files=files, **_inputs(errors, typed))
    )
    assert plan.ok and plan.planned
    patches = {patch["path"]: patch for patch in plan.composition_summary.to_dict()["patches"]}
    assert set(patches) == set(originals)
    for path, original in originals.items():
        patch = patches[path]
        assert patch["before_hash"] == hashlib.sha256(original.encode("utf-8")).hexdigest()
        assert patch["after_hash"] == hashlib.sha256(patch["content_after"].encode("utf-8")).hexdigest()
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(original, encoding="utf-8")
    writes: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        writes.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True, "file": path}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-equivalent-alias",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files=files,
            allowed_paths=tuple(originals),
            **_inputs(errors, typed),
        ),
        writer=writer,
    )
    assert result.ok and set(writes) == set(originals) and len(writes) == 2
    receipt = result.receipts[0].to_dict()
    assert not result.receipts[0].authoritative
    for path in originals:
        assert receipt["before_hashes"][path] == patches[path]["before_hash"]
        assert receipt["after_hashes"][path] == patches[path]["after_hash"]
        assert (tmp_path / path).read_text(encoding="utf-8") == patches[path]["content_after"]
    assert files == before


def test_equivalent_aliases_still_reject_stale_composer_input(tmp_path: Path) -> None:
    original = _config()
    current = original + " \n"
    target = tmp_path / "tsconfig.json"
    target.write_text(current, encoding="utf-8")
    effects: list[str] = []

    def denied_effect(*args: object, **kwargs: object) -> dict[str, object]:
        effects.append("effect")
        raise AssertionError("Stale snapshot cannot execute")

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-alias-stale",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files={"tsconfig.json": original, "./tsconfig.json": original},
            artifact_quality_errors=(_raw(),),
        ),
        writer=denied_effect,
        editor=denied_effect,
        deleter=denied_effect,
    )
    assert not result.ok and not effects
    assert target.read_text(encoding="utf-8") == current
