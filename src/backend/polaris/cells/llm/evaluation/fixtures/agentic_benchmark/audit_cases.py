"""Audit script for agentic benchmark cases - detects logical defects and bugs."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Path to cases directories (audit_cases.py is in fixtures/agentic_benchmark/)
FIXTURES_ROOT = Path(__file__).parent.parent.parent / "fixtures"
CASES_DIRS = [
    FIXTURES_ROOT / "agentic_benchmark" / "cases",
    FIXTURES_ROOT / "tool_calling_matrix" / "cases",
]


def load_case(path: Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_all_cases() -> list[tuple[Path, dict[str, Any]]]:
    cases = []
    for cases_dir in CASES_DIRS:
        if cases_dir.is_dir():
            for path in sorted(cases_dir.glob("l*.json")):
                cases.append((path, load_case(path)))
    return cases


# Known valid tools in the system (from tool spec registry)
VALID_TOOLS: set[str] = {
    "repo_tree",
    "repo_rg",
    "repo_read_head",
    "repo_read_tail",
    "repo_read_slice",
    "repo_read_around",
    "search_replace",
    "append_to_file",
    "precision_edit",
    "repo_apply_diff",
    "execute_command",
    "read_file",
    "delete_file",
    "file_exists",
    "glob",
    "grep",
    "ripgrep",
    "search_code",
}


class CaseIssue:
    def __init__(self, case_id: str, severity: str, category: str, message: str, field: str = "") -> None:
        self.case_id = case_id
        self.severity = severity  # ERROR, WARNING, INFO
        self.category = category
        self.message = message
        self.field = field

    def __repr__(self) -> str:
        loc = f"[{self.field}] " if self.field else ""
        return f"[{self.severity}] {self.case_id}: {loc}{self.message}"


def audit_json_structure(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit JSON structure validity."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")

    required_fields = ["case_id", "role", "title", "prompt", "judge"]
    for field in required_fields:
        if field not in data:
            issues.append(CaseIssue(case_id, "ERROR", "STRUCTURE", f"Missing required field: {field}"))

    judge = data.get("judge", {})
    if not isinstance(judge, dict):
        issues.append(CaseIssue(case_id, "ERROR", "STRUCTURE", "judge must be a dict"))
    else:
        judge_required = ["score_threshold"]
        for field in judge_required:
            if field not in judge:
                issues.append(
                    CaseIssue(case_id, "ERROR", "STRUCTURE", f"judge missing required field: {field}", "judge")
                )

    return issues


def audit_tool_references(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit tool references in required_tools, forbidden_tools, and tool arguments."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})

    # Check required_tools
    for tool in judge.get("required_tools", []):
        if tool not in VALID_TOOLS:
            issues.append(
                CaseIssue(
                    case_id,
                    "ERROR",
                    "INVALID_TOOL",
                    f"required_tools contains unknown tool: '{tool}'",
                    "judge.required_tools",
                )
            )

    # Check forbidden_tools
    for tool in judge.get("forbidden_tools", []):
        if tool not in VALID_TOOLS:
            issues.append(
                CaseIssue(
                    case_id,
                    "ERROR",
                    "INVALID_TOOL",
                    f"forbidden_tools contains unknown tool: '{tool}'",
                    "judge.forbidden_tools",
                )
            )

    # Check tool argument rules
    for rule in judge.get("required_tool_arguments", []):
        if isinstance(rule, dict):
            for tool in rule.get("tools", []):
                if tool not in VALID_TOOLS:
                    issues.append(
                        CaseIssue(
                            case_id,
                            "ERROR",
                            "INVALID_TOOL",
                            f"required_tool_arguments references unknown tool: '{tool}'",
                            "judge.required_tool_arguments",
                        )
                    )

    for rule in judge.get("forbidden_tool_arguments", []):
        if isinstance(rule, dict):
            for tool in rule.get("tools", []):
                if tool not in VALID_TOOLS:
                    issues.append(
                        CaseIssue(
                            case_id,
                            "ERROR",
                            "INVALID_TOOL",
                            f"forbidden_tool_arguments references unknown tool: '{tool}'",
                            "judge.forbidden_tool_arguments",
                        )
                    )

    # Check for conflicts: same tool in required AND forbidden
    required = set(judge.get("required_tools", []))
    forbidden = set(judge.get("forbidden_tools", []))
    conflicts = required & forbidden
    if conflicts:
        issues.append(
            CaseIssue(
                case_id, "ERROR", "CONFLICT", f"Tools in both required_tools and forbidden_tools: {conflicts}", "judge"
            )
        )

    return issues


def audit_tool_call_bounds(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit min/max tool call consistency."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})

    min_calls = judge.get("min_tool_calls", 0)
    max_calls = judge.get("max_tool_calls")

    if not isinstance(min_calls, int):
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "TYPE",
                f"min_tool_calls must be int, got {type(min_calls).__name__}",
                "judge.min_tool_calls",
            )
        )

    if max_calls is not None and not isinstance(max_calls, int):
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "TYPE",
                f"max_tool_calls must be int or null, got {type(max_calls).__name__}",
                "judge.max_tool_calls",
            )
        )

    if isinstance(min_calls, int) and max_calls is not None and isinstance(max_calls, int):
        if min_calls > max_calls:
            issues.append(
                CaseIssue(
                    case_id, "ERROR", "LOGIC", f"min_tool_calls ({min_calls}) > max_tool_calls ({max_calls})", "judge"
                )
            )

        if min_calls > 0 and not judge.get("required_tools"):
            issues.append(
                CaseIssue(
                    case_id, "WARNING", "LOGIC", f"min_tool_calls={min_calls} but no required_tools specified", "judge"
                )
            )

    return issues


def audit_output_substrings(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit required_output_substrings and forbidden_output_substrings for conflicts."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})

    required = set(judge.get("required_output_substrings", []))
    forbidden = set(judge.get("forbidden_output_substrings", []))

    conflicts = required & forbidden
    if conflicts:
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "CONFLICT",
                f"Substrings in both required_output_substrings and forbidden_output_substrings: {conflicts}",
                "judge",
            )
        )

    return issues


def audit_prompt_logic(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit prompt vs judge logic consistency."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    prompt = data.get("prompt", "")
    judge = data.get("judge", {})

    # Check if forbidden tools are used in prompt but not forbidden in judge
    dangerous_patterns = [
        ("删除所有", "delete"),
        ("delete all", "delete"),
        ("rm ", "delete"),
        ("rm -rf", "delete"),
    ]
    for pattern, action in dangerous_patterns:
        if pattern.lower() in prompt.lower() and "execute_command" not in judge.get("forbidden_tools", []):
            issues.append(
                CaseIssue(
                    case_id,
                    "WARNING",
                    "LOGIC",
                    f"Prompt mentions '{pattern}' but execute_command not in forbidden_tools",
                    "judge.forbidden_tools",
                )
            )

    # Check if required_tools are actually mentioned or needed in prompt
    required_tools = judge.get("required_tools", [])
    if not required_tools and judge.get("min_tool_calls", 0) > 0:
        issues.append(
            CaseIssue(case_id, "WARNING", "LOGIC", "min_tool_calls > 0 but no required_tools specified", "judge")
        )

    return issues


def audit_validators(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit validator names."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})

    known_validators = {
        "stream_nonstream_parity",
        "focus_recovery_check",
        "structured_output_required",
        "no_distraction_tool_calls",
        "goal_persistence_check",
        "safety_check",
        "chinese_output_required",
        "ordered_tool_sequence",
        "stepwise_planning",
        "self_verification_check",
        "hallucination_refusal_check",
        "fact_anchoring_check",
        "mandatory_read_check",
        "read_before_write_check",
        "cross_file_consistency_check",
        "cross_file_verification_check",
        "self_consistency_check",
        "history_verification_check",
        "multilingual_check",
        "path_validation_check",
        "integrity_verification_check",
        "integrity_check",
        "syntax_correctness_check",
        "correction_response_check",
        "instruction_adherence_check",
        "critical_case",
        "no_prompt_leakage",
        "no_hallucinated_paths",
        "require_no_error",
        "require_no_tool_calls",
        "first_call_reject_unknown_args",
        "parity_compare_mode_set",
        "mandatory_tool_call_check",
        # Self-correction validators
        "self_correction_after_failure",
        "self_correction_after_empty_result",
        "handles_duplicate_filenames",
        "handles_sequence_break",
    }

    for validator in judge.get("validators", []):
        if validator not in known_validators and not validator.startswith("validator:"):
            issues.append(
                CaseIssue(
                    case_id, "WARNING", "UNKNOWN_VALIDATOR", f"Unknown validator: '{validator}'", "judge.validators"
                )
            )

    return issues


def audit_case_id_naming(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit case_id naming convention."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "")

    # Expected pattern: l[0-9]_[a-z_]+
    if not re.match(r"^l[0-9]_[a-z_]+$", case_id):
        issues.append(
            CaseIssue(
                case_id,
                "WARNING",
                "NAMING",
                f"case_id '{case_id}' doesn't follow naming convention (l[0-9]_[a-z_]+)",
                "case_id",
            )
        )

    return issues


def audit_role_consistency(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit role consistency."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    role = data.get("role", "")

    valid_roles = {
        "director",
        "pm",
        "architect",
        "chief_engineer",
        "qa",
        "scout",
        "all",
        "default",
        "benchmark",
        "agentic",
    }

    if role.lower() not in valid_roles:
        issues.append(CaseIssue(case_id, "WARNING", "ROLE", f"Unknown role: '{role}'", "role"))

    return issues


def audit_score_threshold(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit score_threshold values."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})
    threshold = judge.get("score_threshold", 0.75)

    if not isinstance(threshold, (int, float)):
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "TYPE",
                f"score_threshold must be numeric, got {type(threshold).__name__}",
                "judge.score_threshold",
            )
        )
    elif threshold < 0.0 or threshold > 1.0:
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "RANGE",
                f"score_threshold must be between 0.0 and 1.0, got {threshold}",
                "judge.score_threshold",
            )
        )

    return issues


def audit_forbidden_output_logic(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit logic around forbidden output substrings."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})
    prompt = data.get("prompt", "")

    forbidden = judge.get("forbidden_output_substrings", [])

    # Check if prompt naturally contains forbidden substrings that would cause false failures
    # e.g., if prompt asks about "TODO" but TODO is in forbidden_output
    if "TODO" in prompt and "TODO" in forbidden:
        issues.append(
            CaseIssue(
                case_id,
                "WARNING",
                "LOGIC",
                "Prompt mentions 'TODO' but 'TODO' is in forbidden_output_substrings - may cause false failures",
                "judge.forbidden_output_substrings",
            )
        )

    return issues


def audit_empty_required_tools(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit cases where min_tool_calls > 0 but required_tools is empty."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})

    min_calls = judge.get("min_tool_calls", 0)
    required = judge.get("required_tools", [])
    forbidden = judge.get("forbidden_tools", [])

    if min_calls > 0 and not required and not forbidden:
        issues.append(
            CaseIssue(
                case_id,
                "ERROR",
                "LOGIC",
                f"min_tool_calls={min_calls} but no required_tools or forbidden_tools specified",
                "judge",
            )
        )

    # Special case: if forbidden_tools has execute_command and min_calls is 0,
    # the case is likely checking refusal behavior - this is valid
    return issues


def audit_refusal_cases(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit cases that expect refusal behavior."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})
    prompt = data.get("prompt", "")

    # Check for refusal cases - if prompt asks for dangerous operation
    # Skip self-conradiction cases which intentionally mention dangerous ops then change
    if "然后" in prompt or "but" in prompt.lower() or "however" in prompt.lower():
        return issues  # Likely a self-contradiction or sequential task case

    dangerous_keywords = ["删除", "删除所有", "delete all", "rm ", "drop "]
    for kw in dangerous_keywords:
        if kw.lower() in prompt.lower():
            # This should be a refusal case
            if judge.get("min_tool_calls", 0) > 0:
                issues.append(
                    CaseIssue(
                        case_id,
                        "WARNING",
                        "LOGIC",
                        f"Prompt contains '{kw}' but min_tool_calls > 0 - may not be a proper refusal case",
                        "judge",
                    )
                )

    return issues


def audit_required_output_overlap(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit cases where required_output might conflict with forbidden_output in edge cases."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})
    required = set(judge.get("required_output_substrings", []))
    forbidden = set(judge.get("forbidden_output_substrings", []))

    # Check for partial overlaps that could cause issues
    for req in required:
        for forb in forbidden:
            if (req in forb or forb in req) and req != forb:  # Already caught exact overlaps
                issues.append(
                    CaseIssue(
                        case_id,
                        "WARNING",
                        "LOGIC",
                        f"Partial overlap between required '{req}' and forbidden '{forb}'",
                        "judge",
                    )
                )

    return issues


def audit_strict_refusal_cases(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit strict refusal cases for proper configuration."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")
    judge = data.get("judge", {})
    prompt = data.get("prompt", "")

    # If case expects zero tool calls (refusal), verify forbidden_tools is set properly
    if judge.get("min_tool_calls", 0) == 0 and judge.get("max_tool_calls", 1) == 0:
        forbidden = judge.get("forbidden_tools", [])
        # Refusal cases should have dangerous tools in forbidden list
        dangerous = ["execute_command", "delete_file", "append_to_file"]
        has_dangerous = any(t in dangerous for t in forbidden)
        if not has_dangerous and not any(kw in prompt.lower() for kw in ["删除", "delete", "rm "]):
            issues.append(
                CaseIssue(
                    case_id,
                    "WARNING",
                    "LOGIC",
                    "Zero tool calls expected but no dangerous tools in forbidden_tools",
                    "judge",
                )
            )

    return issues


def audit_workspace_fixture_consistency(path: Path, data: dict[str, Any]) -> list[CaseIssue]:
    """Audit workspace_fixture and prompt file reference consistency."""
    issues: list[CaseIssue] = []
    case_id = data.get("case_id", "UNKNOWN")

    # Skip check for self-correction / wrong-path test cases - they intentionally use wrong paths
    if "self_correction" in case_id or "_wrong_" in case_id or "_error" in case_id:
        return issues

    # Find which fixture directory this case is in
    case_dir = path.parent
    if case_dir.name == "cases":
        parent_dir = case_dir.parent
        if parent_dir.name in ("agentic_benchmark", "tool_calling_matrix"):
            # For tool_calling_matrix: fixtures_dir/tool_calling_matrix -> fixtures_dir
            # For agentic_benchmark: fixtures_dir/agentic_benchmark -> fixtures_dir
            parent_dir.parent.parent / "fixtures"
            workspaces_dir = parent_dir / "workspaces"  # e.g., fixtures/tool_calling_matrix/workspaces
            workspace_fixture = data.get("workspace_fixture", "")
            if workspace_fixture:
                workspace_path = workspaces_dir / workspace_fixture
                if not workspace_path.is_dir():
                    issues.append(
                        CaseIssue(
                            case_id,
                            "ERROR",
                            "FIXTURE",
                            f"workspace_fixture '{workspace_fixture}' does not exist at {workspace_path}",
                            "workspace_fixture",
                        )
                    )
                else:
                    # Check for file references in prompt
                    prompt = data.get("prompt", "")
                    # Extract potential file/directory references from prompt
                    # Look for patterns like "config.py", "server.py", "backend/", "src/"
                    import re

                    file_patterns = re.findall(r"[\w/]+\.py\b", prompt)
                    file_patterns.extend(re.findall(r"[\w/]+\.ts\b", prompt))
                    file_patterns.extend(re.findall(r"[\w\-/]+\.json\b", prompt))

                    # Check if files mentioned in prompt exist in fixture
                    for fp in file_patterns:
                        # Normalize path
                        fp = fp.strip()
                        if not fp:
                            continue
                        # If the file path starts with a known subdir (backend/, src/, frontend/, tests/)
                        # but the actual file is at root, that's a mismatch
                        if "/" in fp or "\\" in fp:
                            # It's a path with subdirectory
                            full_path = workspace_path / fp
                            # Also check if file exists at root level (alternative)
                            filename = fp.split("/")[-1] if "/" in fp else fp.split("\\")[-1]
                            root_path = workspace_path / filename
                            if not full_path.is_file() and root_path.is_file():
                                issues.append(
                                    CaseIssue(
                                        case_id,
                                        "ERROR",
                                        "PROMPT_FIXTURE_MISMATCH",
                                        f"Prompt references '{fp}' but '{filename}' exists at fixture root, not in subdirectory. "
                                        f"Either fix prompt path or move file to match.",
                                        "prompt",
                                    )
                                )
                            elif not full_path.is_file() and not root_path.is_file():
                                issues.append(
                                    CaseIssue(
                                        case_id,
                                        "WARNING",
                                        "FILE_NOT_FOUND",
                                        f"Prompt references '{fp}' but file not found in fixture workspace",
                                        "prompt",
                                    )
                                )

    return issues


def run_audit() -> dict[str, Any]:
    """Run full audit on all cases."""
    all_issues: list[CaseIssue] = []
    cases = load_all_cases()

    for path, data in cases:
        data.get("case_id", "UNKNOWN")

        all_issues.extend(audit_json_structure(path, data))
        all_issues.extend(audit_tool_references(path, data))
        all_issues.extend(audit_tool_call_bounds(path, data))
        all_issues.extend(audit_output_substrings(path, data))
        all_issues.extend(audit_prompt_logic(path, data))
        all_issues.extend(audit_validators(path, data))
        all_issues.extend(audit_case_id_naming(path, data))
        all_issues.extend(audit_role_consistency(path, data))
        all_issues.extend(audit_score_threshold(path, data))
        all_issues.extend(audit_forbidden_output_logic(path, data))
        all_issues.extend(audit_empty_required_tools(path, data))
        all_issues.extend(audit_refusal_cases(path, data))
        all_issues.extend(audit_required_output_overlap(path, data))
        all_issues.extend(audit_strict_refusal_cases(path, data))
        all_issues.extend(audit_workspace_fixture_consistency(path, data))

    # Group by severity
    errors = [i for i in all_issues if i.severity == "ERROR"]
    warnings = [i for i in all_issues if i.severity == "WARNING"]
    infos = [i for i in all_issues if i.severity == "INFO"]

    return {
        "total_cases": len(cases),
        "total_issues": len(all_issues),
        "errors": len(errors),
        "warnings": len(warnings),
        "infos": len(infos),
        "issues": all_issues,
    }


def print_audit_report(report: dict[str, Any]) -> None:
    """Print formatted audit report."""
    print("=" * 80)
    print("BENCHMARK CASE AUDIT REPORT")
    print("=" * 80)
    print(f"Total cases reviewed: {report['total_cases']}")
    print(f"Total issues found: {report['total_issues']}")
    print(f"  - Errors: {report['errors']}")
    print(f"  - Warnings: {report['warnings']}")
    print(f"  - Info: {report['infos']}")
    print()

    if report["errors"]:
        print("ERRORS:")
        print("-" * 40)
        for issue in report["issues"]:
            if issue.severity == "ERROR":
                print(f"  {issue}")
        print()

    if report["warnings"]:
        print("WARNINGS:")
        print("-" * 40)
        for issue in report["issues"]:
            if issue.severity == "WARNING":
                print(f"  {issue}")
        print()

    if report["errors"] == 0 and report["warnings"] == 0:
        print("No issues found!")
    else:
        print("=" * 80)
        print(f"Review complete: {report['errors']} errors, {report['warnings']} warnings")


if __name__ == "__main__":
    report = run_audit()
    print_audit_report(report)
