"""Existence Gate - Zero-cost file existence validation.

Prevents AI from claiming "modified file" when it doesn't exist.
Migrated from: core/polaris_loop/existence_gate.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class GateResult:
    """Result of existence gate check."""

    mode: str  # "create", "modify", "mixed"
    existing: list[str]
    missing: list[str]
    all_exist: bool
    all_missing: bool


class ExistenceGate:
    """Zero-LLM-cost file existence validator.

    This is the first line of defense against hallucination:
    - Pure Python implementation
    - No LLM calls
    - Distinguishes create vs modify operations
    """

    @staticmethod
    def check(
        target_files: list[str],
        workspace: str,
        *,
        mode_hint: str | None = None,
    ) -> GateResult:
        """Check file existence and determine operation mode.

        Args:
            target_files: List of target file paths (relative to workspace)
            workspace: Workspace root directory
            mode_hint: Optional hint ("create", "modify", "mixed")

        Returns:
            GateResult with mode classification and file lists
        """
        clean_targets = [t.strip() for t in target_files if t.strip()]

        existing: list[str] = []
        missing: list[str] = []

        for rel in clean_targets:
            full = os.path.join(workspace, rel) if workspace else rel
            if os.path.exists(full):
                existing.append(rel)
            else:
                missing.append(rel)

        # Determine mode
        if mode_hint in ("create", "modify", "mixed"):
            mode = mode_hint
        # Auto-detect mode
        elif not existing:
            mode = "create"
        elif not missing:
            mode = "modify"
        else:
            mode = "mixed"

        return GateResult(
            mode=mode,
            existing=existing,
            missing=missing,
            all_exist=len(missing) == 0 and len(existing) > 0,
            all_missing=len(existing) == 0 and len(missing) > 0,
        )

    @staticmethod
    def filter_existing(
        target_files: list[str],
        workspace: str,
    ) -> list[str]:
        """Filter to only existing files."""
        result = ExistenceGate.check(target_files, workspace)
        return result.existing

    @staticmethod
    def filter_missing(
        target_files: list[str],
        workspace: str,
    ) -> list[str]:
        """Filter to only missing files."""
        result = ExistenceGate.check(target_files, workspace)
        return result.missing


def check_mode(
    target_files: list[str],
    workspace: str,
    *,
    mode_hint: str | None = None,
) -> GateResult:
    """Convenience function for existence gate check."""
    return ExistenceGate.check(target_files, workspace, mode_hint=mode_hint)
