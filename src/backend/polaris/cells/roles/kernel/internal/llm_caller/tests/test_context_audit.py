from __future__ import annotations

from polaris.cells.roles.kernel.internal.llm_caller.context_audit import _add_evidence_coverage_findings


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
