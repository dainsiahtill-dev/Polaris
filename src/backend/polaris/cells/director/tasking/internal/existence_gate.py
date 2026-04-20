"""
existence_gate.py — Pure-Python pre-flight mode detector for the Director.

Before spending any LLM tokens, Director calls `check_mode()` to determine
whether a task requires *creating* files from scratch or *modifying* existing
ones.  This single check eliminates the need for:

  • required_evidence.must_read blocking execution
  • should_bootstrap_direct_edit() (now inlined here)
  • The bootstrap fallback chain in loop-director.py

Design constraints:
  - Zero LLM calls.
  - Zero side-effects (read-only filesystem access).
  - Deterministic: same inputs always produce same output.
  - Testable without any mocks.
"""

from __future__ import annotations

import os
from typing import Any, Literal

# ---------------------------------------------------------------------------
# Public types
# ---------------------------------------------------------------------------

ExecutionMode = Literal["create", "modify", "mixed"]


class GateResult:
    """Result of an existence gate check."""

    __slots__ = (
        "existing",
        "existing_count",
        "missing",
        "missing_count",
        "mode",
        "target_total",
    )

    def __init__(
        self,
        mode: ExecutionMode,
        existing: list[str],
        missing: list[str],
    ) -> None:
        self.mode: ExecutionMode = mode
        self.existing: list[str] = existing
        self.missing: list[str] = missing
        self.existing_count: int = len(existing)
        self.missing_count: int = len(missing)
        self.target_total: int = len(existing) + len(missing)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "target_total": self.target_total,
            "existing_count": self.existing_count,
            "missing_count": self.missing_count,
            "existing": self.existing,
            "missing": self.missing,
        }

    def __repr__(self) -> str:  # pragma: no cover
        return f"GateResult(mode={self.mode!r}, existing={self.existing_count}, missing={self.missing_count})"


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------


def check_mode(
    target_files: list[str],
    workspace: str,
    *,
    mode_hint: str | None = None,
) -> GateResult:
    """Determine whether task targets need to be *created* or *modified*.

    Args:
        target_files: Relative paths declared in the PM task's ``target_files``.
        workspace:    Absolute workspace root path.
        mode_hint:    Optional explicit hint from PM task (``"create"``,
                      ``"modify"``, or ``"auto"``).  If not ``"auto"`` or
                      ``None``, it overrides the filesystem check *except* for
                      the existence data (always computed so callers can log it).

    Returns:
        A :class:`GateResult` with ``mode``, per-file existence lists, and
        summary counts.
    """
    # Normalise inputs
    workspace = os.path.abspath(workspace) if workspace else ""
    clean_targets: list[str] = [
        t.strip().lstrip("/\\").replace("\\", "/") for t in (target_files or []) if isinstance(t, str) and t.strip()
    ]

    existing: list[str] = []
    missing: list[str] = []

    for rel in clean_targets:
        full = os.path.join(workspace, rel) if workspace else rel
        if os.path.exists(full):
            existing.append(rel)
        else:
            missing.append(rel)

    # --- Explicit PM hint overrides auto-detection --------------------------
    hint = (mode_hint or "auto").lower().strip()
    if hint in ("create", "modify"):
        return GateResult(mode=hint, existing=existing, missing=missing)  # type: ignore[arg-type]

    # --- Auto-detect --------------------------------------------------------
    if not clean_targets:
        # No target files declared — Director will need to decide on its own;
        # treat as modify (read-then-write) since we can't know in advance.
        return GateResult(mode="modify", existing=[], missing=[])

    if missing_count := len(missing):
        existing_count = len(existing)
        if existing_count == 0:
            # ALL targets are missing → pure creation task.
            return GateResult(mode="create", existing=existing, missing=missing)
        if missing_count == existing_count or missing_count > existing_count:
            # More missing than existing → lean toward create for the absent ones.
            return GateResult(mode="mixed", existing=existing, missing=missing)
        # Some missing, but mostly existing → modify with new-file side-effects.
        return GateResult(mode="mixed", existing=existing, missing=missing)

    # All targets exist → pure modification.
    return GateResult(mode="modify", existing=existing, missing=missing)


# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def is_pure_create(result: GateResult) -> bool:
    """Return True only when ALL target files are absent (safe to skip reads)."""
    return result.mode == "create" and result.existing_count == 0


def is_any_missing(result: GateResult) -> bool:
    """Return True when at least one target file does not yet exist."""
    return result.missing_count > 0
