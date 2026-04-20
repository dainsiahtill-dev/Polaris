"""Write Gate - Enforce write scope to prevent unauthorized modifications.

Prevents AI from "accidentally" or "hallucinatorily" modifying files outside
the declared scope.

Migrated from: app/services/director_logic_rules.py
"""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WriteGateResult:
    """Result of write gate validation."""

    allowed: bool
    reason: str = ""
    extra_files: list[str] | None = None

    def __post_init__(self):
        if self.extra_files is None:
            object.__setattr__(self, "extra_files", [])


class WriteGate:
    """Enforces that AI only modifies files within declared scope."""

    @staticmethod
    def validate(
        changed_files: list[str],
        act_files: list[str],
        pm_target_files: list[str] | None = None,
        *,
        require_change: bool = False,
    ) -> WriteGateResult:
        """Validate that changed files are within allowed scope.

        Args:
            changed_files: Files that were actually modified
            act_files: Files declared in the act (planner output)
            pm_target_files: PM task target files (optional, stricter check)
            require_change: Whether at least one change is required

        Returns:
            WriteGateResult indicating if write is allowed
        """
        # Normalize all paths
        normalized_changed = set(_normalize_paths(changed_files))
        normalized_act = set(_normalize_paths(act_files))
        normalized_pm = set(_normalize_paths(pm_target_files or []))

        # Check 1: Must have changes if required
        if require_change and not normalized_changed:
            return WriteGateResult(
                allowed=False,
                reason="No files were changed",
            )

        # Check 2: Changed files must be subset of act.files
        if normalized_changed and normalized_act:
            extra_in_act = normalized_changed - normalized_act
            if extra_in_act:
                return WriteGateResult(
                    allowed=False,
                    reason=f"Changed files exceed act.files scope: {sorted(extra_in_act)}",
                    extra_files=sorted(extra_in_act),
                )

        # Check 3: If PM target files specified, validate against them
        if normalized_pm and normalized_changed:
            # Allow "companion files" (tests, configs) with relaxed checking
            companion_patterns = (".test.", ".spec.", "test_", "_test.", ".config.", ".json", ".md")

            for changed in normalized_changed:
                # Skip companion files for PM scope check
                if any(p in changed for p in companion_patterns):
                    continue

                # Check if within PM scope
                if not _scope_matches(changed, normalized_pm):
                    return WriteGateResult(
                        allowed=False,
                        reason=f"Changed file '{changed}' not within PM target_files scope",
                        extra_files=[changed],
                    )

        return WriteGateResult(allowed=True, reason="Write scope validated")


def validate_write_scope(
    changed_files: list[str],
    allowed_scope: list[str],
    workspace: str = ".",
) -> WriteGateResult:
    """Convenience function to validate write scope.

    Args:
        changed_files: Files that were modified
        allowed_scope: List of allowed file patterns/paths
        workspace: Workspace root

    Returns:
        Validation result
    """
    return WriteGate.validate(
        changed_files=changed_files,
        act_files=allowed_scope,
        pm_target_files=allowed_scope,
    )


def _normalize_paths(paths: list[str]) -> list[str]:
    """Normalize paths for comparison."""
    result = []
    for p in paths:
        if not p:
            continue
        # Normalize separators, remove leading ./
        normalized = os.path.normpath(p).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        result.append(normalized)
    return result


def _scope_matches(file_path: str, scopes: set[str]) -> bool:
    """Check if file_path matches any scope pattern.

    Supports:
    - Exact match: "src/fastapi_entrypoint.py" matches "src/fastapi_entrypoint.py"
    - Directory prefix: "src/utils/helper.py" matches "src/utils"
    - Module prefix: "src/utils/helper.py" matches "src/utils" (as directory)
    """
    normalized_path = file_path.replace("\\", "/")

    for scope in scopes:
        normalized_scope = scope.replace("\\", "/").rstrip("/")

        # Exact match
        if normalized_path == normalized_scope:
            return True

        # Wildcards
        if normalized_scope in (".", "*", "**"):
            return True

        # Directory prefix match
        if normalized_path.startswith(normalized_scope + "/"):
            return True

        # Check if scope is a directory containing the file
        scope_as_dir = normalized_scope
        if normalized_path.startswith(scope_as_dir + "/"):
            return True

    return False
