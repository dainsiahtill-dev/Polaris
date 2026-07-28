from __future__ import annotations

from types import SimpleNamespace

from polaris.cells.roles.kernel.internal.llm_caller.context_audit import (
    _add_context_os_audit_findings,
    _add_evidence_coverage_findings,
    _coverage_flags,
)


def test_context_os_audit_failure_is_first_class_final_request_finding() -> None:
    quality: dict[str, object] = {"findings": [], "missing_coverage": []}
    context_os_audit = {
        "ok": False,
        "expected": True,
        "control_plane": {
            "isolated": False,
            "metadata_key_hits": [],
            "content_hits": [
                "chief_engineer_llm_timeout_seconds:",
                "chief_engineer_deadline_decision:",
            ],
        },
    }

    projected = _add_context_os_audit_findings(quality, context_os_audit)

    assert projected["context_needs_review"] is True
    assert projected["findings"] == [
        {
            "code": "context_os_prompt_audit_failed",
            "severity": "error",
            "control_plane_isolated": False,
            "metadata_key_hits": [],
            "content_hits": [
                "chief_engineer_llm_timeout_seconds:",
                "chief_engineer_deadline_decision:",
            ],
        }
    ]


def test_context_os_audit_success_does_not_change_final_request_quality() -> None:
    quality: dict[str, object] = {"findings": [], "missing_coverage": []}

    projected = _add_context_os_audit_findings(
        quality,
        {
            "ok": True,
            "expected": True,
            "control_plane": {
                "isolated": True,
                "metadata_key_hits": [],
                "content_hits": [],
            },
        },
    )

    assert projected["context_needs_review"] is False
    assert projected["findings"] == []


def test_add_evidence_coverage_findings_uses_structured_missing_evidence_slots() -> None:
    quality: dict[str, object] = {"findings": [], "missing_coverage": ["legacy-needle"]}
    evidence_coverage = {
        "pass": False,
        "request_hash": "req-1",
        "evidence_slots": [
            {
                "schema_version": "polaris.final_request_evidence_slot.v1",
                "ref_type": "pm_contract",
                "required": True,
                "missing": True,
            }
        ],
    }

    projected = _add_evidence_coverage_findings(quality, evidence_coverage)

    assert projected["final_request_evidence_coverage_pass"] is False
    assert projected["missing_required_refs"] == ["pm_contract"]
    assert projected["context_needs_review"] is True
    assert projected["findings"] == [
        {
            "code": "missing_required_final_request_evidence",
            "severity": "warning",
            "missing_required_refs": ["pm_contract"],
            "request_hash": "req-1",
        }
    ]


def test_add_evidence_coverage_findings_uses_structured_missing_tool_slots() -> None:
    evidence_coverage = {
        "pass": False,
        "request_hash": "req-2",
        "tool_evidence_slots": [
            {
                "schema_version": "polaris.final_request_tool_slot.v1",
                "tool_name": "write_file",
                "required": True,
                "missing": True,
            }
        ],
    }

    projected = _add_evidence_coverage_findings({"findings": []}, evidence_coverage)

    assert projected["missing_required_tools"] == ["write_file"]
    assert projected["findings"] == [
        {
            "code": "missing_required_final_request_tools",
            "severity": "error",
            "missing_required_tools": ["write_file"],
            "request_hash": "req-2",
        }
    ]


def test_coverage_flags_use_kernelone_structured_context_discovery() -> None:
    ai_request = SimpleNamespace(
        context={
            "nested_context_bundle": {
                "evidence": {
                    "task_contract": {
                        "schema_version": "pm.task_contract.v1",
                        "task_id": "TASK-1",
                        "target_files": ["src/main.ts"],
                    },
                    "blueprint": {
                        "schema_version": "chief_engineer.blueprint.v1",
                        "blueprint_id": "ce-1",
                        "target_files": ["src/main.ts"],
                    },
                    "target_scope": {"target_files": ["src/main.ts"]},
                    "failed_gate": {"failed_required_modalities": ["npm_test"]},
                    "workspace_quality": {"artifact_quality_errors": ["npm test failed"]},
                }
            },
            "messages": [
                {
                    "role": "system",
                    "content": "PM contract and Chief Engineer blueprint prose must not be the coverage source.",
                }
            ],
        }
    )

    coverage = _coverage_flags(ai_request=ai_request)

    assert coverage["has_pm_contract"] is True
    assert coverage["has_chief_engineer_blueprint"] is True
    assert coverage["has_target_files"] is True
    assert coverage["has_failure_feedback"] is True
    assert coverage["has_workspace_quality_evidence"] is True
