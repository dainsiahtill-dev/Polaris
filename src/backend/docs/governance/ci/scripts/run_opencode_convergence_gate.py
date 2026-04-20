"""OpenCode convergence governance gate.

Enforces canonical import entrypoints for OpenCode-derived capabilities in
KernelOne so the codebase does not drift into parallel deep-module forks.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MODE_AUDIT_ONLY = "audit-only"
_MODE_HARD_FAIL = "hard-fail"
_SUPPORTED_MODES = (_MODE_AUDIT_ONLY, _MODE_HARD_FAIL)

_RULE_ID = "opencode_canonical_entrypoint_non_regressive"
_SEVERITY_HIGH = "high"


@dataclass(frozen=True)
class ConvergenceIssue:
    rule_id: str
    severity: str
    message: str
    path: str
    line: int
    import_token: str
    canonical_import: str

    def fingerprint(self) -> str:
        key = f"{self.rule_id}|{self.severity}|{self.path}|{self.line}|{self.import_token}|{self.canonical_import}"
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "message": self.message,
            "path": self.path,
            "line": self.line,
            "import_token": self.import_token,
            "canonical_import": self.canonical_import,
            "fingerprint": self.fingerprint(),
        }


@dataclass(frozen=True)
class ConvergenceRule:
    deep_prefix: str
    canonical_import: str
    allowed_owner_prefixes: tuple[str, ...]
    message: str


_RULES: tuple[ConvergenceRule, ...] = (
    ConvergenceRule(
        deep_prefix="polaris.kernelone.events.typed.schemas",
        canonical_import="from polaris.kernelone.events.typed import ...",
        allowed_owner_prefixes=("polaris/kernelone/events/typed/",),
        message="typed events imports must use the package-level public surface",
    ),
    ConvergenceRule(
        deep_prefix="polaris.kernelone.events.typed.registry",
        canonical_import="from polaris.kernelone.events.typed import EventRegistry, EventPattern, Subscription",
        allowed_owner_prefixes=("polaris/kernelone/events/typed/",),
        message="typed registry imports must use package-level re-exports",
    ),
    ConvergenceRule(
        deep_prefix="polaris.kernelone.events.typed.bus_adapter",
        canonical_import="from polaris.kernelone.events.typed import TypedEventBusAdapter",
        allowed_owner_prefixes=("polaris/kernelone/events/typed/",),
        message="typed bus adapter imports must use package-level re-exports",
    ),
    ConvergenceRule(
        deep_prefix="polaris.kernelone.tool.state_machine",
        canonical_import="from polaris.kernelone.tool import ToolState, ToolStateStatus, ...",
        allowed_owner_prefixes=("polaris/kernelone/tool/",),
        message="tool state imports must use package-level tool exports",
    ),
    ConvergenceRule(
        deep_prefix="polaris.kernelone.messages.part_types",
        canonical_import="from polaris.kernelone.messages import Part, MessageContent, ...",
        allowed_owner_prefixes=("polaris/kernelone/messages/",),
        message="message part imports must use package-level message exports",
    ),
    ConvergenceRule(
        deep_prefix="polaris.kernelone.editing.replacers.opencode_replacers",
        canonical_import="from polaris.kernelone.editing.replacers import ...",
        allowed_owner_prefixes=("polaris/kernelone/editing/replacers/",),
        message="OpenCode replacer imports must use replacers package exports",
    ),
)


def _normalize_rel(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _iter_python_files(repo_root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(repo_root.rglob("*.py")):
        rel = _normalize_rel(path, repo_root)
        if "__pycache__" in rel:
            continue
        if rel.startswith(".venv/"):
            continue
        # Governance gate focuses on production code paths.
        if "/tests/" in rel or rel.startswith("tests/"):
            continue
        files.append(path)
    return files


def _is_allowed_path(rel: str, allowed_prefixes: tuple[str, ...]) -> bool:
    return any(rel.startswith(prefix) for prefix in allowed_prefixes)


def _check_token_against_rules(
    *,
    rel: str,
    line: int,
    module_token: str,
    imported_names: str,
) -> list[ConvergenceIssue]:
    issues: list[ConvergenceIssue] = []
    for rule in _RULES:
        if not module_token.startswith(rule.deep_prefix):
            continue
        if _is_allowed_path(rel, rule.allowed_owner_prefixes):
            continue
        token = module_token if not imported_names else f"{module_token}:{imported_names}"
        issues.append(
            ConvergenceIssue(
                rule_id=_RULE_ID,
                severity=_SEVERITY_HIGH,
                message=rule.message,
                path=rel,
                line=line,
                import_token=token,
                canonical_import=rule.canonical_import,
            )
        )
    return issues


def _collect_import_issues_fallback(source: str, *, rel: str) -> list[ConvergenceIssue]:
    issues: list[ConvergenceIssue] = []
    import_re = re.compile(r"^\s*import\s+(.+?)\s*(#.*)?$")
    from_re = re.compile(r"^\s*from\s+([A-Za-z0-9_\.]+)\s+import\s+(.+?)\s*(#.*)?$")
    for line_no, raw_line in enumerate(source.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        import_match = import_re.match(raw_line)
        if import_match:
            payload = import_match.group(1) or ""
            for chunk in payload.split(","):
                token = chunk.strip().split(" as ", 1)[0].strip()
                if not token:
                    continue
                issues.extend(
                    _check_token_against_rules(
                        rel=rel,
                        line=line_no,
                        module_token=token,
                        imported_names="",
                    )
                )
            continue

        from_match = from_re.match(raw_line)
        if from_match:
            module = (from_match.group(1) or "").strip()
            imported_names = (from_match.group(2) or "").strip()
            if not module:
                continue
            issues.extend(
                _check_token_against_rules(
                    rel=rel,
                    line=line_no,
                    module_token=module,
                    imported_names=imported_names,
                )
            )
    return issues


def _collect_import_issues(repo_root: Path) -> list[ConvergenceIssue]:
    issues: list[ConvergenceIssue] = []

    for file_path in _iter_python_files(repo_root):
        rel = _normalize_rel(file_path, repo_root)
        source = file_path.read_text(encoding="utf-8")
        try:
            tree = ast.parse(source, filename=str(file_path))
        except SyntaxError:
            # Fall back to line-level import scanning for legacy files that are not
            # valid Python modules. This keeps the gate focused on import drift.
            issues.extend(_collect_import_issues_fallback(source, rel=rel))
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    token = str(alias.name or "").strip()
                    if not token:
                        continue
                    issues.extend(
                        _check_token_against_rules(
                            rel=rel,
                            line=int(getattr(node, "lineno", 0) or 0),
                            module_token=token,
                            imported_names="",
                        )
                    )
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "").strip()
                if not module:
                    continue
                imported_names = ",".join(str(alias.name or "").strip() for alias in node.names)
                issues.extend(
                    _check_token_against_rules(
                        rel=rel,
                        line=int(getattr(node, "lineno", 0) or 0),
                        module_token=module,
                        imported_names=imported_names,
                    )
                )

    # Deduplicate by fingerprint for stable reporting.
    unique: dict[str, ConvergenceIssue] = {}
    for issue in issues:
        unique[issue.fingerprint()] = issue
    return sorted(unique.values(), key=lambda item: (item.path, item.line, item.import_token))


def _build_report(*, workspace: Path, mode: str, issues: list[ConvergenceIssue]) -> dict[str, Any]:
    issue_count = len(issues)
    exit_code = 0
    if mode == _MODE_HARD_FAIL and issue_count > 0:
        exit_code = 1
    return {
        "workspace": str(workspace.resolve()),
        "mode": mode,
        "exit_code": exit_code,
        "rule_id": _RULE_ID,
        "issue_count": issue_count,
        "issues": [item.to_dict() for item in issues],
        "issue_fingerprints": [item.fingerprint() for item in issues],
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run OpenCode convergence governance gate.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Repository root (default: current directory)",
    )
    parser.add_argument(
        "--mode",
        choices=_SUPPORTED_MODES,
        default=_MODE_AUDIT_ONLY,
        help="Gate mode (audit-only or hard-fail)",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional report path for JSON output",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    issues = _collect_import_issues(workspace)
    report = _build_report(workspace=workspace, mode=str(args.mode), issues=issues)

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.report:
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = workspace / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")

    print(rendered)
    return int(report["exit_code"])


if __name__ == "__main__":
    raise SystemExit(main())
