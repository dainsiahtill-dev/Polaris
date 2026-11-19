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
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal

DimensionStatus = Literal["passed", "failed", "unknown", "not_required"]

_CLAUSE_STRIP = re.compile(r"^\s*(?:[-*•+]|\d+[.、)])\s*")
_TABLE_SEPARATOR = re.compile(r"^\|[\s\-|:]+\|$")
# The frozen catalog's real requirement clauses run 8..25 (p50 17, max 25 across
# all 120 projects). A 14-clause cap silently truncated 85 of them, making the
# fidelity dimension permanently unknown; 32 covers the catalog with headroom.
_MAX_CLAUSES = 32
_CLAUSE_MAX_CHARS = 120
_FINDING_STATUSES = {"pass", "fail"}

# The internal acceptance adapter prepends a platform notice and appends platform
# delivery contracts around the frozen user requirements. Fidelity is about the
# user's goal, so the clause source starts after this marker and, for markdown
# requirement docs, uses the opening goal/use-experience section rather than the
# platform contract sections that follow it.
FROZEN_REQUIREMENTS_MARKER = "--- Frozen original requirements (verbatim) ---"


def _goal_clause_source(instruction: str) -> str:
    text = instruction or ""
    if FROZEN_REQUIREMENTS_MARKER in text:
        text = text.split(FROZEN_REQUIREMENTS_MARKER, 1)[1]
    lines = text.splitlines()
    start = 0
    for index, line in enumerate(lines):
        if line.strip().startswith("## "):
            start = index + 1
            break
    else:
        return text
    end = len(lines)
    for index in range(start, len(lines)):
        stripped = lines[index].strip()
        if stripped.startswith("## ") or stripped.startswith("# "):
            end = index
            break
    return "\n".join(lines[start:end])


def _goal_clause_candidates(instruction: str) -> list[str]:
    clauses: list[str] = []
    seen: set[str] = set()
    for raw in _goal_clause_source(instruction).splitlines():
        line = _CLAUSE_STRIP.sub("", raw.strip()).strip()
        if not line or len(line) < 6:
            continue
        if line.startswith("#") or _TABLE_SEPARATOR.match(line):
            continue
        line = line[:_CLAUSE_MAX_CHARS]
        if line in seen:
            continue
        seen.add(line)
        clauses.append(line)
    return clauses


def extract_goal_clauses(instruction: str) -> list[str]:
    """Split a goal instruction into concrete verifiable clauses.

    Line-bullets and numbered items become clauses; empty and meta lines are
    dropped. This is deliberately mechanical — it exists to give QA and the
    acceptance ledger a stable clause list, not to understand semantics.
    """
    return _goal_clause_candidates(instruction)[:_MAX_CLAUSES]


def has_truncated_goal_clauses(instruction: str) -> bool:
    """True when the mechanical extractor capped the clause list.

    A capped list cannot certify fidelity for the whole goal, so the ledger must
    treat the fidelity dimension as unknown instead of passing on a prefix.
    """
    return len(_goal_clause_candidates(instruction)) > _MAX_CLAUSES


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


def clause_coverage_gap(goal_clauses: Sequence[str], findings: list[ClauseFinding]) -> list[str]:
    """Return the goal clauses that no finding covers.

    Matching is deliberately forgiving about formatting drift (a finding covers
    a clause when either text contains the other), but it never invents coverage:
    a clause with no finding stays in the gap. Both the dispatch-boundary gate
    and the acceptance ledger call this one helper, so the corrective message
    and the grade cannot drift apart.
    """
    by_clause = {finding.clause: finding for finding in findings}
    gap: list[str] = []
    for clause in goal_clauses:
        finding = by_clause.get(clause)
        if finding is None:
            finding = next((f for f in findings if f.clause in clause or clause in f.clause), None)
        if finding is None:
            gap.append(clause)
    return gap


_TRUNCATION_NOTE = "[目标条款多于验收清单上限，无法证明全部条款保真]"


def assess_fidelity(
    *,
    goal_clauses: list[str],
    findings: list[ClauseFinding],
    qa_verdict: str,
    goal_truncated: bool = False,
) -> FidelityVerdict:
    """Grade the fidelity dimension from structured QA clause rulings.

    approved + full pass coverage -> passed; any fail -> failed; approved
    without complete structured coverage -> unknown (never defaults to
    passed). rejected QA already implies failed fidelity. A goal whose clause
    list was capped cannot be certified for the whole goal, so it stays unknown
    even when the visible prefix passes.
    """
    if qa_verdict == "rejected":
        return FidelityVerdict("failed", 0, max(1, len(goal_clauses)), (), ("qa rejected",))
    if qa_verdict != "approved":
        return FidelityVerdict("unknown", 0, max(1, len(goal_clauses)), tuple(goal_clauses), ())
    if not findings and len(goal_clauses) < 2 and not goal_truncated:
        # Single-clause goals carry no decomposition to be faithful to; the
        # QA verdict itself is the fidelity measurement. Clause matrices are
        # enforced for multi-clause (decomposed) goals.
        return FidelityVerdict("passed", len(goal_clauses), len(goal_clauses), (), ())
    missing = clause_coverage_gap(goal_clauses, findings)
    missing_set = set(missing)
    failures = tuple(
        clause
        for clause in goal_clauses
        if clause not in missing_set
        and any(
            (finding.clause == clause or finding.clause in clause or clause in finding.clause)
            and finding.status == "fail"
            for finding in findings
        )
    )
    covered = len(goal_clauses) - len(missing) - len(failures)
    if failures:
        return FidelityVerdict("failed", covered, len(goal_clauses), tuple(missing), failures)
    if missing:
        return FidelityVerdict("unknown", covered, len(goal_clauses), tuple(missing), ())
    if goal_truncated:
        return FidelityVerdict("unknown", covered, len(goal_clauses), (_TRUNCATION_NOTE,), ())
    return FidelityVerdict("passed", covered, len(goal_clauses), (), ())


def acceptance_dimensions_summary(
    *,
    function_status: DimensionStatus,
    fidelity: FidelityVerdict,
    require_qa: bool,
) -> dict[str, str]:
    dims: dict[str, str] = {"function": function_status}
    dims["fidelity"] = fidelity.status if require_qa else "not_required"
    return dims
