from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.verifier_policy.public import (
    UpdateVerifierPolicyCommandV1,
    update_verifier_policy,
)
from polaris.cells.factory.pipeline.internal import bench_gates
from polaris.cells.factory.pipeline.internal.bench_gates import (
    _collect_go_local_imports,
    _command_serves_build_output,
    _discover_go_package_dirs,
    _go_command,
    _go_version_of,
    _infer_go_module_name,
    _normalize_go_imports,
    _primary_source_language,
    _read_go_mod_module,
    _repair_go_import_subpath,
    _resolve_polaris_roots_runtime_dir,
    _script_depends_on_build_output,
    aggregate_goal_audit,
    apply_factory_bench_failure_taxonomy,
    build_llm_route_audit,
    build_real_run_gate,
    classify_factory_bench_failure,
    collect_llm_events,
)


def _real_llm_event(
    role: str,
    provider_id: str,
    model: str,
    binding_id: str = "",
) -> dict[str, Any]:
    event: dict[str, Any] = {
        "event": "llm_call_end",
        "role": role,
        "provider_id": provider_id,
        "model": model,
        "source": "llm",
        "terminal": True,
        "invocation": True,
    }
    if binding_id:
        event["binding_id"] = binding_id
    return event


def _canonical_run_ledger_projection(
    *,
    task_boundary_failures: list[dict[str, Any]] | None = None,
    tool_lifecycle_failures: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build the minimal authoritative projection used by taxonomy tests."""

    boundary_failures = [dict(item) for item in task_boundary_failures or []]
    lifecycle_failures = {str(task_key): dict(item) for task_key, item in (tool_lifecycle_failures or {}).items()}
    return {
        "schema_version": 1,
        "source": "run_ledger",
        "ok": not boundary_failures and not lifecycle_failures,
        "integrity_ok": not lifecycle_failures,
        "outcome_ok": not boundary_failures,
        "gate_count": 1,
        "gates": [
            {
                "name": "qa_verdict",
                "stage": "qa",
                "ok": True,
                "summary": "QA passed",
                "content_id": "qa-content-1",
                "append_id": "qa-append-1",
                "capability_ok": True,
            }
        ],
        "capability": {"ok": True, "issues": []},
        "evidence_policy": {
            "ok": True,
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "task_boundary": {
            "ok": not boundary_failures,
            "verdict_count": max(1, len(boundary_failures)),
            "failed": boundary_failures,
            "latest": boundary_failures[-1] if boundary_failures else {"ok": True, "status": "completed_verified"},
        },
        "tool_lifecycle": {
            "ok": not lifecycle_failures,
            "unresolved_by_task": lifecycle_failures,
        },
    }


def _canonical_task_runtime_projection(
    *,
    authoritative: bool = True,
) -> dict[str, Any]:
    return {
        "schema_version": "task_runtime.observable_task_rows_authority.v1",
        "source": "task_runtime.execution_fact",
        "authoritative": authoritative,
        "degraded": not authoritative,
        "row_count": 1,
        "rows": [
            {
                "task_id": "TASK-1",
                "status": "completed",
                "execution_state": "completed",
                "fact_event_seq": 1,
                "source": "task_runtime.execution_fact",
                "status_source": "task_runtime.execution_fact",
            }
        ],
        "readiness": {"ready": authoritative, "blocking_reasons": []},
    }




class TestGoImportNormalization:
    """Verify bench gates do not mutate inconsistent module prefixes."""

    def _write_go_files(self, tmp_path: Path) -> list[str]:
        go_files = ["main.go", "src/engine/engine.go", "src/models/pet.go"]
        (tmp_path / "src" / "engine").mkdir(parents=True, exist_ok=True)
        (tmp_path / "src" / "models").mkdir(parents=True, exist_ok=True)
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "my-project/src/engine"\n\nfunc main() { _ = engine.X }\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "engine" / "engine.go").write_text(
            'package engine\n\nimport "my-proj/src/models"\n\nvar X = models.Y\n',
            encoding="utf-8",
        )
        (tmp_path / "src" / "models" / "pet.go").write_text(
            "package models\n\nvar Y = 42\n",
            encoding="utf-8",
        )
        return go_files

    def test_collect_local_imports(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        imports = _collect_go_local_imports(tmp_path, go_files)
        assert len(imports) == 2
        assert imports[0][1] == "my-project/src/engine"
        assert imports[1][1] == "my-proj/src/models"

    def test_infer_module_name_dominant_prefix(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        # "my-project" appears in main.go (1 import), "my-proj" in engine.go (1 import)
        # Both have count 1, so max picks whichever is lexicographically last.
        # The important thing is it returns a valid prefix.
        name = _infer_go_module_name(tmp_path, go_files)
        assert name in ("my-project", "my-proj")

    def test_normalize_repairs_inconsistent_imports(self, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        before = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        modified = _normalize_go_imports(tmp_path, go_files, "my-project")
        assert modified == 0
        engine_text = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        assert engine_text == before
        assert '"my-proj/src/models"' in engine_text

    def test_normalize_no_change_when_consistent(self, tmp_path: Path) -> None:
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "mymod/pkg"\n\nfunc main() {}\n',
            encoding="utf-8",
        )
        modified = _normalize_go_imports(tmp_path, ["main.go"], "mymod")
        assert modified == 0

    def test_go_command_auto_init_with_normalization(self, monkeypatch: Any, tmp_path: Path) -> None:
        go_files = self._write_go_files(tmp_path)
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr=""),
        )
        cmd = _go_command(tmp_path, go_files)
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]
        # Verify imports were not normalized by bench_gates.
        engine_text = (tmp_path / "src" / "engine" / "engine.go").read_text(encoding="utf-8")
        assert '"my-proj/src/models"' in engine_text

    def test_bench_gates_source_has_no_workspace_mutation_calls(self) -> None:
        source = Path(bench_gates.__file__).read_text(encoding="utf-8")

        assert ".write_text(" not in source
        assert ".unlink(" not in source


# ---------------------------------------------------------------------------
# Tests for _go_version_of
# ---------------------------------------------------------------------------


class TestGoVersionOf:
    def test_parses_version_string(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="go version go1.23.8 linux/amd64\n", stderr=""
            ),
        )
        assert _go_version_of("/fake/go") == (1, 23, 8)

    def test_handles_failure(self, monkeypatch: Any) -> None:
        monkeypatch.setattr(
            bench_gates.subprocess,
            "run",
            lambda cmd, **kw: subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="error"),
        )
        assert _go_version_of("/fake/go") == (0,)

    def test_handles_timeout(self, monkeypatch: Any) -> None:
        def _raise(*a: Any, **kw: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="go", timeout=5)

        monkeypatch.setattr(bench_gates.subprocess, "run", _raise)
        assert _go_version_of("/fake/go") == (0,)


# ---------------------------------------------------------------------------
# Tests for _read_go_mod_module (F7: go.mod as canonical module authority)
# ---------------------------------------------------------------------------


class TestReadGoModModule:
    def test_reads_module_name(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module ascii-pet-terminal\n\ngo 1.23\n", encoding="utf-8")
        assert _read_go_mod_module(tmp_path) == "ascii-pet-terminal"

    def test_returns_empty_when_no_go_mod(self, tmp_path: Path) -> None:
        assert _read_go_mod_module(tmp_path) == ""

    def test_handles_malformed_go_mod(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("// comment only\n", encoding="utf-8")
        assert _read_go_mod_module(tmp_path) == ""


# ---------------------------------------------------------------------------
# Test: normalization discovers undeclared Go files on disk (F7)
# ---------------------------------------------------------------------------


def test_normalize_go_imports_discovers_disk_files(tmp_path: Path) -> None:
    """Files NOT in go_files list remain untouched by the measurement gate."""
    (tmp_path / "go.mod").write_text("module myproject\n\ngo 1.23\n", encoding="utf-8")
    (tmp_path / "src" / "pkg").mkdir(parents=True)
    # Declared file with correct import.
    (tmp_path / "main.go").write_text(
        'package main\n\nimport "myproject/src/pkg"\n\nfunc main() { _ = pkg.X }\n',
        encoding="utf-8",
    )
    # Undeclared file with wrong prefix.
    (tmp_path / "src" / "pkg" / "helper.go").write_text(
        'package pkg\n\nimport "my-proj/src/pkg"\n\nvar X = 1\nvar _ = Y\n',
        encoding="utf-8",
    )
    # Only pass main.go — helper.go is on disk but not declared.
    modified = _normalize_go_imports(tmp_path, ["main.go"], "myproject")
    assert modified == 0
    helper_text = (tmp_path / "src" / "pkg" / "helper.go").read_text(encoding="utf-8")
    assert '"my-proj/src/pkg"' in helper_text


def test_go_command_normalizes_even_with_go_mod(monkeypatch: Any, tmp_path: Path) -> None:
    """_go_command must not normalize imports when go.mod already exists."""
    (tmp_path / "go.mod").write_text("module canonical\n\ngo 1.23\n", encoding="utf-8")
    (tmp_path / "main.go").write_text(
        'package main\n\nimport "wrong-prefix/pkg"\n\nfunc main() {}\n',
        encoding="utf-8",
    )
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "lib.go").write_text("package pkg\n", encoding="utf-8")

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    cmd = _go_command(tmp_path, ["main.go", "pkg/lib.go"])
    assert cmd == ["/usr/local/go/bin/go", "test", "./..."]
    # Verify the import was not normalized.
    main_text = (tmp_path / "main.go").read_text(encoding="utf-8")
    assert '"wrong-prefix/pkg"' in main_text


# ---------------------------------------------------------------------------
# Tests for F8: Go import sub-path hallucination repair
# ---------------------------------------------------------------------------


class TestGoImportSubpathRepair:
    """Verify _repair_go_import_subpath fixes hallucinated sub-paths."""

    def test_repairs_hallucinated_subpath(self) -> None:
        pkg_dirs = {"src/engine", "src/models"}
        result = _repair_go_import_subpath("mymod/example/pet-ascii/src/engine", "mymod", pkg_dirs)
        assert result == "mymod/src/engine"

    def test_leaves_valid_subpath_unchanged(self) -> None:
        pkg_dirs = {"src/engine", "src/models"}
        result = _repair_go_import_subpath("mymod/src/engine", "mymod", pkg_dirs)
        assert result == "mymod/src/engine"

    def test_leaves_non_matching_module_unchanged(self) -> None:
        pkg_dirs = {"src/engine"}
        result = _repair_go_import_subpath("other/src/engine", "mymod", pkg_dirs)
        assert result == "other/src/engine"

    def test_discovers_go_package_dirs(self, tmp_path: Path) -> None:
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "engine.go").write_text("package engine\n", encoding="utf-8")
        (tmp_path / "src" / "models").mkdir(parents=True)
        (tmp_path / "src" / "models" / "pet.go").write_text("package models\n", encoding="utf-8")
        dirs = _discover_go_package_dirs(tmp_path)
        assert "src/engine" in dirs
        assert "src/models" in dirs

    def test_normalize_repairs_both_prefix_and_subpath(self, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module my-project\n\ngo 1.23\n", encoding="utf-8")
        (tmp_path / "src" / "engine").mkdir(parents=True)
        (tmp_path / "src" / "engine" / "engine.go").write_text("package engine\n", encoding="utf-8")
        # main.go has a hallucinated sub-path with the correct prefix.
        (tmp_path / "main.go").write_text(
            'package main\n\nimport "my-project/hallucinated/path/src/engine"\n\n'
            "// comment about my-project should NOT change\n"
            "func main() {}\n",
            encoding="utf-8",
        )
        modified = _normalize_go_imports(tmp_path, ["main.go", "src/engine/engine.go"], "my-project")
        assert modified == 0
        main_text = (tmp_path / "main.go").read_text(encoding="utf-8")
        assert '"my-project/hallucinated/path/src/engine"' in main_text
        # Comment must NOT be modified.
        assert "// comment about my-project should NOT change" in main_text
