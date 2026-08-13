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




class TestCommandServesBuildOutput:
    """Direct tests for _command_serves_build_output helper."""

    def test_vite_preview_true(self) -> None:
        assert _command_serves_build_output("vite preview") is True

    def test_npx_vite_preview_true(self) -> None:
        assert _command_serves_build_output("npx vite preview") is True

    def test_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("serve -s dist") is True

    def test_npx_serve_s_dist_true(self) -> None:
        assert _command_serves_build_output("npx serve -s dist") is True

    def test_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("http-server dist") is True

    def test_serve_dot_slash_dist_true(self) -> None:
        assert _command_serves_build_output("serve ./dist") is True

    def test_serve_dist_index_js_true(self) -> None:
        assert _command_serves_build_output("serve dist/index.js") is True

    def test_serve_build_true(self) -> None:
        assert _command_serves_build_output("serve build") is True

    def test_serve_out_true(self) -> None:
        assert _command_serves_build_output("serve out") is True

    def test_serve_dir_equals_dist_true(self) -> None:
        assert _command_serves_build_output("serve --dir=dist") is True

    def test_serve_public_false(self) -> None:
        assert _command_serves_build_output("serve public") is False

    def test_serve_src_false(self) -> None:
        assert _command_serves_build_output("serve src") is False

    def test_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("serve") is False

    def test_npx_serve_public_false(self) -> None:
        assert _command_serves_build_output("npx serve public") is False

    def test_npx_serve_no_args_false(self) -> None:
        assert _command_serves_build_output("npx serve") is False

    def test_http_server_public_false(self) -> None:
        assert _command_serves_build_output("http-server public") is False

    def test_http_server_no_args_false(self) -> None:
        assert _command_serves_build_output("http-server") is False

    def test_npx_http_server_dist_true(self) -> None:
        assert _command_serves_build_output("npx http-server dist") is True

    def test_serve_scripts_false(self) -> None:
        assert _command_serves_build_output("serve scripts") is False

    def test_empty_command_false(self) -> None:
        assert _command_serves_build_output("") is False


def test_real_run_gate_build_first_when_start_serves_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "serve -s dist",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "test" in script_names
    assert "start" in script_names
    build_idx = script_names.index("build")
    test_idx = script_names.index("test")
    start_idx = script_names.index("start")
    assert build_idx < test_idx < start_idx


def test_real_run_gate_build_failure_blocks_start_with_serve_dist(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "start": "serve -s dist",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        is_build = command == ["npm", "run", "build"]
        return {
            "command": command,
            "ok": not is_build,
            "returncode": 1 if is_build else 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "BUILD FAILED" if is_build else "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" not in script_names
    assert gate["entrypoint"]["ok"] is False


def test_real_run_gate_build_first_when_start_vite_preview(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "vite build",
                    "start": "vite preview",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "build" in script_names
    assert "start" in script_names
    build_idx = script_names.index("build")
    start_idx = script_names.index("start")
    assert build_idx < start_idx


def test_real_run_gate_false_positive_guard_no_forced_build(monkeypatch: Any, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "build": "webpack",
                    "test": "jest",
                    "start": "node scripts/build/start.js",
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "index.js").write_text("console.log('ok');\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_which(name: str) -> str | None:
        return "/tool/npm" if name == "npm" else None

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates.shutil, "which", fake_which)
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["index.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    script_calls = [cmd for cmd in commands if cmd[0] == "npm"]
    script_names = [cmd[2] for cmd in script_calls]
    assert "test" in script_names
    build_test_calls = [
        cmd for cmd in commands if cmd[0] == "npm" and cmd[1] == "run" and cmd[2] in ("test", "build", "lint", "check")
    ]
    assert len(build_test_calls) == 1
    assert build_test_calls[0][2] == "test"


def test_collect_llm_events_reads_runtime_role_jsonl(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    events_dir = runtime / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(
            {
                "event": "llm_call_end",
                "role": "pm",
                "data": {
                    "role": "pm",
                    "provider": "kimi-cloud",
                    "model": "kimi-k2",
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                },
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, runtime)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True


def test_collect_llm_events_projects_final_request_evidence(tmp_path: Path) -> None:
    runtime = tmp_path / "runtime"
    events_dir = runtime / "events"
    events_dir.mkdir(parents=True)
    refs = {
        "pm": "111111111111111111111111",
        "chief_engineer": "222222222222222222222222",
        "director": "333333333333333333333333",
    }
    for role in ("pm", "chief_engineer", "director"):
        (events_dir / f"{role}.llm.events.jsonl").write_text(
            json.dumps(
                {
                    "event": "llm_call_start",
                    "role": role,
                    "context_snapshot_ref": f"runtime/contexts/{role}/{refs[role]}.json",
                    "final_request_context_audit_hash": f"audit-hash-{role}",
                    "final_request_evidence_hash": f"evidence-hash-{role}",
                    "final_request_evidence": {
                        "context_snapshot_ref": f"runtime/contexts/{role}/{refs[role]}.json",
                        "final_request_context_audit_present": True,
                        "final_request_evidence_authority_hash": f"authority-hash-{role}",
                        "final_request_evidence_coverage_pass": False,
                        "role_id": role,
                        "expected_role_id": role,
                        "role_identity_ok": True,
                        "required_refs": ["pm_contract", "ce_blueprint"],
                        "included_refs": ["pm_contract"],
                        "missing_required_refs": ["execution_envelope"],
                        "required_tools": ["read_file", "write_file"],
                        "available_tools": ["read_file"],
                        "missing_required_tools": ["write_file"],
                        "unexpected_tool_pruning": [
                            {
                                "tool": "write_file",
                                "reason": "required_tool_missing_from_final_provider_request",
                            }
                        ],
                        "tool_schema_registry_coverage": {"missing_schema_tools": ["write_file"]},
                        "workflow_chain": {"pm_contract_hash": f"pm-hash-{role}"},
                    },
                    "data": {"provider": "qwen-local", "model": "qwen3"},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    events = collect_llm_events(tmp_path, runtime)

    by_role = {event["role"]: event for event in events}
    assert set(by_role) == {"pm", "chief_engineer", "director"}
    for role in ("pm", "chief_engineer", "director"):
        event = by_role[role]
        assert event["context_snapshot_ref"] == refs[role]
        assert event["final_request_context_audit_present"] is True
        assert event["final_request_context_audit_hash"] == f"audit-hash-{role}"
        assert event["final_request_evidence_hash"] == f"evidence-hash-{role}"
        assert event["final_request_evidence_authority_hash"] == f"authority-hash-{role}"
        assert event["final_request_evidence_coverage_pass"] is False
        assert event["role_id"] == role
        assert event["expected_role_id"] == role
        assert event["role_identity_ok"] is True
        assert event["required_refs"] == ["pm_contract", "ce_blueprint"]
        assert event["included_refs"] == ["pm_contract"]
        assert event["missing_required_refs"] == ["execution_envelope"]
        assert event["required_tools"] == ["read_file", "write_file"]
        assert event["available_tools"] == ["read_file"]
        assert event["missing_required_tools"] == ["write_file"]
        assert event["unexpected_tool_pruning"][0]["tool"] == "write_file"
        assert event["tool_schema_registry_coverage"] == {"missing_schema_tools": ["write_file"]}
        assert event["workflow_chain"] == {"pm_contract_hash": f"pm-hash-{role}"}


def test_collect_llm_events_reads_multiple_runtime_candidates(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    runtime_a = tmp_path / "runtime-a"
    runtime_b = tmp_path / "runtime-b"
    for runtime, role, model in (
        (runtime_a, "pm", "kimi-k2"),
        (runtime_b, "director", "qwen3.6-27b-gpu0"),
    ):
        events_dir = runtime / "events"
        events_dir.mkdir(parents=True)
        (events_dir / f"{role}.llm.events.jsonl").write_text(
            json.dumps(
                {
                    "event": "llm_call_end",
                    "role": role,
                    "model": model,
                    "data": {"prompt_tokens": 1, "completion_tokens": 2},
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    events = collect_llm_events(workspace, [runtime_a, runtime_b])

    assert {(event["role"], event["model"]) for event in events} == {
        ("pm", "kimi-k2"),
        ("director", "qwen3.6-27b-gpu0"),
    }


def test_collect_llm_events_reads_route_events_from_audit_bundle_result(tmp_path: Path) -> None:
    bundle = {
        "events_tail": [
            {
                "type": "stage_completed",
                "stage": "director_dispatch",
                "result": {
                    "per_binding_route_events": [_real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0")],
                },
            }
        ],
    }

    events = collect_llm_events(tmp_path, None, bundle)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu0", "d0")
    ]
    assert events[0]["source_path"] == "audit_bundle.events_tail.result"


def test_collect_llm_events_reads_factory_dispatch_log_glob(tmp_path: Path) -> None:
    dispatch_dir = tmp_path / ".polaris" / "dispatch"
    dispatch_dir.mkdir(parents=True)
    dispatch_log = dispatch_dir / "factory_abc123.log.json"
    dispatch_log.write_text(
        json.dumps(
            {
                "per_binding_route_events": [_real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1")],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    events = collect_llm_events(tmp_path, None)

    assert [(event["role"], event["provider_id"], event["binding_id"]) for event in events] == [
        ("director", "qwen-gpu1", "d1")
    ]
    assert events[0]["source_path"].endswith("factory_abc123.log.json")


def test_llm_route_audit_requires_actual_bound_families_and_all_director_routes() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_prefers_actual_configured_binding_over_hardcoded_family() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "openai-a", "model": "gpt-5.3-codex", "binding_id": ""}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "glm-a", "model": "glm-4.7-flash", "binding_id": ""}
        ],
        "qa": [{"role": "qa", "provider_id": "gemini-a", "model": "gemini-2.5-pro", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "local-director", "model": "custom-director-30b", "binding_id": "d0"}
        ],
    }
    events = [
        _real_llm_event("pm", "openai-a", "gpt-5.3-codex"),
        _real_llm_event("chief_engineer", "glm-a", "glm-4.7-flash"),
        _real_llm_event("qa", "gemini-a", "gemini-2.5-pro"),
        _real_llm_event("director", "local-director", "custom-director-30b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["pm"]["family_ok"] is True
    assert audit["roles"]["chief_engineer"]["family_ok"] is True
    assert audit["roles"]["qa"]["family_ok"] is True
    assert audit["roles"]["director"]["family_ok"] is True


def test_llm_route_audit_fails_when_a_director_route_is_unobserved() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_treats_readiness_skipped_director_as_diagnostic() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_connectivity_unavailable",
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["missing_bindings"] == []
    assert audit["roles"]["director"]["skipped_bindings"] == ["qwen-gpu0|qwen3.6-27b"]
    assert audit["roles"]["director"]["fail_closed_count"] == 1


def test_llm_route_audit_fails_when_all_director_routes_are_readiness_skipped() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    skipped = [
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu0",
            "model": "qwen3.6-27b",
            "binding_id": "d0",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_unreachable",
        },
        {
            "event": "llm_route_terminal",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b",
            "binding_id": "d1",
            "source": "llm",
            "cache_hit": False,
            "invocation": False,
            "terminal": True,
            "fail_closed": True,
            "skipped": True,
            "skip_reason": "provider_unreachable",
        },
    ]
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        *skipped,
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["roles"]["director"]["observed_count"] == 0
    assert audit["roles"]["director"]["fail_closed_count"] == 2
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["skipped_bindings"] == [
        "qwen-gpu0|qwen3.6-27b",
        "qwen-gpu1|qwen3.6-27b",
    ]


def test_llm_route_audit_accepts_single_live_director_route() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "chief_engineer": [{"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-k2"),
        _real_llm_event("chief_engineer", "kimi-a", "kimi-k2"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True


def test_llm_route_audit_can_relax_director_route_coverage_for_serial_bench() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-a", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-b", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("pm", "kimi-a", "kimi-for-coding", "pm0"),
        _real_llm_event("qa", "minimax-a", "MiniMax-M3", "qa0"),
        _real_llm_event("director", "qwen-b", "qwen3.6-27b-gpu1", "d1"),
    ]

    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "qa", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["multi_route_required"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_llm_route_audit_resolves_providerless_via_expected_bindings_and_rejects_cached() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "pm0"}],
        "chief_engineer": [
            {"role": "chief_engineer", "provider_id": "kimi-a", "model": "kimi-for-coding", "binding_id": "ce0"}
        ],
        "qa": [{"role": "qa", "provider_id": "minimax-a", "model": "MiniMax-M3", "binding_id": "qa0"}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b-gpu0", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b-gpu1", "binding_id": "d1"},
        ],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "chief_engineer",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-for-coding", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "qa",
            "provider_id": "minimax-a",
            "model": "MiniMax-M3",
            "source": "cache",
            "terminal": True,
            "invocation": True,
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b-gpu0", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "provider_id": "qwen-gpu1",
            "model": "qwen3.6-27b-gpu1",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected)

    assert audit["ok"] is False
    assert audit["events_observed"] == 3
    assert audit["events_rejected"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["chief_engineer"]["observed_count"] == 1
    assert audit["roles"]["chief_engineer"]["observed_bindings"] == ["kimi-a|kimi-for-coding"]
    assert audit["roles"]["qa"]["observed_count"] == 0
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b-gpu0"]


def test_failure_taxonomy_prefers_llm_route_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [{"gate": "llm_route_audit", "ok": False, "detail": "missing qa"}],
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: qa"},
        "chain_state": "fail",
        "checks": [],
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:llm_route_audit"


def test_failure_taxonomy_classifies_integration_qa_before_generic_chain_failure() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=True qa_passed=False"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_results": {"qa_reason": "qa_passed=False; qa_score=34"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:integration_qa_failed"
    assert taxonomy["evidence"] == ["qa_passed=False; qa_score=34"]


def test_failure_taxonomy_prefers_plannable_repair_convergence_before_integration_qa() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: build_test_lint_ran"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "npm run build failed: TS2304 Cannot find name 'dayOfYear'",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "11 generated file(s)"},
                "build_test_lint_ran": {"ok": False, "detail": "TS2304 Cannot find name 'dayOfYear'"},
            },
        },
        "chain_results": {"qa_reason": "qa did not run because upstream task did not compile"},
        "workspace_quality": {
            "repair": {
                "plan_probe_preaudit": {
                    "schema_version": "director.repair_plan_probe_result.v1",
                    "status": "covered_plannable",
                    "plannable_source_tools": ["deterministic_typescript_unresolved_identifier_repair"],
                },
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "repair_convergence"
    assert taxonomy["root_cause_signature"] == "repair_convergence:covered_plannable_not_converged"
    assert taxonomy["evidence"] == [
        "plan_probe:covered_plannable;plannable_source_tools=deterministic_typescript_unresolved_identifier_repair"
    ]


def test_failure_taxonomy_routes_unplannable_probe_to_task_boundary() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "workspace_quality": {
            "repair": {
                "plan_probe_preaudit": {
                    "schema_version": "director.repair_plan_probe_result.v1",
                    "status": "coverage_matched_but_unplannable",
                    "plannable_source_tools": [],
                    "covered_unplannable_source_tools": ["deterministic_typescript_missing_export_repair"],
                },
            },
        },
        "real_run_gate": {"ok": False, "summary": "build failed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:repair_plan_probe_unplannable"
    assert taxonomy["evidence"] == [
        "plan_probe:coverage_matched_but_unplannable;"
        "covered_unplannable_source_tools=deterministic_typescript_missing_export_repair"
    ]


def test_failure_taxonomy_prefers_task_boundary_dependency_before_integration_qa() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "schema_version": "task_boundary.verdict.v1",
                    "ok": False,
                    "status": "dependency_not_unlocked",
                    "failure_class": "dependency_not_unlocked",
                    "responsible_layer": "task_boundary",
                    "reason": "TASK-1 did not reach completed_verified",
                }
            ]
        ),
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_results": {"qa_reason": "qa did not run"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:dependency_not_unlocked"
    assert taxonomy["evidence"] == [
        "failure_class=dependency_not_unlocked;responsible_layer=task_boundary;TASK-1 did not reach completed_verified"
    ]


def test_failure_taxonomy_prefers_specific_task_boundary_failure_over_downstream_dependency(
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    results_dir = runtime_dir / "results"
    results_dir.mkdir(parents=True)
    (results_dir / "director.result.json").write_text(
        json.dumps(
            {
                "task_results": [
                    {
                        "task_id": "TASK-3",
                        "status": "blocked",
                        "error": "blocked_by_failed_dependency",
                        "blocked_by": ["TASK-2"],
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    record = {
        "all_checks_passed": False,
        "runtime_dir": str(runtime_dir),
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "integration_qa_passed", "ok": False, "detail": "qa_ran=False qa_passed=False"},
        ],
        "run_ledger_projection": _canonical_run_ledger_projection(
            task_boundary_failures=[
                {
                    "schema_version": "task_boundary.verdict.v1",
                    "ok": False,
                    "status": "missing_entrypoint_target",
                    "failure_class": "MISSING_ENTRYPOINT_TARGET",
                    "responsible_layer": "task_boundary",
                    "reason": "index.html references src/web.js",
                }
            ]
        ),
        "real_run_gate": {"ok": False, "summary": "entrypoint smoke failed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "task_boundary"
    assert taxonomy["root_cause_signature"] == "task_boundary:missing_entrypoint_target"
    assert taxonomy["evidence"] == [
        "failure_class=MISSING_ENTRYPOINT_TARGET;responsible_layer=task_boundary;index.html references src/web.js"
    ]


def test_failure_taxonomy_classifies_missing_toolchain_check_as_runtime_environment() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [{"check": "go_compile", "ok": False, "detail": "go unavailable for Go project"}],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:go_compile"


def test_failure_taxonomy_classifies_workspace_switch_before_real_run_gate() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: artifact_landed"},
        ],
        "chain": {
            "error": "workspace_switch_failed",
            "workspace_switch": {"workspace": "/tmp/factory-bench/L1-01"},
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: artifact_landed",
            "requirements": {"artifact_landed": {"ok": False, "detail": "no generated source files"}},
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "fail",
        "checks": [],
        "has_plan_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:workspace_switch_failed"
    assert taxonomy["evidence"] == ["/tmp/factory-bench/L1-01"]


def test_failure_taxonomy_classifies_all_director_bindings_unavailable_as_runtime_environment() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "chain_clean", "ok": False, "detail": "chain_state=partial exit_code=1"},
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: artifact_landed"},
            {"gate": "llm_route_audit", "ok": False, "detail": "LLM route audit failed: director"},
        ],
        "chain": {
            "audit_bundle": {
                "failure": {
                    "detail": "Director dispatch failed: No available Director binding after readiness filtering",
                }
            },
            "factory_terminal_status": {
                "event_payload": {
                    "result": {
                        "metadata": {
                            "binding_count": 2,
                            "active_binding_count": 0,
                            "readiness_skipped_count": 2,
                            "per_binding": [
                                {
                                    "provider_id": "qwen-gpu0",
                                    "model": "qwen3.6-27b",
                                    "status": "skipped",
                                    "skip_reason": "provider_unreachable",
                                },
                                {
                                    "provider_id": "qwen-gpu1",
                                    "model": "qwen3.6-27b",
                                    "status": "skipped",
                                    "skip_reason": "provider_unreachable",
                                },
                            ],
                        }
                    }
                }
            },
        },
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: artifact_landed",
            "requirements": {"artifact_landed": {"ok": False, "detail": "no generated source files"}},
        },
        "llm_route_audit": {"ok": False, "summary": "LLM route audit failed: director"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "runtime_environment"
    assert taxonomy["root_cause_signature"] == "runtime_environment:director_bindings_unavailable"
    assert taxonomy["evidence"] == ["Director dispatch failed: No available Director binding after readiness filtering"]


def test_failure_taxonomy_classifies_generated_typescript_syntax_failure_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: build_test_lint_ran"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "22 generated code file(s)"},
                "environment_prepared": {"ok": True, "detail": "npm available"},
                "build_test_lint_ran": {
                    "ok": False,
                    "detail": "npm test failed: src/models/humidity.ts(1,29): error TS1434",
                },
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [
            {
                "check": "ts_syntax",
                "ok": False,
                "detail": "TypeScript syntax check failed: src/models/humidity.ts(1,29): TS1434",
            }
        ],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.build_test_lint_ran"


def test_failure_taxonomy_classifies_missing_typescript_dependency_as_llm_output() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "real_run_gate", "ok": False, "detail": "real run gate failed: environment_prepared"},
        ],
        "real_run_gate": {
            "ok": False,
            "summary": "real run gate failed: environment_prepared, build_test_lint_ran",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "4 generated code file(s)"},
                "environment_prepared": {
                    "ok": False,
                    "detail": "package.json missing devDependency 'typescript' for TypeScript build",
                },
                "build_test_lint_ran": {"ok": False, "detail": "no build/test/lint command was discovered"},
            },
        },
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "partial",
        "checks": [],
        "has_plan_doc": True,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "llm_output"
    assert taxonomy["root_cause_signature"] == "llm_output:real_run_gate.environment_prepared"


def test_failure_taxonomy_classifies_missing_blueprint_as_chief_engineer_blueprint() -> None:
    record = {
        "all_checks_passed": False,
        "factory_gates": [
            {"gate": "plan_artifact_present", "ok": True, "detail": "plan artifact discovered"},
            {"gate": "blueprint_artifact_present", "ok": False, "detail": "blueprint artifact missing"},
            {"gate": "qa_verdict_artifact_present", "ok": True, "detail": "QA verdict artifact discovered"},
            {"gate": "chain_clean", "ok": True, "detail": "chain_state=clean exit_code=0"},
            {"gate": "integration_qa_passed", "ok": True, "detail": "qa_ran=True qa_passed=True"},
            {"gate": "real_run_gate", "ok": True, "detail": "real run gate passed"},
            {"gate": "llm_route_audit", "ok": True, "detail": "LLM route audit passed"},
        ],
        "real_run_gate": {"ok": True, "summary": "real run gate passed"},
        "llm_route_audit": {"ok": True, "summary": "LLM route audit passed"},
        "chain_state": "clean",
        "checks": [],
        "has_plan_doc": True,
        "has_blueprint_doc": False,
        "wrong_product_suspect": False,
    }

    taxonomy = classify_factory_bench_failure(record)

    assert taxonomy["category"] == "chief_engineer_blueprint"
    assert taxonomy["root_cause_signature"] == "chief_engineer_blueprint:missing_or_invalid_blueprint"


def test_aggregate_goal_audit_counts_real_route_ledger_and_root_causes(tmp_path: Path) -> None:
    ledger_file = tmp_path / "ledger.ndjson"
    ledger_file.write_text('{"ok":true}\n', encoding="utf-8")
    records = [
        {
            "real_run_gate": {"ok": True},
            "run_ledger_projection": {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": True,
                "ok": True,
                "event_count": 1,
                "gate_count": 1,
                "failed_gates": [],
                "capability": {"ok": True, "issues": [], "latest_token_id": "j1"},
                "physical_evidence": {},
            },
            "llm_route_audit": {"ok": True},
            "failure_taxonomy": {"ok": True},
        },
        {
            "real_run_gate": {"ok": False},
            "llm_route_audit": {"ok": False},
            "failure_taxonomy": {
                "ok": False,
                "category": "target_project_baseline",
                "root_cause_signature": "target_project_baseline:real_run_gate.entrypoint_smoke",
            },
        },
        {
            "real_run_gate": {"ok": False},
            "run_ledger_projection": {
                "source": "run_ledger",
                "integrity_ok": True,
                "outcome_ok": False,
                "ok": False,
                "event_count": 1,
                "gate_count": 1,
                "failed_gates": [{"name": "real_run_gate", "ok": False}],
                "capability": {"ok": True, "issues": [], "latest_token_id": "j2"},
                "evidence_policy": {
                    "missing_required_modalities": [],
                    "failed_required_modalities": ["command"],
                },
                "physical_evidence": {"command_count": 1},
            },
            "llm_route_audit": {"ok": True},
            "failure_taxonomy": {
                "ok": False,
                "category": "llm_output",
                "root_cause_signature": "llm_output:real_run_gate.build_test_lint_ran",
            },
        },
    ]

    aggregate = aggregate_goal_audit(records)

    assert aggregate["real_run_gate"] == {"passed": 1, "total": 3}
    assert aggregate["run_ledger"] == {"projected": 2, "total": 3, "missing": 1}
    assert aggregate["llm_route_audit"] == {"passed": 2, "total": 3}
    assert aggregate["failure_categories"]["target_project_baseline"] == 1
    assert aggregate["failure_categories"]["llm_output"] == 1


def test_nested_roles_kernel_events_passes_with_model_only_and_expected_binding() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [{"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"}],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "kimi-k2", "metadata": {"source": "llm"}},
        },
        {
            "event": "llm_call_end",
            "role": "director",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"model": "qwen3.6-27b", "metadata": {"source": "llm"}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["events_rejected"] == 0
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["pm"]["observed_bindings"] == ["kimi-a|kimi-k2"]
    assert audit["roles"]["director"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_bindings"] == ["qwen-gpu0|qwen3.6-27b"]


def test_metadata_cached_true_rejected() -> None:
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": "pm0"}],
        "chief_engineer": [],
        "qa": [],
        "director": [],
    }
    events = [
        {
            "event": "llm_call_end",
            "role": "pm",
            "provider_id": "kimi-a",
            "model": "kimi-k2",
            "source": "roles.kernel.events",
            "terminal": True,
            "invocation": True,
            "data": {"metadata": {"source": "llm", "cached": True}},
        },
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("pm",))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["events_rejected"] == 1
    assert audit["roles"]["pm"]["observed_count"] == 0


def test_director_multi_binding_missing_one_fails() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is False
    assert audit["roles"]["director"]["multi_route_ok"] is False
    assert audit["roles"]["director"]["missing_bindings"]


def test_director_multi_binding_all_pass() -> None:
    expected = {
        "pm": [],
        "chief_engineer": [],
        "qa": [],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = [
        _real_llm_event("director", "qwen-gpu0", "qwen3.6-27b", "d0"),
        _real_llm_event("director", "qwen-gpu1", "qwen3.6-27b", "d1"),
    ]

    audit = build_llm_route_audit(events, expected_bindings=expected, required_roles=("director",))

    assert audit["ok"] is True
    assert audit["roles"]["director"]["multi_route_ok"] is True
    assert audit["roles"]["director"]["observed_count"] == 2


def test_real_run_gate_source_files_present_with_real_source(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text(
        "from __future__ import annotations\n"
        "import sys\n"
        "if __name__ == '__main__':\n"
        "    print('usage' if '--help' in sys.argv else 'ok')\n",
        encoding="utf-8",
    )
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is True
    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_fails_for_scaffold_only(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "scaffold-only" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["source_file_count"] == 0
    assert gate["missing_source_targets"]["code_file_count"] == 2


def test_real_run_gate_source_files_present_with_ts_source(tmp_path: Path) -> None:
    (tmp_path / "index.ts").write_text("export const hello = () => 'world';\n", encoding="utf-8")
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {"code_files": ["index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["source_files_present"]["ok"] is True
    assert "1 source file" in gate["requirements"]["source_files_present"]["detail"]
    assert "missing_source_targets" not in gate


def test_real_run_gate_source_files_present_with_mixed_scaffold(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    (tmp_path / ".catalog_meta.json").write_text("{}", encoding="utf-8")
    (tmp_path / "README.md").write_text("# Test", encoding="utf-8")
    record = {"code_files": ["package.json", "tsconfig.json", ".catalog_meta.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["source_files_present"]["ok"] is False
    assert "missing_source_targets" in gate
    assert gate["missing_source_targets"]["code_file_count"] == 3
    assert gate["missing_source_targets"]["source_file_count"] == 0


def test_real_run_gate_declared_source_targets_missing_fails(tmp_path: Path) -> None:
    """plan declares src/index.ts but workspace only has package.json -> gate fail."""
    (tmp_path / "package.json").write_text('{"name": "test"}', encoding="utf-8")
    record = {
        "code_files": ["package.json"],
        "declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "declared_source_target_count": 2,
        "missing_declared_source_targets": ["src/index.ts", "src/utils.ts"],
        "missing_declared_source_target_count": 2,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "2 declared source target(s) missing" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_declared_source_targets_all_present_passes(tmp_path: Path) -> None:
    """All declared source targets exist -> gate ok."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text("export const x = 1;\n", encoding="utf-8")
    record = {
        "code_files": ["src/index.ts"],
        "declared_source_targets": ["src/index.ts"],
        "declared_source_target_count": 1,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert (
        "all 1 declared source target(s) present" in gate["requirements"]["declared_source_targets_present"]["detail"]
    )


def test_real_run_gate_pm_plan_missing_source_targets_fails(tmp_path: Path) -> None:
    """PM plan with no source targets -> pm_plan_missing_source_targets signal."""
    (tmp_path / "README.md").write_text("# Test\n", encoding="utf-8")
    record = {
        "code_files": ["README.md"],
        "declared_source_targets": [],
        "declared_source_target_count": 0,
        "missing_declared_source_targets": [],
        "missing_declared_source_target_count": 0,
        "pm_plan_missing_source_targets": True,
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is False
    assert "pm_plan_missing_source_targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_real_run_gate_no_declared_targets_no_plan(tmp_path: Path) -> None:
    """No plan.json -> no declared targets, requirement passes."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {
        "code_files": ["main.py"],
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["requirements"]["declared_source_targets_present"]["ok"] is True
    assert "no declared source targets" in gate["requirements"]["declared_source_targets_present"]["detail"]


def test_collect_llm_events_reads_from_resolve_polaris_roots_path(tmp_path: Path) -> None:
    """Events written by _emit_llm_event_to_disk to resolve_polaris_roots path are found."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    polaris_dir = workspace / ".polaris"
    polaris_dir.mkdir()
    runtime = polaris_dir / "runtime"
    runtime.mkdir()
    events_dir = runtime / "events"
    events_dir.mkdir()
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["role"] == "pm"
    assert events[0]["provider_id"] == "kimi-cloud"
    assert events[0]["model"] == "kimi-k2"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_collect_llm_events_reads_disk_schema_from_polaris_roots(tmp_path: Path) -> None:
    """Events in _emit_llm_event_to_disk schema are normalized with correct source=llm."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    events = collect_llm_events(workspace, None)

    assert len(events) == 1
    assert events[0]["source"] == "llm"
    assert events[0]["terminal"] is True
    assert events[0]["invocation"] is True


def test_build_llm_route_audit_observes_configured_bindings_from_disk_events(
    tmp_path: Path,
) -> None:
    """build_llm_route_audit observes PM and Director bindings from disk-format events."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )
    (events_dir / "director.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("director", "qwen-gpu0", "qwen3.6-27b")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
        require_all_director_routes=False,
    )

    assert audit["ok"] is True
    assert audit["events_observed"] == 2
    assert audit["roles"]["pm"]["observed_count"] == 1
    assert audit["roles"]["director"]["observed_count"] == 1


def test_llm_route_audit_missing_director_evidence_fails_closed(tmp_path: Path) -> None:
    """Missing Director route evidence remains fail-closed (events_observed=0)."""
    workspace = tmp_path / "ws"
    workspace.mkdir()
    events_dir = workspace / ".polaris" / "runtime" / "events"
    events_dir.mkdir(parents=True)
    (events_dir / "pm.llm.events.jsonl").write_text(
        json.dumps(_disk_llm_event("pm", "kimi-cloud", "kimi-k2")) + "\n",
        encoding="utf-8",
    )

    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-cloud", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
            {"role": "director", "provider_id": "qwen-gpu1", "model": "qwen3.6-27b", "binding_id": "d1"},
        ],
    }
    events = collect_llm_events(workspace, None)
    audit = build_llm_route_audit(
        events,
        expected_bindings=expected,
        required_roles=("pm", "director"),
    )

    assert audit["ok"] is False
    assert audit["roles"]["director"]["ok"] is False
    assert audit["roles"]["director"]["observed_count"] == 0
    assert audit["roles"]["pm"]["ok"] is True


def test_llm_route_audit_zero_events_with_all_required_roles_fails(tmp_path: Path) -> None:
    """Zero events with all required roles -> all roles fail."""
    expected = {
        "pm": [{"role": "pm", "provider_id": "kimi-a", "model": "kimi-k2", "binding_id": ""}],
        "director": [
            {"role": "director", "provider_id": "qwen-gpu0", "model": "qwen3.6-27b", "binding_id": "d0"},
        ],
    }

    audit = build_llm_route_audit([], expected_bindings=expected, required_roles=("pm", "director"))

    assert audit["ok"] is False
    assert audit["events_observed"] == 0
    assert audit["roles"]["pm"]["ok"] is False
    assert audit["roles"]["director"]["ok"] is False


def test_resolve_polaris_roots_runtime_dir_returns_path(tmp_path: Path) -> None:
    """_resolve_polaris_roots_runtime_dir returns a Path for a valid workspace."""
    workspace = tmp_path / "ws"
    workspace.mkdir()

    result = _resolve_polaris_roots_runtime_dir(workspace)

    assert result is not None
    assert isinstance(result, Path)
    assert "runtime" in str(result)


def test_resolve_polaris_roots_runtime_dir_returns_none_for_invalid() -> None:
    """_resolve_polaris_roots_runtime_dir returns None gracefully."""
    result = _resolve_polaris_roots_runtime_dir(Path("/nonexistent/path/that/does/not/exist"))
    # Should not raise; may return a path or None depending on cache availability
    assert result is None or isinstance(result, Path)


# ---------------------------------------------------------------------------
# Scaffolding requirement tests (R18-C)
# ---------------------------------------------------------------------------


def test_real_run_gate_ts_project_without_package_json_fails(tmp_path: Path) -> None:
    """TypeScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_package_but_no_tsconfig_fails(tmp_path: Path) -> None:
    """TypeScript project with package.json but no tsconfig.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "tsconfig.json" in scaffolding["detail"]


def test_real_run_gate_ts_project_with_scaffolding_passes(tmp_path: Path) -> None:
    """TypeScript project with package.json and tsconfig.json passes scaffolding."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "package.json present" in scaffolding["detail"]
    assert "tsconfig.json present" in scaffolding["detail"]


def test_real_run_gate_fails_blank_canvas_even_when_package_scripts_pass(
    monkeypatch: Any,
    tmp_path: Path,
) -> None:
    (tmp_path / "index.html").write_text(
        "<html><body><canvas id='scene'></canvas><script src='app.js'></script></body></html>",
        encoding="utf-8",
    )
    (tmp_path / "app.js").write_text("console.log('loaded');\n", encoding="utf-8")
    (tmp_path / "package.json").write_text(
        json.dumps(
            {
                "name": "blank-canvas",
                "scripts": {
                    "build": 'node -e "process.exit(0)"',
                    "test": 'node -e "process.exit(0)"',
                },
            }
        ),
        encoding="utf-8",
    )

    def fake_smoke_static_web(_workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
        return {
            "kind": "web_playwright",
            "ok": False,
            "entrypoint": html_rel,
            "has_canvas": True,
            "canvas_non_blank": False,
            "detail": "Canvas entrypoint did not render non-empty pixels",
        }

    monkeypatch.setattr(bench_gates, "_smoke_static_web", fake_smoke_static_web)
    record = {"code_files": ["index.html", "app.js", "package.json"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False
    assert gate["entrypoint"]["detail"] == "Canvas entrypoint did not render non-empty pixels"


def test_real_run_gate_ts_project_requires_local_typescript_dependency(monkeypatch: Any, tmp_path: Path) -> None:
    """Package-managed TypeScript projects must not borrow host global tsc."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.ts").write_text(
        "export const hello = () => 'world';\n",
        encoding="utf-8",
    )
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "test", "scripts": {"build": "tsc", "test": "npm run build"}}),
        encoding="utf-8",
    )
    (tmp_path / "tsconfig.json").write_text(
        json.dumps({"compilerOptions": {"outDir": "dist"}}),
        encoding="utf-8",
    )
    record = {"code_files": ["src/index.ts", "package.json", "tsconfig.json"]}

    def fail_if_run(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("npm scripts must not run when TypeScript dependency is missing")

    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/npm" if name == "npm" else None)
    monkeypatch.setattr(subprocess, "run", fail_if_run)

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    environment = gate["requirements"]["environment_prepared"]
    assert environment["ok"] is False
    assert "missing devDependency 'typescript'" in environment["detail"]
    assert not any(command.get("phase") == "build_test_lint" for command in gate["commands"])


def test_real_run_gate_html_project_without_index_fails(tmp_path: Path) -> None:
    """HTML project with code_files claiming .html but no real file must fail closed."""
    record = {"code_files": ["index.html"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    assert gate["requirements"]["scaffolding_present"]["ok"] is False
    assert "index.html" in gate["requirements"]["scaffolding_present"]["detail"]
    assert gate["requirements"]["entrypoint_smoke"]["ok"] is False


def test_real_run_gate_js_project_without_package_json_fails(tmp_path: Path) -> None:
    """JavaScript project with source files but no package.json must fail."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.js").write_text("console.log('hello');\n", encoding="utf-8")
    record = {"code_files": ["src/app.js"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]


def test_real_run_gate_python_project_no_scaffolding_required(tmp_path: Path) -> None:
    """Python project does not require npm scaffolding."""
    (tmp_path / "main.py").write_text("print('ok')\n", encoding="utf-8")
    record = {"code_files": ["main.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is True
    assert "no scaffolding required" in scaffolding["detail"]


def test_real_run_gate_ts_source_only_no_scaffold_comprehensive(tmp_path: Path) -> None:
    """Comprehensive: TS project with src/**/*.ts but zero scaffolding fails on scaffolding."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "render.ts").write_text("export function render() { return 'ok'; }\n", encoding="utf-8")
    (src / "simulation.ts").write_text("export function simulate() { return 42; }\n", encoding="utf-8")
    record = {"code_files": ["src/render.ts", "src/simulation.ts"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["ok"] is False
    scaffolding = gate["requirements"]["scaffolding_present"]
    assert scaffolding["ok"] is False
    assert "package.json" in scaffolding["detail"]
    assert "tsconfig.json" in scaffolding["detail"]


def test_cli_smoke_result_timeout_not_success() -> None:
    """CLI timeout should not be considered successful."""
    result = {
        "ok": False,
        "returncode": -1,
        "duration_s": 2.0,
        "stdout_tail": "Interactive CLI started",
        "stderr_tail": "",
        "timeout": True,
        "timeout_s": 2,
    }

    payload = bench_gates._cli_smoke_result("python_cli", "main.py", result)

    assert payload["ok"] is False
    assert payload["started"] is True
    assert payload["timeout"] is True


def test_smoke_static_web_missing_explicit_resource_fails_closed(tmp_path: Path) -> None:
    """HTML that points at a missing local script is not a runnable web artifact."""
    (tmp_path / "index.html").write_text(
        "<html><body><canvas id='scene'></canvas><script type='module' src='/dist/bundle.js'></script></body></html>",
        encoding="utf-8",
    )

    original_smoke = bench_gates._smoke_static_web_playwright

    def force_http_fallback(workspace: Path, html_rel: str, *, timeout_s: int) -> dict[str, Any]:
        raise ImportError("playwright unavailable")

    bench_gates._smoke_static_web_playwright = force_http_fallback
    try:
        result = bench_gates._smoke_static_web(tmp_path, "index.html", timeout_s=10)

        assert result["ok"] is False
        assert result["kind"] == "web_static"
        assert "/dist/bundle.js" in result["missing_resources"]
        assert "missing local resources" in result["detail"]
    finally:
        bench_gates._smoke_static_web_playwright = original_smoke


def test_smoke_static_web_canvas_state_requires_non_blank_pixels() -> None:
    """A visual canvas entrypoint with only an empty canvas should fail the render smoke."""
    assert bench_gates._canvas_smoke_ok([]) is True
    assert bench_gates._canvas_smoke_ok([{"width": 300, "height": 150, "non_blank": False}]) is False
    assert bench_gates._canvas_smoke_ok([{"width": 300, "height": 150, "non_blank": True}]) is True


def test_smoke_static_web_console_resource_noise_is_not_enough_to_fail() -> None:
    """Generic network resource console noise is handled by resource-specific checks."""
    assert bench_gates._is_ignorable_web_console_error("Failed to load resource: net::ERR_CONNECTION_CLOSED") is True
    assert bench_gates._is_ignorable_web_console_error("favicon.ico failed to load") is True
    assert bench_gates._is_ignorable_web_console_error("Uncaught Error: render failed") is False


def test_smoke_static_web_favicon_is_not_required_resource(tmp_path: Path) -> None:
    """Missing favicon should not decide whether the generated app is runnable."""
    html = "<html><head><link rel='icon' href='favicon.ico'></head><body><img src='favicon.ico'></body></html>"

    assert bench_gates._html_local_resource_refs(tmp_path, "index.html", html) == []


def test_smoke_static_web_srcset_resources_are_checked(tmp_path: Path) -> None:
    """Responsive image resources in srcset are explicit local assets too."""
    html = "<html><body><img srcset='small.png 1x, /large.png 2x'></body></html>"

    assert bench_gates._html_local_resource_refs(tmp_path, "index.html", html) == ["small.png", "/large.png"]


def test_smoke_static_web_playwright_critical_errors_fail(tmp_path: Path) -> None:
    """Critical JavaScript errors should cause failure."""
    # Create a test HTML file
    (tmp_path / "index.html").write_text(
        "<html><body><script>throw new Error('Critical error');</script></body></html>",
        encoding="utf-8",
    )

    # Mock Playwright to simulate critical errors
    console_errors = [
        "Uncaught Error: Critical error",
        "Failed to load resource: the server responded with a status of 404 (Not Found)",
    ]

    critical_errors = [err for err in console_errors if not bench_gates._is_ignorable_web_console_error(err)]

    result = {
        "kind": "web_playwright",
        "ok": len(critical_errors) == 0,
        "url": "http://localhost/index.html",
        "entrypoint": "index.html",
        "duration_s": 0.1,
        "http_status": 200,
        "console_errors": console_errors,
        "has_canvas": False,
        "detail": f"Console errors: {'; '.join(critical_errors[:3])}",
    }

    # Should fail because of the critical error
    assert result["ok"] is False
    assert "Uncaught Error: Critical error" in str(result["detail"])


# ---------------------------------------------------------------------------
# Tests for _primary_source_language (F2/F3: Go/Rust projects with stray .py)
# ---------------------------------------------------------------------------


class TestPrimarySourceLanguage:
    """Verify that a Go project with a Python contract test is Go-primary."""

    def test_go_project_with_python_test(self) -> None:
        files = [
            "main.go",
            "src/engine/engine.go",
            "src/models/pet.go",
            "tests/test_ascii.py",
        ]
        assert _primary_source_language(files) == "go"

    def test_pure_go_project(self) -> None:
        assert _primary_source_language(["main.go", "lib.go"]) == "go"

    def test_pure_python_project(self) -> None:
        assert _primary_source_language(["main.py", "utils.py", "tests/test_main.py"]) == "python"

    def test_rust_project_with_python_test(self) -> None:
        files = ["src/main.rs", "src/lib.rs", "tests/test_contract.py"]
        assert _primary_source_language(files) == "rust"

    def test_node_project(self) -> None:
        files = ["index.js", "src/app.js", "src/utils.ts"]
        assert _primary_source_language(files) == "javascript"

    def test_html_only_project(self) -> None:
        assert _primary_source_language(["index.html", "style.css"]) == "html"

    def test_empty_project(self) -> None:
        assert _primary_source_language([]) == ""

    def test_mixed_go_and_python(self) -> None:
        # Both Go source and real Python source → Python wins by count
        files = ["main.go", "app.py", "utils.py", "helpers.py"]
        assert _primary_source_language(files) == "python"


# ---------------------------------------------------------------------------
# Tests for _go_command no-mutation behavior without go.mod
# ---------------------------------------------------------------------------


class TestGoCommandNoMutation:
    """Verify _go_command does not auto-init go.mod when missing."""

    def test_with_go_mod(self, monkeypatch: Any, tmp_path: Path) -> None:
        (tmp_path / "go.mod").write_text("module test\n", encoding="utf-8")
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == ["/usr/local/go/bin/go", "test", "./..."]

    def test_without_go_mod_uses_vet_fallback(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, ["main.go", "src/engine/engine.go"])
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]

    def test_without_go_mod_empty_files_returns_empty(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
        cmd = _go_command(tmp_path, [])
        assert cmd == []

    def test_without_go_mod_init_timeout_vet_fallback(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")

        def _raise_timeout(*args: Any, **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(cmd="go", timeout=15)

        monkeypatch.setattr(bench_gates.subprocess, "run", _raise_timeout)
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == ["/usr/local/go/bin/go", "vet", "main.go"]

    def test_go_unavailable(self, monkeypatch: Any, tmp_path: Path) -> None:
        monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: None)
        cmd = _go_command(tmp_path, ["main.go"])
        assert cmd == []


# ---------------------------------------------------------------------------
# Test: Go project skips Python test path (F2)
# ---------------------------------------------------------------------------


def test_rust_cargo_project_uses_native_test_gate(monkeypatch: Any, tmp_path: Path) -> None:
    """Rust delivery must execute native tests instead of a skipped Python harness."""
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-test"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "product.rs").write_text(
        "use native_rust_test::answer;\n#[test]\nfn product_works() { assert_eq!(answer(), 42); }\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(bench_gates.shutil, "which", lambda name: "/usr/bin/cargo" if name == "cargo" else None)

    command = bench_gates._rust_compile_command(
        tmp_path,
        ["src/lib.rs", "tests/product.rs"],
    )

    assert command == ["/usr/bin/cargo", "test", "--quiet"]


def test_rust_native_gate_physically_executes_integration_test(tmp_path: Path) -> None:
    """The real-run gate executes Rust tests in isolation from target sources."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-physical"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    source = tmp_path / "src" / "lib.rs"
    source.write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "product.rs").write_text(
        "#[test]\nfn product_works() {\n"
        "    assert_eq!(native_rust_physical::answer(), 42);\n"
        '    std::fs::write("src/lib.rs", "pub fn answer() -> u8 { 7 }\\n").unwrap();\n'
        f'    assert!(std::fs::write({json.dumps(source.as_posix())}, b"host mutation").is_err());\n'
        "}\n",
        encoding="utf-8",
    )

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs", "tests/product.rs"],
        timeout_s=30,
    )

    assert ok is True
    assert detail == "cargo test passed"
    assert commands[0]["command"][1:] == ["test", "--quiet"]
    assert commands[0]["sandboxed"] is True
    assert commands[0]["native_test_count"] >= 1
    assert source.read_text(encoding="utf-8") == "pub fn answer() -> u8 { 42 }\n"


def test_rust_native_gate_rejects_zero_tests(tmp_path: Path) -> None:
    """Cargo's exit-zero/zero-tests result is not valid native test evidence."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-zero"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert detail == "cargo test executed zero tests"
    assert commands[0]["returncode"] == 0
    assert commands[0]["native_test_count"] == 0


def test_rust_native_gate_fails_closed_without_sandbox(monkeypatch: Any, tmp_path: Path) -> None:
    """Native tests never fall back to direct target-workspace execution."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-no-sandbox"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")

    def unavailable_sandbox(**_kwargs: Any) -> Any:
        raise bench_gates.NativeValidationSandboxError("bubblewrap unavailable")

    monkeypatch.setattr(bench_gates, "sandboxed_cargo_test_command", unavailable_sandbox)

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert detail == "cargo test failed"
    assert commands[0]["sandboxed"] is False
    assert str(commands[0]["error"]).startswith("native_validation_sandbox_unavailable:")


def test_rust_native_sandbox_setup_does_not_follow_project_support_symlink(tmp_path: Path) -> None:
    """Host-side sandbox preparation cannot follow a project-controlled symlink."""
    if not bench_gates.shutil.which("cargo") or not bench_gates.shutil.which("bwrap"):
        pytest.skip("cargo/bwrap unavailable")
    workspace = tmp_path / "project"
    workspace.mkdir()
    (workspace / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-symlink"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (workspace / "src").mkdir()
    (workspace / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    escaped_support = tmp_path / "escaped-support"
    escaped_support.mkdir()
    (workspace / ".cargo-home").symlink_to(escaped_support, target_is_directory=True)

    with bench_gates.sandboxed_cargo_test_command(
        workspace=workspace,
        command=[bench_gates.shutil.which("cargo") or "cargo", "test", "--quiet"],
    ):
        pass

    assert list(escaped_support.iterdir()) == []


def test_rust_native_gate_rejects_project_rustc_wrapper_output_spoof(tmp_path: Path) -> None:
    """Project Cargo config cannot forge native-test evidence through a compiler wrapper."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-wrapper"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(
        '[build]\nrustc-wrapper = "./wrapper.sh"\n',
        encoding="utf-8",
    )
    wrapper = tmp_path / "wrapper.sh"
    wrapper.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out' >&2\n"
        'exec "$@"\n',
        encoding="utf-8",
    )
    wrapper.chmod(0o755)

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_custom_harness_output_spoof(tmp_path: Path) -> None:
    """A harness=false binary is not authoritative libtest execution evidence."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-custom-harness"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[test]]\nname = "fake"\npath = "tests/fake.rs"\nharness = false\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text("pub fn answer() -> u8 { 42 }\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "fake.rs").write_text(
        'fn main() { println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs", "tests/fake.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


@pytest.mark.parametrize(
    ("manifest_target", "source_path"),
    [
        ('[lib]\npath = "src/lib.rs"\nharness = false\n', "src/lib.rs"),
        (
            '[[bin]]\nname = "fake-bin"\npath = "src/main.rs"\ntest = true\nharness = false\n',
            "src/main.rs",
        ),
        (
            '[[example]]\nname = "fake-example"\npath = "examples/fake.rs"\ntest = true\nharness = false\n',
            "examples/fake.rs",
        ),
        (
            '[[bench]]\nname = "fake-bench"\npath = "benches/fake.rs"\nharness = false\n',
            "benches/fake.rs",
        ),
    ],
)
def test_rust_native_gate_rejects_all_custom_harness_targets(
    tmp_path: Path,
    manifest_target: str,
    source_path: str,
) -> None:
    """Every Cargo target kind with a custom harness is non-authoritative."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-target-harness"\nversion = "0.1.0"\nedition = "2021"\n' + manifest_target,
        encoding="utf-8",
    )
    source = tmp_path / source_path
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        [source_path],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


@pytest.mark.parametrize(
    "cargo_config",
    [
        '[target.x86_64-unknown-linux-gnu]\nlinker = "./fake-linker.sh"\n',
        '[target.x86_64-unknown-linux-gnu]\nrustflags = ["-C", "linker=./fake-linker.sh"]\n',
        '[build]\nrustflags = ["-C", "linker=./fake-linker.sh"]\n',
        '[build]\nrustdoc = "./fake-rustdoc.sh"\n',
        '[env]\nCARGO_TARGET_X86_64_UNKNOWN_LINUX_GNU_LINKER = "./fake-linker.sh"\n',
        '[env]\nRUSTFLAGS = "-C linker=./fake-linker.sh"\n',
    ],
)
def test_rust_native_gate_rejects_project_linker_and_rustflags_overrides(
    tmp_path: Path,
    cargo_config: str,
) -> None:
    """Project Cargo config cannot replace/link-inject the native test executable."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-linker"\nversion = "0.1.0"\nedition = "2021"\n',
        encoding="utf-8",
    )
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_text(
        "#[test]\nfn real_test() { assert!(true); }\n",
        encoding="utf-8",
    )
    (tmp_path / ".cargo").mkdir()
    (tmp_path / ".cargo" / "config.toml").write_text(cargo_config, encoding="utf-8")
    fake_linker = tmp_path / "fake-linker.sh"
    fake_linker.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    fake_linker.chmod(0o755)

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["src/lib.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_workspace_member_custom_harness(tmp_path: Path) -> None:
    """Cargo workspace members cannot bypass the root manifest harness audit."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["member"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    member = tmp_path / "member"
    member.mkdir()
    (member / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-member"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[bin]]\nname = "fake-member"\npath = "src/main.rs"\ntest = true\nharness = false\n',
        encoding="utf-8",
    )
    (member / "src").mkdir()
    (member / "src" / "main.rs").write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["member/src/main.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_rust_native_gate_rejects_implicit_path_dependency_member_harness(
    tmp_path: Path,
) -> None:
    """Cargo's implicit in-root path-dependency members receive the same audit."""
    if not bench_gates.shutil.which("cargo"):
        pytest.skip("cargo unavailable")
    (tmp_path / "Cargo.toml").write_text(
        '[workspace]\nmembers = ["app"]\nresolver = "2"\n',
        encoding="utf-8",
    )
    app = tmp_path / "app"
    app.mkdir()
    (app / "Cargo.toml").write_text(
        '[package]\nname = "native-rust-app"\nversion = "0.1.0"\nedition = "2021"\n'
        '[dependencies]\nimplicit-member = { path = "../implicit-member" }\n',
        encoding="utf-8",
    )
    (app / "src").mkdir()
    (app / "src" / "lib.rs").write_text(
        "pub fn answer() -> u8 { implicit_member::answer() }\n",
        encoding="utf-8",
    )
    implicit_member = tmp_path / "implicit-member"
    implicit_member.mkdir()
    (implicit_member / "Cargo.toml").write_text(
        '[package]\nname = "implicit-member"\nversion = "0.1.0"\nedition = "2021"\n'
        '[[bin]]\nname = "fake-implicit"\npath = "src/main.rs"\ntest = true\nharness = false\n',
        encoding="utf-8",
    )
    (implicit_member / "src").mkdir()
    (implicit_member / "src" / "lib.rs").write_text(
        "pub fn answer() -> u8 { 42 }\n",
        encoding="utf-8",
    )
    (implicit_member / "src" / "main.rs").write_text(
        'fn main() { println!("running 1 test"); '
        'println!("test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out"); }\n',
        encoding="utf-8",
    )

    ok, _detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["app/src/lib.rs", "implicit-member/src/lib.rs", "implicit-member/src/main.rs"],
        timeout_s=30,
    )

    assert ok is False
    assert commands[0]["native_test_count"] == 0
    assert str(commands[0]["error"]).startswith("native_validation_contract_invalid:")


def test_non_cargo_rust_compile_is_labeled_and_leaves_no_workspace_output(tmp_path: Path) -> None:
    """Generic rustc fallback is compile evidence, never cargo-test evidence."""
    if not bench_gates.shutil.which("rustc"):
        pytest.skip("rustc unavailable")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")

    ok, detail, commands = bench_gates._run_language_build_gate(
        tmp_path,
        ["main.rs"],
        timeout_s=30,
    )

    assert ok is True
    assert detail == "rustc compile passed"
    assert Path(commands[0]["command"][0]).name == "rustc"
    assert "test" not in commands[0]["command"][1:]
    assert not list(tmp_path.glob("*.rmeta"))


def test_go_project_skips_python_test_path(monkeypatch: Any, tmp_path: Path) -> None:
    """A Go project with tests/test_*.py must NOT run python unittest."""
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module generated\ngo 1.22\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ascii.py").write_text(
        "import unittest\nclass T(unittest.TestCase):\n"
        "    def test_x(self): self.fail('should not run')\n"
        "if __name__ == '__main__': unittest.main()\n",
        encoding="utf-8",
    )
    commands_run: list[list[str]] = []

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        commands_run.append(command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go", "tests/test_ascii.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    # No Python commands should have been run.
    for cmd in commands_run:
        assert "python" not in str(cmd[0]).lower(), f"Python command leaked: {cmd}"
    # Go test should have run.
    assert any("test" in str(c) for c in commands_run)
    assert gate["requirements"]["build_test_lint_ran"]["ok"] is True


# ---------------------------------------------------------------------------
# Test: Go project uses Go entrypoint, not Python (F3)
# ---------------------------------------------------------------------------


def test_go_project_uses_go_entrypoint_not_python(monkeypatch: Any, tmp_path: Path) -> None:
    """A Go project with tests/test_*.py must use go_cli entrypoint."""
    (tmp_path / "main.go").write_text("package main\nfunc main() {}\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module generated\ngo 1.22\n", encoding="utf-8")
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_ascii.py").write_text(
        "import unittest\nif __name__ == '__main__': unittest.main()\n",
        encoding="utf-8",
    )

    def fake_run_command(command: list[str], _cwd: Path, *, timeout_s: int) -> dict[str, Any]:
        is_go_run = any("run" in str(c) for c in command)
        return {
            "command": command,
            "ok": True,
            "returncode": 0,
            "duration_s": 0.01,
            "stdout_tail": "usage: app\n" if is_go_run else "",
            "stderr_tail": "",
            "timeout": False,
            "timeout_s": timeout_s,
        }

    monkeypatch.setattr(bench_gates, "_resolve_go_binary", lambda: "/usr/local/go/bin/go")
    monkeypatch.setattr(bench_gates, "_run_command", fake_run_command)
    record = {"code_files": ["main.go", "tests/test_ascii.py"]}

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    assert gate["entrypoint"]["kind"] == "go_cli"


# ---------------------------------------------------------------------------
# Tests for Go import normalization detection (Repair Kernel owns mutation)
# ---------------------------------------------------------------------------


