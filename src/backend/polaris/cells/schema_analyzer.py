#!/usr/bin/env python3
"""
Cell Schema Analyzer
Analyzes all cell.yaml files for schema inconsistencies.
"""

from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

CELLS_DIR = Path(__file__).parent
OUTPUT_FILE = CELLS_DIR / "schema_analysis_report.txt"

# Standard schema fields (required and optional)
REQUIRED_FIELDS = [
    "id",
    "title",
    "kind",
    "visibility",
    "stateful",
    "owner",
    "purpose",
    "owned_paths",
    "public_contracts",
    "depends_on",
    "subgraphs",
    "state_owners",
    "effects_allowed",
    "verification",
]

OPTIONAL_FIELDS = [
    "current_modules",
    "tags",
    "generated_artifacts",
]

# Public contracts sub-fields
PUBLIC_CONTRACT_REQUIRED = ["modules"]
PUBLIC_CONTRACT_OPTIONAL = ["commands", "queries", "events", "results", "errors"]

# Verification sub-fields
VERIFICATION_REQUIRED: list[str] = []
VERIFICATION_OPTIONAL = ["tests", "smoke_commands", "gaps"]


def analyze_cell(cell_path: Path) -> dict[str, Any]:
    """Analyze a single cell.yaml file."""
    with open(cell_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)

    issues = []
    warnings = []

    # Check required fields
    for field in REQUIRED_FIELDS:
        if field not in data:
            issues.append(f"Missing required field: '{field}'")

    # Check optional fields
    for field in OPTIONAL_FIELDS:
        if field not in data:
            warnings.append(f"Missing optional field: '{field}'")

    # Check current_modules vs public_contracts.modules consistency
    has_current_modules = "current_modules" in data
    has_public_contracts = "public_contracts" in data
    has_contract_modules = has_public_contracts and "modules" in data.get("public_contracts", {})

    if has_current_modules and not has_contract_modules:
        warnings.append("Has 'current_modules' but no 'public_contracts.modules'")
    elif has_contract_modules and not has_current_modules:
        warnings.append("Has 'public_contracts.modules' but no 'current_modules'")

    # Check public_contracts structure
    if has_public_contracts:
        pc = data["public_contracts"]
        for field in PUBLIC_CONTRACT_REQUIRED:
            if field not in pc:
                issues.append(f"Missing 'public_contracts.{field}'")
        for field in PUBLIC_CONTRACT_OPTIONAL:
            if field not in pc:
                warnings.append(f"Missing 'public_contracts.{field}' (defaults to empty)")

        # Check for empty arrays that could be normalized
        for field in PUBLIC_CONTRACT_OPTIONAL:
            if field in pc and isinstance(pc[field], list) and len(pc[field]) == 0:
                warnings.append(f"'public_contracts.{field}' is empty array []")

    # Check verification structure
    if "verification" in data:
        v = data["verification"]
        for field in VERIFICATION_OPTIONAL:
            if field not in v:
                warnings.append(f"Missing 'verification.{field}'")
        if "gaps" in v and isinstance(v["gaps"], list) and len(v["gaps"]) == 0:
            warnings.append("'verification.gaps' is empty - no migration plan")
        if "tests" in v and isinstance(v["tests"], list) and len(v["tests"]) == 0:
            warnings.append("'verification.tests' is empty - no test coverage")
        if "smoke_commands" not in v:
            warnings.append("'verification.smoke_commands' missing - no smoke test defined")

    # Check subgraphs - should be present (even if empty)
    if "subgraphs" not in data:
        issues.append("Missing 'subgraphs'")
    elif not isinstance(data["subgraphs"], list):
        issues.append("'subgraphs' must be a list")
    elif len(data["subgraphs"]) == 0:
        warnings.append("'subgraphs' is empty - cell not connected to any pipeline")

    # Check state_owners
    if "state_owners" not in data:
        issues.append("Missing 'state_owners'")
    elif not isinstance(data["state_owners"], list):
        issues.append("'state_owners' must be a list")

    # Check effects_allowed
    if "effects_allowed" not in data:
        issues.append("Missing 'effects_allowed'")
    elif not isinstance(data["effects_allowed"], list):
        issues.append("'effects_allowed' must be a list")

    # Check tags consistency
    if "tags" in data:
        if not isinstance(data["tags"], list):
            issues.append("'tags' must be a list")
        elif len(data["tags"]) == 0:
            warnings.append("'tags' is empty")

    # Check generated_artifacts
    if "generated_artifacts" not in data:
        warnings.append("Missing 'generated_artifacts'")

    return {
        "id": data.get("id", "UNKNOWN"),
        "path": str(cell_path),
        "issues": issues,
        "warnings": warnings,
    }


def main():
    # Find all cell.yaml files
    # Use **/cell.yaml for cross-platform compatibility
    cell_files = list(CELLS_DIR.glob("**/cell.yaml"))
    # Exclude fixtures directory and ensure we only get direct cell.yaml files
    cell_files = [f for f in cell_files if "fixtures" not in str(f) and f.name == "cell.yaml"]

    print(f"Found {len(cell_files)} cell.yaml files")

    # Analyze each cell
    results = []
    for cell_path in sorted(cell_files):
        result = analyze_cell(cell_path)
        results.append(result)

    # Generate report
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("CELL SCHEMA ANALYSIS REPORT")
    report_lines.append("=" * 80)
    report_lines.append("")

    # Summary stats
    total_issues = sum(len(r["issues"]) for r in results)
    total_warnings = sum(len(r["warnings"]) for r in results)
    cells_with_issues = sum(1 for r in results if r["issues"])
    cells_with_warnings = sum(1 for r in results if r["warnings"])

    report_lines.append("SUMMARY")
    report_lines.append("-" * 40)
    report_lines.append(f"Total cells analyzed: {len(results)}")
    report_lines.append(f"Cells with issues: {cells_with_issues}")
    report_lines.append(f"Cells with warnings: {cells_with_warnings}")
    report_lines.append(f"Total issues: {total_issues}")
    report_lines.append(f"Total warnings: {total_warnings}")
    report_lines.append("")

    # Categorize issues
    issue_categories = defaultdict(list)
    warning_categories = defaultdict(list)

    for r in results:
        cell_id = r["id"]
        for issue in r["issues"]:
            # Extract issue type
            key = issue.split(":")[0].strip() if "Missing" in issue else issue.split("'")[1] if "'" in issue else issue
            issue_categories[key].append(cell_id)

        for warn in r["warnings"]:
            key = warn.split(":")[0].strip() if "Missing" in warn else warn.split("'")[1] if "'" in warn else warn
            warning_categories[key].append(cell_id)

    # Issue distribution
    report_lines.append("ISSUE DISTRIBUTION")
    report_lines.append("-" * 40)
    for category, cells in sorted(issue_categories.items()):
        report_lines.append(f"  {category}: {len(cells)} cells")
    report_lines.append("")

    # Warning distribution
    report_lines.append("WARNING DISTRIBUTION")
    report_lines.append("-" * 40)
    for category, cells in sorted(warning_categories.items()):
        report_lines.append(f"  {category}: {len(cells)} cells")
    report_lines.append("")

    # Detailed cell reports
    report_lines.append("=" * 80)
    report_lines.append("DETAILED CELL REPORTS")
    report_lines.append("=" * 80)

    # Sort by severity (issues first, then warnings)
    results.sort(key=lambda r: (len(r["issues"]) > 0, len(r["warnings"]) > 0), reverse=True)

    for r in results:
        if r["issues"] or r["warnings"]:
            report_lines.append("")
            report_lines.append(f"Cell: {r['id']}")
            report_lines.append(f"Path: {r['path']}")
            if r["issues"]:
                report_lines.append("  ISSUES:")
                for issue in r["issues"]:
                    report_lines.append(f"    - {issue}")
            if r["warnings"]:
                report_lines.append("  WARNINGS:")
                for warn in r["warnings"]:
                    report_lines.append(f"    - {warn}")

    # Problem cells (most issues)
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("PROBLEM CELLS (sorted by issue count)")
    report_lines.append("=" * 80)

    sorted_by_issues = sorted(results, key=lambda r: len(r["issues"]), reverse=True)
    for r in sorted_by_issues[:10]:
        if r["issues"] or r["warnings"]:
            report_lines.append(f"{r['id']}: {len(r['issues'])} issues, {len(r['warnings'])} warnings")

    # Write report
    report_content = "\n".join(report_lines)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(report_content)

    print(f"\nReport written to: {OUTPUT_FILE}")
    print(
        f"\nSummary: {cells_with_issues} cells with issues, {total_issues} total issues, {total_warnings} total warnings"
    )

    # Print summary to console
    print("\n" + "=" * 60)
    print("ISSUE CATEGORIES:")
    for category, cells in sorted(issue_categories.items()):
        print(f"  {category}: {len(cells)}")
    print("\nWARNING CATEGORIES:")
    for category, cells in sorted(warning_categories.items()):
        print(f"  {category}: {len(cells)}")


if __name__ == "__main__":
    main()
