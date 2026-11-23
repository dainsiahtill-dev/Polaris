"""Acceptance dimension ontology and goal-clause machinery.

The acceptance semantics are multi-dimensional, but the run outcome used to
collapse them into one bit: function-checks plus a QA thumbs-up equaled
"accepted", even when no tool could verify requirement fidelity — a missing
measurement defaulted to "passed" instead of "unknown". Real runs accepted a
mis-assembled pelican because nothing measured pose/assembly correctness.

Dimensions:
- ``function``: machine-graded by the current candidate’s recorded verification checks.
- ``fidelity``: requirement-clause fidelity — QA must rule on every concrete
  clause of the goal with evidence (structured ``clause_findings``).

A required dimension without a passing measurement is ``unknown`` and blocks
``accepted``. Extending the ontology means registering another dimension with
its measurement tool — never another implicit default.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

DimensionStatus = Literal["passed", "failed", "unknown", "not_required"]

_CLAUSE_STRIP = re.compile(r"^\s*(?:[-*•+]|\d+[.、)])\s*")
_TABLE_SEPARATOR = re.compile(r"^\|[\s\-|:]+\|$")
# Checklist scaffolding ("[ ] ...", "[x] ...", "☐ ...") is a requirement body,
# not a marker of its own; drop the box so the clause reads as plain text.
_CHECKBOX_MARKER = re.compile(r"^(?:\[[ xX✓✗☑☒]\]|☐|☑|✓|✗)\s*")
# A short line ending in a colon introduces the list or table that follows it
# ("先固定这些实验判定：", "交付时请逐项核验："). It states no requirement.
_LEAD_IN = re.compile(r"^.{0,48}[:：]$")
# Enumeration markers a reviewer naturally writes: "1", "C1", "条款1", "Clause 3".
_CLAUSE_ENUM = re.compile(r"^\s*(?:(?:clause|条款|第)\s*)?[A-Za-z]{0,4}\s*\.?\s*(\d{1,3})\b", re.IGNORECASE)
_CLAUSE_RANGE = re.compile(r"^\s*(\d{1,3})\s*[-–~]\s*(\d{1,3})\s*$")
_CLAUSE_LIST = re.compile(r"^\s*\d{1,3}(?:\s*[,，、]\s*\d{1,3})*\s*$")
# The frozen catalog's real requirement clauses run 8..25 (p50 17, max 25 across
# all 120 projects). A 14-clause cap silently truncated 85 of them, making the
# fidelity dimension permanently unknown; 32 covers the catalog with headroom.
_MAX_CLAUSES = 32
_EVIDENCE_TOKEN = re.compile(r"[\w@+./:-]+")
_NATIVE_ID_MARKERS = ("call_", "toolu_", "verification-")
_FINDING_STATUSES = {"pass", "fail", "unknown"}

# The internal acceptance adapter prepends a platform notice and appends platform
# delivery contracts around the frozen user requirements. Fidelity is about the
# user's goal, so the clause source starts after this marker and, for markdown
# requirement docs, uses the opening goal/use-experience section rather than the
# platform contract sections that follow it.
FROZEN_REQUIREMENTS_MARKER = "--- Frozen original requirements (verbatim) ---"


def _goal_clause_source(instruction: str) -> str:
    text = instruction or ""
    if FROZEN_REQUIREMENTS_MARKER not in text:
        return text
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
    lines = _goal_clause_source(instruction).splitlines()
    for index, raw in enumerate(lines):
        stripped = raw.strip()
        line = _CLAUSE_STRIP.sub("", stripped).strip()
        line = _CHECKBOX_MARKER.sub("", line).strip()
        if not line:
            continue
        if line.startswith("#") or _TABLE_SEPARATOR.match(line):
            continue
        if _LEAD_IN.match(line):
            continue
        if stripped.startswith("|"):
            # A Markdown table header is layout, not an independent acceptance
            # requirement. Keep the data rows, but skip the header immediately
            # followed by its delimiter row (observed blocking L1-01 QA).
            following = next((item.strip() for item in lines[index + 1 :] if item.strip()), "")
            if _TABLE_SEPARATOR.match(following):
                continue
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
            raise ValueError("clause finding requires clause, status pass|fail|unknown and evidence")
        findings.append(
            ClauseFinding(clause=clause, status=status, evidence=evidence if status == "pass" else evidence[:400])
        )
    return findings


@dataclass(frozen=True)
class FidelityVerdict:
    status: DimensionStatus
    covered: int
    total: int
    missing: tuple[str, ...]
    failures: tuple[str, ...]


def _clause_reference(clause: str, goal_clauses: Sequence[str]) -> list[str]:
    """Resolve one reported label to goal clauses.

    Accepts the verbatim text, a unique excerpt, a 1-based number, a numeric
    range or list (``1-5``, ``1,2,3``), or an enumeration marker such as ``C1``,
    ``条款1`` and ``Clause 3``. Anything ambiguous resolves to nothing: coverage
    must never be guessed.
    """

    total = len(goal_clauses)
    text = clause.strip()
    if not text:
        return []
    if text in goal_clauses:
        return [text]
    candidates = [item for item in goal_clauses if item in text or text in item]
    if len(candidates) == 1:
        return candidates
    indices: list[int] = []
    ranged = _CLAUSE_RANGE.match(text)
    if ranged:
        start, end = int(ranged.group(1)), int(ranged.group(2))
        indices.extend(range(min(start, end), max(start, end) + 1))
    elif _CLAUSE_LIST.match(text):
        indices.extend(int(value) for value in re.findall(r"\d{1,3}", text))
    else:
        enumerated = _CLAUSE_ENUM.match(text)
        if enumerated:
            indices.append(int(enumerated.group(1)))
    resolved: list[str] = []
    for index in indices:
        if 1 <= index <= total and goal_clauses[index - 1] not in resolved:
            resolved.append(goal_clauses[index - 1])
    return resolved


@dataclass(frozen=True)
class ClauseMatchResult:
    """Clause rulings plus how the reviewer's labels were resolved."""

    matched: dict[str, set[str]]
    mode: str  # "reference" when at least one label resolved, else "none"


def match_clause_findings(goal_clauses: Sequence[str], findings: Sequence[ClauseFinding]) -> ClauseMatchResult:
    """Map reviewer findings onto goal clauses without inventing coverage."""

    matched: dict[str, set[str]] = {}
    for finding in findings:
        for clause in _clause_reference(finding.clause, goal_clauses):
            matched.setdefault(clause, set()).add(finding.status)
    return ClauseMatchResult(matched, "reference" if matched else "none")


def _matched_clauses(goal_clauses: Sequence[str], findings: Sequence[ClauseFinding]) -> dict[str, set[str]]:
    """Accept the verbatim text, a unique excerpt, a number, or an enumeration marker.

    Echoing ten long requirement paragraphs verbatim is an unreasonable contract
    for a small reviewer: live runs showed it covering a single clause and being
    rejected over and over. A stable index keeps the matrix checkable without
    turning coverage into a copy test.
    """

    return match_clause_findings(goal_clauses, findings).matched


def clause_coverage_gap(goal_clauses: Sequence[str], findings: list[ClauseFinding]) -> list[str]:
    """Allow a unique clause excerpt; an ambiguous fragment establishes no coverage."""
    matched = _matched_clauses(goal_clauses, findings)
    return [clause for clause in goal_clauses if clause not in matched]


def current_browser_capture(observation: Mapping[str, Any]) -> dict[str, Any] | None:
    """Recognize a physical capture; historical image reinspection carries a different payload."""
    if (
        observation.get("tool") != "browser_observe"
        or observation.get("ok") is not True
        or observation.get("interrupted")
        or observation.get("timed_out")
    ):
        return None
    try:
        result = json.loads(observation["content"])
    except (TypeError, KeyError, ValueError):
        return None
    if not isinstance(result, dict) or not result.get("entry") or not isinstance(result.get("page_errors"), list):
        return None
    return result


def require_observed_clause_evidence(
    findings: Sequence[ClauseFinding],
    *,
    events: Sequence[Mapping[str, Any]],
    checks: Sequence[Mapping[str, Any]],
    frame_seq: int,
    report_turn_seq: int,
) -> None:
    """Resolve passing citations against host checks and earlier observations from this QA frame."""
    identities: Counter[str] = Counter()
    usable: set[str] = set()
    observations = [
        event["payload"]
        for event in events
        if frame_seq < event["seq"] < report_turn_seq and event["role"] == "qa" and event["kind"] == "tool_observed"
    ]
    for observation in (*checks, *observations):
        identity = observation.get("call_id")
        if not isinstance(identity, str) or not identity:
            continue
        identities[identity] += 1
        if observation.get("tool") == "browser_observe" and current_browser_capture(observation) is None:
            continue
        if (
            observation.get("tool") in {"read_file", "repo_rg", "browser_observe", "execute_command"}
            and observation.get("ok") is True
            and not observation.get("interrupted")
            and not observation.get("timed_out")
            and (observation.get("tool") != "repo_rg" or observation.get("exit_code") == 0)
        ):
            usable.add(identity)
    for finding in findings:
        if finding.status != "pass":
            continue
        if finding.evidence in usable:
            references = {finding.evidence}
        else:
            # Explanatory prose may cite several receipts. Resolve each whole
            # token so a genuine receipt cannot conceal a stale or invented one.
            tokens = (token.rstrip(".:") for token in _EVIDENCE_TOKEN.findall(finding.evidence))
            references = {
                token
                for token in tokens
                if token in identities or any(marker in token for marker in _NATIVE_ID_MARKERS)
            }
            if any(not token.startswith(_NATIVE_ID_MARKERS) for token in references):
                references = set()
        if not references or any(ref not in usable or identities[ref] != 1 for ref in references):
            raise ValueError(
                f"Clause {finding.clause!r} needs the exact call ID of a successful current check or earlier QA observation for each citation."
            )


_TRUNCATION_NOTE = "[目标条款多于验收清单上限，无法证明全部条款保真]"


def assess_fidelity(
    *,
    goal_clauses: list[str],
    findings: list[ClauseFinding],
    qa_verdict: str,
    goal_truncated: bool = False,
) -> FidelityVerdict:
    """Grade the fidelity dimension from structured QA clause rulings.

    Approved full pass coverage is passed. A failing clause is failed.
    Missing or inconclusive measurements keep the result unknown. rejected QA already implies failed fidelity. A goal whose clause
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
    matched = _matched_clauses(goal_clauses, findings)
    failures = tuple(clause for clause in goal_clauses if "fail" in matched.get(clause, ()))
    missing = [clause for clause in goal_clauses if clause not in failures and matched.get(clause) != {"pass"}]
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
