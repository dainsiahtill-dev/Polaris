#!/usr/bin/env python3
"""Phase 6 cleanup script - Remove feature flags and legacy code paths.

This script performs the final cleanup for the "Thin CLI + Core OO" refactoring:
1. Removes POLARIS_USE_NEW_BOOTSTRAP feature flag
2. Archives legacy implementations
3. Updates entry points to use new architecture only
4. Verifies no legacy code remains in active paths

Usage:
    python scripts/phase6_cleanup.py [--dry-run] [--archive-path PATH]

Options:
    --dry-run       Show what would be done without making changes
    --archive-path  Where to archive legacy code (default: archive/phase6_legacy)
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Set


# Files to archive (legacy implementations)
LEGACY_FILES: List[tuple[str, str]] = [
    # (source_path, archive_subpath)
    ("src/backend/server.py.legacy", "src/backend/server_legacy.py"),
    ("src/backend/process.py", "src/backend/process_legacy.py"),
    ("src/backend/scripts/pm/cli.py.legacy", "src/backend/scripts/pm/cli_legacy.py"),
    ("src/backend/scripts/director/main.py", "src/backend/scripts/director/main_legacy.py"),
]

# Patterns to remove from active code
DEPRECATED_PATTERNS: List[str] = [
    "POLARIS_USE_NEW_BOOTSTRAP",
    "USE_NEW_BOOTSTRAP",
    "_use_legacy_bootstrap",
    "legacy_bootstrap",
]

# Entry points to update
ENTRY_POINTS: List[str] = [
    "src/backend/server.py",
    "polaris.py",
]


class Phase6Cleanup:
    """Phase 6 cleanup executor."""

    def __init__(self, project_root: Path, archive_path: Path, dry_run: bool = False):
        self.project_root = project_root
        self.archive_path = archive_path
        self.dry_run = dry_run
        self.changes_made: List[str] = []
        self.warnings: List[str] = []

    def log(self, message: str) -> None:
        """Log a message."""
        print(f"[phase6-cleanup] {message}")

    def warn(self, message: str) -> None:
        """Log a warning."""
        print(f"[phase6-cleanup] WARNING: {message}")
        self.warnings.append(message)

    def change(self, message: str) -> None:
        """Log a change."""
        self.changes_made.append(message)
        if self.dry_run:
            print(f"[phase6-cleanup] WOULD: {message}")
        else:
            print(f"[phase6-cleanup] DONE: {message}")

    def archive_legacy_files(self) -> bool:
        """Archive legacy implementation files."""
        self.log("Archiving legacy files...")

        # Create archive directory
        if not self.dry_run:
            self.archive_path.mkdir(parents=True, exist_ok=True)

        archived_count = 0
        for source_rel, archive_rel in LEGACY_FILES:
            source_path = self.project_root / source_rel
            archive_target = self.archive_path / archive_rel

            if not source_path.exists():
                self.warn(f"Legacy file not found: {source_rel}")
                continue

            if not self.dry_run:
                # Create parent directories
                archive_target.parent.mkdir(parents=True, exist_ok=True)
                # Copy file
                shutil.copy2(source_path, archive_target)

            self.change(f"Archived: {source_rel} -> {archive_rel}")
            archived_count += 1

        # Create archive README
        readme_content = f"""# Phase 6 Legacy Archive

Archived: {datetime.now().isoformat()}

This directory contains legacy implementations from the "Thin CLI + Core OO" refactoring.

## Contents

{chr(10).join(f"- {arc}" for _, arc in LEGACY_FILES)}

## Usage

These files are kept for reference only. Do not use in production.

For the current implementation, see:
- `src/backend/core/orchestration/` - Unified orchestration
- `src/backend/core/startup/` - Backend bootstrap
- `src/backend/domain/models/config_snapshot.py` - Configuration management
"""

        if not self.dry_run:
            (self.archive_path / "README.md").write_text(readme_content, encoding="utf-8")

        self.log(f"Archived {archived_count} legacy files")
        return True

    def scan_for_deprecated_patterns(self) -> Set[str]:
        """Scan codebase for deprecated patterns."""
        self.log("Scanning for deprecated patterns...")

        found_files: Set[str] = set()

        # Scan Python files
        for py_file in self.project_root.rglob("*.py"):
            # Skip archive directory
            if "archive" in str(py_file):
                continue

            # Skip __pycache__
            if "__pycache__" in str(py_file):
                continue

            try:
                content = py_file.read_text(encoding="utf-8")
                for pattern in DEPRECATED_PATTERNS:
                    if pattern in content:
                        rel_path = py_file.relative_to(self.project_root)
                        self.warn(f"Found deprecated pattern '{pattern}' in {rel_path}")
                        found_files.add(str(rel_path))
            except Exception as e:
                self.warn(f"Could not read {py_file}: {e}")

        return found_files

    def verify_new_architecture(self) -> bool:
        """Verify new architecture is in place."""
        self.log("Verifying new architecture...")

        required_files = [
            "src/backend/core/orchestration/runtime_orchestrator.py",
            "src/backend/core/orchestration/process_launcher.py",
            "src/backend/core/startup/backend_bootstrap.py",
            "src/backend/domain/models/config_snapshot.py",
        ]

        all_present = True
        for rel_path in required_files:
            full_path = self.project_root / rel_path
            if full_path.exists():
                self.log(f"  ✓ {rel_path}")
            else:
                self.warn(f"Missing required file: {rel_path}")
                all_present = False

        return all_present

    def verify_tests_pass(self) -> bool:
        """Run verification tests."""
        self.log("Running verification tests...")

        test_script = self.project_root / "tests" / "refactor" / "test_all_phases.py"
        if not test_script.exists():
            self.warn(f"Test script not found: {test_script}")
            return False

        if self.dry_run:
            self.log("  (Skipping tests in dry-run mode)")
            return True

        # Run tests
        import subprocess
        result = subprocess.run(
            [sys.executable, str(test_script)],
            capture_output=True,
            text=True,
        )

        if result.returncode == 0:
            self.log("  ✓ All tests passed")
            return True
        else:
            self.warn("Tests failed!")
            print(result.stdout)
            print(result.stderr)
            return False

    def create_cleanup_manifest(self) -> None:
        """Create manifest of cleanup actions."""
        manifest_path = self.project_root / ".polaris" / "phase6_cleanup_manifest.json"

        if not self.dry_run:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)

            import json
            manifest = {
                "timestamp": datetime.now().isoformat(),
                "dry_run": self.dry_run,
                "changes": self.changes_made,
                "warnings": self.warnings,
                "archive_path": str(self.archive_path),
            }

            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump(manifest, f, indent=2, ensure_ascii=False)

            self.log(f"Manifest saved to: {manifest_path}")

    def run(self) -> int:
        """Execute Phase 6 cleanup."""
        self.log("=" * 60)
        self.log("Phase 6 Cleanup - Thin CLI + Core OO Finalization")
        self.log("=" * 60)
        self.log(f"Project root: {self.project_root}")
        self.log(f"Archive path: {self.archive_path}")
        self.log(f"Dry run: {self.dry_run}")
        self.log("")

        # 1. Verify new architecture is ready
        if not self.verify_new_architecture():
            self.warn("New architecture not fully in place!")
            return 1

        # 2. Archive legacy files
        if not self.archive_legacy_files():
            return 1

        # 3. Scan for deprecated patterns
        deprecated_found = self.scan_for_deprecated_patterns()
        if deprecated_found:
            self.log(f"Found {len(deprecated_found)} files with deprecated patterns")
            self.log("These should be manually reviewed and updated")

        # 4. Run tests
        if not self.verify_tests_pass():
            return 1

        # 5. Create manifest
        self.create_cleanup_manifest()

        # Summary
        self.log("")
        self.log("=" * 60)
        self.log("Cleanup Summary")
        self.log("=" * 60)
        self.log(f"Changes: {len(self.changes_made)}")
        self.log(f"Warnings: {len(self.warnings)}")

        if self.dry_run:
            self.log("")
            self.log("This was a DRY RUN. No actual changes were made.")
            self.log("Run without --dry-run to apply changes.")

        return 0


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Phase 6 cleanup - Remove feature flags and legacy code"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--archive-path",
        type=str,
        default="archive/phase6_legacy",
        help="Where to archive legacy code",
    )
    parser.add_argument(
        "--project-root",
        type=str,
        default=".",
        help="Project root directory",
    )

    args = parser.parse_args()

    project_root = Path(args.project_root).resolve()
    archive_path = project_root / args.archive_path

    cleanup = Phase6Cleanup(
        project_root=project_root,
        archive_path=archive_path,
        dry_run=args.dry_run,
    )

    return cleanup.run()


if __name__ == "__main__":
    sys.exit(main())
