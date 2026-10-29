"""A verifier discovery result is not a receipt for an executed command."""

from __future__ import annotations

from typing import Any

import pytest
from polaris.cells.control_plane.run_ledger.public.projection import build_run_ledger_projection


def _project(evidence: dict[str, Any]) -> dict[str, Any]:
    projection: dict[str, Any] = build_run_ledger_projection(
        [
            {
                "event_type": "gate_evaluated",
                "stage": "real_run_gate",
                "gate": {"name": "real_run_gate", "ok": False, "summary": "verification incomplete"},
                "job_token": {
                    "token_id": "token-command-presence",
                    "project_id": "project-command-presence",
                    "capability_audit": {"ok": True, "issues": []},
                    "gate_policy": {"required_evidence_modalities": ["command"]},
                },
                "physical_evidence": evidence,
            }
        ]
    )
    return projection


@pytest.mark.parametrize("commands", [[], None])
def test_no_discovered_or_executed_commands_are_missing_evidence(commands: list[Any] | None) -> None:
    projection = _project(
        {
            "command_count": 0,
            "commands": commands,
            "requirements": {
                "build_test_lint_ran": {
                    "ok": False,
                    "detail": "no build/test/lint command was discovered",
                }
            },
        }
    )
    assert projection["evidence_policy"]["missing_required_modalities"] == ["command"]
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert projection["evidence_modalities"]["command"]["present"] == 0
    assert projection["ok"] is False


@pytest.mark.parametrize("passed", [False, True])
def test_real_executed_command_keeps_its_outcome(passed: bool) -> None:
    projection = _project(
        {
            "command_count": 1,
            "commands": [{"command": "python -m pytest", "ok": passed, "exit_code": 0 if passed else 1}],
            "requirements": {"build_test_lint_ran": {"ok": passed, "detail": "test executed"}},
        }
    )
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ([] if passed else ["command"])
    assert projection["evidence_modalities"]["command"]["present"] == 1


def test_explicit_command_receipt_is_not_erased_by_empty_discovery_projection() -> None:
    projection = _project(
        {
            "modalities": {"command": {"present": True, "ok": False, "detail": "test exit1 receipt"}},
            "commands": [],
            "command_count": 0,
            "requirements": {"build_test_lint_ran": {"ok": False, "detail": "no additional commands"}},
        }
    )
    assert projection["evidence_policy"]["missing_required_modalities"] == []
    assert projection["evidence_policy"]["failed_required_modalities"] == ["command"]


def test_cached_zero_command_presence_cannot_override_actual_empty_inventory() -> None:
    evidence: dict[str, Any] = {
        "modalities": {
            "command": {
                "present": True,
                "ok": False,
                "detail": "no build/test/lint command was discovered",
                "metadata": {"command_count": 0, "entrypoint_kind": ""},
            }
        },
        "commands": [],
        "command_count": 0,
        "requirements": {
            "build_test_lint_ran": {
                "ok": False,
                "detail": "no build/test/lint command was discovered",
            }
        },
    }
    from copy import deepcopy

    original = deepcopy(evidence)
    projection = _project(evidence)
    assert projection["evidence_policy"]["missing_required_modalities"] == ["command"]
    assert projection["evidence_policy"]["failed_required_modalities"] == []
    assert evidence == original
