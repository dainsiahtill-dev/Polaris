"""Shared runner audit fixtures retained across splitting; test-only."""

from __future__ import annotations

import ast
import json
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

from scripts.factory_bench import run_factory_bench as bench
from scripts.factory_bench._bench_lib import chain as bench_chain

_LAST_FACTORY_START_PAYLOAD: dict[str, Any] = {}
_LAST_FACTORY_RESUME_CALL: dict[str, Any] = {}


def _guard_runner_external_io(monkeypatch: Any, tmp_path: Path) -> None:
    """Wrong mocks fail before process/network IO; OS isolation remains required."""

    monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "kernelone-home"))
    monkeypatch.setenv("KERNELONE_INSTANCE_HOME", str(tmp_path / "instances-home"))
    monkeypatch.setenv("HOME", str(tmp_path / "user-home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config-home"))

    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("unmocked runner external IO: use canonical dependency owner")

    original_popen = subprocess.Popen

    def scoped_process(args: Any, **kwargs: Any) -> Any:
        # Real git initialization is harmless and preserves the chain's native
        # workspace setup; role/backend/frontend processes remain forbidden.
        cwd = Path(kwargs.get("cwd") or "/").resolve()
        if args == ["git", "init", "-q"] and cwd.is_relative_to(tmp_path.resolve()):
            return original_popen(args, **kwargs)
        return blocked(args, **kwargs)

    monkeypatch.setattr(urllib.request, "urlopen", blocked)
    monkeypatch.setattr(subprocess, "Popen", scoped_process)
    from polaris.cells.instances.internal.service import InstanceSupervisor

    monkeypatch.setattr(InstanceSupervisor, "_start_backend", blocked)
    monkeypatch.setattr(InstanceSupervisor, "_start_frontend", blocked)


def _factory_chain_destructive_findings(
    module_source: str,
    protected_names: set[str],
) -> tuple[list[str], list[str], set[str], str]:
    """Audit every module-local helper reachable from protected Factory entrypoints."""
    module_tree = ast.parse(module_source)
    module_functions = {
        node.name: node for node in module_tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    missing = protected_names.difference(module_functions)
    assert not missing, f"protected Factory entrypoints missing: {sorted(missing)}"

    reachable_names: set[str] = set()
    pending = list(protected_names)
    while pending:
        function_name = pending.pop()
        if function_name in reachable_names:
            continue
        reachable_names.add(function_name)
        for node in ast.walk(module_functions[function_name]):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                continue
            called_name = node.func.id
            if called_name in module_functions and called_name not in reachable_names:
                pending.append(called_name)

    destructive_names = {"rmtree", "rmdir", "unlink", "remove", "removedirs"}
    destructive_aliases = set(destructive_names)
    for node in module_tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        for alias in node.names:
            if alias.name in destructive_names:
                destructive_aliases.add(alias.asname or alias.name)
    destructive_calls: list[str] = []
    subprocess_deletions: list[str] = []
    reachable_source: list[str] = []

    def _looks_destructive_shell(command: str) -> bool:
        normalized = " ".join(command.lower().replace("\\", "/").split())
        return any(
            token in normalized
            for token in (
                "rm -rf",
                "rm -fr",
                "rm -r ",
                "rmdir ",
                "remove-item -recurse",
                "remove-item -r ",
            )
        )

    for function_name in sorted(reachable_names):
        function_node = module_functions[function_name]
        reachable_source.append(ast.get_source_segment(module_source, function_node) or "")
        for node in ast.walk(function_node):
            if not isinstance(node, ast.Call):
                continue
            call_name = ""
            if isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
                if call_name in destructive_names:
                    destructive_calls.append(call_name)
            elif isinstance(node.func, ast.Name):
                call_name = node.func.id
                if call_name in destructive_aliases:
                    destructive_calls.append(call_name)
            lowered_call_name = call_name.lower()
            if any(
                action in lowered_call_name for action in ("purge", "cleanup", "delete", "remove", "destroy")
            ) and any(target in lowered_call_name for target in ("runtime", "workspace", "project")):
                destructive_calls.append(call_name)
            literal_fragments: list[str] = []
            for expression in (*node.args, *(keyword.value for keyword in node.keywords)):
                literal_fragments.extend(
                    str(child.value)
                    for child in ast.walk(expression)
                    if isinstance(child, ast.Constant) and isinstance(child.value, str)
                )
            literal_command = " ".join(literal_fragments)
            if _looks_destructive_shell(literal_command):
                subprocess_deletions.append(literal_command)
    return destructive_calls, subprocess_deletions, reachable_names, "\n".join(reachable_source)


def _write_test_workspace_catalog(
    bench_workspace: Path,
    workspace: Path,
    *,
    run_id: str,
    project_id: str,
) -> dict[str, Any]:
    return bench._write_workspace_catalog_meta_exclusive(
        bench_workspace,
        workspace,
        {"run_id": run_id, "project_id": project_id},
    )


def _matching_isolated_launch_identity(
    tmp_path: Path,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    workspace = tmp_path / "L1-01"
    workspace.mkdir()
    catalog_meta = _write_test_workspace_catalog(
        tmp_path,
        workspace,
        run_id="run-001",
        project_id="L1-01",
    )
    receipt = {
        "schema_version": "factory_bench.isolated_launch_receipt.v1",
        "launch_scope": "run-001:L1-01:nonce",
        "launch_nonce": "nonce",
        "run_id": "run-001",
        "workspace_source_run_id": "run-001",
        "project_id": "L1-01",
        "requested_project_id": "L1-01",
        "canonical_project_id": "L1-01",
        "requested_instance_id": "bench-l1-01",
        "instance_id": "bench-l1-01-run-run-001-nonce",
        "bench_workspace": str(tmp_path.absolute()),
        "workspace": str(workspace.resolve()),
        "workspace_device": catalog_meta["workspace_device"],
        "workspace_inode": catalog_meta["workspace_inode"],
        "workspace_catalog_hash": bench._workspace_catalog_hash(catalog_meta),
        "runtime_root": str((tmp_path / "L1-01" / "runtime").resolve()),
        "expected_backend_root": str(bench._BACKEND_ROOT),
        "expected_source_fingerprint": "expected-source",
    }
    instance = {
        "instance_id": receipt["instance_id"],
        "workspace": receipt["workspace"],
        "runtime_root": receipt["runtime_root"],
        "backend_pid": 73101,
        "metadata": {"instance_launch_receipt": dict(receipt)},
    }
    backend_context = {
        "backend_freshness": {
            "ok": True,
            "expected_fingerprint": "expected-source",
            "actual_fingerprint": "expected-source",
            "backend_info": {
                "pid": 73101,
                "instance_id": receipt["instance_id"],
                "workspace": receipt["workspace"],
                "backend_root": receipt["expected_backend_root"],
            },
        }
    }
    return receipt, instance, backend_context


def _successful_audit_record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "static_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "code_file_count": 1,
        "source_file_count": 1,
        "implementation_depth": {"ok": True, "detail": "implementation depth passed"},
        "code_files": ["src/index.js"],
        "target_files": ["src/index.js"],
        "allowed_paths": ["src/index.js"],
        "required_artifacts": ["src/index.js"],
        "project_brief": "Build something",
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
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
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "factory_terminal_status": {
            "status": "completed",
            "metadata": {
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "task_row_read_model_source": "task_runtime.execution_fact",
            },
        },
        "blueprint_id": "bp-test",
        "blueprints": [{"id": "bp-test"}],
        "checks": [],
    }
    record.update(overrides)
    return record


def _ok_run_ledger_projection(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
    return {
        "source": "run_ledger",
        "ok": True,
        "integrity_ok": True,
        "outcome_ok": True,
        "event_count": 1,
        "gate_count": 1,
        "gates": [
            {
                "name": "qa_verdict",
                "stage": "qa",
                "ok": True,
                "summary": "QA passed",
                "content_id": "qa-content-id",
                "append_id": "qa-append-id",
                "capability_ok": True,
            }
        ],
        "failed_gates": [],
        "capability": {"ok": True, "issues": [], "latest_token_id": "job-token-id"},
        "physical_evidence": {"command_count": 1},
        "evidence_policy": {
            "ok": True,
            "integrity_ok": True,
            "outcome_ok": True,
            "missing_required_modalities": [],
            "failed_required_modalities": [],
        },
        "tool_lifecycle": {"ok": True},
        "task_boundary": {
            "ok": True,
            "verdict_count": 1,
            "latest": {"ok": True, "status": "completed_verified"},
            "failed": [],
        },
    }


def _capture_run_chain_command(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    director_workflow_execution_mode: str | None = None,
    director_dispatch_driver: str | None = None,
) -> list[list[str]]:
    workspace = tmp_path / "L6-31"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    captured: list[list[str]] = []

    def _fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        captured.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(bench_chain.subprocess, "run", _fake_run)
    kwargs: dict[str, Any] = {}
    if director_workflow_execution_mode is not None:
        kwargs["director_workflow_execution_mode"] = director_workflow_execution_mode
    if director_dispatch_driver is not None:
        kwargs["director_dispatch_driver"] = director_dispatch_driver
    bench.run_chain(
        {"id": "L6-31", "title": "Kanban", "brief": "Build Kanban", "test_focus": "runtime"},
        workspace,
        timeout_s=30,
        log_path=tmp_path / "L6-31.chain.log",
        **kwargs,
    )
    return captured


def _setup_run_factory_chain_mocks(
    monkeypatch: Any,
    tmp_path: Path,
    *,
    start_response: dict[str, Any] | None,
    terminal_status: dict[str, Any] | None,
    audit_bundle: dict[str, Any] | None,
) -> Path:
    workspace = tmp_path / "L2-07"
    workspace.mkdir()
    (workspace / ".git").mkdir()
    expected_workspace = str(workspace)
    _LAST_FACTORY_START_PAYLOAD.clear()
    _LAST_FACTORY_RESUME_CALL.clear()

    def _fake_start_factory_run(_backend_url: str, _payload: dict[str, Any], token: str = "") -> dict[str, Any] | None:
        _LAST_FACTORY_START_PAYLOAD.update(_payload)
        return start_response

    def _fake_list_factory_runs(
        _backend_url: str,
        *,
        token: str = "",
        workspace: str = "",
        limit: int = 100,
    ) -> dict[str, Any]:
        del token, limit
        assert workspace == expected_workspace
        run_id = str((start_response or {}).get("run_id") or "run-director")
        return {
            "runs": [
                {
                    "run_id": run_id,
                    "status": "failed",
                    "current_stage": "director_dispatch",
                    "last_successful_stage": "chief_engineer_review",
                    "failure": {"stage": "director_dispatch"},
                    "roles": {
                        "pm": {"status": "completed"},
                        "chief_engineer": {"status": "completed"},
                        "director": {"status": "failed"},
                    },
                    "metadata": {},
                }
            ],
            "total": 1,
        }

    def _fake_retry_factory_run_from_director(
        _backend_url: str,
        run_id: str,
        *,
        token: str = "",
        workspace: str = "",
        reason: str = "",
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        _LAST_FACTORY_RESUME_CALL.update({"run_id": run_id, "token": token, "workspace": workspace, "reason": reason})
        return start_response

    def _fake_wait_run_until_terminal(
        _backend_url: str,
        run_id: str,
        token: str = "",
        workspace: str = "",
        on_status: Any = None,
        **_kwargs: Any,
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        if on_status is not None and terminal_status is not None:
            on_status(terminal_status)
        return terminal_status

    def _fake_get_audit_bundle(
        _backend_url: str,
        _run_id: str,
        token: str = "",
        workspace: str = "",
    ) -> dict[str, Any] | None:
        assert workspace == expected_workspace
        return audit_bundle

    def _fake_cancel_factory_run(
        _backend_url: str,
        _run_id: str,
        *,
        reason: str = "",
        token: str = "",
        workspace: str = "",
        return_errors: bool = False,
    ) -> dict[str, Any]:
        del return_errors
        assert reason
        assert workspace == expected_workspace
        return {"status": "cancelled"}

    monkeypatch.setattr(bench_chain, "start_factory_run", _fake_start_factory_run)
    monkeypatch.setattr(bench_chain, "list_factory_runs", _fake_list_factory_runs)
    monkeypatch.setattr(bench_chain, "retry_factory_run_from_director", _fake_retry_factory_run_from_director)
    monkeypatch.setattr(bench_chain, "wait_run_until_terminal", _fake_wait_run_until_terminal)
    monkeypatch.setattr(bench_chain, "get_audit_bundle", _fake_get_audit_bundle)
    monkeypatch.setattr(bench_chain, "cancel_factory_run", _fake_cancel_factory_run)

    return workspace


def _write_director_resume_evidence(workspace: Path) -> None:
    from polaris.kernelone.storage import resolve_runtime_path

    plan_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/plan.json"))
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(
        json.dumps(
            {
                "tasks": [
                    {
                        "id": "TASK-1",
                        "goal": "Implement feature",
                        "target_files": ["src/index.ts"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    task_path = Path(resolve_runtime_path(str(workspace), "runtime/tasks/task_1.json"))
    task_path.write_text(
        json.dumps({"id": 1, "status": "pending", "subject": "Implement feature"}, ensure_ascii=False),
        encoding="utf-8",
    )
    blueprint_path = workspace / ".polaris" / "blueprints" / "latest.review.json"
    blueprint_path.parent.mkdir(parents=True, exist_ok=True)
    blueprint_path.write_text(
        json.dumps({"generated_blueprints": 1, "blueprints": [{"task_id": "TASK-1"}]}, ensure_ascii=False),
        encoding="utf-8",
    )


def _record(**overrides: Any) -> dict[str, Any]:
    record: dict[str, Any] = {
        "all_checks_passed": True,
        "has_plan_doc": True,
        "has_blueprint_doc": True,
        "has_qa_verdict": True,
        "chain_state": "clean",
        "chain_results": {"qa_ran": True, "qa_passed": True},
        "wrong_product_suspect": False,
        "implementation_depth": {"ok": True, "detail": "implementation depth passed"},
        "backend_freshness": {"ok": True, "detail": "backend fresh"},
        "task_runtime_projection": {
            "schema_version": "task_runtime.observable_task_rows_authority.v1",
            "source": "task_runtime.execution_fact",
            "authoritative": True,
            "degraded": False,
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
            "readiness": {"ready": True, "blocking_reasons": []},
        },
        "factory_terminal_status": {
            "status": "completed",
            "metadata": {
                "canonical_authoritative": True,
                "terminal_source": "task_runtime.execution_fact",
                "task_row_read_model_source": "task_runtime.execution_fact",
            },
        },
        "run_ledger": {
            "ledger_path": __file__,
            "content_id": "content-id",
            "event_id": "content-id",
            "append_id": "append-id",
            "job_token_id": "job-token-id",
            "job_token": {"capability_audit": {"ok": True, "issues": []}},
        },
        "run_ledger_projection": {
            "source": "run_ledger",
            "integrity_ok": True,
            "outcome_ok": True,
            "ok": True,
            "event_count": 1,
            "gate_count": 1,
            "gates": [
                {
                    "name": "qa_verdict",
                    "stage": "qa",
                    "ok": True,
                    "summary": "QA passed",
                    "content_id": "qa-content-id",
                    "append_id": "qa-append-id",
                    "capability_ok": True,
                }
            ],
            "failed_gates": [],
            "capability": {"ok": True, "issues": [], "latest_token_id": "job-token-id"},
            "physical_evidence": {},
            "evidence_policy": {
                "ok": True,
                "integrity_ok": True,
                "outcome_ok": True,
                "missing_required_modalities": [],
                "failed_required_modalities": [],
            },
            "tool_lifecycle": {"ok": True},
            "task_boundary": {
                "ok": True,
                "verdict_count": 1,
                "latest": {"ok": True, "status": "completed_verified"},
                "failed": [],
            },
        },
    }
    record.update(overrides)
    return record
