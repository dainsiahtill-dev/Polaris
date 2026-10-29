"""Tests for PM plan language consistency validation.

Regression tests for the fix that detects when the PM model plans files
in the wrong language (e.g. Java files for a JavaScript project).
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from copy import deepcopy
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor


class TestPMPlanLanguageConsistency:
    """Validate _validate_pm_plan_language_consistency catches mismatches."""

    def _build_executor(self, tmp_path: Path, tasks: list[dict[str, Any]], language: str) -> Any:
        """Build a minimal stage executor with workspace fixtures."""
        from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
            OrchestrationStageExecutor,
        )

        # Write catalog_contract.json
        polaris_dir = tmp_path / ".polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)
        catalog_contract = {"primary_language": language, "project_id": "TEST-01"}
        (polaris_dir / "catalog_contract.json").write_text(json.dumps(catalog_contract), encoding="utf-8")

        # Build minimal executor via __new__ (skip __init__)
        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = tmp_path
        return executor

    def test_javascript_project_with_java_files_detected(self, tmp_path: Path) -> None:
        """PM plans .java files for a javascript project -> mismatch."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement dream alchemy",
                "target_files": [
                    "src/main/java/polaris/factory/Main.java",
                    "src/main/java/polaris/factory/domain/RhythmMonster.java",
                    "tests/test_product.py",
                    "README.md",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "javascript")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result != ""
        assert "pm_plan_language_mismatch" in result
        assert "javascript" in result

    def test_javascript_project_with_js_files_passes(self, tmp_path: Path) -> None:
        """PM plans .js files for a javascript project -> consistent."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement dream alchemy",
                "target_files": [
                    "src/models/dream_note.js",
                    "src/engine/alchemy.js",
                    "src/index.js",
                    "tests/test_product.py",
                    "README.md",
                    "package.json",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "javascript")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_rust_project_with_rust_files_passes(self, tmp_path: Path) -> None:
        """PM plans .rs files for a rust project -> consistent."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement stardust alchemy",
                "target_files": [
                    "src/main.rs",
                    "src/lib.rs",
                    "src/models/recipe.rs",
                    "tests/test_product.py",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "rust")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_rust_project_with_python_files_detected(self, tmp_path: Path) -> None:
        """PM plans .py files for a rust project -> mismatch."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement stardust alchemy",
                "target_files": [
                    "src/main.py",
                    "src/engine.py",
                    "tests/test_product.py",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "rust")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result != ""
        assert "pm_plan_language_mismatch" in result

    def test_no_catalog_contract_skips_validation(self, tmp_path: Path) -> None:
        """Missing catalog_contract.json -> skip validation (non-bench project)."""
        from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = tmp_path
        result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_unknown_language_skips_validation(self, tmp_path: Path) -> None:
        """Unknown primary_language -> skip validation."""
        polaris_dir = tmp_path / ".polaris"
        polaris_dir.mkdir(parents=True, exist_ok=True)
        catalog_contract = {"primary_language": "haskell", "project_id": "TEST-01"}
        (polaris_dir / "catalog_contract.json").write_text(json.dumps(catalog_contract), encoding="utf-8")

        from polaris.cells.factory.pipeline.internal.factory_stage_executor import (
            OrchestrationStageExecutor,
        )

        executor = OrchestrationStageExecutor.__new__(OrchestrationStageExecutor)
        executor.workspace = tmp_path
        tasks = [{"id": "TASK-1", "goal": "x", "target_files": ["src/Main.hs"]}]
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_neutral_extensions_ignored(self, tmp_path: Path) -> None:
        """Config/doc files (.json, .md, .html) don't trigger mismatch."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Setup",
                "target_files": [
                    "package.json",
                    "README.md",
                    "index.html",
                    "tsconfig.json",
                    "src/index.js",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "javascript")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_mixed_correct_and_wrong_extensions(self, tmp_path: Path) -> None:
        """Some correct + some wrong -> mismatch detected."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement",
                "target_files": [
                    "src/index.js",
                    "src/engine.js",
                    "src/wrong.java",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "javascript")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result != ""
        assert "wrong.java" in result

    def test_go_project_with_go_files_passes(self, tmp_path: Path) -> None:
        """PM plans .go files for a go project -> consistent."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement Go CLI",
                "target_files": [
                    "main.go",
                    "internal/engine.go",
                    "internal/model.go",
                    "tests/test_product.py",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "go")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_go_project_with_go_mod_manifest_passes(self, tmp_path: Path) -> None:
        """Go manifest files are language-neutral and must not trigger mismatch."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement Go CLI",
                "target_files": [
                    "go.mod",
                    "go.sum",
                    "models/capsule.go",
                    "engine/museum.go",
                    "main.go",
                    "tests/test_product.py",
                    "README.md",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "go")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_go_project_with_shell_qa_script_passes(self, tmp_path: Path) -> None:
        """Regression: a Go project whose plan includes a required shell QA/build
        helper (e.g. ``scripts/qa.sh``) must NOT be flagged as a language mismatch.

        Build/QA/run scripts are language-neutral auxiliary automation that
        legitimately appears in projects of any primary language. Before the fix,
        ``scripts/qa.sh`` tripped the guard ('.sh' not in {'.go'}) and
        deterministically failed PM planning for compiled-language projects whose
        acceptance criteria require an executable verification script.
        """
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement ASCII pet terminal",
                "target_files": [
                    "go.mod",
                    "main.go",
                    "models/pet.go",
                    "scripts/qa.sh",
                    "scripts/build.sh",
                    "README.md",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "go")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result == ""

    def test_shell_helper_does_not_mask_real_language_mismatch(self, tmp_path: Path) -> None:
        """A neutral shell helper must not suppress a genuine wrong-language file."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement Go CLI",
                "target_files": [
                    "main.go",
                    "scripts/qa.sh",  # neutral helper -> allowed
                    "src/Engine.java",  # genuine wrong language -> must still flag
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "go")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result != ""
        assert "Engine.java" in result

    def test_cpp_project_with_java_files_detected(self, tmp_path: Path) -> None:
        """PM plans .java files for a cpp project -> mismatch."""
        tasks = [
            {
                "id": "TASK-1",
                "goal": "Implement C++ tool",
                "target_files": [
                    "src/Main.java",
                    "src/Engine.java",
                ],
            }
        ]
        executor = self._build_executor(tmp_path, tasks, "cpp")
        with patch.object(type(executor), "_load_pm_plan_tasks", return_value=tasks):
            result = executor._validate_pm_plan_language_consistency("tasks/plan.json")
        assert result != ""
        assert "pm_plan_language_mismatch" in result


def _declared_build_task() -> dict[str, Any]:
    return {
        "id": "BUILD",
        "phase": "implementation",
        "assigned_to": "Director",
        "target_files": ["src/main.ts", "dist/main.js"],
        "verification_commands": [
            {"modality": "build", "argv": ["npx", "tsc", "-p", "."], "cwd": "."},
            {"modality": "entrypoint", "argv": ["node", "dist/main.js"], "cwd": "."},
        ],
    }


def _declared_qa_task() -> dict[str, Any]:
    return {
        "id": "QA",
        "phase": "verification",
        "assigned_to": "QA",
        "target_files": ["qa/checks.mjs"],
        "verification_commands": [{"modality": "test", "argv": ["node", "qa/checks.mjs"], "cwd": "."}],
    }


def _evaluate_loaded_plan(tmp_path: Path, plan: dict[str, Any], language: str = "typescript") -> str:
    """Exercise production construction/storage layout/loader without generating a product."""
    executor = OrchestrationStageExecutor(tmp_path)
    catalog = tmp_path / ".polaris/catalog_contract.json"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    catalog.write_text(json.dumps({"primary_language": language, "project_id": "LANGUAGE-TEST"}), encoding="utf-8")
    target = executor._artifact_path("tasks/plan.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(plan, ensure_ascii=False)
    target.write_text(encoded, encoding="utf-8")
    assert executor._load_pm_plan_tasks() == plan["tasks"]
    result = executor._validate_pm_plan_language_consistency()
    assert target.read_text(encoding="utf-8") == encoded
    assert isinstance(result, str)
    (tmp_path / "language-gate-result.json").write_text(
        json.dumps(
            {
                "diagnostic": result,
                "plan_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
                "task_count": len(plan["tasks"]),
                "planning_declaration_only": True,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return result


@pytest.fixture
def pol73_plan() -> dict[str, Any]:
    """Exact eight-task observed plan; compression retains every original metadata field."""
    raw = zlib.decompress(base64.b64decode(_POL73_PLAN_COMPRESSED))
    assert hashlib.sha256(raw).hexdigest() == _POL73_PLAN_SHA256
    plan: dict[str, Any] = json.loads(raw.decode("utf-8"))
    assert len(plan["tasks"]) == 8
    assert plan["quality_gate"]["score"] == 100
    return plan


def test_exact_pol73_plan_classifies_artifacts_without_changing_plan(
    tmp_path: Path, pol73_plan: dict[str, Any]
) -> None:
    assert _evaluate_loaded_plan(tmp_path, pol73_plan) == ""


@pytest.mark.parametrize("role", ["build", "qa"])
def test_declared_artifact_roles_independently(tmp_path: Path, role: str) -> None:
    tasks = [_declared_build_task()]
    if role == "qa":
        tasks[0]["target_files"] = ["src/main.ts"]
        tasks.append(_declared_qa_task())
    assert _evaluate_loaded_plan(tmp_path, {"tasks": tasks}) == ""


@pytest.mark.parametrize("role", ["compiled_output", "qa_script"])
def test_exact_pol73_roles_independently(tmp_path: Path, pol73_plan: dict[str, Any], role: str) -> None:
    plan = deepcopy(pol73_plan)
    if role == "compiled_output":
        plan["tasks"][7]["target_files"].remove("qa/checks.mjs")
    else:
        plan["tasks"][3]["target_files"].remove("dist/main.js")
    assert _evaluate_loaded_plan(tmp_path, plan) == ""


@pytest.mark.parametrize(
    "defect",
    [
        "foreign_source",
        "foreign_under_dist",
        "no_build",
        "no_emit",
        "declaration_only",
        "eval_entrypoint",
        "business_qa",
        "eval_test",
        "wrong_cwd",
        "conflicting_owner",
        "unsafe_target",
        "no_primary",
        "borrowed_build",
        "disguised_business",
    ],
)
def test_exact_pol73_negative_boundaries(tmp_path: Path, pol73_plan: dict[str, Any], defect: str) -> None:
    """Keep the Root boundary controls on the complete plan, including unrelated tasks."""
    plan = deepcopy(pol73_plan)
    tasks = plan["tasks"]
    build, qa = tasks[3], tasks[7]
    expected = "dist/main.js"
    if defect == "foreign_source":
        expected = "src/Engine.java"
        tasks[2]["target_files"].append(expected)
    elif defect == "foreign_under_dist":
        expected = "dist/Engine.java"
        build["target_files"].append(expected)
    elif defect in {"no_build", "borrowed_build"}:
        compiler = build["verification_commands"].pop(0)
        if defect == "borrowed_build":
            tasks[2]["verification_commands"].append(compiler)
    elif defect in {"no_emit", "declaration_only"}:
        build["verification_commands"][0]["argv"].append("--noEmit" if defect == "no_emit" else "--emitDeclarationOnly")
    elif defect == "eval_entrypoint":
        build["verification_commands"][1]["argv"] = ["node", "-e", "require('fs').accessSync('dist/main.js')"]
    else:
        expected = "qa/checks.mjs"
        if defect == "business_qa":
            qa["phase"], qa["assigned_to"] = "implementation", "Director"
        elif defect == "eval_test":
            qa["verification_commands"][0]["argv"] = ["node", "-e", "require('fs').accessSync('qa/checks.mjs')"]
        elif defect == "wrong_cwd":
            qa["verification_commands"][0]["cwd"] = "other"
        elif defect == "conflicting_owner":
            tasks[2]["target_files"].append(expected)
        elif defect == "unsafe_target":
            expected = "../escape.mjs"
            qa["target_files"] = [expected]
            qa["verification_commands"][0]["argv"] = ["node", expected]
        elif defect == "no_primary":
            for task in tasks:
                task["target_files"] = [p for p in task["target_files"] if not p.endswith((".ts", ".tsx"))]
        elif defect == "disguised_business":
            expected = "qa/business.js"
            tasks[2]["target_files"].append(expected)
    diagnostic = _evaluate_loaded_plan(tmp_path, plan)
    assert "pm_plan_language_mismatch" in diagnostic
    assert expected in diagnostic


@pytest.mark.parametrize("path", ["src/Engine.java", "dist/Engine.java", "qa/business.js", "tests/Engine.java"])
def test_valid_auxiliaries_do_not_hide_foreign_product_source(tmp_path: Path, path: str) -> None:
    tasks = [_declared_build_task(), _declared_qa_task()]
    tasks[0]["target_files"].append(path)
    diagnostic = _evaluate_loaded_plan(tmp_path, {"tasks": tasks})
    assert "pm_plan_language_mismatch" in diagnostic
    assert path in diagnostic
    assert "qa/checks.mjs" not in diagnostic
    assert "dist/main.js" not in diagnostic


@pytest.mark.parametrize(
    "argv",
    [
        ["npx", "tsc", "--noEmit"],
        ["npx", "tsc", "--noEmit=true"],
        ["npx", "tsc", "--emitDeclarationOnly"],
        ["npx", "tsc", "--emitDeclarationOnly=true"],
        ["tsc", "--help"],
        ["tsc", "--version"],
        ["tsc", "--showConfig"],
        ["tsc", "--listFilesOnly"],
        ["npx", "tsc", "-p", "unrelated"],
        ["npx", "tsc", "-p"],
        ["npx", "tsc", "-p", ".", "src/main.ts"],
        ["tsc", "-p", ".", "--project", "other"],
        ["tsc", "src/main.ts", "src/other.ts"],
        ["echo", "tsc"],
        ["sh", "-c", "npx tsc -p ."],
        ["npx", "typescript", "-p", "."],
    ],
)
def test_output_requires_supported_same_task_emitting_build(tmp_path: Path, argv: list[str]) -> None:
    task = _declared_build_task()
    task["verification_commands"][0]["argv"] = argv
    assert "dist/main.js" in _evaluate_loaded_plan(tmp_path, {"tasks": [task]})


@pytest.mark.parametrize(
    "defect",
    [
        "no_build",
        "no_sources",
        "declaration_only_source",
        "test_sources",
        "wrong_modality",
        "build_cwd",
        "entrypoint_cwd",
        "other_entrypoint",
        "prose_only",
        "cross_task",
        "extra_output",
    ],
)
def test_output_declaration_is_bound_to_its_owner(tmp_path: Path, defect: str) -> None:
    task = _declared_build_task()
    tasks = [task]
    expected = "dist/main.js"
    if defect == "no_build":
        task["verification_commands"].pop(0)
    elif defect == "no_sources":
        task["target_files"].remove("src/main.ts")
    elif defect == "declaration_only_source":
        task["target_files"][0] = "src/main.d.ts"
    elif defect == "test_sources":
        task["target_files"][0] = "tests/main.test.ts"
    elif defect == "wrong_modality":
        task["verification_commands"][0]["modality"] = "lint"
    elif defect == "build_cwd":
        task["verification_commands"][0]["cwd"] = "other"
    elif defect == "entrypoint_cwd":
        task["verification_commands"][1]["cwd"] = "other"
    elif defect == "other_entrypoint":
        task["verification_commands"][1]["argv"] = ["node", "dist/other.js"]
    elif defect == "prose_only":
        task["verification_commands"] = []
        task["title"] = "QA generated output"
        task["description"] = "Compile src/main.ts with npx tsc -p . and execute node dist/main.js"
    elif defect == "cross_task":
        tasks.append(
            {
                "id": "OTHER",
                "target_files": ["src/other.ts"],
                "verification_commands": [task["verification_commands"].pop(0)],
            }
        )
    elif defect == "extra_output":
        expected = "dist/business.js"
        task["target_files"].append(expected)
    assert expected in _evaluate_loaded_plan(tmp_path, {"tasks": tasks})


@pytest.mark.parametrize(
    "argv",
    [
        ["node", "-e", "require('./qa/checks.mjs')"],
        ["node", "--eval=qa/checks.mjs"],
        ["node", "--check", "qa/checks.mjs"],
        ["node", "--version", "qa/checks.mjs"],
        ["sh", "-c", "node qa/checks.mjs"],
        ["echo", "qa/checks.mjs"],
        ["node", "qa/other.mjs"],
    ],
)
def test_qa_requires_direct_test_script_invocation(tmp_path: Path, argv: list[str]) -> None:
    build, qa = _declared_build_task(), _declared_qa_task()
    qa["verification_commands"][0]["argv"] = argv
    assert "qa/checks.mjs" in _evaluate_loaded_plan(tmp_path, {"tasks": [build, qa]})


@pytest.mark.parametrize(
    "defect",
    [
        "phase_only",
        "role_only",
        "no_verifier",
        "wrong_modality",
        "wrong_cwd",
        "no_primary",
        "test_only_primary",
        "cross_task",
        "extra_helper",
        "conflicting_owner",
        "prose_only",
        "business_targets",
    ],
)
def test_qa_declaration_cannot_hide_implementation_ownership(tmp_path: Path, defect: str) -> None:
    build, qa = _declared_build_task(), _declared_qa_task()
    tasks = [build, qa]
    expected = "qa/checks.mjs"
    if defect == "phase_only":
        qa["assigned_to"] = "Director"
    elif defect == "role_only":
        qa["phase"] = "implementation"
    elif defect == "no_verifier":
        qa["verification_commands"] = []
    elif defect == "wrong_modality":
        qa["verification_commands"][0]["modality"] = "entrypoint"
    elif defect == "wrong_cwd":
        qa["verification_commands"][0]["cwd"] = "other"
    elif defect == "no_primary":
        build["target_files"] = ["package.json"]
    elif defect == "test_only_primary":
        build["target_files"] = ["tests/main.test.ts"]
    elif defect == "cross_task":
        build["verification_commands"].append(qa["verification_commands"].pop(0))
    elif defect == "extra_helper":
        expected = "qa/business.js"
        qa["target_files"].append(expected)
    elif defect == "conflicting_owner":
        build["target_files"].append(expected)
    elif defect == "prose_only":
        qa["verification_commands"] = []
        qa["description"] = "Run node qa/checks.mjs as test"
    elif defect == "business_targets":
        qa["target_files"].append("src/business.ts")
    assert expected in _evaluate_loaded_plan(tmp_path, {"tasks": tasks})


@pytest.mark.parametrize(
    "path",
    [
        "../checks.mjs",
        "/qa/checks.mjs",
        "C:/qa/checks.mjs",
        "C:qa/checks.mjs",
        "qa/../checks.mjs",
        "qa//checks.mjs",
        "qa/./checks.mjs",
        "qa/checks.mjs/",
        " qa/checks.mjs",
        "qa/checks.mjs ",
        "~/checks.mjs",
        "$ROOT/checks.mjs",
        "qa/*.mjs",
        "qa/checks.mjs\n",
    ],
)
def test_unsafe_target_never_becomes_auxiliary(tmp_path: Path, path: str) -> None:
    build, qa = _declared_build_task(), _declared_qa_task()
    qa["target_files"] = [path]
    qa["verification_commands"][0]["argv"] = ["node", path]
    assert "pm_plan_language_mismatch" in _evaluate_loaded_plan(tmp_path, {"tasks": [build, qa]})


@pytest.mark.parametrize("cwd", ["..", "/tmp", "C:/tmp", "qa/..", "qa//nested", "qa/", " ", None, 7])
def test_unsafe_cwd_never_binds_auxiliary(tmp_path: Path, cwd: Any) -> None:
    qa = _declared_qa_task()
    qa["verification_commands"][0]["cwd"] = cwd
    assert "qa/checks.mjs" in _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]})


@pytest.mark.parametrize(
    "row",
    [
        None,
        "node qa/checks.mjs",
        {},
        {"modality": "test", "argv": "node qa/checks.mjs", "cwd": "."},
        {"modality": "test", "argv": ["node", 7], "cwd": "."},
        {"modality": "test", "argv": ["node", "qa/checks.mjs"]},
    ],
)
def test_malformed_verifier_cannot_classify_file(tmp_path: Path, row: Any) -> None:
    qa = _declared_qa_task()
    qa["verification_commands"] = [row]
    assert "qa/checks.mjs" in _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]})


@pytest.mark.parametrize("runner", ["node", "python3"])
def test_qa_binding_uses_canonical_cwd_and_script_path(tmp_path: Path, runner: str) -> None:
    qa = _declared_qa_task()
    suffix = "mjs" if runner == "node" else "py"
    qa["target_files"] = [f"./qa/checks.{suffix}"]
    qa["verification_commands"] = [{"modality": "test", "argv": [runner, f"./checks.{suffix}"], "cwd": "qa"}]
    assert _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]}) == ""


def test_build_binding_uses_nested_cwd(tmp_path: Path) -> None:
    task = _declared_build_task()
    task["target_files"] = ["app/src/main.ts", "app/dist/main.js"]
    for command in task["verification_commands"]:
        command["cwd"] = "app"
    assert _evaluate_loaded_plan(tmp_path, {"tasks": [task]}) == ""


@pytest.mark.parametrize(
    "argv",
    [
        ["tsc", "-p", "tsconfig.json"],
        ["tsc", "src/main.ts"],
        ["npx", "--no-install", "tsc", "-p", "."],
        ["tsc", "--target", "ES2022", "--module", "commonjs", "-p", "."],
    ],
)
def test_supported_emitting_compiler_forms(tmp_path: Path, argv: list[str]) -> None:
    task = _declared_build_task()
    task["verification_commands"][0]["argv"] = argv
    assert _evaluate_loaded_plan(tmp_path, {"tasks": [task]}) == ""


def test_direct_node_test_verifier_is_qa_tooling(tmp_path: Path) -> None:
    qa = _declared_qa_task()
    qa["verification_commands"][0]["argv"] = ["node", "--test", "qa/checks.mjs"]
    assert _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]}) == ""


@pytest.mark.parametrize(
    "language,foreign",
    [("java", "src/Main.ts"), ("rust", "src/main.js"), ("go", "src/Main.java"), ("cpp", "src/Main.ts")],
)
def test_other_language_controls_with_actual_loader(tmp_path: Path, language: str, foreign: str) -> None:
    assert foreign in _evaluate_loaded_plan(tmp_path, {"tasks": [{"target_files": [foreign]}]}, language)


@pytest.mark.parametrize("modality", [None, {}, [], 17])
def test_invalid_modality_is_unclassified_instead_of_crashing(tmp_path: Path, modality: Any) -> None:
    qa = _declared_qa_task()
    qa["verification_commands"][0]["modality"] = modality
    assert "qa/checks.mjs" in _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]})


def test_qa_lint_script_does_not_replace_required_test_binding(tmp_path: Path) -> None:
    qa = _declared_qa_task()
    qa["verification_commands"][0]["modality"] = "lint"
    assert "qa/checks.mjs" in _evaluate_loaded_plan(tmp_path, {"tasks": [_declared_build_task(), qa]})


def test_node_test_invocation_cannot_declare_product_entrypoint(tmp_path: Path) -> None:
    build = _declared_build_task()
    build["verification_commands"][1]["argv"] = ["node", "--test", "dist/main.js"]
    assert "dist/main.js" in _evaluate_loaded_plan(tmp_path, {"tasks": [build]})


@pytest.mark.parametrize("cwd", [".", "app"])
@pytest.mark.parametrize(
    "options,accepted",
    [
        ([], True),
        (["--outDir", "dist"], True),
        (["--outFile", "dist/main.js"], True),
        (["--outDir", "./dist"], True),
        (["--outFile", "./dist/main.js"], True),
        (["--outDir", "."], True),
        (["--outDir", "generated"], False),
        (["--outFile", "dist/actual.js"], False),
        (["--outDir", "dis"], False),
        (["--outDir", "dist/main.js"], False),
        (["--outFile", "dist/main.js/child.js"], False),
        (["--outFile", "dist"], False),
        (["--outDir", "app/dist"], False),
        (["--outFile", "app/dist/main.js"], False),
    ],
)
def test_explicit_compiler_output_binding_with_real_loader(
    tmp_path: Path, cwd: str, options: list[str], accepted: bool
) -> None:
    """Output paths are relative to compiler cwd; prefix strings cannot bind siblings."""
    build = _declared_build_task()
    prefix = "" if cwd == "." else "app/"
    build["target_files"] = [f"{prefix}src/main.ts", f"{prefix}dist/main.js"]
    compiler, entrypoint = build["verification_commands"]
    compiler["cwd"] = cwd
    compiler["argv"].extend(options)
    # Resolve the entrypoint from workspace root, independently of compiler cwd.
    entrypoint["argv"] = ["node", f"{prefix}dist/main.js"]
    diagnostic = _evaluate_loaded_plan(tmp_path, {"tasks": [build]})
    if accepted:
        assert diagnostic == ""
    else:
        assert "pm_plan_language_mismatch" in diagnostic
        assert f"{prefix}dist/main.js" in diagnostic


@pytest.mark.parametrize("source_args", [["-p", "."], ["src/main.ts"]])
@pytest.mark.parametrize(
    "options",
    [
        ["--outDir"],
        ["--outFile"],
        ["--outDir", ""],
        ["--outDir", "dist/"],
        ["--outFile", "."],
        ["--outDir", "../dist"],
        ["--outFile", "../dist/main.js"],
        ["--outDir", "/dist"],
        ["--outFile", "/dist/main.js"],
        ["--outDir", "$OUTPUT"],
        ["--outFile", "dist/*.js"],
        ["--outDir", "--outFile", "dist/main.js"],
        ["--outDir", "dist", "--outDir", "generated"],
        ["--outDir", "generated", "--outDir", "dist"],
        ["--outDir", "dist", "--outDir", "dist"],
        ["--outFile", "dist/main.js", "--outFile", "dist/other.js"],
        ["--outFile", "dist/other.js", "--outFile", "dist/main.js"],
        ["--outFile", "dist/main.js", "--outFile", "dist/main.js"],
        ["--outDir", "dist", "--outFile", "dist/main.js"],
        ["--outFile", "dist/main.js", "--outDir", "dist"],
    ],
)
def test_invalid_or_ambiguous_compiler_output_is_not_auxiliary(
    tmp_path: Path, source_args: list[str], options: list[str]
) -> None:
    build = _declared_build_task()
    build["verification_commands"][0]["argv"] = ["tsc", *source_args, *options]
    diagnostic = _evaluate_loaded_plan(tmp_path, {"tasks": [build]})
    assert "pm_plan_language_mismatch" in diagnostic
    assert "dist/main.js" in diagnostic


@pytest.mark.parametrize("option,path", [("--outDir", "dist"), ("--outFile", "dist/main.js")])
def test_output_binding_keeps_explicit_source_compilation(tmp_path: Path, option: str, path: str) -> None:
    build = _declared_build_task()
    build["verification_commands"][0]["argv"] = ["tsc", "src/main.ts", option, path]
    assert _evaluate_loaded_plan(tmp_path, {"tasks": [build]}) == ""


@pytest.mark.parametrize("option,path", [("--outDir", "generated"), ("--outFile", "dist/actual.js")])
def test_exact_pol73_output_conflict_with_real_loader(
    tmp_path: Path, pol73_plan: dict[str, Any], option: str, path: str
) -> None:
    pol73_plan["tasks"][3]["verification_commands"][0]["argv"].extend([option, path])
    diagnostic = _evaluate_loaded_plan(tmp_path, pol73_plan)
    assert "pm_plan_language_mismatch" in diagnostic
    assert "dist/main.js" in diagnostic
    assert "qa/checks.mjs" not in diagnostic


@pytest.mark.parametrize(
    "defect",
    ["valid", "foreign_product", "no_emit", "qa_unbound", "unqualified_origin", "outdir_conflict", "outfile_conflict"],
)
async def test_factory_pm_stage_keeps_admission_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pol73_plan: dict[str, Any], defect: str
) -> None:
    """Run the real Factory PM path and file gates; substitute only the offline PM service."""
    from datetime import datetime, timezone

    from polaris.cells.factory.pipeline.internal.factory_run_models import FactoryConfig, FactoryRun, FactoryRunStatus
    from polaris.cells.factory.pipeline.tests._characterization_helpers import _factory_stage_context
    from polaris.cells.orchestration.pm_dispatch.public.service import CommandResult
    from polaris.cells.runtime.task_runtime.public.service import TaskRuntimeService
    from polaris.infrastructure.log_pipeline import jetstream_publisher

    class OfflinePublisher:
        def publish(self, *, subject: str, payload: dict[str, Any]) -> bool:
            assert subject.startswith("hp.runtime.")
            assert payload["schema_version"] == "runtime.v2"
            return True

    # Only the outbound notification transport is isolated; persisted owner facts stay real.
    monkeypatch.setattr(jetstream_publisher, "get_log_jetstream_publisher", lambda: OfflinePublisher())

    plan = deepcopy(pol73_plan)
    if defect == "foreign_product":
        plan["tasks"][2]["target_files"].append("src/Engine.java")
    elif defect == "no_emit":
        plan["tasks"][3]["verification_commands"][0]["argv"].append("--noEmit")
    elif defect == "qa_unbound":
        plan["tasks"][7]["verification_commands"][0]["argv"] = ["node", "qa/other.mjs"]
    elif defect == "unqualified_origin":
        plan["quality_gate"]["signals"] = []
    elif defect == "outdir_conflict":
        plan["tasks"][3]["verification_commands"][0]["argv"].extend(["--outDir", "generated"])
    elif defect == "outfile_conflict":
        plan["tasks"][3]["verification_commands"][0]["argv"].extend(["--outFile", "dist/actual.js"])
    _evaluate_loaded_plan(tmp_path, plan)
    executor = OrchestrationStageExecutor(tmp_path)

    class OfflinePMService:
        async def execute_pm_run(self, **kwargs: Any) -> CommandResult:
            assert kwargs["workspace"] == str(tmp_path)
            assert kwargs["run_type"] == "pm"
            assert kwargs["options"]["run_director"] is False
            return CommandResult(status="completed", run_id="offline-pm-language")

    async def completed(_service: Any, command_result: CommandResult, **kwargs: Any) -> CommandResult:
        return command_result

    monkeypatch.setattr(executor, "_build_orchestration_service", lambda _context: OfflinePMService())
    monkeypatch.setattr(executor, "_wait_run_completion", completed)
    run = FactoryRun(
        id="factory-language-test",
        config=FactoryConfig(name="language", stages=["pm_planning", "chief_engineer_review"]),
        status=FactoryRunStatus.RUNNING,
        created_at=datetime.now(timezone.utc).isoformat(),
    )
    result = await executor._execute_pm_planning(run, _factory_stage_context())
    rows = TaskRuntimeService(str(tmp_path)).list_observable_task_rows()
    (tmp_path / "factory-stage-result.json").write_text(
        json.dumps(
            {
                "stage": result.stage,
                "status": result.status,
                "output": result.output,
                "metadata": result.metadata,
                "artifacts": result.artifacts,
                "runtime_rows": rows,
                "offline_pm_transport": True,
                "original_native_run_resumed": False,
                "product_completed_verified": False,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    if defect == "valid":
        assert result.status == "success", result
        assert {row["metadata"]["pm_task_id"] for row in rows} == {task["id"] for task in plan["tasks"]}
        assert result.metadata["error_code"] == ""
    else:
        assert result.status == "failed", result
        assert rows == []
        expected = (
            "pm.real_planning_not_qualified" if defect == "unqualified_origin" else "pm.language_mismatch_detected"
        )
        assert result.metadata["error_code"] == expected
        assert not any(signal["code"] == "pm.taskboard_materialized_from_plan" for signal in result.metadata["signals"])
    assert run.stages_completed == []


# Exact observed POL73 artifact, including all eight tasks and their original metadata.
# Compressed fixture avoids discarding fields or requiring private host evidence paths.
_POL73_PLAN_SHA256 = "e5290468e97446428f46670daed421b6f85e3c9ba5b983378d1c50837b5b4272"
_POL73_PLAN_COMPRESSED = (
    "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"
    "/n++X31d"
)


def test_pol77_artifact_admission_is_required_without_language_catalog(tmp_path: Path) -> None:
    from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor

    executor = OrchestrationStageExecutor(tmp_path)
    executor._write_json_artifact(
        "tasks/plan.json",
        {
            "tasks": [
                {
                    "id": "TASK-1",
                    "target_files": ["qa/report.md"],
                    "context_files": ["qa/report.md"],
                    "depends_on": [],
                    "execution_backend": "code_edit",
                }
            ]
        },
    )
    assert "artifact_readonly_write_collision" in executor._validate_pm_plan_artifact_contract()
