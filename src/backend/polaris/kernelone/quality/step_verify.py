"""Step-contract machine verify toolkit (ce-blueprint-tasks/1).

Single source of truth for the three verify touchpoints — Director in-turn
self-check (写后即查), QA acceptance, and claim-time punch list (现状勘察).
These started as deliberate per-cell mirrors and drifted toward triple
maintenance; per the KernelOne-first rule they are consolidated here and the
cells delegate.

Diagnosis discipline (adversarial-review hardened, live I3-r12):
the full command stays the pass/fail ground truth; clause-level work only
sharpens teaching. Clauses are re-run individually in fresh shells, so
diagnosis is abandoned whenever that could name a wrong clause — quoted text
cut by the ``" && "`` split (sh -n guard), top-level ``||`` regrouping, or
state-carrying clauses (cd/export/VAR=…) whose effects do not reach their
successors in a fresh shell. A wrong teaching is worse than none.

Residual quantification (T2, theory report 法则4): where a failing clause is
machine-measurable, report "measured vs required" — live I3-r12: a step died
because teaching said WHICH clause failed but not that the file needed 38
lines removed; the missing information was literally a handful of bits.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_STATE_CARRYING_CLAUSE_RE = re.compile(r"^(?:cd|export|unset|umask|set)\b|^[A-Za-z_][A-Za-z0-9_]*=")

_WC_COMPARE_RE = re.compile(
    r'^\[\s*"?\$\(\s*wc\s+-l\s*<\s*(?P<path>[^)]+?)\s*\)"?\s+(?P<op>-le|-lt|-ge|-gt|-eq)\s+(?P<num>\d+)\s*\]$'
)
_TEST_FILE_RE = re.compile(r"^test\s+-[fe]\s+(?P<path>\S+)$")
_GREP_FILE_RE = re.compile(r"^grep\s+(?:-[A-Za-z]+\s+)*(?P<quote>['\"]).*?(?P=quote)\s+(?P<path>\S+)\s*$")

_OP_TEXT = {"-le": "≤", "-lt": "<", "-ge": "≥", "-gt": ">", "-eq": "="}

# A single ≤120-line step legitimately carries many cheap grep/test obligations
# (live I3-r14: a main.js step had 15) — diagnosing them sharpens the punch list
# instead of degrading to a whole-verdict. Re-runs stay bounded by the per-clause
# timeout. 24 mirrors the CE _MAX_STEPS_PER_TASK ceiling.
_MAX_DIAGNOSABLE_CLAUSES = 24
_CLAUSE_TIMEOUT_SECONDS = 10
_VERIFY_TIMEOUT_SECONDS = 60


def normalize_step_verify(raw_verify: Any) -> str:
    """Join array-shaped verify into one machine-runnable command.

    Cloud models drift between string and array shapes (live I3-r10: a bare
    str() turned the array into Python-repr garbage that bash can never pass).
    """
    if isinstance(raw_verify, (list, tuple)):
        return " && ".join(str(part).strip() for part in raw_verify if str(part).strip())
    return str(raw_verify or "").strip()


def split_verify_clauses(verify: str) -> list[str]:
    return [part.strip() for part in verify.split(" && ") if part.strip()]


def _clean_path(raw: str) -> str:
    path = raw.strip().strip("'\"")
    return path.removeprefix("./")


def _count_lines(path: Path) -> int | None:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return sum(1 for _ in fh)
    except OSError:
        return None


def clause_residual(clause: str, *, cwd: str) -> str:
    """T2 residual: 'measured vs required' for machine-measurable clauses.

    Returns "" when the clause shape is not confidently parseable — a wrong
    number is worse than no number.
    """
    wc_match = _WC_COMPARE_RE.match(clause)
    if wc_match:
        path = Path(cwd) / _clean_path(wc_match.group("path"))
        required = int(wc_match.group("num"))
        op = wc_match.group("op")
        measured = _count_lines(path)
        if measured is None:
            return "文件不存在"
        detail = f"实测 {measured} 行, 要求 {_OP_TEXT[op]}{required}"
        if op in ("-le", "-lt") and measured > required:
            excess = measured - required + (1 if op == "-lt" else 0)
            detail += f", 需删 {excess} 行"
        elif op in ("-ge", "-gt") and measured < required:
            shortfall = required - measured + (1 if op == "-gt" else 0)
            detail += f", 需增 {shortfall} 行"
        return detail
    test_match = _TEST_FILE_RE.match(clause)
    if test_match:
        path = Path(cwd) / _clean_path(test_match.group("path"))
        if not path.exists():
            return "文件不存在, 需创建"
        return ""
    grep_match = _GREP_FILE_RE.match(clause)
    if grep_match:
        path = Path(cwd) / _clean_path(grep_match.group("path"))
        if not path.exists():
            return f"文件 {_clean_path(grep_match.group('path'))} 不存在"
        return ""
    return ""


def _clause_diagnosis_allowed(verify: str, clauses: list[str]) -> bool:
    if " || " in verify:
        return False
    if len(clauses) < 2 or len(clauses) > _MAX_DIAGNOSABLE_CLAUSES:
        return False
    for clause in clauses:
        if _STATE_CARRYING_CLAUSE_RE.match(clause):
            return False
        try:
            syntax = subprocess.run(
                ["/bin/sh", "-n", "-c", clause],
                capture_output=True,
                timeout=_CLAUSE_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        if syntax.returncode != 0:
            return False
    return True


def _run_clause(clause: str, *, cwd: str) -> int | None:
    try:
        proc = subprocess.run(
            clause,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_CLAUSE_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode


def _decorate(clause: str, *, cwd: str) -> str:
    residual = clause_residual(clause, cwd=cwd)
    return f"{clause} ({residual})" if residual else clause


def first_failing_verify_clause(verify: str, *, cwd: str) -> str:
    """Name the first failing clause (with residual) or "" when diagnosis
    must be abandoned."""
    clauses = split_verify_clauses(verify)
    if not _clause_diagnosis_allowed(verify, clauses):
        return ""
    for index, clause in enumerate(clauses, start=1):
        returncode = _run_clause(clause, cwd=cwd)
        if returncode is None:
            return ""
        if returncode != 0:
            return f"failing clause [{index}/{len(clauses)}]: {_decorate(clause, cwd=cwd)}"
    return ""


def run_step_verify(verify: str, *, cwd: str) -> tuple[int, str] | None:
    """Run the full verify command. Returns (exit_code, output_tail) or None
    when it could not run at all."""
    try:
        proc = subprocess.run(
            verify,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=_VERIFY_TIMEOUT_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return proc.returncode, ((proc.stdout or "") + (proc.stderr or ""))


def collect_failing_clauses(verify: str, *, cwd: str) -> dict[str, Any] | None:
    """Punch-list core (现状勘察): run the full verify, then list every
    failing clause (with residuals) when diagnosis is allowed.

    Returns ``{"exit_code", "failing_clauses", "total_clauses"}`` or None when
    the verify could not run. ``failing_clauses`` stays empty when clause
    diagnosis must be abandoned — the caller still learns pass/fail.
    """
    outcome = run_step_verify(verify, cwd=cwd)
    if outcome is None:
        return None
    exit_code, _tail = outcome
    clauses = split_verify_clauses(verify)
    result: dict[str, Any] = {
        "exit_code": int(exit_code),
        "failing_clauses": [],
        "total_clauses": len(clauses),
    }
    if exit_code == 0 or not _clause_diagnosis_allowed(verify, clauses):
        return result
    failing: list[str] = []
    for clause in clauses:
        returncode = _run_clause(clause, cwd=cwd)
        if returncode is None:
            return result
        if returncode != 0:
            failing.append(_decorate(clause, cwd=cwd))
    result["failing_clauses"] = failing
    return result


__all__ = [
    "clause_residual",
    "collect_failing_clauses",
    "first_failing_verify_clause",
    "normalize_step_verify",
    "run_step_verify",
    "split_verify_clauses",
]
