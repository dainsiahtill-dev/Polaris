from __future__ import annotations

from pathlib import Path

from polaris.cells.factory.pipeline.internal.bench_gates import build_real_run_gate
from polaris.cells.factory.pipeline.internal.run_ledger import (
    RunLedger,
    build_gate_ledger_event,
    build_job_token_from_record,
    build_run_ledger_projection,
    load_run_ledger_projection,
    persist_real_run_gate_ledger,
    summarize_run_ledger_meta,
)


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
