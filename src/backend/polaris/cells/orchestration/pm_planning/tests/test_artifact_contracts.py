"""Authored writes, read dependencies and expected build outputs stay distinct."""

from copy import deepcopy
from pathlib import Path

import pytest


def _plan():
    return [
        {
            "id": "build",
            "phase": "implementation",
            "assigned_to": "Director",
            "target_files": ["src/main.ts"],
            "depends_on": [],
            "verification_commands": [{"modality": "build", "argv": ["npm", "run", "build"], "cwd": "."}],
            "generated_outputs": [
                {
                    "path": "dist/main.js",
                    "inputs": [{"task_id": "build", "path": "src/main.ts"}],
                    "build": {"argv": ["npm", "run", "build"], "cwd": "."},
                }
            ],
        },
        {
            "id": "verify",
            "phase": "verification",
            "assigned_to": "QA",
            "depends_on": ["build"],
            "target_files": ["qa/check.mjs"],
            "context_files": ["dist/main.js"],
            "verification_commands": [{"modality": "test", "argv": ["node", "qa/check.mjs"], "cwd": "."}],
        },
    ]


def _validate(tasks):
    from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts

    return validate_pm_artifact_contracts(tasks, primary_language="typescript")


def test_generated_promise_binds_by_build_value_and_stays_out_of_writes():
    tasks = _plan()
    original = deepcopy(tasks)
    result = _validate(tasks)
    assert result.ok, result.diagnostics
    assert result.authored_targets == ("src/main.ts", "qa/check.mjs")
    assert result.generated_outputs[0]["task_id"] == "build"
    assert result.generated_outputs[0]["path"] == "dist/main.js"
    assert tasks == original
    tasks[0]["verification_commands"].insert(0, {"modality": "lint", "argv": ["tsc", "--noEmit"], "cwd": "."})
    assert _validate(tasks).ok


@pytest.mark.parametrize(
    "defect",
    [
        "owner",
        "source",
        "build",
        "collision",
        "duplicate",
        "escape",
        "schema",
        "no_emit",
        "outdir",
        "cycle",
        "readonly_write",
        "consumer_dependency",
    ],
)
def test_generated_contract_rejects_invalid_bindings(defect):
    tasks = _plan()
    row = tasks[0]["generated_outputs"][0]
    if defect == "owner":
        row["inputs"][0]["task_id"] = "verify"
    elif defect == "source":
        row["inputs"][0]["path"] = "dist/main.js"
    elif defect == "build":
        row["build"]["argv"] = ["npm", "run", "other"]
    elif defect == "collision":
        tasks[1]["target_files"].append("dist/main.js")
    elif defect == "duplicate":
        tasks[0]["generated_outputs"].append(deepcopy(row))
    elif defect == "escape":
        row["path"] = "../main.js"
    elif defect == "schema":
        row["owner"] = "build"
    elif defect in {"no_emit", "outdir"}:
        argv = (
            ["tsc", "src/main.ts", "--noEmit"] if defect == "no_emit" else ["tsc", "src/main.ts", "--outDir", "other"]
        )
        row["build"]["argv"] = argv
        tasks[0]["verification_commands"][0]["argv"] = argv
    elif defect == "cycle":
        tasks[0]["depends_on"] = ["verify"]
    elif defect == "readonly_write":
        tasks[1]["context_files"].append("qa/check.mjs")
    elif defect == "consumer_dependency":
        tasks[1]["depends_on"] = []
    result = _validate(tasks)
    assert not result.ok
    assert result.generated_outputs == ()


@pytest.mark.parametrize("value", [None, "dist/main.js", [None], ["dist/main.js"], [{}]])
def test_present_generated_field_is_never_salvaged(value):
    tasks = _plan()
    tasks[0]["generated_outputs"] = value
    assert not _validate(tasks).ok


def test_passive_milestone_cannot_masquerade_as_code_edit():
    tasks = _plan()
    tasks[1]["scope"] = "Stage milestone, no code changes"
    result = _validate(tasks)
    assert not result.ok
    assert any(d["code"] == "artifact_action_contract_invalid" for d in result.diagnostics)


def test_generated_build_cwd_rejects_control_characters():
    tasks = _plan()
    tasks[0]["verification_commands"][0]["cwd"] = "build\x00dir"
    tasks[0]["generated_outputs"][0]["build"]["cwd"] = "build\x00dir"
    assert not _validate(tasks).ok


def test_invalid_metadata_is_a_diagnostic_instead_of_validator_exception():
    tasks = _plan()
    tasks[0]["metadata"] = None
    assert not _validate(tasks).ok


@pytest.mark.parametrize("argv", [["tsc", "--noEmit"], ["npm", "run", "build"]])
def test_readonly_prose_and_stems_do_not_require_output_declarations(argv):
    task = _plan()[0]
    task.pop("generated_outputs")
    task["context_files"] = ["fixtures/main.js"]
    task["description"] = "Read fixtures/main.js; do not generate it."
    task["verification_commands"][0]["argv"] = argv
    original = deepcopy(task)
    assert _validate([task]).ok
    assert task == original


@pytest.mark.parametrize("language", ["", "typescript"])
@pytest.mark.parametrize("argv", [["tsc", "--noEmit"], ["tsc", "--outDir", "other"], ["tsc", "--outFile", "other.js"]])
def test_explicit_compiler_contradictions_do_not_depend_on_catalog(language, argv):
    from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts

    tasks = _plan()
    tasks[0]["verification_commands"][0]["argv"] = argv
    tasks[0]["generated_outputs"][0]["build"]["argv"] = argv
    result = validate_pm_artifact_contracts(tasks, primary_language=language)
    assert "generated_output_build_invalid" in {d["code"] for d in result.diagnostics}


@pytest.mark.parametrize("language", ["", "typescript"])
@pytest.mark.parametrize(
    "output,argv,allowed",
    [
        ("dist/main.d.ts", ["tsc", "--noEmit"], False),
        ("dist/main.js.map", ["tsc", "--sourceMap", "--outDir", "elsewhere"], False),
        ("dist/main.d.ts", ["tsc", "--declaration", "--emitDeclarationOnly", "--outDir", "dist", "src/main.ts"], True),
        ("dist/main.js.map", ["tsc", "--sourceMap", "--outDir", "dist", "src/main.ts"], True),
        ("dist/main.js.map", ["tsc", "--emitDeclarationOnly", "--declaration", "--outDir", "dist"], False),
        ("dist/main.d.ts.map", ["tsc", "--noEmit", "--declarationMap"], False),
        (
            "dist/main.d.ts.map",
            ["tsc", "--declaration", "--emitDeclarationOnly", "--declarationMap", "--outDir", "dist"],
            True,
        ),
        ("dist/bundle.d.ts", ["tsc", "--declaration", "--emitDeclarationOnly", "--outFile", "dist/bundle.js"], True),
        ("dist/bundle.js.map", ["tsc", "--sourceMap", "--outFile", "dist/bundle.js"], True),
        ("dist/main.d.ts", ["tsc", "--declaration", "--outFile", "other.js"], False),
        ("dist/main.js.map", ["tsc", "--sourceMap", "--outFile", "other.js"], False),
        ("build.tsbuildinfo", ["tsc", "--noEmit", "--incremental"], True),
    ],
)
def test_explicit_compiler_options_follow_output_kind(language, output, argv, allowed):
    from polaris.cells.orchestration.pm_planning.public.artifact_contracts import validate_pm_artifact_contracts

    task = _plan()[0]
    task["verification_commands"][0]["argv"] = argv
    record = task["generated_outputs"][0]
    record["path"] = output
    record["build"]["argv"] = argv
    result = validate_pm_artifact_contracts([task], primary_language=language)
    assert result.ok is allowed, result.diagnostics
    if not allowed:
        assert "generated_output_build_invalid" in {row["code"] for row in result.diagnostics}


def test_javascript_write_declaration_does_not_invent_a_generated_output_promise():
    tasks = _plan()
    tasks[0].pop("generated_outputs")
    tasks[1]["target_files"] = ["dist/main.js"]
    tasks[1]["context_files"] = []
    tasks[1]["verification_commands"] = []
    result = _validate(tasks)
    assert result.ok, result.diagnostics
    assert result.generated_outputs == ()
    tasks[0]["verification_commands"][0]["argv"] = ["tsc", "--noEmit"]
    result = _validate(tasks)
    assert result.ok, result.diagnostics
    assert result.generated_outputs == ()


def test_artifact_public_boundary_and_dependencies_are_mirrored_in_catalog():
    import yaml

    backend = Path(__file__).resolve().parents[5]
    catalog = yaml.safe_load((backend / "docs/graph/catalog/cells.yaml").read_text(encoding="utf-8"))
    cells = {row["id"]: row for row in catalog["cells"]}
    module = "polaris.cells.orchestration.pm_planning.public.artifact_contracts"
    test = "polaris/cells/orchestration/pm_planning/tests/test_artifact_contracts.py"
    for source in [
        cells["orchestration.pm_planning"],
        yaml.safe_load((backend / "polaris/cells/orchestration/pm_planning/cell.yaml").read_text(encoding="utf-8")),
    ]:
        assert module in source["public_contracts"]["modules"]
        assert module in source["current_modules"]
        assert test in source["verification"]["tests"]
    for identity in ["factory.pipeline", "chief_engineer.blueprint"]:
        path = backend / "polaris/cells" / identity.replace(".", "/") / "cell.yaml"
        assert "orchestration.pm_planning" in cells[identity]["depends_on"]
        assert "orchestration.pm_planning" in yaml.safe_load(path.read_text(encoding="utf-8"))["depends_on"]
