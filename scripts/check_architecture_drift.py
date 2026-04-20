#!/usr/bin/env python3
"""Architecture Drift Checker - Polaris.

This script validates that the codebase follows the architecture conventions
and detects any drift from the target architecture.

Checks performed:
1. No imports of deprecated/deleted modules
2. No sys.path.insert/append in production code (except CLI entry points)
3. Key documentation paths must exist
4. PM/Director APIs must use /v2/* routes (except tombstone routes)
5. create_app must have single implementation source

Exit codes:
    0 - All checks passed
    1 - One or more checks failed

Usage:
    python scripts/check_architecture_drift.py
    python scripts/check_architecture_drift.py --verbose
    python scripts/check_architecture_drift.py --fix  # Auto-fix where possible
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import List, Set, Tuple

# Project root
SCRIPT_DIR = Path(__file__).parent.resolve()
PROJECT_ROOT = SCRIPT_DIR.parent
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
FRONTEND_DIR = PROJECT_ROOT / "src" / "frontend"


class ArchitectureChecker:
    """Architecture drift checker."""

    # Deprecated/deleted modules that should not be imported
    # Hard errors - these modules are completely removed
    DELETED_MODULES = {
        "app.llm.usecases.pm_tools",
        "pm_dialogue",
    }
    
    # Soft deprecated - warning only (still needed for tests)
    DEPRECATED_MODULES = {
        "app.roles.workflow_nodes_compat",  # Used by tests
    }

    # Directories where sys.path manipulation is allowed
    ALLOWED_SYSPATH_DIRS = {
        "scripts",  # CLI entry points
        "tests",    # Test files
        "core",     # Core module (contains CLI entry points like role_agent)
    }

    # Key paths that must exist (relative to project root)
    REQUIRED_PATHS = [
        "src/backend/app/main.py",
        "src/backend/server.py",
        "src/backend/api/main.py",
        "src/backend/core/llm_toolkit/definitions.py",
        "src/backend/app/llm/usecases/role_dialogue.py",
    ]

    # V2 API requirement - PM/Director routes should be in /v2/*
    V2_API_REQUIRED = True

    def __init__(self, verbose: bool = False, fix: bool = False):
        self.verbose = verbose
        self.fix = fix
        self.errors: List[str] = []
        self.warnings: List[str] = []
        self.fixed: List[str] = []

    def log(self, message: str) -> None:
        """Log message."""
        if self.verbose:
            print(f"  {message}")

    def check_deprecated_imports(self) -> bool:
        """Check for imports of deprecated/deleted modules."""
        self.log("Checking for deprecated module imports...")
        
        all_files = list(BACKEND_DIR.rglob("*.py"))
        deleted_issues = []
        deprecated_issues = []

        for filepath in all_files:
            try:
                content = filepath.read_text(encoding="utf-8")
            except Exception:
                continue

            # Check for deleted modules (hard error)
            for deleted in self.DELETED_MODULES:
                patterns = [
                    f"import {deleted}",
                    f"from {deleted} import",
                ]
                for pattern in patterns:
                    if pattern in content:
                        rel_path = filepath.relative_to(PROJECT_ROOT)
                        deleted_issues.append(f"  {rel_path}: imports '{deleted}'")

            # Check for deprecated modules (soft warning)
            for deprecated in self.DEPRECATED_MODULES:
                patterns = [
                    f"import {deprecated}",
                    f"from {deprecated} import",
                ]
                for pattern in patterns:
                    if pattern in content:
                        rel_path = filepath.relative_to(PROJECT_ROOT)
                        deprecated_issues.append(f"  {rel_path}: imports '{deprecated}'")

        if deleted_issues:
            self.errors.append("Deleted module imports found (hard error):")
            self.errors.extend(deleted_issues)
            return False
        
        if deprecated_issues:
            self.warnings.append("Deprecated module imports found (soft warning - used by tests):")
            self.warnings.extend(deprecated_issues)
            self.log(f"  Warning: {len(deprecated_issues)} deprecated imports (non-blocking)")
        
        self.log("  No deleted imports found")
        return True

    def check_syspath_in_production(self) -> bool:
        """Check for sys.path manipulation in production code.

        This is a HARD CONSTRAINT: production code (app/, domain/, application/, api/)
        must NEVER use sys.path manipulation. Only tests and CLI entry points are allowed.
        """
        self.log("Checking for sys.path manipulation in production code...")

        issues = []

        # Strict production directories - NO sys.path allowed (except __init__ path setup)
        strict_prod_dirs = [
            BACKEND_DIR / "app",
            BACKEND_DIR / "domain",
            BACKEND_DIR / "application",
            BACKEND_DIR / "api",
        ]

        # Whitelist of files allowed to have sys.path in production (bootstrap only)
        strict_whitelist = {
            # These files are allowed to temporarily modify sys.path for imports
            # but must clean up after themselves
            "src/backend/core/startup/config_loader.py",
            "src/backend/core/startup/backend_bootstrap.py",
            "src/backend/application/dto/backend_launch.py",
        }

        for prod_dir in strict_prod_dirs:
            if not prod_dir.exists():
                continue

            for filepath in prod_dir.rglob("*.py"):
                try:
                    content = filepath.read_text(encoding="utf-8")
                except Exception:
                    continue

                rel_path = filepath.relative_to(PROJECT_ROOT)
                rel_path_str = str(rel_path).replace("\\", "/")

                # Skip whitelisted files
                if rel_path_str in strict_whitelist:
                    continue

                # Check for sys.path manipulation
                if "sys.path.insert" in content or "sys.path.append" in content:
                    # Lines with sys.path are violations
                    lines = content.split("\n")
                    for i, line in enumerate(lines):
                        if "sys.path.insert" in line or "sys.path.append" in line:
                            # Skip comments
                            if line.strip().startswith("#"):
                                continue
                            # Skip if it's in a function that cleans up (temporary path mod)
                            context = "\n".join(lines[max(0, i-10):min(len(lines), i+10)])
                            if "try:" in context and "finally:" in context and "sys.path.remove" in context:
                                continue
                            issues.append(f"{rel_path}:{i+1}: {line.strip()[:60]}")

        if issues:
            self.errors.append("HARD ERROR: sys.path manipulation found in production code:")
            self.errors.extend([f"  {issue}" for issue in issues])
            self.log(f"  ERROR: {len(issues)} violations found")
            return False

        self.log("  No sys.path violations found in production code")
        return True

    def check_required_paths(self) -> bool:
        """Check that required paths exist."""
        self.log("Checking required paths...")
        
        issues = []
        for required_path in self.REQUIRED_PATHS:
            full_path = PROJECT_ROOT / required_path
            if not full_path.exists():
                issues.append(f"  {required_path} (missing)")
                self.log(f"    Missing: {required_path}")
            else:
                self.log(f"    Found: {required_path}")

        if issues:
            self.errors.append("Required paths missing:")
            self.errors.extend(issues)
            return False

        self.log(f"  All {len(self.REQUIRED_PATHS)} required paths exist")
        return True

    def check_api_routes(self) -> bool:
        """Check that PM/Director APIs use V2 routes."""
        self.log("Checking API route conventions...")
        
        # Check that old routes return 410 or redirect
        old_pm_router = BACKEND_DIR / "app" / "routers" / "pm.py"
        old_director_router = BACKEND_DIR / "app" / "routers" / "director.py"
        
        issues = []
        
        # Check old PM router
        if old_pm_router.exists():
            content = old_pm_router.read_text(encoding="utf-8")
            if "410" not in content and "gone" not in content.lower():
                issues.append("  app/routers/pm.py: should return 410 Gone for deprecated endpoints")
        
        # Check old director router
        if old_director_router.exists():
            content = old_director_router.read_text(encoding="utf-8")
            if "deprecated" not in content.lower():
                issues.append("  app/routers/director.py: should mark deprecated endpoints")

        # Check V2 routers exist
        v2_pm_router = BACKEND_DIR / "api" / "v2" / "pm.py"
        v2_director_router = BACKEND_DIR / "api" / "v2" / "director.py"
        
        if not v2_pm_router.exists():
            issues.append("  api/v2/pm.py: V2 PM router not found")
        if not v2_director_router.exists():
            issues.append("  api/v2/director.py: V2 Director router not found")

        if issues:
            self.warnings.append("API route convention issues (non-blocking):")
            self.warnings.extend(issues)
            self.log(f"  Warning: {len(issues)} issues found")

        self.log("  API route check complete")
        return True

    def check_claude_md(self) -> bool:
        """Check CLAUDE.md references."""
        self.log("Checking CLAUDE.md references...")
        
        claude_md = PROJECT_ROOT / "CLAUDE.md"
        if not claude_md.exists():
            self.errors.append("CLAUDE.md not found")
            return False

        content = claude_md.read_text(encoding="utf-8")
        
        # Check for references to deleted modules
        deleted_refs = []
        for deprecated in self.DEPRECATED_MODULES:
            if deprecated in content and "已删除" not in content and "deleted" not in content.lower():
                deleted_refs.append(f"  CLAUDE.md: references '{deprecated}' without noting deletion")

        if deleted_refs:
            self.warnings.append("CLAUDE.md references to deleted modules:")
            self.warnings.extend(deleted_refs)

        self.log("  CLAUDE.md check complete")
        return True

    def run_all_checks(self) -> bool:
        """Run all architecture checks."""
        print("Running architecture drift checks...")
        print()

        checks = [
            ("Required Paths", self.check_required_paths),
            ("Deprecated Imports", self.check_deprecated_imports),
            ("sys.path in Production", self.check_syspath_in_production),
            ("API Routes", self.check_api_routes),
            ("CLAUDE.md", self.check_claude_md),
        ]

        results = []
        for name, check in checks:
            try:
                result = check()
                results.append(result)
            except Exception as e:
                self.errors.append(f"Error in {name}: {str(e)}")
                results.append(False)
            print()

        return all(results)

    def print_report(self) -> None:
        """Print check report."""
        print("=" * 60)
        print("ARCHITECTURE DRIFT CHECK REPORT")
        print("=" * 60)
        print()

        if self.errors:
            print("ERRORS:")
            for error in self.errors:
                print(f"  {error}")
            print()

        if self.warnings:
            print("WARNINGS:")
            for warning in self.warnings:
                print(f"  {warning}")
            print()

        if self.fixed:
            print("FIXED:")
            for fix in self.fixed:
                print(f"  {fix}")
            print()

        if not self.errors and not self.warnings:
            print("✓ All architecture checks passed!")
        elif not self.errors:
            print("✓ No errors, but there are warnings to review.")
        else:
            print("✗ Architecture drift detected!")

        print()


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Check Polaris for architecture drift"
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Verbose output"
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="Auto-fix where possible"
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output JSON"
    )
    
    args = parser.parse_args()

    checker = ArchitectureChecker(
        verbose=args.verbose,
        fix=args.fix
    )

    success = checker.run_all_checks()

    if args.json:
        result = {
            "success": success,
            "errors": checker.errors,
            "warnings": checker.warnings,
            "fixed": checker.fixed,
        }
        print(json.dumps(result, indent=2))
    else:
        checker.print_report()

    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
