"""Tool Catalog Consistency Governance Gate.

This gate validates tool catalog consistency across three dimensions:
1. Alias conflicts: Each alias must map to exactly one canonical name
2. Profile whitelist: Role profiles must use only canonical tool names
3. Fixture required_tools: Benchmark fixtures must use only canonical tool names

Usage:
    python docs/governance/ci/scripts/run_tool_catalog_consistency_gate.py \
        --workspace . \
        --mode hard-fail \
        --check-aliases \
        --check-profiles \
        --check-fixtures
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure polaris is on the import path when running as a standalone script.
_BACKEND_ROOT = Path(__file__).resolve().parents[4]
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

_MODE_HARD_FAIL = "hard-fail"
_MODE_SOFT_FAIL = "soft-fail"
_SUPPORTED_MODES = (_MODE_HARD_FAIL, _MODE_SOFT_FAIL)


@dataclass(frozen=True)
class GateIssue:
    """Represents a single governance issue found by the gate."""

    category: str
    message: str
    file: str | None = None
    severity: str = "error"
    evidence: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        file_part = f" ({self.file})" if self.file else ""
        return f"[{self.severity}] {self.category}: {self.message}{file_part}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category,
            "message": self.message,
            "file": self.file,
            "severity": self.severity,
            "evidence": self.evidence,
        }


def _non_empty(value: Any) -> str:
    """Return stripped string or empty string."""
    return str(value or "").strip()


def _load_json(path: Path) -> dict[str, Any]:
    """Load JSON file with UTF-8 encoding."""
    return json.loads(path.read_text(encoding="utf-8"))


def _extract_tool_specs_from_contracts_py(contracts_path: Path) -> dict[str, dict[str, Any]]:
    """Extract _TOOL_SPECS dict from contracts.py.

    Uses direct import when possible, falls back to regex extraction for edge cases.

    Returns:
        Dict mapping canonical tool name to its spec (including aliases).
    """
    if not contracts_path.exists():
        return {}

    # Try direct import first (most reliable)
    try:
        from polaris.kernelone.tool_execution.contracts import _TOOL_SPECS

        return dict(_TOOL_SPECS)
    except ImportError:
        pass

    # Fallback: regex extraction for aliases only (lightweight)
    # This is used when direct import fails (e.g., in isolated environments)
    source = contracts_path.read_text(encoding="utf-8")

    # Extract tool names and aliases using regex
    # Pattern: "tool_name": { ... "aliases": ["alias1", "alias2", ...], ... }
    tool_specs: dict[str, dict[str, Any]] = {}

    # Find all tool definitions
    tool_pattern = re.compile(r'"([a-z_][a-z0-9_]+)":\s*\{[^}]*"aliases":\s*\[([^\]]*)\]', re.MULTILINE | re.DOTALL)

    for match in tool_pattern.finditer(source):
        tool_name = match.group(1)
        aliases_str = match.group(2)

        # Parse aliases list
        aliases: list[str] = []
        alias_pattern = re.compile(r'"([^"]+)"')
        for alias_match in alias_pattern.finditer(aliases_str):
            aliases.append(alias_match.group(1))

        tool_specs[tool_name] = {"aliases": aliases}

    return tool_specs


def _get_canonical_names_and_aliases(tool_specs: dict[str, dict[str, Any]]) -> tuple[set[str], dict[str, str]]:
    """Extract canonical names and alias-to-canonical mapping.

    Returns:
        (canonical_names_set, alias_to_canonical_dict)
    """
    canonical_names: set[str] = set()
    alias_to_canonical: dict[str, str] = {}

    for tool_name, spec in tool_specs.items():
        canonical_names.add(tool_name.lower())

        # Extract aliases
        aliases = spec.get("aliases", [])
        if isinstance(aliases, list):
            for alias in aliases:
                if isinstance(alias, str):
                    alias_lower = alias.lower()
                    alias_to_canonical[alias_lower] = tool_name.lower()

    return canonical_names, alias_to_canonical


def check_alias_conflicts(tool_specs: dict[str, dict[str, Any]]) -> list[GateIssue]:
    """Detect aliases that map to multiple canonical names.

    An alias conflict occurs when the same alias string is declared
    for two different canonical tool names.
    """
    issues: list[GateIssue] = []
    alias_to_canonical: dict[str, str] = {}

    for tool_name, spec in tool_specs.items():
        aliases = spec.get("aliases", [])
        if not isinstance(aliases, list):
            continue

        for alias in aliases:
            if not isinstance(alias, str):
                continue

            alias_lower = alias.lower()
            tool_name_lower = tool_name.lower()

            if alias_lower in alias_to_canonical:
                existing_canonical = alias_to_canonical[alias_lower]
                if existing_canonical != tool_name_lower:
                    issues.append(
                        GateIssue(
                            category="alias_conflict",
                            message=(f"alias `{alias}` maps to both `{existing_canonical}` and `{tool_name_lower}`"),
                            severity="error",
                            evidence={
                                "alias": alias,
                                "canonical_1": existing_canonical,
                                "canonical_2": tool_name_lower,
                            },
                        )
                    )
            else:
                alias_to_canonical[alias_lower] = tool_name_lower

    return issues


def check_profiles_use_canonical(
    workspace: Path,
    canonical_names: set[str],
    alias_to_canonical: dict[str, str],
) -> list[GateIssue]:
    """Verify role whitelists use only canonical tool names.

    Checks builtin_profiles.py and any loaded role config files.
    """
    issues: list[GateIssue] = []

    # Check builtin_profiles.py
    builtin_profiles_path = workspace / "polaris" / "cells" / "roles" / "profile" / "internal" / "builtin_profiles.py"
    if builtin_profiles_path.exists():
        issues.extend(_check_builtin_profiles(builtin_profiles_path, canonical_names, alias_to_canonical))

    # Check core_roles.yaml if exists
    core_roles_yaml = workspace / "polaris" / "cells" / "roles" / "profile" / "config" / "core_roles.yaml"
    if core_roles_yaml.exists():
        issues.extend(_check_yaml_profiles(core_roles_yaml, canonical_names, alias_to_canonical))

    return issues


def _check_builtin_profiles(
    profiles_path: Path,
    canonical_names: set[str],
    alias_to_canonical: dict[str, str],
) -> list[GateIssue]:
    """Check builtin_profiles.py for non-canonical tool names in whitelists.

    Uses regex extraction to avoid AST parsing complexity.
    """
    issues: list[GateIssue] = []

    source = profiles_path.read_text(encoding="utf-8")

    # Find role_id and whitelist pairs using regex
    # Pattern: "role_id": "xxx", ... "whitelist": ["tool1", "tool2", ...]
    role_pattern = re.compile(r'"role_id":\s*"([^"]+)"', re.MULTILINE)

    whitelist_pattern = re.compile(r'"whitelist":\s*\[([^\]]+)\]', re.MULTILINE | re.DOTALL)

    # Find all role definitions
    # Split by role_id to get individual profile blocks
    role_matches = list(role_pattern.finditer(source))

    for i, role_match in enumerate(role_matches):
        role_id = role_match.group(1)

        # Find the whitelist for this role
        # Start from role_id position, look for whitelist
        start_pos = role_match.end()

        # Find next role_id or end of file
        next_role_start = role_matches[i + 1].start() if i + 1 < len(role_matches) else len(source)
        profile_block = source[start_pos:next_role_start]

        # Find whitelist in this block
        whitelist_match = whitelist_pattern.search(profile_block)
        if whitelist_match:
            whitelist_str = whitelist_match.group(1)

            # Parse whitelist tools
            tool_pattern = re.compile(r'"([^"]+)"')
            for tool_match in tool_pattern.finditer(whitelist_str):
                tool_name = tool_match.group(1)
                tool_lower = tool_name.lower()

                # Check if it's canonical
                if tool_lower not in canonical_names:
                    # Check if it's a known alias
                    if tool_lower in alias_to_canonical:
                        canonical = alias_to_canonical[tool_lower]
                        issues.append(
                            GateIssue(
                                category="alias_in_whitelist",
                                message=(
                                    f"profile `{role_id}` whitelist uses alias "
                                    f"`{tool_name}` instead of canonical `{canonical}`"
                                ),
                                file=str(profiles_path),
                                severity="warning",
                                evidence={
                                    "role_id": role_id,
                                    "alias": tool_name,
                                    "canonical": canonical,
                                },
                            )
                        )
                    else:
                        # Unknown tool name
                        issues.append(
                            GateIssue(
                                category="unknown_tool_in_whitelist",
                                message=(f"profile `{role_id}` whitelist uses unknown tool `{tool_name}`"),
                                file=str(profiles_path),
                                severity="warning",
                                evidence={
                                    "role_id": role_id,
                                    "tool": tool_name,
                                },
                            )
                        )

    return issues


def _check_yaml_profiles(
    yaml_path: Path,
    canonical_names: set[str],
    alias_to_canonical: dict[str, str],
) -> list[GateIssue]:
    """Check YAML profiles file for non-canonical tool names."""
    issues: list[GateIssue] = []

    try:
        import yaml
    except ImportError:
        # YAML not available, skip
        return issues

    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

    profiles: list[dict[str, Any]] = []
    if isinstance(data, dict):
        if "roles" in data:
            profiles = data.get("roles", [])
        else:
            # Format: {role_id: {...profile...}}
            for role_id, role_data in data.items():
                if isinstance(role_data, dict):
                    role_data["role_id"] = role_id
                    profiles.append(role_data)

    for profile in profiles:
        if not isinstance(profile, dict):
            continue

        role_id = profile.get("role_id", "unknown")
        tool_policy = profile.get("tool_policy", {})
        if isinstance(tool_policy, dict):
            whitelist = tool_policy.get("whitelist", [])
            if isinstance(whitelist, list):
                for tool_name in whitelist:
                    if isinstance(tool_name, str):
                        tool_lower = tool_name.lower()

                        if tool_lower not in canonical_names:
                            if tool_lower in alias_to_canonical:
                                canonical = alias_to_canonical[tool_lower]
                                issues.append(
                                    GateIssue(
                                        category="alias_in_whitelist",
                                        message=(
                                            f"profile `{role_id}` whitelist uses alias "
                                            f"`{tool_name}` instead of canonical `{canonical}`"
                                        ),
                                        file=str(yaml_path),
                                        severity="warning",
                                        evidence={
                                            "role_id": role_id,
                                            "alias": tool_name,
                                            "canonical": canonical,
                                        },
                                    )
                                )
                            else:
                                issues.append(
                                    GateIssue(
                                        category="unknown_tool_in_whitelist",
                                        message=(f"profile `{role_id}` whitelist uses unknown tool `{tool_name}`"),
                                        file=str(yaml_path),
                                        severity="warning",
                                        evidence={
                                            "role_id": role_id,
                                            "tool": tool_name,
                                        },
                                    )
                                )

    return issues


def check_fixtures_use_canonical(
    workspace: Path,
    canonical_names: set[str],
    alias_to_canonical: dict[str, str],
) -> list[GateIssue]:
    """Verify benchmark fixtures expect canonical tool names in required_tools."""
    issues: list[GateIssue] = []

    fixtures_dirs = [
        workspace / "polaris" / "cells" / "llm" / "evaluation" / "fixtures" / "tool_calling_matrix" / "cases",
        workspace / "polaris" / "cells" / "llm" / "evaluation" / "fixtures" / "agentic_benchmark" / "cases",
        workspace / "polaris" / "cells" / "llm" / "evaluation" / "fixtures" / "performance" / "cases",
    ]

    for fixtures_dir in fixtures_dirs:
        if not fixtures_dir.exists():
            continue

        for fixture_file in fixtures_dir.glob("*.json"):
            issues.extend(
                _check_fixture_file(
                    fixture_file,
                    canonical_names,
                    alias_to_canonical,
                )
            )

    return issues


def _check_fixture_file(
    fixture_path: Path,
    canonical_names: set[str],
    alias_to_canonical: dict[str, str],
) -> list[GateIssue]:
    """Check a single fixture JSON file for non-canonical required_tools."""
    issues: list[GateIssue] = []

    try:
        data = _load_json(fixture_path)
    except json.JSONDecodeError:
        issues.append(
            GateIssue(
                category="fixture_parse_error",
                message=f"fixture `{fixture_path.name}` is not valid JSON",
                file=str(fixture_path),
                severity="error",
            )
        )
        return issues

    # Check cases array
    cases = data.get("cases", [])
    if isinstance(cases, list):
        for case in cases:
            if not isinstance(case, dict):
                continue

            case_id = case.get("case_id", "unknown")

            # Check judge.stream.required_tools
            judge = case.get("judge", {})
            if isinstance(judge, dict):
                for mode in ["stream", "non_stream"]:
                    mode_spec = judge.get(mode, {})
                    if isinstance(mode_spec, dict):
                        required_tools = mode_spec.get("required_tools", [])
                        if isinstance(required_tools, list):
                            for tool_name in required_tools:
                                if isinstance(tool_name, str):
                                    tool_lower = tool_name.lower()

                                    if tool_lower not in canonical_names:
                                        if tool_lower in alias_to_canonical:
                                            canonical = alias_to_canonical[tool_lower]
                                            issues.append(
                                                GateIssue(
                                                    category="alias_in_fixture",
                                                    message=(
                                                        f"fixture `{fixture_path.name}` case `{case_id}` "
                                                        f"uses alias `{tool_name}` instead of canonical `{canonical}`"
                                                    ),
                                                    file=str(fixture_path),
                                                    severity="warning",
                                                    evidence={
                                                        "case_id": case_id,
                                                        "mode": mode,
                                                        "alias": tool_name,
                                                        "canonical": canonical,
                                                    },
                                                )
                                            )
                                        else:
                                            issues.append(
                                                GateIssue(
                                                    category="unknown_tool_in_fixture",
                                                    message=(
                                                        f"fixture `{fixture_path.name}` case `{case_id}` "
                                                        f"uses unknown tool `{tool_name}`"
                                                    ),
                                                    file=str(fixture_path),
                                                    severity="warning",
                                                    evidence={
                                                        "case_id": case_id,
                                                        "mode": mode,
                                                        "tool": tool_name,
                                                    },
                                                )
                                            )

    # Check single case format (case_id at top level)
    case_id = data.get("case_id")
    if case_id:
        judge = data.get("judge", {})
        if isinstance(judge, dict):
            for mode in ["stream", "non_stream"]:
                mode_spec = judge.get(mode, {})
                if isinstance(mode_spec, dict):
                    required_tools = mode_spec.get("required_tools", [])
                    if isinstance(required_tools, list):
                        for tool_name in required_tools:
                            if isinstance(tool_name, str):
                                tool_lower = tool_name.lower()

                                if tool_lower not in canonical_names:
                                    if tool_lower in alias_to_canonical:
                                        canonical = alias_to_canonical[tool_lower]
                                        issues.append(
                                            GateIssue(
                                                category="alias_in_fixture",
                                                message=(
                                                    f"fixture `{fixture_path.name}` case `{case_id}` "
                                                    f"uses alias `{tool_name}` instead of canonical `{canonical}`"
                                                ),
                                                file=str(fixture_path),
                                                severity="warning",
                                                evidence={
                                                    "case_id": case_id,
                                                    "mode": mode,
                                                    "alias": tool_name,
                                                    "canonical": canonical,
                                                },
                                            )
                                        )
                                    else:
                                        issues.append(
                                            GateIssue(
                                                category="unknown_tool_in_fixture",
                                                message=(
                                                    f"fixture `{fixture_path.name}` case `{case_id}` "
                                                    f"uses unknown tool `{tool_name}`"
                                                ),
                                                file=str(fixture_path),
                                                severity="warning",
                                                evidence={
                                                    "case_id": case_id,
                                                    "mode": mode,
                                                    "tool": tool_name,
                                                },
                                            )
                                        )

    return issues


def _parse_args() -> argparse.Namespace:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Run tool catalog consistency governance gate.")
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace root path (default: current directory).",
    )
    parser.add_argument(
        "--mode",
        choices=_SUPPORTED_MODES,
        default=_MODE_HARD_FAIL,
        help="Gate mode: hard-fail (exit 1 on issues) or soft-fail (exit 0).",
    )
    parser.add_argument(
        "--check-aliases",
        action="store_true",
        help="Check for alias conflicts.",
    )
    parser.add_argument(
        "--check-profiles",
        action="store_true",
        help="Check profiles use canonical names.",
    )
    parser.add_argument(
        "--check-fixtures",
        action="store_true",
        help="Check fixtures use canonical names.",
    )
    parser.add_argument(
        "--check-all",
        action="store_true",
        help="Run all checks (aliases, profiles, fixtures).",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional path to write gate JSON report.",
    )
    args = parser.parse_args()

    # If no specific check is specified, run all checks by default
    if not (args.check_aliases or args.check_profiles or args.check_fixtures or args.check_all) or args.check_all:
        args.check_aliases = True
        args.check_profiles = True
        args.check_fixtures = True

    return args


def main() -> int:
    """Run the governance gate and return exit code."""
    args = _parse_args()
    workspace = Path(args.workspace).resolve()

    # Load tool specs from contracts.py
    contracts_path = workspace / "polaris" / "kernelone" / "tool_execution" / "contracts.py"
    tool_specs = _extract_tool_specs_from_contracts_py(contracts_path)

    if not tool_specs:
        print("[error] tool_catalog_load: failed to load _TOOL_SPECS from contracts.py")
        return 1 if args.mode == _MODE_HARD_FAIL else 0

    # Get canonical names and alias mapping
    canonical_names, alias_to_canonical = _get_canonical_names_and_aliases(tool_specs)

    all_issues: list[GateIssue] = []

    # Run checks based on flags
    if args.check_aliases:
        all_issues.extend(check_alias_conflicts(tool_specs))

    if args.check_profiles:
        all_issues.extend(check_profiles_use_canonical(workspace, canonical_names, alias_to_canonical))

    if args.check_fixtures:
        all_issues.extend(check_fixtures_use_canonical(workspace, canonical_names, alias_to_canonical))

    # Build report
    report = {
        "version": 1,
        "gate": "tool_catalog_consistency",
        "mode": args.mode,
        "workspace": str(workspace),
        "canonical_tool_count": len(canonical_names),
        "alias_count": len(alias_to_canonical),
        "issue_count": len(all_issues),
        "error_count": sum(1 for i in all_issues if i.severity == "error"),
        "warning_count": sum(1 for i in all_issues if i.severity == "warning"),
        "issues": [i.to_dict() for i in all_issues],
        "checks": {
            "aliases": args.check_aliases,
            "profiles": args.check_profiles,
            "fixtures": args.check_fixtures,
        },
    }

    # Print issues to stdout
    for issue in all_issues:
        print(str(issue))

    # Write report if requested
    if _non_empty(args.report):
        report_path = Path(args.report)
        if not report_path.is_absolute():
            report_path = workspace / report_path
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

    # Print summary
    print("\n=== Tool Catalog Consistency Gate Summary ===")
    print(f"Canonical tools: {len(canonical_names)}")
    print(f"Aliases: {len(alias_to_canonical)}")
    print(f"Issues: {len(all_issues)} (errors: {report['error_count']}, warnings: {report['warning_count']})")

    # Return exit code based on mode
    if args.mode == _MODE_HARD_FAIL and report["error_count"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
