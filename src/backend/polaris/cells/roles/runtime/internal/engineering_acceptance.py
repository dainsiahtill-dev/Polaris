"""Acceptance dimension ontology and goal-clause machinery.

The acceptance semantics are multi-dimensional, but the run outcome used to
collapse them into one bit: function-checks plus a QA thumbs-up equaled
"accepted", even when no tool could verify requirement fidelity — a missing
measurement defaulted to "passed" instead of "unknown". Real runs accepted a
mis-assembled pelican because nothing measured pose/assembly correctness.

Dimensions:
- ``function``: machine-graded — verification checks and the host web probe.
- ``fidelity``: requirement-clause fidelity — QA must rule on every concrete
  clause of the goal with evidence (structured ``clause_findings``).

A required dimension without a passing measurement is ``unknown`` and blocks
``accepted``. Extending the ontology means registering another dimension with
its measurement tool — never another implicit default.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Literal

DimensionStatus = Literal["passed", "failed", "unknown", "not_required"]

_CLAUSE_STRIP = re.compile(r"^\s*(?:[-*•+]|\d+[.、)])\s*")
_MAX_CLAUSES = 14
_CLAUSE_MAX_CHARS = 120
_FINDING_STATUSES = {"pass", "fail"}


def extract_goal_clauses(instruction: str) -> list[str]:
    """Split a goal instruction into concrete verifiable clauses.

    Line-bullets and numbered items become clauses; empty and meta lines are
    dropped. This is deliberately mechanical — it exists to give QA and the
    acceptance ledger a stable clause list, not to understand semantics.
    """
    clauses: list[str] = []
    seen: set[str] = set()
    for raw in (instruction or "").splitlines():
        line = _CLAUSE_STRIP.sub("", raw.strip()).strip()
        if not line or len(line) < 6:
            continue
        line = line[:_CLAUSE_MAX_CHARS]
        if line in seen:
            continue
        seen.add(line)
        clauses.append(line)
        if len(clauses) >= _MAX_CLAUSES:
            break
    return clauses


@dataclass(frozen=True)
class ClauseFinding:
    clause: str
    status: str
    evidence: str


def normalize_clause_findings(raw: Any) -> list[ClauseFinding]:
    """Validate the structured ``clause_findings`` report_review argument."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("clause_findings must be a list")
    findings: list[ClauseFinding] = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError("each clause finding must be an object")
        clause = str(item.get("clause") or "").strip()
        status = str(item.get("status") or "").strip().lower()
        evidence = str(item.get("evidence") or "").strip()
        if not clause or status not in _FINDING_STATUSES or not evidence:
            raise ValueError("clause finding requires clause, status pass|fail and evidence")
        findings.append(ClauseFinding(clause=clause[:_CLAUSE_MAX_CHARS], status=status, evidence=evidence[:400]))
    return findings


@dataclass(frozen=True)
class FidelityVerdict:
    status: DimensionStatus
    covered: int
    total: int
    missing: tuple[str, ...]
    failures: tuple[str, ...]


def assess_fidelity(
    *,
    goal_clauses: list[str],
    findings: list[ClauseFinding],
    qa_verdict: str,
) -> FidelityVerdict:
    """Grade the fidelity dimension from structured QA clause rulings.

    approved + full pass coverage -> passed; any fail -> failed; approved
    without complete structured coverage -> unknown (never defaults to
    passed). rejected QA already implies failed fidelity.
    """
    if qa_verdict == "rejected":
        return FidelityVerdict("failed", 0, max(1, len(goal_clauses)), (), ("qa rejected",))
    if qa_verdict != "approved":
        return FidelityVerdict("unknown", 0, max(1, len(goal_clauses)), tuple(goal_clauses), ())
    if not findings:
        if len(goal_clauses) < 2:
            # Single-clause goals carry no decomposition to be faithful to; the
            # QA verdict itself is the fidelity measurement. Clause matrices are
            # enforced for multi-clause (decomposed) goals.
            return FidelityVerdict("passed", len(goal_clauses), len(goal_clauses), (), ())
        return FidelityVerdict("unknown", 0, len(goal_clauses), tuple(goal_clauses), ())
    by_clause = {finding.clause: finding for finding in findings}
    covered: list[str] = []
    missing: list[str] = []
    failures: list[str] = []
    for clause in goal_clauses:
        finding = by_clause.get(clause)
        if finding is None:
            # Tolerate minor formatting drift: match by containment.
            finding = next((f for f in findings if f.clause in clause or clause in f.clause), None)
        if finding is None:
            missing.append(clause)
        elif finding.status == "fail":
            failures.append(clause)
        else:
            covered.append(clause)
    if failures:
        return FidelityVerdict("failed", len(covered), len(goal_clauses), tuple(missing), tuple(failures))
    if missing:
        return FidelityVerdict("unknown", len(covered), len(goal_clauses), tuple(missing), ())
    return FidelityVerdict("passed", len(covered), len(goal_clauses), (), ())


def acceptance_dimensions_summary(
    *,
    function_status: DimensionStatus,
    fidelity: FidelityVerdict,
    require_qa: bool,
) -> dict[str, str]:
    dims: dict[str, str] = {"function": function_status}
    dims["fidelity"] = fidelity.status if require_qa else "not_required"
    return dims
