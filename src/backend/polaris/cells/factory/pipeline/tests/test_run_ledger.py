from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import polaris.cells.factory.pipeline.internal.run_ledger as run_ledger_module
import pytest
from polaris.cells.control_plane.run_ledger.public.projection import (
    summarize_run_ledger_projection as summarize_platform_run_ledger_projection,
)
from polaris.cells.control_plane.verifier_policy.public import (
    UpdateVerifierPolicyCommandV1,
    update_verifier_policy,
)
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.bench_gates import build_real_run_gate
from polaris.cells.factory.pipeline.internal.run_ledger import (
    RunLedger,
    _missing_required_modalities,
    build_gate_ledger_event,
    build_job_token_from_record,
    build_run_ledger_projection,
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_meta,
    summarize_run_ledger_projection,
)


@pytest.fixture(autouse=True)
def _bootstrap_real_fact_stream_workspace(request: pytest.FixtureRequest) -> None:
    if "tmp_path" not in request.fixturenames:
        return
    workspace = Path(request.getfixturevalue("tmp_path")).resolve()
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(workspace),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_run_ledger_test_bootstrap",
        )
    )


def test_legacy_missing_required_modalities_does_not_treat_failed_present_evidence_as_missing() -> None:
    missing = _missing_required_modalities(
        ["command", "browser"],
        {
            "command": {"present": True, "ok": False, "failed": 1},
            "browser": {"present": False, "ok": False},
        },
    )

    assert missing == ["browser"]


def test_run_ledger_command_modality_accepts_passed_field() -> None:
    token = build_job_token_from_record(
        {
            "id": "L1-command",
            "target_files": ["src/index.js"],
            "scope_paths": ["src/index.js"],
            "required_evidence_modalities": ["command"],
        },
        run_id="run-command",
        project_id="L1-command",
        stage="workspace_validation",
    )
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "workspace validation passed",
            "command_count_total": 1,
            "commands": [
                {
                    "command": ["npm", "test"],
                    "passed": True,
                    "exit_code": 0,
                }
            ],
            "requirements": {"workspace_validation": {"ok": True}},
        },
        gate_name="workspace_validation",
    )

    projection = build_run_ledger_projection([event])

    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["evidence_modalities"]["command"]["ok"] == 1


def test_job_token_is_stable_and_carries_canonical_paths() -> None:
    record = {
        "id": "L1-01",
        "target_files": ["src/index.ts", "src/index.ts", "/README.md"],
        "scope_paths": ["src/index.ts", "README.md"],
        "chain_results": {"contract_goal": "Build a working TypeScript app"},
        "chain": {"run_id": "factory_123", "audit_bundle": {"blueprint_id": "bp-1"}},
        "parent_token_id": "parent-token-1",
        "factory_workspace_quality_repair": {
            "run_id": "factory_123",
            "changed_files": ["src/index.ts"],
            "target_files": ["src/index.ts", "README.md"],
        },
    }

    first = build_job_token_from_record(record, run_id="bench_a", project_id="L1-01")
    second = build_job_token_from_record(record, run_id="bench_a", project_id="L1-01")

    assert first.token_id == second.token_id
    assert first.run_id == "bench_a"
    assert first.factory_run_id == "factory_123"
    assert first.project_id == "L1-01"
    assert first.target_files == ["src/index.ts", "README.md"]
    assert first.allowed_paths == ["src/index.ts", "README.md"]
    assert first.capability_audit["ok"] is True
    assert first.capability_audit["contract_sources"] == ["contract_goal", "target_files"]
    assert first.capability_audit["blueprint_sources"] == ["blueprint_id"]
    assert first.parent_token_id == "parent-token-1"
    assert first.source == "control_plane.job_token"
    assert first.repair_lineage == [
        {
            "changed_files": ["src/index.ts"],
            "run_id": "factory_123",
            "source": "factory_workspace_quality_repair",
            "target_files": ["src/index.ts", "README.md"],
        }
    ]
    assert first.contract_hash
    assert first.blueprint_hash


def test_job_token_accepts_blueprint_artifacts_as_ce_source() -> None:
    record = {
        "id": "L1-02",
        "target_files": ["src/index.js", "README.md"],
        "chain_results": {"contract_goal": "Build a runnable JavaScript app"},
        "artifacts": {
            "blueprint": [
                "rt:blueprints/ce_TASK-1.json",
                "ws:.polaris/blueprints/ce_TASK-1.json",
            ]
        },
    }

    token = build_job_token_from_record(record, run_id="bench_a", project_id="L1-02")

    assert token.capability_audit["ok"] is True
    assert token.capability_audit["blueprint_sources"] == ["artifacts.blueprint"]
    assert token.blueprint_hash


def test_run_ledger_appends_gate_evidence(tmp_path: Path) -> None:
    token = build_job_token_from_record(
        {
            "code_files": ["main.py"],
            "chain_results": {"contract_goal": "run cli"},
            "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        },
        run_id="bench_1",
        project_id="P1",
    )
    gate = {
        "ok": False,
        "summary": "real run gate failed: entrypoint_smoke",
        "requirements": {
            "entrypoint_smoke": {"ok": False, "detail": "missing entrypoint"},
            "artifact_landed": {"ok": True, "detail": "1 file"},
        },
        "entrypoint": {"kind": "python_cli", "ok": False},
        "commands": [{"phase": "build_test_lint", "ok": True}],
        "command_count_total": 17,
        "commands_truncated": True,
    }
    event = build_gate_ledger_event(token, gate)

    persisted = RunLedger(tmp_path, run_id="bench_1").append_event(event)
    events = RunLedger(tmp_path, run_id="bench_1").read_events()

    assert Path(persisted["ledger_path"]).is_file()
    assert Path(persisted["ledger_path"]).parent == tmp_path / "runtime" / "control_plane" / "ledger"
    assert len(events) == 1
    assert events[0]["event_type"] == "gate_evaluated"
    assert events[0]["content_id"] == event["content_id"]
    assert events[0]["event_id"] == event["content_id"]
    assert events[0]["append_id"]
    assert events[0]["gate"]["failing_requirements"] == ["entrypoint_smoke"]
    assert events[0]["physical_evidence"]["command_count"] == 17
    assert events[0]["physical_evidence"]["sampled_command_count"] == 1
    assert events[0]["physical_evidence"]["commands_truncated"] is True
    assert events[0]["job_token"]["token_id"] == token.token_id
    projection = build_run_ledger_projection(events)
    assert projection["gates"][0]["evidence_modalities"]["command"]["ok"] is False
    assert projection["evidence_modalities"]["command"]["ok"] == 0
    assert projection["evidence_modalities"]["command"]["failed"] == 1
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]


def test_factory_gate_ledger_projects_repair_and_environment_prep_modalities() -> None:
    token = build_job_token_from_record(
        {
            "code_files": ["package.json"],
            "chain_results": {"contract_goal": "repair package manifest"},
            "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
            "required_evidence_modalities": ["repair", "environment_prep"],
        },
        run_id="bench_1",
        project_id="P1",
        stage="director_repair",
    )
    gate = {
        "ok": True,
        "summary": "repair and env prep evidence projected",
        "repair_receipts": [
            {
                "receipt_id": "repair-1",
                "source_tool": "deterministic_runtime_dependency_repair",
                "status": "applied",
                "authoritative": True,
                "evidence_status": "resolved_evidence",
            }
        ],
        "receipt_authority_policy": {
            "schema_version": "director.repair_receipt_authority_policy.v1",
            "authoritative_success": True,
            "receipt_count": 1,
            "missing_evidence_receipt_count": 0,
            "failed_evidence_receipt_count": 0,
            "non_authoritative_receipt_count": 0,
        },
        "environment_prep_receipts": [
            {
                "schema_version": "director.environment_prep_receipt.v1",
                "plan_id": "env-prep-1",
                "ecosystem": "node",
                "package_manager": "npm",
                "command": ["npm", "install", "--ignore-scripts", "--no-audit", "--no-fund"],
                "exit_code": 0,
                "status": "succeeded",
                "manifest": "package.json",
            }
        ],
    }

    event = build_gate_ledger_event(token, gate, gate_name="director_repair_gate")
    projection = build_run_ledger_projection([event])

    assert event["physical_evidence"]["repair_receipts"][0]["receipt_id"] == "repair-1"
    assert event["physical_evidence"]["environment_prep_receipts"][0]["plan_id"] == "env-prep-1"
    assert projection["ok"] is True
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["gates"][0]["evidence_modalities"]["repair"]["ok"] is True
    assert projection["gates"][0]["evidence_modalities"]["environment_prep"]["ok"] is True


def test_real_run_gate_persists_ledger_event_from_single_token_source(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "code_files": ["main.py"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run cli"},
    }

    gate = build_real_run_gate(tmp_path, record, timeout_s=10)
    ledger_meta = persist_real_run_gate_ledger(
        tmp_path,
        record,
        gate,
        run_id="bench_1",
        project_id="P1",
    )

    assert "job_token" not in gate
    assert "ledger_event" not in gate
    assert ledger_meta["event_id"]
    assert ledger_meta["content_id"] == ledger_meta["event_id"]
    assert ledger_meta["append_id"]
    assert ledger_meta["job_token_id"] == ledger_meta["job_token"]["token_id"]
    assert ledger_meta["ledger_event"]["event_id"] == ledger_meta["event_id"]
    assert ledger_meta["ledger_event"]["physical_evidence"]["entrypoint"]["kind"] == "python_cli"
    assert Path(ledger_meta["ledger_path"]).is_file()


def test_non_terminal_gate_can_be_persisted_with_job_token(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": [],
        "target_files": ["main.py"],
        "scope_paths": ["main.py"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run cli"},
    }
    gate = {
        "ok": False,
        "summary": "chain is not terminal: workspace_switch_failed",
        "requirements": {
            "chain_terminal": {
                "ok": False,
                "detail": "phase=workspace_switch status=workspace_switch_failed",
            }
        },
        "entrypoint": {},
        "commands": [],
        "command_count_total": 0,
        "commands_truncated": False,
    }

    ledger_meta = persist_real_run_gate_ledger(
        tmp_path,
        record,
        gate,
        run_id="bench_1",
        project_id="P1",
        stage="workspace_switch_failed",
        gate_name="chain_non_terminal",
    )

    assert ledger_meta["gate"] == "chain_non_terminal"
    assert ledger_meta["stage"] == "workspace_switch_failed"
    assert ledger_meta["job_token"]["factory_run_id"] == "bench_1"
    assert ledger_meta["ledger_event"]["gate"]["name"] == "chain_non_terminal"
    assert ledger_meta["ledger_event"]["gate"]["failing_requirements"] == ["chain_terminal"]


def test_run_ledger_summary_fails_closed_on_capability_drift(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/main.py"],
        "scope_paths": ["src"],
        "factory_workspace_quality_repair": {
            "run_id": "bench_1",
            "target_files": ["outside/main.py"],
            "changed_files": ["outside/main.py"],
        },
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run cli"},
    }
    gate = {
        "ok": False,
        "summary": "real run gate failed: build_test_lint_ran",
        "requirements": {"build_test_lint_ran": {"ok": False}},
        "entrypoint": {},
        "commands": [],
    }

    ledger_meta = persist_real_run_gate_ledger(
        tmp_path,
        record,
        gate,
        run_id="bench_1",
        project_id="P1",
    )
    summary = summarize_run_ledger_meta(ledger_meta)

    assert summary["ok"] is False
    assert "repair_targets_outside_allowed_paths" in summary["missing"]
    assert summary["capability_audit"]["drift"]["repair_targets_outside_allowed_paths"] == ["outside/main.py"]


def test_run_ledger_projection_is_canonical_read_model(tmp_path: Path) -> None:
    (tmp_path / "main.py").write_text("print('hello')\n", encoding="utf-8")
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["main.py"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run cli"},
    }
    gate = build_real_run_gate(tmp_path, record, timeout_s=10)

    ledger_meta = persist_real_run_gate_ledger(
        tmp_path,
        record,
        gate,
        run_id="bench_1",
        project_id="P1",
    )
    events = RunLedger(tmp_path, run_id="bench_1").read_events()
    projection = build_run_ledger_projection(events)
    loaded_projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection == loaded_projection
    assert projection["source"] == "run_ledger"
    assert projection["event_count"] == 1
    assert projection["gate_count"] == 1
    assert projection["gates"][0]["name"] == "real_run_gate"
    assert projection["gates"][0]["content_id"] == ledger_meta["content_id"]
    assert projection["capability"]["ok"] is True
    assert projection["capability"]["latest_token_id"] == ledger_meta["job_token_id"]
    assert projection["physical_evidence"]["command_count"] >= projection["physical_evidence"]["sampled_command_count"]
    assert projection["evidence_policy"]["ok"] is True
    assert projection["evidence_policy"]["enabled_modalities"] == []
    assert projection["evidence_policy"]["required_modalities"] == ["code", "command"]


def test_load_run_ledger_projection_forwards_factory_tree_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def read_projection(query: object) -> SimpleNamespace:
        captured["query"] = query
        return SimpleNamespace(
            projection={
                "run_projection": {
                    "source": "run_ledger",
                    "event_count": 0,
                }
            }
        )

    monkeypatch.setattr(run_ledger_module, "read_run_ledger_projection", read_projection)

    projection = run_ledger_module.load_run_ledger_projection(
        tmp_path,
        run_id="bench-parent",
        factory_run_id="factory-child-tree",
        project_id="L1-01",
    )

    query = captured["query"]
    assert query.run_id == "bench-parent"
    assert query.factory_run_id == "factory-child-tree"
    assert query.project_id == "L1-01"
    assert projection == {"source": "run_ledger", "event_count": 0}


def test_factory_projection_summary_delegates_to_control_plane_public_contract(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["main.py"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run cli"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1")
    event = build_gate_ledger_event(
        token,
        {
            "ok": False,
            "summary": "real run gate failed: command",
            "requirements": {"build_test_lint_ran": {"ok": False}},
            "commands": [{"ok": False, "tool": "python -m pytest"}],
            "command_count_total": 1,
        },
    )
    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert summarize_run_ledger_projection(projection) == summarize_platform_run_ledger_projection(projection)
    assert summarize_run_ledger_projection({"source": "other"}) == summarize_platform_run_ledger_projection(
        {"source": "other"}
    )


def test_run_ledger_projection_tracks_user_verifier_modalities(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/physics.ts"],
        "scope_paths": ["src/physics.ts", "tests/physics.test.ts"],
        "required_evidence_modalities": ["physics"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "verify particle physics"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="qa_verifier")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "domain verifier passed",
            "user_verifiers": [
                {
                    "id": "physics-energy-conservation",
                    "name": "Energy conservation",
                    "modality": "physics",
                    "script": "tests/physics.test.ts",
                    "ok": True,
                    "hash": "sha256:physics-evidence",
                    "metric": "energy_delta",
                    "threshold": 0.01,
                    "detail": "energy drift within tolerance",
                }
            ],
        },
        gate_name="qa_domain_verifier",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is True
    assert projection["evidence_policy"] == {
        "ok": True,
        "integrity_ok": True,
        "outcome_ok": True,
        "enabled_modalities": [],
        "required_modalities": ["physics"],
        "missing_required_modalities": [],
        "failed_required_modalities": [],
    }
    assert projection["evidence_modalities"]["verifier"]["ok"] == 1
    assert projection["evidence_modalities"]["physics"]["ok"] == 1
    assert projection["gates"][0]["evidence_modalities"]["physics"]["metadata"]["script"] == "tests/physics.test.ts"


def test_run_ledger_projection_fails_closed_when_required_verifier_evidence_is_missing(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/physics.ts"],
        "scope_paths": ["src/physics.ts"],
        "required_evidence_modalities": ["physics"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "verify particle physics"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="qa_verifier")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "domain verifier claimed pass without evidence",
        },
        gate_name="qa_domain_verifier",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is False
    assert projection["integrity_ok"] is False
    assert projection["evidence_policy"]["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == ["physics"]


def test_run_ledger_projection_fails_closed_when_required_tool_receipt_is_missing(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/app.ts"],
        "scope_paths": ["src/app.ts"],
        "required_evidence_modalities": ["tool_receipt"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "write app source"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="director_mutation")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "mutation claimed without tool receipt",
        },
        gate_name="director_mutation",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is False
    assert projection["integrity_ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == ["tool_receipt"]


def test_run_ledger_projection_accepts_token_scoped_tool_receipt(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/app.ts"],
        "scope_paths": ["src/app.ts"],
        "required_evidence_modalities": ["tool_receipt"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "write app source"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="director_mutation")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "mutation wrote file through tool runtime",
            "commands": [
                {
                    "ok": True,
                    "tool": "write_file",
                    "effect_receipt": {
                        "operation": "write_file",
                        "file": "src/app.ts",
                        "capability_token": {
                            "token_id": token.token_id,
                            "contract_hash": token.contract_hash,
                            "blueprint_hash": token.blueprint_hash,
                        },
                    },
                }
            ],
        },
        gate_name="director_mutation",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is True
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_modalities"]["tool_receipt"]["ok"] == 1
    gate_receipt = projection["gates"][0]["evidence_modalities"]["tool_receipt"]
    assert gate_receipt["metadata"]["receipt_count"] == 1
    assert gate_receipt["metadata"]["expected_token_id"] == token.token_id


def test_run_ledger_event_preserves_top_level_batch_receipt(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/app.ts"],
        "scope_paths": ["src/app.ts"],
        "required_evidence_modalities": ["tool_receipt"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "write app source"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="director_mutation")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "mutation carried role-runtime batch receipt",
            "batch_receipt": {
                "ok": True,
                "results": [
                    {
                        "tool": "write_file",
                        "success": True,
                        "effect_receipt": {
                            "operation": "write_file",
                            "file": "src/app.ts",
                            "capability_token": {
                                "token_id": token.token_id,
                                "contract_hash": token.contract_hash,
                                "blueprint_hash": token.blueprint_hash,
                            },
                        },
                    }
                ],
            },
        },
        gate_name="director_mutation",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    physical_evidence = projection["gates"][0]["evidence_modalities"]["tool_receipt"]
    assert event["physical_evidence"]["batch_receipt"]["ok"] is True
    assert projection["ok"] is True
    assert physical_evidence["ok"] is True
    assert physical_evidence["metadata"]["operations"] == ["write_file"]


def test_run_ledger_projection_rejects_mismatched_tool_receipt_token(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["src/app.ts"],
        "scope_paths": ["src/app.ts"],
        "required_evidence_modalities": ["tool_receipt"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "write app source"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="director_mutation")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "mutation wrote file with mismatched receipt",
            "commands": [
                {
                    "ok": True,
                    "tool": "write_file",
                    "effect_receipt": {
                        "operation": "write_file",
                        "file": "src/app.ts",
                        "capability_token": {"token_id": "other-token"},
                    },
                }
            ],
        },
        gate_name="director_mutation",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["tool_receipt"]
    assert projection["gates"][0]["failed_required_evidence_modalities"] == ["tool_receipt"]
    gate_receipt = projection["gates"][0]["evidence_modalities"]["tool_receipt"]
    assert gate_receipt["ok"] is False
    assert gate_receipt["metadata"]["invalid"] == ["receipt[0]:token_mismatch"]


def test_run_ledger_policy_does_not_require_browser_unless_explicit(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["index.html"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run static web app"},
    }

    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1")

    assert token.gate_policy["required_evidence_modalities"] == ["code", "command"]


def test_run_ledger_policy_tracks_enabled_browser_without_requiring_it(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["index.html"],
        "verifier_policy": {
            "browser_enabled": True,
            "visual_enabled": True,
            "enabled_evidence_modalities": ["browser", "visual"],
            "required_evidence_modalities": [],
        },
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run static web app"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1", stage="qa_verifier")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "non-browser verifier passed",
        },
        gate_name="qa_verifier",
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert token.gate_policy["enabled_evidence_modalities"] == ["browser", "visual"]
    assert token.gate_policy["required_evidence_modalities"] == []
    assert projection["ok"] is True
    assert projection["evidence_policy"] == {
        "ok": True,
        "integrity_ok": True,
        "outcome_ok": True,
        "enabled_modalities": ["browser", "visual"],
        "required_modalities": [],
        "missing_required_modalities": [],
        "failed_required_modalities": [],
    }
    assert projection["gates"][0]["enabled_evidence_modalities"] == ["browser", "visual"]


def test_run_ledger_projection_tracks_browser_and_visual_entrypoint_evidence(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["index.html"],
        "target_files": ["index.html"],
        "scope_paths": ["index.html"],
        "requires_browser_evidence": True,
        "verifier_policy": {
            "browser_enabled": True,
            "visual_enabled": True,
            "enabled_evidence_modalities": ["browser", "visual"],
            "required_evidence_modalities": ["browser", "visual"],
        },
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run static web app"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "browser smoke and visual evidence passed",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "index.html"},
                "source_files_present": {"ok": True, "detail": "index.html"},
                "build_test_lint_ran": {"ok": True, "detail": "browser smoke command"},
            },
            "commands": [{"ok": True, "tool": "playwright-smoke"}],
            "command_count_total": 1,
            "entrypoint": {
                "kind": "web_playwright",
                "ok": True,
                "detail": "canvas rendered",
                "url": "http://127.0.0.1:8123/",
                "http_status": 200,
                "has_canvas": True,
                "canvas_non_blank": True,
                "screenshot_hash": "sha256:visual-proof",
            },
        },
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is True
    assert projection["evidence_policy"] == {
        "ok": True,
        "integrity_ok": True,
        "outcome_ok": True,
        "enabled_modalities": ["browser", "visual"],
        "required_modalities": ["code", "command", "browser", "visual"],
        "missing_required_modalities": [],
        "failed_required_modalities": [],
    }
    assert projection["evidence_modalities"]["browser"]["ok"] == 1
    assert projection["evidence_modalities"]["visual"]["ok"] == 1
    assert projection["gates"][0]["evidence_modalities"]["browser"]["metadata"]["url"] == "http://127.0.0.1:8123/"
    assert (
        projection["gates"][0]["evidence_modalities"]["visual"]["metadata"]["screenshot_hash"] == "sha256:visual-proof"
    )


def test_run_ledger_policy_can_explicitly_require_browser_evidence(tmp_path: Path) -> None:
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "code_files": ["index.html"],
        "requires_browser_evidence": True,
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run static web app"},
    }
    token = build_job_token_from_record(record, run_id="bench_1", project_id="P1")
    event = build_gate_ledger_event(
        token,
        {
            "ok": True,
            "summary": "claimed pass without browser smoke",
            "requirements": {
                "artifact_landed": {"ok": True, "detail": "1 file"},
                "build_test_lint_ran": {"ok": True, "detail": "static check"},
            },
            "commands": [{"ok": True, "tool": "static-check"}],
            "command_count_total": 1,
        },
    )

    RunLedger(tmp_path, run_id="bench_1").append_event(event)
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert projection["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == ["browser"]


def test_persist_real_run_gate_ledger_consumes_platform_verifier_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_BROWSER_VERIFIER_AVAILABLE", "1")

    update_verifier_policy(
        UpdateVerifierPolicyCommandV1(
            workspace=str(tmp_path),
            browser_enabled=True,
            required_modalities=("browser",),
        )
    )
    record = {
        "id": "P1",
        "run_id": "bench_1",
        "project_id": "P1",
        "factory_run_id": "bench_1",
        "target_files": ["index.html"],
        "scope_paths": ["index.html"],
        "chain": {"audit_bundle": {"blueprint_id": "bp-1"}},
        "chain_results": {"contract_goal": "run static web app"},
    }
    gate = {
        "ok": True,
        "summary": "code and command evidence only",
        "requirements": {
            "artifact_landed": {"ok": True, "detail": "index.html"},
            "source_files_present": {"ok": True, "detail": "index.html"},
            "build_test_lint_ran": {"ok": True, "detail": "npm test"},
        },
        "commands": [{"ok": True, "tool": "npm test"}],
        "command_count_total": 1,
    }

    ledger_meta = persist_real_run_gate_ledger(tmp_path, record, gate, run_id="bench_1", project_id="P1")
    projection = load_run_ledger_projection(tmp_path, run_id="bench_1")

    assert ledger_meta["job_token"]["gate_policy"]["enabled_evidence_modalities"] == ["browser"]
    assert ledger_meta["job_token"]["gate_policy"]["required_evidence_modalities"] == ["code", "command", "browser"]
    assert projection["ok"] is False
    assert projection["evidence_policy"]["missing_required_modalities"] == ["browser"]
