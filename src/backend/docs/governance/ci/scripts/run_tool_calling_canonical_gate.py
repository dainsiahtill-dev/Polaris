"""Canonical tool-calling governance gate.

This gate validates tool identity at raw stream-event level so alias mapping
cannot hide missing canonical tool coverage.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_MODE_AUDIT_ONLY = "audit-only"
_MODE_HARD_FAIL = "hard-fail"
_SUPPORTED_MODES = (_MODE_AUDIT_ONLY, _MODE_HARD_FAIL)


def _non_empty(value: Any) -> str:
    return str(value or "").strip()


def _get_polaris_imports():
    """Lazy import of polaris modules after sys.path is set up."""
    # Ensure polaris is on the import path when running as a standalone script.
    _BACKEND_ROOT = Path(__file__).resolve().parents[4]
    if str(_BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(_BACKEND_ROOT))

    from polaris.kernelone.llm.toolkit.tool_normalization import (
        normalize_tool_name,
    )
    from polaris.kernelone.tool_execution.contracts import canonicalize_tool_name, supported_tool_names

    return normalize_tool_name, canonicalize_tool_name, supported_tool_names


# Canonical tool whitelist - tools that are NOT allowed to be aliased to other tools
# This list defines tools that MUST NOT be cross-tool mapped


def _is_cross_tool_mapping(
    requested: str,
    executed: str,
    normalize_tool_name_func: Any,
    canonical_tools: set[str],
) -> bool:
    """Detect forbidden cross-tool semantic mapping.

    A cross-tool mapping occurs when:
    1. requested != executed (names are different)
    2. requested is a canonical tool (in canonical_tools)
    3. The mapping is not a same-tool alias (e.g., execute_command variants)

    Cross-tool mappings are FORBIDDEN because they change tool semantics.
    Example (FORBIDDEN): repo_read_head -> read_file (different semantics)
    Example (ALLOWED): run_command -> execute_command (same tool, different invocation)
    """
    if requested == executed:
        return False

    # Normalize both names
    norm_requested = normalize_tool_name_func(requested.lower())
    norm_executed = normalize_tool_name_func(executed.lower())

    # If names are now the same after normalization, it's a same-tool alias (OK)
    if norm_requested == norm_executed:
        return False

    # Check if requested is in canonical tools
    requested_lower = requested.lower()
    if requested_lower not in canonical_tools:
        # Not a canonical tool, we don't enforce strict mapping rules
        return False

    # Check if executed is in canonical tools
    executed_lower = executed.lower()
    if executed_lower not in canonical_tools:
        # Executed tool is not canonical - this is a downgrade
        return True

    # Both are canonical but different - this is cross-tool mapping
    return True


def _detect_cross_tool_mapping_issues(
    raw_tools: list[str],
    observed_tools: list[str],
    case_id: str,
    role: str,
    normalize_tool_name_func: Any,
    canonical_tools: set[str],
) -> list[GateIssue]:
    """Detect cross-tool semantic mapping violations.

    Cross-tool mapping is FORBIDDEN. If a canonical tool is requested
    but a different canonical tool is executed, this is a policy violation.
    """
    issues = []

    for index, (raw_tool, observed_tool) in enumerate(zip(raw_tools, observed_tools)):
        if _is_cross_tool_mapping(raw_tool, observed_tool, normalize_tool_name_func, canonical_tools):
            issues.append(
                GateIssue(
                    case_id=case_id,
                    role=role,
                    category="forbidden_cross_tool_mapping",
                    message=(
                        f"FORDIDDEN: canonical tool `{raw_tool}` was mapped to different "
                        f"canonical tool `{observed_tool}` - this changes tool semantics"
                    ),
                    evidence={
                        "index": index,
                        "requested_tool": raw_tool,
                        "executed_tool": observed_tool,
                        "severity": "P0",
                        "reason": "cross_tool_semantic_mapping_forbidden",
                    },
                )
            )

    return issues


def _mapping_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _tuple_of_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return tuple()
    output: list[str] = []
    for item in value:
        token = _non_empty(item)
        if token:
            output.append(token)
    return tuple(output)


def _event_value(event: Mapping[str, Any], key: str) -> Any:
    direct = event.get(key)
    if direct is not None:
        return direct
    nested = event.get("data")
    if isinstance(nested, Mapping):
        return nested.get(key)
    return None


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_latest_matrix_report(workspace: Path) -> Path:
    # Import here to avoid module-level circular/deferred import issues
    from polaris.kernelone.storage.layout import resolve_runtime_path

    root = Path(resolve_runtime_path(str(workspace), "runtime/llm_evaluations"))
    candidates = sorted(
        root.rglob("TOOL_CALLING_MATRIX_REPORT.json"),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise FileNotFoundError(f"no TOOL_CALLING_MATRIX_REPORT.json found under: {root}")
    return candidates[0]


def _extract_raw_tool_calls(raw_events: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_events, list):
        return []
    calls: list[dict[str, Any]] = []
    for item in raw_events:
        event = _mapping_dict(item)
        event_type = _non_empty(event.get("type")).lower()
        if event_type != "tool_call":
            continue
        calls.append(
            {
                "tool": _non_empty(_event_value(event, "tool") or _event_value(event, "name")),
                "args": _mapping_dict(_event_value(event, "args") or _event_value(event, "arguments")),
            }
        )
    return calls


@dataclass(frozen=True)
class GateIssue:
    case_id: str
    role: str
    category: str
    message: str
    evidence: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "role": self.role,
            "category": self.category,
            "message": self.message,
            "evidence": dict(self.evidence or {}),
        }


def _required_tools_from_case(case_payload: Mapping[str, Any]) -> tuple[str, ...]:
    case = _mapping_dict(case_payload.get("case"))
    judge = _mapping_dict(case.get("judge"))
    stream = _mapping_dict(judge.get("stream"))
    return _tuple_of_strings(stream.get("required_tools"))


def _evaluate_case(
    case_payload: Mapping[str, Any],
    *,
    canonical_tools: set[str],
    normalize_tool_name_func: Any,
    canonicalize_tool_name_func: Any,
) -> list[GateIssue]:
    issues: list[GateIssue] = []
    case = _mapping_dict(case_payload.get("case"))
    stream_observed = _mapping_dict(case_payload.get("stream_observed"))
    case_id = _non_empty(case.get("case_id"))
    role = _non_empty(case.get("role"))

    raw_tool_calls = _extract_raw_tool_calls(case_payload.get("raw_events"))
    observed_tool_calls = list(stream_observed.get("tool_calls") or [])
    raw_tools = [_non_empty(item.get("tool")) for item in raw_tool_calls if _non_empty(item.get("tool"))]
    observed_tools = [
        _non_empty(_mapping_dict(item).get("tool"))
        for item in observed_tool_calls
        if _non_empty(_mapping_dict(item).get("tool"))
    ]

    # P0: Detect FORBIDDEN cross-tool semantic mapping
    cross_tool_issues = _detect_cross_tool_mapping_issues(
        raw_tools, observed_tools, case_id, role, normalize_tool_name_func, canonical_tools
    )
    issues.extend(cross_tool_issues)

    required_tools = _required_tools_from_case(case_payload)
    for required_tool in required_tools:
        if required_tool not in raw_tools:
            issues.append(
                GateIssue(
                    case_id=case_id,
                    role=role,
                    category="missing_required_raw_tool",
                    message=f"required tool `{required_tool}` not found in raw stream tool calls",
                    evidence={
                        "required_tool": required_tool,
                        "raw_tools": raw_tools,
                    },
                )
            )

    if len(raw_tools) != len(observed_tools):
        issues.append(
            GateIssue(
                case_id=case_id,
                role=role,
                category="raw_observed_count_mismatch",
                message="raw tool-call count differs from stream_observed tool-call count",
                evidence={
                    "raw_count": len(raw_tools),
                    "observed_count": len(observed_tools),
                    "raw_tools": raw_tools,
                    "observed_tools": observed_tools,
                },
            )
        )

    for index, raw_tool in enumerate(raw_tools):
        canonical_raw = canonicalize_tool_name_func(raw_tool, keep_unknown=True)
        if raw_tool not in canonical_tools and canonical_raw in canonical_tools and canonical_raw != raw_tool:
            issues.append(
                GateIssue(
                    case_id=case_id,
                    role=role,
                    category="alias_tool_name_used",
                    message=(
                        f"raw tool `{raw_tool}` is not canonical; mapped canonical name would be `{canonical_raw}`"
                    ),
                    evidence={
                        "raw_tool": raw_tool,
                        "canonical_raw": canonical_raw,
                        "index": index,
                    },
                )
            )

        if index >= len(observed_tools):
            continue
        observed_tool = observed_tools[index]
        if raw_tool != observed_tool:
            issues.append(
                GateIssue(
                    case_id=case_id,
                    role=role,
                    category="raw_observed_name_drift",
                    message="raw tool name differs from stream_observed tool name",
                    evidence={
                        "index": index,
                        "raw_tool": raw_tool,
                        "observed_tool": observed_tool,
                        "canonical_raw": canonical_raw,
                        "drift_type": ("alias_mapping" if canonical_raw == observed_tool else "shape_or_parser_drift"),
                    },
                )
            )

    return issues


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run canonical tool-calling governance gate.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root path (default: current directory).",
    )
    parser.add_argument(
        "--input-report",
        default="",
        help=("Path to TOOL_CALLING_MATRIX_REPORT.json. If omitted, the latest report under workspace is used."),
    )
    parser.add_argument(
        "--role",
        default="director",
        help="Evaluate only cases for this role (default: director, use all for all roles).",
    )
    parser.add_argument(
        "--mode",
        choices=_SUPPORTED_MODES,
        default=_MODE_HARD_FAIL,
        help="Gate mode: audit-only or hard-fail.",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional path to write gate JSON report.",
    )
    return parser.parse_args()


def main() -> int:
    normalize_tool_name, canonicalize_tool_name, supported_tool_names = _get_polaris_imports()

    args = _parse_args()
    workspace = Path(args.workspace).resolve()
    role_filter = _non_empty(args.role).lower() or "director"
    canonical_tools = set(supported_tool_names())

    if _non_empty(args.input_report):
        input_report = Path(args.input_report)
        if not input_report.is_absolute():
            input_report = (workspace / input_report).resolve()
    else:
        try:
            input_report = _find_latest_matrix_report(workspace)
        except FileNotFoundError:
            print(f"[warning] no TOOL_CALLING_MATRIX_REPORT.json found under workspace; running with empty case set")
            input_report = None

    if input_report is None:
        payload = {"cases": []}
    else:
        payload = _load_json(input_report)

    all_cases = list(payload.get("cases") or [])
    target_cases: list[dict[str, Any]] = []
    for item in all_cases:
        case_payload = _mapping_dict(item)
        case = _mapping_dict(case_payload.get("case"))
        case_role = _non_empty(case.get("role")).lower()
        if role_filter == "all" or role_filter == case_role:
            target_cases.append(case_payload)

    issues: list[GateIssue] = []
    for case_payload in target_cases:
        issues.extend(
            _evaluate_case(
                case_payload,
                canonical_tools=canonical_tools,
                normalize_tool_name_func=normalize_tool_name,
                canonicalize_tool_name_func=canonicalize_tool_name,
            )
        )

    report = {
        "version": 1,
        "gate": "tool_calling_canonical_identity",
        "mode": args.mode,
        "workspace": str(workspace),
        "input_report": str(input_report),
        "role_filter": role_filter,
        "total_cases": len(all_cases),
        "target_case_count": len(target_cases),
        "issue_count": len(issues),
        "issues": [item.to_dict() for item in issues],
    }
    if role_filter != "all" and not target_cases:
        report["issue_count"] = len(issues) + 1
        report["issues"].append(
            GateIssue(
                case_id="",
                role=role_filter,
                category="target_role_not_found",
                message=f"no cases found for role `{role_filter}` in input report",
                evidence={
                    "available_roles": sorted({_non_empty(_mapping_dict(c.get("case")).get("role")) for c in all_cases})
                },
            ).to_dict()
        )

    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if _non_empty(args.report):
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = workspace / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered + "\n", encoding="utf-8")

    print(rendered)
    if args.mode == _MODE_HARD_FAIL and int(report.get("issue_count") or 0) > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
