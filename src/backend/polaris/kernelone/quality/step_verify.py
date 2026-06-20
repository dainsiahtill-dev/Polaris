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

import json
import re
import shlex
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
_VERIFY_COMMAND_TOKEN_RE = re.compile(
    r"(?:^|&&|\|\|)\s*"
    r"(?:pytest|python|python3|node|npm|pnpm|test|grep|ruff|mypy|make|bash|sh)\b"
)
_NODE_VERIFY_SCRIPT_RE = re.compile(r"\bnode\s+(?:\./)?scripts/verify\.js\b", re.IGNORECASE)
_HTML_OPEN_TAG_LITERAL_GREP_PATTERNS = {"<html>": "<html"}
_NATURAL_LANGUAGE_TAIL_MARKERS = (
    " 通过",
    " 验证",
    "，验证",
    "， 验证",
    "；验证",
    "。验证",
)


def _first_unquoted_marker_index(value: str, markers: tuple[str, ...]) -> int | None:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if any(value.startswith(marker, index) for marker in markers):
            return index
    return None


def _strip_unquoted_natural_language_tail(value: str) -> str:
    marker_index = _first_unquoted_marker_index(value, _NATURAL_LANGUAGE_TAIL_MARKERS)
    if marker_index is None:
        return value
    candidate = value[:marker_index].strip()
    if not candidate or not _VERIFY_COMMAND_TOKEN_RE.search(candidate):
        return value
    return candidate


def _split_first_shell_word(value: str) -> tuple[str, str] | None:
    quote: str | None = None
    escaped = False
    start: int | None = None
    for index, char in enumerate(value):
        if start is None:
            if char.isspace():
                continue
            if char in "|;&":
                return None
            start = index
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            continue
        if char.isspace():
            return value[start:index], value[index:].strip()
        if char in "|;&":
            return value[start:index].strip(), value[index:].strip()
    if start is None:
        return None
    return value[start:].strip(), ""


def _rewrite_simple_bash_here_string_clause(clause: str) -> str:
    marker_index = _first_unquoted_marker_index(clause, ("<<<",))
    if marker_index is None:
        return clause
    command = clause[:marker_index].strip()
    rhs = clause[marker_index + 3 :].strip()
    if not command or not rhs:
        return clause
    split_rhs = _split_first_shell_word(rhs)
    if split_rhs is None:
        return clause
    rhs_word, rhs_suffix = split_rhs
    try:
        rhs_tokens = shlex.split(rhs_word, posix=True)
    except ValueError:
        return clause
    if len(rhs_tokens) != 1:
        return clause
    rewritten = f"printf '%s\\n' {shlex.quote(rhs_tokens[0])} | {command}"
    if rhs_suffix:
        return f"{rewritten} {rhs_suffix}"
    return rewritten


def _normalize_bash_here_strings(value: str) -> str:
    if "<<<" not in value:
        return value
    return " && ".join(_rewrite_simple_bash_here_string_clause(part) for part in value.split(" && "))


def _normalize_simple_literal_grep_clause(clause: str) -> str:
    stripped = clause.strip()
    if not stripped.startswith("grep "):
        return clause
    try:
        parts = shlex.split(stripped)
    except ValueError:
        return clause
    if len(parts) != 4 or parts[0] != "grep":
        return clause

    raw_flags = parts[1]
    if not raw_flags.startswith("-") or raw_flags.startswith("--"):
        return clause
    flag_text = raw_flags[1:]
    if not flag_text or any(char in flag_text for char in "EPG") or "q" not in flag_text:
        return clause

    html_open_tag_pattern = _HTML_OPEN_TAG_LITERAL_GREP_PATTERNS.get(parts[2].lower())
    if html_open_tag_pattern is not None:
        normalized_flags = "-" + "".join(_dedupe_flag_order("Fi" + flag_text.replace("F", "")))
        return " ".join(shlex.quote(part) for part in ("grep", normalized_flags, html_open_tag_pattern, parts[3]))

    alternate_patterns = _split_basic_grep_or_pattern(parts[2])
    if alternate_patterns is not None:
        normalized_flags = "-" + "".join(_dedupe_flag_order("F" + flag_text))
        grep_parts = ["grep", normalized_flags]
        for pattern in alternate_patterns:
            grep_parts.extend(("-e", pattern))
        grep_parts.append(parts[3])
        return " ".join(shlex.quote(part) for part in grep_parts)

    if _grep_pattern_looks_regex_like(parts[2]):
        normalized_flags = "-" + "".join(_dedupe_flag_order("E" + flag_text.replace("F", "")))
        return " ".join(shlex.quote(part) for part in ("grep", normalized_flags, parts[2], parts[3]))

    if "F" in flag_text:
        return clause

    normalized_flags = "-" + "".join(_dedupe_flag_order("F" + flag_text))
    return " ".join(shlex.quote(part) for part in ("grep", normalized_flags, parts[2], parts[3]))


def _split_basic_grep_or_pattern(pattern: str) -> list[str] | None:
    if "\\|" not in pattern:
        return None
    parts = pattern.split("\\|")
    if len(parts) < 2 or any(not part.strip() for part in parts):
        return None
    return parts


def _grep_pattern_looks_regex_like(pattern: str) -> bool:
    if re.search(r"(?<!\\)\[(?:[^\]]*[A-Za-z0-9]-[A-Za-z0-9][^\]]*|[^\]]*\\[dDsSwW][^\]]*)\]", pattern):
        return True
    if re.search(r"\\[dDsSwW]", pattern):
        return True
    if re.search(r"(?<!\\)\.\*|(?<!\\)\.\+|(?<!\\)\.\?", pattern):
        return True
    if re.search(r"(?<!\\)\|", pattern):
        return True
    return pattern.startswith("^") or pattern.endswith("$")


def _dedupe_flag_order(flags: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for flag in flags:
        if flag in seen:
            continue
        seen.add(flag)
        ordered.append(flag)
    return ordered


def _normalize_literal_grep_clauses(value: str) -> str:
    if "grep " not in value:
        return value
    return " && ".join(_normalize_simple_literal_grep_clause(part) for part in value.split(" && "))


def normalize_step_verify(raw_verify: Any) -> str:
    """Join array-shaped verify into one machine-runnable command.

    Cloud models drift between string and array shapes (live I3-r10: a bare
    str() turned the array into Python-repr garbage that bash can never pass).
    """
    if isinstance(raw_verify, (list, tuple)):
        raw_verify = " && ".join(str(part).strip() for part in raw_verify if str(part).strip())
    normalized = _strip_unquoted_natural_language_tail(str(raw_verify or "").strip())
    normalized = _normalize_bash_here_strings(normalized)
    return _normalize_literal_grep_clauses(normalized)


def split_verify_clauses(verify: str) -> list[str]:
    return [part.strip() for part in verify.split(" && ") if part.strip()]


# A grep clause that searches for a DECLARED signature/interface token proves
# real code is present; a grep for the filename or a trivial marker does not.
_GREP_PATTERN_RE = re.compile(r"grep\s+(?:-[A-Za-z]+\s+)*(?P<quote>['\"])(?P<pat>.*?)(?P=quote)")


def _grep_clause_hits_signature(clause: str, signature_tokens: set[str]) -> bool:
    """True when a grep clause searches for one of the step's declared symbols."""
    match = _GREP_PATTERN_RE.search(clause)
    if match is None:
        return False
    pattern = match.group("pat").strip().lower()
    if not pattern:
        return False
    for token in signature_tokens:
        normalized = str(token or "").strip().lower()
        if len(normalized) < 3:
            continue
        if pattern in normalized or normalized in pattern:
            return True
    return False


def verify_has_structural_clause(verify: str, *, signature_tokens: set[str]) -> bool:
    """True when the verify carries at least one NON-hollow clause.

    A clause is *hollow* when it only proves the file exists (``test -f``), has a
    line count (``wc -l`` compare), or contains a trivial marker grep — none of
    which proves the declared code was actually written (live I3-r21: a step
    "resolved" on a ``polaris-deterministic-bootstrap`` stub because its verify
    was existence-only). A clause is *structural* when it is a syntax/compile/run
    check, a behaviour assertion, or a grep for a declared signature token.

    Fail-OPEN by design: any clause shape this function does not confidently
    recognize as hollow is treated as structural, so a malformed/exotic verify
    is never rejected — only a verify whose every clause is provably hollow.
    """
    clauses = split_verify_clauses(verify)
    if not clauses:
        return False
    for clause in clauses:
        candidate = clause.strip()
        if _TEST_FILE_RE.match(candidate) or _WC_COMPARE_RE.match(candidate):
            continue
        if _GREP_FILE_RE.match(candidate):
            if _grep_clause_hits_signature(candidate, signature_tokens):
                return True
            continue
        # Not existence / line-count / marker-grep -> a real check (syntax,
        # behaviour, test run, or a grep too complex to classify as hollow).
        return True
    return False


def verify_is_all_hollow(verify: str, *, signature_tokens: set[str]) -> bool:
    """True when EVERY verify clause is hollow (and there is at least one clause).

    The inverse of :func:`verify_has_structural_clause`, guarded so an empty
    verify (handled elsewhere as "missing verify") is never reported as hollow.
    """
    if not split_verify_clauses(verify):
        return False
    return not verify_has_structural_clause(verify, signature_tokens=signature_tokens)


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
    verify = _prefix_typescript_dist_build_if_needed(verify, cwd=cwd)
    recursion_failure = _verify_script_self_recursion_failure(verify, cwd=cwd)
    if recursion_failure:
        return 1, recursion_failure
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


def _verify_script_self_recursion_failure(verify: str, *, cwd: str) -> str:
    command = str(verify or "")
    if not _NODE_VERIFY_SCRIPT_RE.search(command):
        return ""
    script_path = Path(cwd) / "scripts" / "verify.js"
    if not script_path.exists():
        return ""
    try:
        content = script_path.read_text(encoding="utf-8")
    except OSError:
        return ""
    if not _NODE_VERIFY_SCRIPT_RE.search(content):
        return ""
    return "verify script recursively invokes itself: scripts/verify.js must not run node scripts/verify.js"


def _prefix_typescript_dist_build_if_needed(verify: str, *, cwd: str) -> str:
    command = str(verify or "").strip()
    if all(marker not in command for marker in ("./dist/", "'./dist/", '"./dist/', "\\'./dist/", '\\"./dist/')):
        return command
    if ".js" not in command:
        return command
    package_path = Path(cwd) / "package.json"
    if not package_path.exists():
        return command
    try:
        package_data = json.loads(package_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return command
    scripts = package_data.get("scripts")
    if not isinstance(scripts, dict):
        return command
    build_script = scripts.get("build")
    if not isinstance(build_script, str) or not build_script.strip():
        return command
    return f"npm run build --silent >/dev/null 2>&1 && {command}"


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
    "verify_has_structural_clause",
    "verify_is_all_hollow",
]
