"""TS5110 exact owned config frontier through real public repair services.

Writers below are unit-injected effects, never production artifact receipts.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.director.runtime.public import (
    DirectorRepairRevalidationInputV1,
    DirectorRepairRevalidationRequestV1,
    PlanDirectorRepairCommandV1,
    QueryDirectorRepairCoverageV1,
    QueryDirectorRepairMaterializationPlanProbeV1,
    RunDirectorRepairCommandV1,
    normalize_director_repair_diagnostics,
    normalize_director_repair_issue_diagnostics,
    plan_director_repair,
    query_director_repair_coverage,
    query_director_repair_materialization_plan_probe,
    run_director_repair,
)

SOURCE_TOOL = "deterministic_typescript_tsconfig_lib_repair"


def _message(required: str = "NodeNext", resolution: str = "NodeNext") -> str:
    return f"Option 'module' must be set to '{required}' when option 'moduleResolution' is set to '{resolution}'."


def _raw(path: str = "tsconfig.json", resolution: str = "NodeNext") -> str:
    return f"{path}(3,5): error TS5110: {_message(resolution, resolution)}"


def _config(resolution: str = "NodeNext", **extra: object) -> str:
    return (
        json.dumps(
            {
                "compilerOptions": {
                    "module": "ESNext",
                    "moduleResolution": resolution,
                    "strict": True,
                    "target": "ES2020",
                    "lib": ["ES2020", "DOM"],
                    "noUnusedLocals": True,
                },
                "include": ["src/**/*.ts"],
                **extra,
            }
        )
        + "\n"
    )


def _plan(files: Mapping[str, str], raw: str) -> dict[str, Any]:
    return plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL, base_files=files, artifact_quality_errors=(raw,), mode="shadow"
        )
    ).to_dict()


@pytest.mark.parametrize("path", ["tsconfig.json", "configs/tsconfig.build.json"])
@pytest.mark.parametrize("resolution", ["NodeNext", "Node16"])
@pytest.mark.parametrize("typed", [False, True])
def test_exact_owned_config_is_covered_and_module_only_plannable(path: str, resolution: str, typed: bool) -> None:
    raw = _raw(path, resolution)
    issue = {
        "source": "artifact_quality",
        "code": "typescript_ts5110",
        "path": path,
        "message": _message(resolution, resolution),
        "raw": _raw("configs/stale.json", resolution),
    }
    diagnostics = (
        normalize_director_repair_issue_diagnostics((issue,))
        if typed
        else normalize_director_repair_diagnostics((raw,))
    )
    assert len(diagnostics) == 1
    assert diagnostics[0].code == "typescript_ts5110"
    assert diagnostics[0].path == path
    files = {
        path: _config(resolution),
        "configs/tsconfig.other.json": _config(),
        "src/main.ts": "export const n = 1;\n",
    }
    before = deepcopy(files)
    coverage = query_director_repair_coverage(
        QueryDirectorRepairCoverageV1(
            artifact_quality_errors=() if typed else (raw,), artifact_quality_issues=(issue,) if typed else ()
        )
    )
    assert coverage.items[0].known_rule_matched
    assert coverage.items[0].executable_runtime_plan_matched
    assert SOURCE_TOOL in coverage.items[0].matched_source_tools
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool=SOURCE_TOOL, base_files=files, diagnostics=diagnostics, mode="shadow")
    ).to_dict()
    assert result["ok"] and result["planned"]
    assert result["composition_summary"]["changed_paths"] == [path]
    assert result["plan_summary"]["operation_count"] == 1
    patch = result["composition_summary"]["patches"][0]
    assert patch["before_hash"] == hashlib.sha256(files[path].encode("utf-8")).hexdigest()
    expected = json.loads(files[path])
    expected["compilerOptions"]["module"] = resolution
    assert json.loads(patch["content_after"]) == expected
    probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            base_files=files,
            artifact_quality_errors=() if typed else (raw,),
            artifact_quality_issues=(issue,) if typed else (),
            mode="shadow",
        )
    )
    assert probe.status == "covered_plannable"
    assert SOURCE_TOOL in probe.plannable_source_tools
    assert files == before


@pytest.mark.parametrize(
    ("path", "content", "message"),
    [
        ("", _config(), _message()),
        ("src/main.ts", _config(), _message()),
        ("package.json", _config(), _message()),
        ("other.json", _config(), _message()),
        ("../tsconfig.json", _config(), _message()),
        ("/tmp/tsconfig.json", _config(), _message()),
        ("C:/tmp/tsconfig.json", _config(), _message()),
        ("configs//tsconfig.json", _config(), _message()),
        ("configs/./tsconfig.json", _config(), _message()),
        ("tsconfig.json", "", _message()),
        ("tsconfig.json", "{broken", _message()),
        ("tsconfig.json", '{// comment\n"compilerOptions":{}}', _message()),
        ("tsconfig.json", '{"compilerOptions":{"moduleResolution":"NodeNext",},}', _message()),
        (
            "tsconfig.json",
            '{"compilerOptions":{"moduleResolution":"NodeNext","moduleResolution":"Node16"}}',
            _message(),
        ),
        ("tsconfig.json", '{"compilerOptions":{"moduleResolution":"NodeNext"},"x":NaN}', _message()),
        ("tsconfig.json", "[]", _message()),
        ("tsconfig.json", '{"compilerOptions":[]}', _message()),
        ("tsconfig.json", '{"compilerOptions":{"moduleResolution":"NodeNext","module":null}}', _message()),
        ("tsconfig.json", '{"compilerOptions":{"moduleResolution":"NodeNext","module":false}}', _message()),
        ("tsconfig.json", '{"compilerOptions":{"module":"ESNext"}}', _message()),
        ("tsconfig.json", _config(extends="./base.json"), _message()),
        ("tsconfig.json", _config(extends=[]), _message()),
        ("tsconfig.json", _config(references=[]), _message()),
        ("tsconfig.json", _config("Node16"), _message()),
        ("tsconfig.json", _config().replace('"ESNext"', '"NodeNext"'), _message()),
        ("tsconfig.json", _config(), _message("Node16", "NodeNext")),
        ("tsconfig.json", _config(), "Adjust the module setting to fix TS5110."),
        ("tsconfig.json", _config(), _message() + " " + _message("Node16", "Node16")),
    ],
)
def test_unsafe_config_or_unproven_diagnostic_never_plans(path: str, content: str, message: str) -> None:
    files = {path: content} if path else {"tsconfig.json": content}
    issues = ({"code": "typescript_ts5110", "source": "artifact_quality", "path": path, "message": message},)
    diagnostics = normalize_director_repair_issue_diagnostics(issues)
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(source_tool=SOURCE_TOOL, base_files=files, diagnostics=diagnostics, mode="shadow")
    ).to_dict()
    assert not result["planned"]
    assert not result["composition_summary"]["patches"]
    probe = query_director_repair_materialization_plan_probe(
        QueryDirectorRepairMaterializationPlanProbeV1(
            artifact_quality_errors=(), artifact_quality_issues=issues, base_files=files, mode="shadow"
        )
    )
    assert SOURCE_TOOL not in probe.plannable_source_tools


def test_missing_exact_config_does_not_fall_back_to_root() -> None:
    result = _plan({"tsconfig.json": _config()}, _raw("configs/tsconfig.build.json"))
    assert not result["planned"]


def test_pathless_real_compiler_message_does_not_guess_root() -> None:
    assert not _plan({"tsconfig.json": _config()}, f"error TS5110: {_message()}")["planned"]


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_normalized_config_owners_must_fail_closed(reverse: bool) -> None:
    """Public input loses colliding keys before the authorized config planner.

    This required refusal remains a scope blocker, not an xfail or accepted plan.
    """
    entries = [("tsconfig.json", _config()), ("./tsconfig.json", _config().replace("true", "false"))]
    files = dict(reversed(entries) if reverse else entries)
    result = _plan(files, _raw())
    assert not result["planned"]


def test_two_config_diagnostics_keep_distinct_module_requirements() -> None:
    files = {"tsconfig.json": _config(), "configs/tsconfig.build.json": _config("Node16")}
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files=files,
            mode="shadow",
            artifact_quality_errors=(_raw(), _raw("configs/tsconfig.build.json", "Node16")),
        )
    ).to_dict()
    assert result["planned"] and result["plan_summary"]["operation_count"] == 2
    patches = {patch["path"]: json.loads(patch["content_after"]) for patch in result["composition_summary"]["patches"]}
    assert patches["tsconfig.json"]["compilerOptions"]["module"] == "NodeNext"
    assert patches["configs/tsconfig.build.json"]["compilerOptions"]["module"] == "Node16"


def test_no_ts5110_diagnostic_cannot_authorize_module_alignment() -> None:
    assert not _plan({"tsconfig.json": _config()}, "TypeScript config module is wrong.")["planned"]


def test_duplicate_same_config_diagnostic_composes_once() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={"tsconfig.json": _config()},
            artifact_quality_errors=(_raw(), _raw()),
            mode="shadow",
        )
    ).to_dict()
    assert result["planned"] and result["plan_summary"]["operation_count"] == 1


def test_conflicting_same_config_diagnostics_refuse_plan() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={"tsconfig.json": _config()},
            artifact_quality_errors=(_raw(), _raw(resolution="Node16")),
            mode="shadow",
        )
    )
    assert not result.planned


def test_other_diagnostic_does_not_expand_ts5110_module_only_plan() -> None:
    result = plan_director_repair(
        PlanDirectorRepairCommandV1(
            source_tool=SOURCE_TOOL,
            base_files={"tsconfig.json": _config()},
            mode="shadow",
            artifact_quality_errors=(_raw(), "src/main.ts(1,1): error TS2550: replaceAll needs ES2021 lib."),
        )
    ).to_dict()
    assert result["planned"]
    assert result["plan_summary"]["operation_count"] == 1
    after = json.loads(result["composition_summary"]["patches"][0]["content_after"])
    expected = json.loads(_config())
    expected["compilerOptions"]["module"] = "NodeNext"
    assert after == expected


@pytest.mark.parametrize("failure", ["owner", "stale"])
def test_policy_or_stale_content_denies_unit_effect(tmp_path: Path, failure: str) -> None:
    original = _config()
    current = original if failure == "owner" else original + " \n"
    target = tmp_path / "tsconfig.json"
    target.write_text(current, encoding="utf-8")
    calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        calls.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-ts5110-deny",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files={"tsconfig.json": original},
            artifact_quality_errors=(_raw(),),
            allowed_paths=("configs/tsconfig.other.json",) if failure == "owner" else ("tsconfig.json",),
        ),
        writer=writer,
    )
    assert not result.ok
    assert not calls
    assert target.read_text(encoding="utf-8") == current


def test_two_configs_cannot_expand_one_owner_unit_effect(tmp_path: Path) -> None:
    files = {"tsconfig.json": _config(), "configs/tsconfig.build.json": _config("Node16")}
    for path, content in files.items():
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    calls: list[str] = []

    def writer(path: str, content: str) -> dict[str, object]:
        calls.append(path)
        (tmp_path / path).write_text(content, encoding="utf-8")
        return {"ok": True}

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-ts5110-one-owner",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files=files,
            artifact_quality_errors=(_raw(), _raw("configs/tsconfig.build.json", "Node16")),
            allowed_paths=("tsconfig.json",),
        ),
        writer=writer,
    )
    assert not result.ok and not calls
    assert {path: (tmp_path / path).read_text(encoding="utf-8") for path in files} == files


@pytest.mark.parametrize("path", ["tsconfig.json", "configs/tsconfig.build.json"])
@pytest.mark.parametrize("resolution", ["NodeNext", "Node16"])
def test_real_private_compiler_repair_receipt_and_revalidation(tmp_path: Path, path: str, resolution: str) -> None:
    compiler = next(
        parent / "node_modules/typescript/bin/tsc"
        for parent in Path(__file__).resolve().parents
        if (parent / "node_modules/typescript/bin/tsc").is_file()
    )
    payload = json.loads(_config(resolution))
    payload["include"] = ["../src/main.ts" if path.startswith("configs/") else "src/main.ts"]
    original = json.dumps(payload, indent=2) + "\n"
    target = tmp_path / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(original, encoding="utf-8")
    source = tmp_path / "src/main.ts"
    source.parent.mkdir()
    source.write_text("export const n: number = 1;\n", encoding="utf-8")
    command = ("rtk", "proxy", str(compiler), "--pretty", "false", "--noEmit", "-p", path)

    def compile_project() -> subprocess.CompletedProcess[str]:
        return subprocess.run(command, cwd=tmp_path, capture_output=True, encoding="utf-8", check=False)

    before = compile_project()
    output = before.stdout + before.stderr
    assert before.returncode != 0 and "TS5110" in output
    diagnostics = normalize_director_repair_diagnostics((output,))
    assert len(diagnostics) == 1 and diagnostics[0].path == path
    planning = _plan({path: original}, output)
    assert planning["planned"]
    checks: list[subprocess.CompletedProcess[str]] = []

    def writer(relative: str, content: str) -> dict[str, object]:
        assert relative == path
        (tmp_path / relative).write_text(content, encoding="utf-8")
        return {"ok": True, "file": relative, "bytes_written": len(content.encode("utf-8"))}

    def revalidator(request: DirectorRepairRevalidationRequestV1) -> DirectorRepairRevalidationInputV1:
        assert request.files_changed == (path,)
        checked = compile_project()
        checks.append(checked)
        log = tmp_path / "revalidation.txt"
        log.write_text(checked.stdout + checked.stderr, encoding="utf-8")
        return DirectorRepairRevalidationInputV1(
            command=command,
            exit_code=checked.returncode,
            raw_output_ref=str(log),
            residual_artifact_quality_errors=(checked.stdout + checked.stderr,) if checked.returncode else (),
            metadata={"effect_identity": "unit_injected_writer", "production_artifact_receipt": False},
        )

    result = run_director_repair(
        RunDirectorRepairCommandV1(
            task_id="unit-private-ts5110",
            workspace=str(tmp_path),
            source_tool=SOURCE_TOOL,
            base_files={path: original},
            artifact_quality_errors=(output,),
            allowed_paths=(path,),
        ),
        writer=writer,
        revalidator=revalidator,
    )
    assert result.ok and len(checks) == 1 and checks[0].returncode == 0
    assert len(result.receipts) == 1 and not result.residual_diagnostics
    receipt = result.receipts[0].to_dict()
    assert receipt["before_hashes"][path] == hashlib.sha256(original.encode("utf-8")).hexdigest()
    assert receipt["after_hashes"][path] == hashlib.sha256(target.read_bytes()).hexdigest()
    expected = deepcopy(payload)
    expected["compilerOptions"]["module"] = resolution
    assert json.loads(target.read_text(encoding="utf-8")) == expected
    assert source.read_text(encoding="utf-8") == "export const n: number = 1;\n"
