"""Factory captures physical evidence; platform Run Ledger alone derives modalities."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public.projection import build_run_ledger_projection
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.run_ledger import (
    RunLedger,
    build_gate_ledger_event,
    build_job_token_from_record,
)


@pytest.fixture(autouse=True)
def _bootstrap_workspace(tmp_path: Path) -> None:
    bootstrap_fact_stream_workspace(
        BootstrapFactStreamWorkspaceCommandV1(
            workspace=str(tmp_path),
            streams=fact_stream_bootstrap_streams(),
            maintenance_reason="factory_evidence_owner_component_test",
        )
    )


def _persist(tmp_path: Path, gate: dict[str, Any], required: list[str]) -> tuple[dict[str, Any], dict[str, Any]]:
    token = build_job_token_from_record(
        {
            "id": "component-project",
            "target_files": ["src/main.py"],
            "scope_paths": ["src/main.py"],
            "required_evidence_modalities": required,
        },
        run_id="component-run",
        project_id="component-project",
        stage="real_run_gate",
    )
    before = deepcopy(gate)
    event = build_gate_ledger_event(token, gate)
    ledger = RunLedger(tmp_path, run_id="component-run")
    appended = ledger.append_event(event)
    stored = ledger.read_events()
    assert gate == before
    assert len(stored) == 1
    assert stored[0]["content_id"] == event["content_id"]
    assert stored[0]["append_id"]
    assert Path(appended["ledger_path"]).is_file()
    assert stored[0]["job_token"] == token.to_dict()
    return stored[0], build_run_ledger_projection(stored)


def test_zero_command_discovery_is_missing_after_immutable_append(tmp_path: Path) -> None:
    event, projection = _persist(
        tmp_path,
        {
            "ok": False,
            "summary": "No commands were discovered",
            "commands": [],
            "command_count_total": 0,
            "requirements": {
                "build_test_lint_ran": {"ok": False, "detail": "no build/test/lint command was discovered"}
            },
            "entrypoint": {"kind": "", "ok": False},
        },
        ["command"],
    )

    assert event["physical_evidence"]["command_count"] == 0
    assert event["physical_evidence"]["modalities"]["command"]["present"] is False
    assert "command" in projection["evidence_policy"]["missing_required_modalities"]
    assert "command" not in projection["evidence_policy"]["failed_required_modalities"]
    assert projection["ok"] is False


def test_failed_executed_command_stays_failed_after_immutable_append(tmp_path: Path) -> None:
    _, projection = _persist(
        tmp_path,
        {
            "ok": False,
            "commands": [{"command": ["python", "-m", "pytest"], "ok": False, "exit_code": 1}],
            "command_count_total": 1,
            "requirements": {"build_test_lint_ran": {"ok": False, "detail": "test exit1"}},
        },
        ["command"],
    )

    assert "command" not in projection["evidence_policy"]["missing_required_modalities"]
    assert "command" in projection["evidence_policy"]["failed_required_modalities"]
    assert projection["evidence_modalities"]["command"]["present"] == 1


def test_verifier_browser_and_judgement_source_fields_survive_append(tmp_path: Path) -> None:
    gate: dict[str, Any] = {
        "ok": True,
        "commands": [],
        "verifier_results": [
            {"name": "behavior-check", "modality": "domain", "ok": True, "exit_code": 0, "hash": "c" * 64}
        ],
        "entrypoint": {
            "kind": "web_playwright",
            "ok": True,
            "url": "http://component.invalid/",
            "has_canvas": True,
            "canvas_non_blank": True,
            "screenshot_hash": "d" * 64,
        },
        "llm_judge": {"ok": True, "provider": "component", "model": "readonly-judge", "confidence": 0.9},
        "opaque_provider_payload": {"unrelated": "must not be copied into physical evidence"},
    }
    event, projection = _persist(tmp_path, gate, ["verifier", "domain", "browser", "visual", "llm_judge"])

    assert event["physical_evidence"]["verifier_results"] == gate["verifier_results"]
    assert event["physical_evidence"]["llm_judge"] == gate["llm_judge"]
    assert event["physical_evidence"]["entrypoint"] == gate["entrypoint"]
    assert "opaque_provider_payload" not in event["physical_evidence"]
    for modality in ("verifier", "domain", "browser", "visual", "llm_judge"):
        assert modality not in projection["evidence_policy"]["missing_required_modalities"]
        assert modality not in projection["evidence_policy"]["failed_required_modalities"]
        assert projection["evidence_modalities"][modality]["present"] == 1
        assert projection["evidence_modalities"][modality]["ok"] == 1


def test_environment_and_repair_attachments_are_derived_before_append(tmp_path: Path) -> None:
    gate: dict[str, Any] = {
        "ok": True,
        "repair_receipts": [
            {
                "receipt_id": "repair-1",
                "source_tool": "component-repair",
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
                "plan_id": "environment-1",
                "ecosystem": "python",
                "package_manager": "pip",
                "command": ["python", "-m", "pip", "install", "-e", "."],
                "exit_code": 0,
                "status": "succeeded",
                "manifest": "pyproject.toml",
            }
        ],
        "tool_receipts": [{"receipt_id": "tool-1", "tool": "execute_command", "ok": False, "exit_code": 1}],
    }
    event, projection = _persist(tmp_path, gate, ["repair", "environment_prep"])

    for field in ("repair_receipts", "receipt_authority_policy", "environment_prep_receipts", "tool_receipts"):
        assert event["physical_evidence"][field] == gate[field]
    assert event["physical_evidence"]["modalities"]["repair"]["present"] is True
    assert event["physical_evidence"]["modalities"]["environment_prep"]["present"] is True
    assert projection["evidence_modalities"]["repair"]["present"] == 1
    assert projection["evidence_modalities"]["environment_prep"]["present"] == 1


def test_factory_modalities_match_the_public_owner_after_all_attachments(tmp_path: Path) -> None:
    # The public owner is the independently consuming authority boundary, not a local reimplementation.
    from polaris.cells.control_plane.run_ledger.public.projection import derive_run_ledger_evidence_modalities

    gate: dict[str, Any] = {
        "ok": False,
        "commands": [],
        "command_count_total": 0,
        "requirements": {"build_test_lint_ran": {"ok": False}},
        "custom_verifiers": [{"name": "custom", "modality": "domain", "ok": False, "exit_code": 1}],
        "qa_visual_judgement": {"passed": False, "reason": "negative judgement"},
    }
    event, projection = _persist(tmp_path, gate, ["command", "domain"])
    physical = event["physical_evidence"]
    raw_physical = {key: deepcopy(value) for key, value in physical.items() if key != "modalities"}

    assert physical["modalities"] == derive_run_ledger_evidence_modalities(raw_physical)
    assert "command" in projection["evidence_policy"]["missing_required_modalities"]
    assert "domain" in projection["evidence_policy"]["failed_required_modalities"]
