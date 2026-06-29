"""Tests for PM plan language consistency validation.

Regression tests for the fix that detects when the PM model plans files
in the wrong language (e.g. Java files for a JavaScript project).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch


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
