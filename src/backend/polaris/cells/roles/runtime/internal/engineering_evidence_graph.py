"""Derived acceptance evidence obligations from the engineering journal.

This is not another fact store. Goal clauses and report_review observations stay in
SQLite; this module only projects the evidence still needed for the next QA action.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import extract_goal_clauses

_VISUAL_MARKERS = ("页面", "画布", "浏览器", "视觉", "图形", "旁", "点击", "下载", "布局", "显示")
_ACTION_MARKERS = ("点击", "输入", "参数", "重放", "操作", "干预")


def required_probe(clause: str) -> str:
    """Return the narrowest evidence channel indicated by a requirement clause."""

    if "README" in clause.upper():
        return "read_file:README.md"
    if any(marker in clause for marker in _VISUAL_MARKERS):
        if any(marker in clause for marker in _ACTION_MARKERS):
            return "browser_replay_and_dom_query"
        return "browser_dom_query"
    return "machine_check"


def pending_evidence_obligations(instruction: str, events: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Project unresolved clauses and the next evidence action, never inventing pass."""

    findings: dict[str, dict[str, Any]] = {}
    for event in events:
        if event["kind"] != "tool_observed":
            continue
        payload = event["payload"]
        if payload.get("tool") != "report_review" or payload.get("ok") is not True:
            continue
        try:
            report = json.loads(str(payload.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        for finding in report.get("clause_findings") or []:
            if isinstance(finding, dict) and isinstance(finding.get("clause"), str):
                findings[finding["clause"]] = finding
    pending: list[dict[str, Any]] = []
    for clause in extract_goal_clauses(instruction):
        finding = findings.get(clause)
        status = str(finding.get("status")) if finding else "unknown"
        if status == "pass":
            continue
        pending.append(
            {
                "clause": clause,
                "status": status,
                "evidence": finding.get("evidence") if finding else None,
                "required_probe": required_probe(clause),
            }
        )
    return pending


def evidence_graph_projection(instruction: str, events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Compact QA projection with explicit scope and no acceptance authority."""

    pending = pending_evidence_obligations(instruction, events)
    return {
        "total_clause_count": len(extract_goal_clauses(instruction)),
        "pending": pending,
        "scope": "Derived from original goal clauses and successful report_review observations only; pending is unknown, not failure or acceptance.",
    }
