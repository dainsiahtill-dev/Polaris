"""Generic write-scope validation for KernelOne tool file effects."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class WriteGateResult:
    """Result of write-scope validation."""

    allowed: bool
    reason: str = ""
    extra_files: list[str] | None = None

    def __post_init__(self) -> None:
        if self.extra_files is None:
            object.__setattr__(self, "extra_files", [])


class WriteGate:
    """Enforce that generated writes stay within a declared scope."""

    @staticmethod
    def validate(
        changed_files: list[str],
        act_files: list[str],
        pm_target_files: list[str] | None = None,
        *,
        require_change: bool = False,
    ) -> WriteGateResult:
        """Validate that changed files are within the declared allowed scope."""
        normalized_changed = set(_normalize_paths(changed_files))
        normalized_act = set(_normalize_paths(act_files))
        normalized_pm = set(_normalize_paths(pm_target_files or []))

        if require_change and not normalized_changed:
            return WriteGateResult(
                allowed=False,
                reason="No files were changed",
            )

        if normalized_changed and normalized_act:
            extra_in_act = normalized_changed - normalized_act
            if extra_in_act:
                return WriteGateResult(
                    allowed=False,
                    reason=f"Changed files exceed act.files scope: {sorted(extra_in_act)}",
                    extra_files=sorted(extra_in_act),
                )

        if normalized_pm and normalized_changed:
            companion_patterns = (".test.", ".spec.", "test_", "_test.", ".config.", ".json", ".md")

            for changed in normalized_changed:
                if any(pattern in changed for pattern in companion_patterns):
                    continue
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
    """Validate write scope for compatibility callers."""
    return WriteGate.validate(
        changed_files=changed_files,
        act_files=allowed_scope,
        pm_target_files=allowed_scope,
    )


def _normalize_paths(paths: list[str]) -> list[str]:
    result = []
    for path in paths:
        if not path:
            continue
        normalized = os.path.normpath(path).replace("\\", "/")
        if normalized.startswith("./"):
            normalized = normalized[2:]
        result.append(normalized)
    return result


def _scope_matches(file_path: str, scopes: set[str]) -> bool:
    normalized_path = file_path.replace("\\", "/")

    for scope in scopes:
        normalized_scope = scope.replace("\\", "/").rstrip("/")
        if normalized_path == normalized_scope:
            return True
        if normalized_scope in (".", "*", "**"):
            return True
        if normalized_path.startswith(normalized_scope + "/"):
            return True
        scope_as_dir = normalized_scope
        if normalized_path.startswith(scope_as_dir + "/"):
            return True

    return False


__all__ = [
    "WriteGate",
    "WriteGateResult",
    "validate_write_scope",
]
