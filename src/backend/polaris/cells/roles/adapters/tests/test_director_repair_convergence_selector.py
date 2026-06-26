from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.adapters.internal.director import execute_method as execute_method_module


def test_step_verify_context_selects_step_verifier_without_artifact_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_step_factory(
        workspace: str | Path,
        *,
        task_id: str,
        verify_command: str,
        log_root: str | Path | None = None,
    ) -> Any:
        captured["step"] = {
            "workspace": Path(workspace),
            "task_id": task_id,
            "verify_command": verify_command,
            "log_root": log_root,
        }
        return sentinel_verifier

    def fake_artifact_factory(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("artifact factory must not be called for step verify convergence")

    monkeypatch.setattr(execute_method_module, "build_step_verify_convergence_verifier", fake_step_factory)
    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_artifact_factory)

    verifier = execute_method_module._build_post_execution_repair_convergence_verifier(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="task-step",
        all_affected_files=["src/app.ts"],
        context={"construction_step": {"verify": ["test -f ./src/app.ts", "grep -q ready ./src/app.ts"]}},
        artifact_quality_errors=["step verify failed (exit 1): grep -q ready ./src/app.ts ::"],
    )

    assert verifier is sentinel_verifier
    assert captured["step"] == {
        "workspace": tmp_path.resolve(),
        "task_id": "task-step",
        "verify_command": "test -f ./src/app.ts && grep -Fq ready ./src/app.ts",
        "log_root": None,
    }


def test_step_verify_safety_policy_rejection_selects_step_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_step_factory(
        workspace: str | Path,
        *,
        task_id: str,
        verify_command: str,
        log_root: str | Path | None = None,
    ) -> Any:
        captured["step"] = {
            "workspace": Path(workspace),
            "task_id": task_id,
            "verify_command": verify_command,
            "log_root": log_root,
        }
        return sentinel_verifier

    def fake_artifact_factory(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("artifact factory must not be called for rejected step verify diagnostics")

    monkeypatch.setattr(execute_method_module, "build_step_verify_convergence_verifier", fake_step_factory)
    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_artifact_factory)

    verifier = execute_method_module._build_post_execution_repair_convergence_verifier(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="task-rejected-step",
        all_affected_files=["src/app.ts"],
        context={"construction_step": {"verify": "rm -rf ."}},
        artifact_quality_errors=["step verify command rejected by safety policy: blocked_command:rm :: 'rm -rf .'"],
    )

    assert verifier is sentinel_verifier
    assert captured["step"] == {
        "workspace": tmp_path.resolve(),
        "task_id": "task-rejected-step",
        "verify_command": "rm -rf .",
        "log_root": None,
    }


def test_artifact_errors_with_scoped_affected_files_select_artifact_factory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def sentinel_verifier(request: Any) -> Any:
        return request

    def fake_step_factory(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("step factory must not be called for artifact quality errors")

    def fake_artifact_factory(
        workspace: str | Path,
        *,
        task_id: str,
        relative_paths: Any = None,
        log_root: str | Path | None = None,
    ) -> Any:
        captured["artifact"] = {
            "workspace": Path(workspace),
            "task_id": task_id,
            "relative_paths": tuple(relative_paths or ()),
            "log_root": log_root,
        }
        return sentinel_verifier

    monkeypatch.setattr(execute_method_module, "build_step_verify_convergence_verifier", fake_step_factory)
    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_artifact_factory)

    inside_absolute = tmp_path / "lib" / "model.ts"
    verifier = execute_method_module._build_post_execution_repair_convergence_verifier(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="task-artifact",
        all_affected_files=["src/app.ts", str(inside_absolute), "src/app.ts"],
        context={"construction_step": {"verify": "test -f ./src/app.ts"}},
        artifact_quality_errors=["Artifact quality scan failed: syntax error in src/app.ts"],
    )

    assert verifier is sentinel_verifier
    assert captured["artifact"] == {
        "workspace": tmp_path.resolve(),
        "task_id": "task-artifact",
        "relative_paths": ("src/app.ts", "lib/model.ts"),
        "log_root": None,
    }


def test_empty_affected_files_do_not_create_artifact_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_artifact_factory(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return object()

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_artifact_factory)

    verifier = execute_method_module._build_post_execution_repair_convergence_verifier(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="task-empty",
        all_affected_files=[],
        context={},
        artifact_quality_errors=["Artifact quality scan failed: syntax error"],
    )

    assert verifier is None
    assert calls == []


def test_out_of_workspace_paths_filtered_to_empty_do_not_create_artifact_verifier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    def fake_artifact_factory(*args: Any, **kwargs: Any) -> Any:
        calls.append({"args": args, "kwargs": kwargs})
        return object()

    monkeypatch.setattr(execute_method_module, "build_artifact_quality_convergence_verifier", fake_artifact_factory)

    outside_absolute = tmp_path.parent / "outside.ts"
    verifier = execute_method_module._build_post_execution_repair_convergence_verifier(
        SimpleNamespace(workspace=str(tmp_path)),
        task_id="task-unsafe",
        all_affected_files=[
            "../outside.ts",
            str(outside_absolute),
            "/etc/passwd",
            "src/../../outside.ts",
        ],
        context={},
        artifact_quality_errors=["Artifact quality scan failed: syntax error"],
    )

    assert verifier is None
    assert calls == []
