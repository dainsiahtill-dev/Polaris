"""Pure policy for dangerous command pattern source governance checks."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_RULE_ID = "canonical_dangerous_patterns"

DANGEROUS_PATTERN_DEFINITION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"_DANGEROUS_PATTERNS\s*=\s*\["),
    re.compile(r"DANGEROUS_PATTERNS\s*=\s*\["),
    re.compile(r"DANGEROUS_PATTERNS\s*:\s*list"),
    re.compile(r"_DANGEROUS_PATTERNS\s*:\s*list"),
)


@dataclass(frozen=True)
class DangerousPatternSourcePolicyResult:
    """Evaluation result for dangerous pattern source governance checks."""

    rule_id: str
    passed: bool
    evidence: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def _relative_path(path: Path, workspace: Path) -> str:
    """Return a stable workspace-relative path for evidence messages."""
    try:
        return str(path.relative_to(workspace))
    except ValueError:
        return str(path)


def _canonical_source_has_patterns(canonical_path: Path) -> bool:
    """Return true when the canonical source exists and defines patterns."""
    if not canonical_path.exists():
        return False
    try:
        content = canonical_path.read_text(encoding="utf-8")
    except OSError:
        return False
    return "_DANGEROUS_PATTERNS" in content or "DANGEROUS_PATTERNS" in content


def _find_local_pattern_definitions(workspace: Path, cells_dir: Path, canonical_path: Path) -> tuple[str, ...]:
    """Return local dangerous pattern definitions under ``polaris/cells``.

    Complexity:
        O(f * p) time for scanned Python files and definition patterns.
        O(v) space for emitted violations.
    """
    if not cells_dir.exists():
        return ()

    violations: list[str] = []
    for py_file in cells_dir.rglob("*.py"):
        if py_file == canonical_path:
            continue
        if "test" in py_file.parts or "_fixture" in py_file.name:
            continue

        try:
            content = py_file.read_text(encoding="utf-8")
        except OSError:
            continue

        for pattern in DANGEROUS_PATTERN_DEFINITION_PATTERNS:
            for match in pattern.finditer(content):
                line_num = content[: match.start()].count("\n") + 1
                rel_path = _relative_path(py_file, workspace)
                violations.append(f"Local pattern definition at {rel_path}:{line_num}: {match.group()[:50]}...")

    return tuple(violations)


def evaluate_dangerous_pattern_source(
    workspace: Path,
    *,
    rule_id: str = DEFAULT_RULE_ID,
) -> DangerousPatternSourcePolicyResult:
    """Evaluate that dangerous command patterns have one canonical source."""
    canonical_path = workspace / "polaris" / "kernelone" / "security" / "dangerous_patterns.py"
    cells_dir = workspace / "polaris" / "cells"

    if not _canonical_source_has_patterns(canonical_path):
        return DangerousPatternSourcePolicyResult(
            rule_id=rule_id,
            passed=False,
            violations=(f"Canonical source not found: {_relative_path(canonical_path, workspace)}",),
        )

    evidence = (f"Canonical source verified: {_relative_path(canonical_path, workspace)}",)
    violations = _find_local_pattern_definitions(workspace, cells_dir, canonical_path)
    if violations:
        return DangerousPatternSourcePolicyResult(
            rule_id=rule_id,
            passed=False,
            evidence=evidence,
            violations=violations,
        )

    return DangerousPatternSourcePolicyResult(
        rule_id=rule_id,
        passed=True,
        evidence=(*evidence, "No local dangerous pattern definitions found in cells/"),
    )
