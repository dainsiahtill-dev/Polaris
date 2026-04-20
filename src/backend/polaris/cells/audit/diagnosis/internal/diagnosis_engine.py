"""Audit diagnosis and QA scanning engine.

This module provides:
- 3-hop failure diagnosis
- project-level static QA scan
- code-region QA scan
- trace timeline query

CRITICAL: all text file I/O uses UTF-8.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from polaris.cells.audit.diagnosis.internal.toolkit.query import (
    query_by_run_id,
    query_by_task_id,
    query_by_trace_id,
    query_events,
)
from polaris.kernelone.process.command_executor import CommandExecutionService, CommandRequest

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from collections.abc import Sequence

_SOURCE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".java",
    ".go",
    ".rs",
    ".rb",
    ".php",
    ".cs",
}

_TEST_HINTS = ("test", "spec")

_SECRET_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "hardcoded_secret",
        re.compile(r"(?i)(api[_-]?key|secret|token|password)\s*[:=]\s*[\"'][^\"']{8,}[\"']"),
        "Possible hardcoded secret",
    ),
    (
        "private_key",
        re.compile(r"-----BEGIN (RSA|EC|DSA|OPENSSH) PRIVATE KEY-----"),
        "Private key material found",
    ),
]

_FAILURE_CATEGORY_PATTERNS: list[tuple[str, tuple[str, ...], str, float]] = [
    (
        "permission_denied",
        ("permission denied", "access denied", "unauthorized", "forbidden", "eacces"),
        "Check workspace permissions and tool authorization policy before retry.",
        0.9,
    ),
    (
        "timeout",
        ("timeout", "timed out", "deadline exceeded"),
        "Increase timeout or split the task into smaller tool operations.",
        0.86,
    ),
    (
        "missing_dependency",
        (
            "module not found",
            "no module named",
            "command not found",
            "not recognized as an internal or external command",
            "cannot find module",
        ),
        "Install missing dependencies and pin versions in project config.",
        0.88,
    ),
    (
        "verification_failure",
        ("assertionerror", "test failed", "verification failed", "quality gate"),
        "Fix failed checks and re-run verification with captured evidence.",
        0.82,
    ),
    (
        "prompt_leakage",
        (
            "system prompt",
            "<thinking>",
            "<tool_call>",
            "you are",
            "role setting",
            "角色设定",
        ),
        "Sanitize generated output and enforce prompt-leakage guardrails.",
        0.74,
    ),
]


def _clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _parse_iso_timestamp(value: Any) -> datetime | None:
    token = str(value or "").strip()
    if not token:
        return None
    try:
        return datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError:
        return None


def _parse_time_range(value: str) -> timedelta:
    token = str(value or "").strip().lower()
    if not token:
        return timedelta(hours=1)
    matched = re.fullmatch(r"(\d+)([mhd])", token)
    if not matched:
        return timedelta(hours=1)
    amount = _clamp(_safe_int(matched.group(1), 1), 1, 24 * 30)
    unit = matched.group(2)
    if unit == "m":
        return timedelta(minutes=amount)
    if unit == "h":
        return timedelta(hours=amount)
    return timedelta(days=amount)


def _safe_read_text(path: Path, max_chars: int = 400_000) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    if len(raw) > max_chars:
        return raw[:max_chars]
    return raw


def _extract_event_error(event: dict[str, Any]) -> str:
    fragments: list[str] = []
    for key in ("error", "message", "detail", "reason"):
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            fragments.append(value.strip())

    action = event.get("action")
    if isinstance(action, dict):
        for key in ("error", "message", "detail"):
            value = action.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value.strip())

    data = event.get("data")
    if isinstance(data, dict):
        for key in ("error", "message", "detail", "stderr", "stdout"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                fragments.append(value.strip())

    return " | ".join(fragments)


def _normalize_rel_path(path: Path, workspace_root: Path) -> str:
    try:
        return path.resolve().relative_to(workspace_root.resolve()).as_posix()
    except ValueError:
        return path.resolve().as_posix()


@dataclass(frozen=True)
class ScanFinding:
    severity: str
    category: str
    file: str
    line: int
    message: str
    evidence: str
    recommendation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "category": self.category,
            "file": self.file,
            "line": self.line,
            "message": self.message,
            "evidence": self.evidence,
            "recommendation": self.recommendation,
        }


class AuditDiagnosisEngine:
    """Engine for failure diagnosis and QA/audit scanning."""

    def __init__(self, runtime_root: Path | str, workspace: str) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.workspace = Path(workspace).resolve()

    def analyze_failure(
        self,
        *,
        run_id: str | None = None,
        task_id: str | None = None,
        error_hint: str | None = None,
        time_range: str = "1h",
        depth: int = 3,
    ) -> dict[str, Any]:
        normalized_depth = _clamp(_safe_int(depth, 3), 1, 3)
        events = self._load_events(run_id=run_id, task_id=task_id, limit=3000)
        failure_event = self._pick_failure_event(events, error_hint=error_hint)

        hops: list[dict[str, Any]] = []
        if failure_event:
            hop1 = self._build_phase_hop(failure_event)
            evidence = self._collect_evidence(
                events=events,
                failure_event=failure_event,
                time_range=time_range,
            )
            hops.append(
                {
                    "hop": 1,
                    "phase": hop1.get("phase"),
                    "evidence": [hop1],
                }
            )
            hops.append(
                {
                    "hop": 2,
                    "phase": "evidence_collection",
                    "evidence": evidence.get("signals", []),
                }
            )
        else:
            evidence = {
                "signals": [],
                "window_event_count": 0,
                "tool_failures": 0,
                "verification_failures": 0,
                "llm_failures": 0,
            }

        root_cause_payload: dict[str, Any] | None = None
        fix_suggestion: str | None = None
        if normalized_depth >= 3:
            root_cause_payload = self._infer_root_cause(
                failure_event=failure_event,
                error_hint=error_hint,
                evidence=evidence,
            )
            fix_suggestion = str(root_cause_payload.get("fix_suggestion") or "").strip() or None
            hops.append(
                {
                    "hop": 3,
                    "root_cause": root_cause_payload.get("category"),
                    "fix_suggestion": fix_suggestion,
                }
            )

        timeline = self._build_timeline(events, limit=160)
        recommended_action = self._determine_action(
            root_cause_payload=root_cause_payload,
            failure_event=failure_event,
            has_failure=bool(failure_event),
        )

        return {
            "run_id": run_id,
            "task_id": task_id,
            "depth": normalized_depth,
            "failure_hops": hops,
            "timeline": timeline,
            "recommended_action": recommended_action,
            "root_cause": root_cause_payload,
            "failure_detected": bool(failure_event),
            "event_count": len(events),
        }

    def scan_project(
        self,
        *,
        scope: str = "full",
        focus: str | None = None,
        max_files: int = 800,
        max_findings: int = 300,
    ) -> dict[str, Any]:
        normalized_scope = str(scope or "full").strip().lower()
        if normalized_scope not in {"full", "changed", "region"}:
            raise ValueError(f"Unsupported scope: {scope}. Use full, changed, or region.")
        normalized_focus = str(focus or "").strip() or None
        if normalized_scope == "region" and not normalized_focus:
            raise ValueError("focus is required when scope=region.")

        files = self._resolve_scan_files(
            scope=normalized_scope,
            focus=normalized_focus,
            max_files=max_files,
        )

        findings: list[ScanFinding] = []
        total_lines = 0
        test_file_count = 0

        for path in files:
            rel = _normalize_rel_path(path, self.workspace)
            lower_name = path.name.lower()
            if any(token in lower_name for token in _TEST_HINTS):
                test_file_count += 1

            try:
                content = _safe_read_text(path)
            except OSError:
                findings.append(
                    ScanFinding(
                        severity="high",
                        category="io",
                        file=rel,
                        line=1,
                        message="Unable to read file for scan",
                        evidence="read_text_failed",
                        recommendation="Check file permissions and encoding.",
                    )
                )
                continue

            lines = content.splitlines()
            total_lines += len(lines)
            findings.extend(self._scan_content(rel, lines))

            complexity_score = sum(
                line.count(" if ") + line.count(" for ") + line.count(" while ") + line.count(" except ")
                for line in lines
            )
            if complexity_score >= 180:
                findings.append(
                    ScanFinding(
                        severity="medium",
                        category="maintainability",
                        file=rel,
                        line=1,
                        message="File complexity appears high",
                        evidence=f"complexity_score={complexity_score}",
                        recommendation="Refactor into smaller functions/modules with clearer boundaries.",
                    )
                )

            if len(lines) > 900:
                findings.append(
                    ScanFinding(
                        severity="medium",
                        category="maintainability",
                        file=rel,
                        line=1,
                        message="File is very large",
                        evidence=f"lines={len(lines)}",
                        recommendation="Split file into focused modules to reduce change risk.",
                    )
                )

        if normalized_scope == "full" and test_file_count == 0:
            findings.append(
                ScanFinding(
                    severity="high",
                    category="test_coverage",
                    file="(workspace)",
                    line=1,
                    message="No test files detected in source scan",
                    evidence="test_file_count=0",
                    recommendation="Add unit/integration tests before expanding feature scope.",
                )
            )

        findings = findings[: max(1, max_findings)]
        summary = self._build_scan_summary(findings, len(files), total_lines)

        return {
            "scope": normalized_scope,
            "focus": normalized_focus or "",
            "summary": summary,
            "findings": [item.to_dict() for item in findings],
            "recommendations": self._derive_recommendations(findings),
        }

    def check_region(
        self,
        *,
        file_path: str | None = None,
        function_name: str | None = None,
        line_range: tuple[int, int] | None = None,
    ) -> dict[str, Any]:
        target_path = self._resolve_region_target(file_path=file_path, function_name=function_name)
        if not target_path:
            raise FileNotFoundError("Unable to locate region target file.")

        rel = _normalize_rel_path(target_path, self.workspace)
        content = _safe_read_text(target_path)
        lines = content.splitlines()

        start_line, end_line = self._resolve_line_window(
            lines=lines,
            function_name=function_name,
            line_range=line_range,
        )
        selected = lines[start_line - 1 : end_line]

        findings = self._scan_content(rel, selected, offset=start_line - 1)
        summary = self._build_scan_summary(findings, file_count=1, total_lines=len(selected))

        return {
            "file": rel,
            "function_name": function_name or "",
            "line_range": {"start": start_line, "end": end_line},
            "summary": summary,
            "findings": [item.to_dict() for item in findings],
        }

    def get_trace(self, *, trace_id: str, limit: int = 300) -> dict[str, Any]:
        normalized_trace_id = str(trace_id or "").strip()
        if not normalized_trace_id:
            raise ValueError("trace_id is required")

        events = query_by_trace_id(
            runtime_root=str(self.runtime_root),
            trace_id=normalized_trace_id,
            limit=max(1, min(_safe_int(limit, 300), 2000)),
        )

        run_ids = sorted(
            {str((event.get("task") or {}).get("run_id") or "").strip() for event in events if isinstance(event, dict)}
            - {""}
        )
        task_ids = sorted(
            {str((event.get("task") or {}).get("task_id") or "").strip() for event in events if isinstance(event, dict)}
            - {""}
        )

        timeline = self._build_timeline(events, limit=min(len(events), 400))
        first_ts = timeline[0]["timestamp"] if timeline else ""
        last_ts = timeline[-1]["timestamp"] if timeline else ""

        return {
            "trace_id": normalized_trace_id,
            "event_count": len(events),
            "run_ids": run_ids,
            "task_ids": task_ids,
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "timeline": timeline,
        }

    def _load_events(
        self,
        *,
        run_id: str | None,
        task_id: str | None,
        limit: int,
    ) -> list[dict[str, Any]]:
        if run_id:
            return list(query_by_run_id(str(self.runtime_root), str(run_id), limit=limit))
        if task_id:
            return list(query_by_task_id(str(self.runtime_root), str(task_id), limit=limit))
        return list(query_events(str(self.runtime_root), limit=limit))

    def _pick_failure_event(
        self,
        events: Sequence[dict[str, Any]],
        *,
        error_hint: str | None,
    ) -> dict[str, Any] | None:
        if not events:
            return None

        hint = str(error_hint or "").strip().lower()
        if hint:
            for event in reversed(events):
                if hint in _extract_event_error(event).lower():
                    return event

        for event in reversed(events):
            event_type = str(event.get("event_type") or "")
            if event_type in {"task_failed", "security_violation"}:
                return event
            action = event.get("action")
            if isinstance(action, dict) and str(action.get("result") or "").lower() == "failure":
                return event
        return None

    def _build_phase_hop(self, event: dict[str, Any]) -> dict[str, Any]:
        event_type = str(event.get("event_type") or "").strip().lower()
        role = str((event.get("source") or {}).get("role") or "").strip().lower()

        phase = "unknown"
        if role == "pm":
            phase = "pm_planning"
        elif role in {"architect", "chief_engineer"}:
            phase = "architecture_design"
        elif role == "director":
            phase = "director_execution"
        elif role == "qa":
            phase = "qa_gate"

        if event_type in {"tool_execution"}:
            phase = "tool_execution"
        elif event_type in {"verification", "audit_verdict"}:
            phase = "qa_verification"
        elif event_type == "llm_call":
            phase = "llm_inference"
        elif event_type == "security_violation":
            phase = "security_policy"

        return {
            "phase": phase,
            "event_type": event_type,
            "role": role,
            "timestamp": str(event.get("timestamp") or ""),
            "task_id": str((event.get("task") or {}).get("task_id") or ""),
            "run_id": str((event.get("task") or {}).get("run_id") or ""),
        }

    def _collect_evidence(
        self,
        *,
        events: Sequence[dict[str, Any]],
        failure_event: dict[str, Any],
        time_range: str,
    ) -> dict[str, Any]:
        failure_ts = _parse_iso_timestamp(failure_event.get("timestamp")) or datetime.now(timezone.utc)
        window = _parse_time_range(time_range)
        start_ts = failure_ts - window

        selected: list[dict[str, Any]] = []
        for event in events:
            ts = _parse_iso_timestamp(event.get("timestamp"))
            if ts is None:
                continue
            if ts < start_ts or ts > failure_ts:
                continue
            selected.append(event)

        tool_failures = 0
        verification_failures = 0
        llm_failures = 0
        files: set[str] = set()

        for event in selected:
            event_type = str(event.get("event_type") or "")
            result = str((event.get("action") or {}).get("result") or "").lower()
            resource_path = str((event.get("resource") or {}).get("path") or "").strip()
            if resource_path:
                files.add(resource_path)
            if result == "failure" and event_type == "tool_execution":
                tool_failures += 1
            if result == "failure" and event_type == "verification":
                verification_failures += 1
            if result == "failure" and event_type == "llm_call":
                llm_failures += 1

        signals = [
            {"key": "window_event_count", "value": len(selected)},
            {"key": "tool_failures", "value": tool_failures},
            {"key": "verification_failures", "value": verification_failures},
            {"key": "llm_failures", "value": llm_failures},
            {"key": "affected_files", "value": sorted(files)[:30]},
        ]
        return {
            "signals": signals,
            "window_event_count": len(selected),
            "tool_failures": tool_failures,
            "verification_failures": verification_failures,
            "llm_failures": llm_failures,
        }

    def _infer_root_cause(
        self,
        *,
        failure_event: dict[str, Any] | None,
        error_hint: str | None,
        evidence: dict[str, Any],
    ) -> dict[str, Any]:
        evidence_signal = " ".join(
            f"{item.get('key')}={item.get('value')}" for item in evidence.get("signals", []) if isinstance(item, dict)
        )
        error_text = " ".join(
            filter(
                None,
                [
                    str(error_hint or ""),
                    _extract_event_error(failure_event or {}),
                    evidence_signal,
                ],
            )
        ).lower()

        for category, patterns, fix_suggestion, confidence in _FAILURE_CATEGORY_PATTERNS:
            if any(pattern in error_text for pattern in patterns):
                return {
                    "category": category,
                    "description": f"Detected by pattern match in failure evidence: {category}",
                    "confidence": confidence,
                    "fix_suggestion": fix_suggestion,
                }

        if evidence.get("tool_failures", 0) > 0:
            return {
                "category": "tool_execution_failure",
                "description": "Tool execution events failed in failure window.",
                "confidence": 0.72,
                "fix_suggestion": "Inspect tool input normalization and command authorization rules.",
            }
        if evidence.get("verification_failures", 0) > 0:
            return {
                "category": "quality_gate_failure",
                "description": "Verification stage failed before completion.",
                "confidence": 0.7,
                "fix_suggestion": "Reproduce failing QA checks and fix deterministic breakpoints first.",
            }
        return {
            "category": "unknown",
            "description": "No deterministic root-cause pattern matched.",
            "confidence": 0.35,
            "fix_suggestion": "Collect more runtime evidence and replay with debug-level audit enabled.",
        }

    def _determine_action(
        self,
        *,
        root_cause_payload: dict[str, Any] | None,
        failure_event: dict[str, Any] | None,
        has_failure: bool,
    ) -> str:
        if not has_failure:
            return "no_failure_detected"
        if not root_cause_payload:
            return "collect_more_evidence"

        category = str(root_cause_payload.get("category") or "unknown")
        mapping = {
            "permission_denied": "retry_with_policy_fix",
            "timeout": "retry_with_timeout_adjustment",
            "missing_dependency": "install_missing_dependencies",
            "verification_failure": "fix_tests_and_rerun",
            "prompt_leakage": "sanitize_output_and_rerun",
            "tool_execution_failure": "repair_tool_inputs",
            "quality_gate_failure": "repair_quality_issues",
            "unknown": "manual_triage_required",
        }
        return mapping.get(category, "manual_triage_required")

    def _resolve_scan_files(
        self,
        *,
        scope: str,
        focus: str | None,
        max_files: int,
    ) -> list[Path]:
        normalized_limit = max(1, _safe_int(max_files, 800))

        if scope == "region":
            if not focus:
                raise ValueError("focus is required when scope=region.")
            target = self._resolve_workspace_file(focus)
            if not target.exists() or not target.is_file():
                raise FileNotFoundError(f"Region focus not found: {focus}")
            return [target]

        if scope == "changed":
            changed = self._git_changed_files()
            return changed[:normalized_limit]

        files: list[Path] = []
        for path in self.workspace.rglob("*"):
            if len(files) >= normalized_limit:
                break
            if not path.is_file():
                continue
            if path.suffix.lower() not in _SOURCE_EXTENSIONS:
                continue
            if ".git" in path.parts or ".polaris" in path.parts:
                continue
            files.append(path)
        return files

    def _git_changed_files(self) -> list[Path]:
        try:
            cmd_svc = CommandExecutionService(str(self.workspace))
            request = CommandRequest(
                executable="git",
                args=["status", "--porcelain"],
                cwd=str(self.workspace),
                timeout_seconds=8,
            )
            completed_result = cmd_svc.run(request)
        except (RuntimeError, ValueError):
            logger.debug(
                "audit_diagnosis: git status failed for changed-files scan: workspace=%s",
                self.workspace,
                exc_info=True,
            )
            return []

        if completed_result.get("returncode", -1) != 0:
            return []

        paths: list[Path] = []
        for raw_line in completed_result.get("stdout", "").splitlines():
            line = raw_line.rstrip()
            if len(line) < 4:
                continue
            payload = line[3:].strip()
            if " -> " in payload:
                payload = payload.split(" -> ", 1)[1].strip()
            if not payload:
                continue
            candidate = self._resolve_workspace_file(payload)
            if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in _SOURCE_EXTENSIONS:
                paths.append(candidate)
        return paths

    def _resolve_workspace_file(self, file_path: str) -> Path:
        token = str(file_path or "").strip()
        if not token:
            raise ValueError("file path is required")
        candidate = Path(token)
        resolved = candidate.resolve() if candidate.is_absolute() else (self.workspace / candidate).resolve()
        if self.workspace not in resolved.parents and resolved != self.workspace:
            raise ValueError(f"Path outside workspace is not allowed: {token}")
        return resolved

    def _scan_content(
        self,
        rel_path: str,
        lines: Sequence[str],
        *,
        offset: int = 0,
    ) -> list[ScanFinding]:
        findings: list[ScanFinding] = []
        for index, line in enumerate(lines, start=1):
            line_no = offset + index
            stripped = line.strip()
            if not stripped:
                continue

            for category, pattern, message in _SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(
                        ScanFinding(
                            severity="critical",
                            category=category,
                            file=rel_path,
                            line=line_no,
                            message=message,
                            evidence=stripped[:260],
                            recommendation="Move secrets to environment variables or secret manager.",
                        )
                    )

            if "TODO" in line or "FIXME" in line:
                findings.append(
                    ScanFinding(
                        severity="low",
                        category="maintainability",
                        file=rel_path,
                        line=line_no,
                        message="Unresolved TODO/FIXME marker",
                        evidence=stripped[:260],
                        recommendation="Resolve TODO/FIXME or convert to tracked issue with owner/deadline.",
                    )
                )

            if len(line) > 180:
                findings.append(
                    ScanFinding(
                        severity="low",
                        category="readability",
                        file=rel_path,
                        line=line_no,
                        message="Very long line",
                        evidence=f"line_length={len(line)}",
                        recommendation="Wrap long statements to improve readability and reviewability.",
                    )
                )
        return findings

    def _build_scan_summary(
        self,
        findings: Sequence[ScanFinding],
        file_count: int,
        total_lines: int,
    ) -> dict[str, Any]:
        counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
        for finding in findings:
            if finding.severity in counts:
                counts[finding.severity] += 1

        score = 100
        score -= counts["critical"] * 25
        score -= counts["high"] * 15
        score -= counts["medium"] * 6
        score -= counts["low"] * 2
        score = _clamp(score, 0, 100)

        return {
            "score": score,
            "files_scanned": file_count,
            "lines_scanned": total_lines,
            "findings_total": len(findings),
            "severity": counts,
        }

    def _derive_recommendations(self, findings: Sequence[ScanFinding]) -> list[str]:
        if not findings:
            return ["No high-risk issues detected by static audit scan."]

        categories = {item.category for item in findings}
        recommendations: list[str] = []
        if "hardcoded_secret" in categories or "private_key" in categories:
            recommendations.append("Rotate exposed credentials and move secrets to secure configuration.")
        if "test_coverage" in categories:
            recommendations.append("Add missing tests before shipping new changes.")
        if "maintainability" in categories:
            recommendations.append("Prioritize refactor tasks for high-churn modules.")
        if "io" in categories:
            recommendations.append("Fix unreadable files and enforce UTF-8 text policy.")
        if not recommendations:
            recommendations.append("Address listed findings and re-run QA scan.")
        return recommendations

    def _resolve_region_target(
        self,
        *,
        file_path: str | None,
        function_name: str | None,
    ) -> Path | None:
        if file_path:
            target = self._resolve_workspace_file(file_path)
            if target.exists() and target.is_file():
                return target
            return None

        token = str(function_name or "").strip()
        if not token:
            return None

        pattern = re.compile(
            rf"(^\s*def\s+{re.escape(token)}\s*\()|(^\s*function\s+{re.escape(token)}\s*\()|(^\s*{re.escape(token)}\s*=\s*\()",
            re.MULTILINE,
        )
        for path in self._resolve_scan_files(scope="full", focus=None, max_files=600):
            try:
                content = _safe_read_text(path, max_chars=120_000)
            except OSError:
                continue
            if pattern.search(content):
                return path
        return None

    def _resolve_line_window(
        self,
        *,
        lines: Sequence[str],
        function_name: str | None,
        line_range: tuple[int, int] | None,
    ) -> tuple[int, int]:
        total = max(1, len(lines))

        if line_range:
            start = _clamp(_safe_int(line_range[0], 1), 1, total)
            end = _clamp(_safe_int(line_range[1], total), start, total)
            return start, end

        token = str(function_name or "").strip()
        if token:
            signature_pattern = re.compile(
                rf"(^\s*def\s+{re.escape(token)}\s*\()|(^\s*function\s+{re.escape(token)}\s*\()|(^\s*{re.escape(token)}\s*=\s*\()"
            )
            start_idx: int | None = None
            for idx, line in enumerate(lines):
                if signature_pattern.search(line):
                    start_idx = idx
                    break
            if start_idx is not None:
                end_idx = min(total - 1, start_idx + 220)
                next_decl = re.compile(r"^\s*(def|class|function)\s+")
                for idx in range(start_idx + 1, min(total, start_idx + 220)):
                    if next_decl.search(lines[idx]):
                        end_idx = idx - 1
                        break
                return start_idx + 1, end_idx + 1

        return 1, total

    def _build_timeline(
        self,
        events: Sequence[dict[str, Any]],
        *,
        limit: int,
    ) -> list[dict[str, Any]]:
        timeline: list[dict[str, Any]] = []
        for event in events:
            timestamp = str(event.get("timestamp") or "")
            source = event.get("source") if isinstance(event.get("source"), dict) else {}
            action = event.get("action") if isinstance(event.get("action"), dict) else {}
            task = event.get("task") if isinstance(event.get("task"), dict) else {}
            timeline.append(
                {
                    "timestamp": timestamp,
                    "event_type": str(event.get("event_type") or ""),
                    "role": str(source.get("role") or "") if isinstance(source, dict) else "",
                    "result": str(action.get("result") or "") if isinstance(action, dict) else "",
                    "action": str(action.get("name") or "") if isinstance(action, dict) else "",
                    "task_id": str(task.get("task_id") or "") if isinstance(task, dict) else "",
                    "run_id": str(task.get("run_id") or "") if isinstance(task, dict) else "",
                }
            )

        timeline.sort(key=lambda item: item.get("timestamp") or "")
        if len(timeline) > limit:
            return timeline[-limit:]
        return timeline
