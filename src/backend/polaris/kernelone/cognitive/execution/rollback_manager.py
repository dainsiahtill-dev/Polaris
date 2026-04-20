"""Rollback Manager - State snapshots and recovery with I/O verification."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class RollbackPlan:
    """Plan for rolling back an action."""

    plan_id: str
    created_at: str
    status: str = "pending"  # pending | executed | aborted
    steps: tuple[str, ...] = field(default_factory=tuple)
    targets: tuple[str, ...] = field(default_factory=tuple)
    etags: dict[str, str] = field(default_factory=dict)  # file_path -> hash


@dataclass(frozen=True)
class RollbackResult:
    """Result of rollback execution."""

    status: str  # SUCCESS | ABORTED | PARTIAL
    reason: str | None
    required_action: str | None  # MANUAL_INTERVENTION | RETRY | NONE
    plan: RollbackPlan
    executed_steps: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class FileSnapshot:
    """Snapshot of a file at a point in time."""

    path: str
    existed_before: bool
    content: str
    hash: str
    size: int
    created_at: str


class RollbackManager:
    """
    Manages rollback preparation and execution for L2+ operations.

    CRITICAL: Implements ETag verification to detect state drift.
    If files have been modified externally, rollback is ABORTED.
    This follows L1 (Truthfulness > Consistency) principle.
    """

    def __init__(self, max_rollback_steps: int = 3) -> None:
        self._max_steps = max_rollback_steps
        self._plans: dict[str, RollbackPlan] = {}
        self._snapshots: dict[str, FileSnapshot] = {}

    async def prepare_rollback(
        self,
        action_description: str,
        target_paths: tuple[str, ...],
    ) -> RollbackPlan:
        """
        Prepare rollback by capturing state snapshots.

        This MUST be called BEFORE executing the action.
        """
        plan_id = f"rollback_{abs(hash(action_description)) % (10**8):08d}"

        # Capture snapshots and compute ETags
        etags = {}
        unreadable: list[str] = []
        for path_str in target_paths:
            path = Path(path_str)
            if path.exists() and path.is_file():
                try:
                    content = path.read_text(encoding="utf-8")
                    file_hash = self._compute_hash(content)
                    etags[path_str] = file_hash

                    snapshot = FileSnapshot(
                        path=path_str,
                        existed_before=True,
                        content=content,
                        hash=file_hash,
                        size=len(content),
                        created_at=datetime.now(timezone.utc).isoformat(),
                    )
                    self._snapshots[f"{plan_id}:{path_str}"] = snapshot
                except (OSError, ValueError):
                    unreadable.append(path_str)
            elif path.exists():
                unreadable.append(path_str)
            else:
                # Creating a new file is rollback-capable: snapshot "absence"
                # so rollback can delete files that did not exist before.
                snapshot = FileSnapshot(
                    path=path_str,
                    existed_before=False,
                    content="",
                    hash="",
                    size=0,
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                self._snapshots[f"{plan_id}:{path_str}"] = snapshot

        if unreadable:
            raise ValueError(f"Cannot prepare rollback: {len(unreadable)} target(s) unreadable: {unreadable}")

        # Generate rollback steps (max 3)
        steps = [f"restore {p}" for p in target_paths[: self._max_steps]]

        plan = RollbackPlan(
            plan_id=plan_id,
            steps=tuple(steps),
            targets=target_paths,
            etags=etags,
            created_at=datetime.now(timezone.utc).isoformat(),
        )

        self._plans[plan_id] = plan
        return plan

    async def execute_rollback(self, plan: RollbackPlan) -> RollbackResult:
        """
        Execute rollback with ETag verification.

        ABORTS if state drift detected (external modifications).
        """
        # Step 1: Verify ETags before rollback
        current_etags = {}
        for path_str in plan.targets:
            path = Path(path_str)
            if path.exists():
                try:
                    content = path.read_text(encoding="utf-8")
                    current_etags[path_str] = self._compute_hash(content)
                except (RuntimeError, ValueError):
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning("Failed to read file for hash computation: %s", path_str)

        # Step 2: Check for state drift on pre-existing files only.
        # Targets that did not exist before action are intentionally excluded.
        drifted_paths = [
            path_str for path_str, expected_hash in plan.etags.items() if current_etags.get(path_str) != expected_hash
        ]
        if drifted_paths:
            return RollbackResult(
                status="ABORTED",
                reason=f"State drift detected on pre-existing files: {drifted_paths}",
                required_action="MANUAL_INTERVENTION",
                plan=plan,
                executed_steps=(),
            )

        # Step 3: Execute rollback
        executed = []
        failed = []
        for path_str in plan.targets:
            snapshot_key = f"{plan.plan_id}:{path_str}"
            if snapshot_key in self._snapshots:
                snapshot = self._snapshots[snapshot_key]
                try:
                    target = Path(snapshot.path)
                    if snapshot.existed_before:
                        target.write_text(snapshot.content, encoding="utf-8")
                        executed.append(f"restored {snapshot.path}")
                    else:
                        if target.exists():
                            if target.is_file():
                                target.unlink()
                            else:
                                failed.append(path_str)
                                continue
                        executed.append(f"removed {snapshot.path}")
                except (OSError, ValueError):
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.warning("Failed to restore snapshot: %s", snapshot.path)
                    failed.append(path_str)

        # Step 4: Re-verify ETag after rollback
        if failed:
            # Cleanup snapshots and plan even on PARTIAL failure to prevent leaks
            self._cleanup_plan_snapshots(plan.plan_id)
            self._plans.pop(plan.plan_id, None)
            return RollbackResult(
                status="PARTIAL",
                reason=f"Failed to restore {len(failed)} file(s): {failed}",
                required_action="RETRY",
                plan=plan,
                executed_steps=tuple(executed),
            )

        # Verify restored content matches snapshots
        verification_failures = []
        for path_str in plan.targets:
            snapshot_key = f"{plan.plan_id}:{path_str}"
            if snapshot_key in self._snapshots:
                snapshot = self._snapshots[snapshot_key]
                try:
                    current_path = Path(snapshot.path)
                    if snapshot.existed_before:
                        current_content = current_path.read_text(encoding="utf-8")
                        current_hash = self._compute_hash(current_content)
                        if current_hash != snapshot.hash:
                            verification_failures.append(f"{path_str}: hash mismatch")
                    elif current_path.exists():
                        verification_failures.append(f"{path_str}: expected missing after rollback")
                except (OSError, ValueError):
                    import logging

                    logger = logging.getLogger(__name__)
                    logger.exception("Failed to verify restored snapshot: %s", path_str)
                    verification_failures.append(f"{path_str}: read error")

        if verification_failures:
            # Cleanup snapshots and plan even on PARTIAL failure to prevent leaks
            self._cleanup_plan_snapshots(plan.plan_id)
            self._plans.pop(plan.plan_id, None)
            return RollbackResult(
                status="PARTIAL",
                reason=f"Verification failures: {verification_failures}",
                required_action="RETRY",
                plan=plan,
                executed_steps=tuple(executed),
            )

        # H-6 Fix: Cleanup snapshots and plan after successful rollback
        self._cleanup_plan_snapshots(plan.plan_id)
        self._plans.pop(plan.plan_id, None)

        return RollbackResult(
            status="SUCCESS",
            reason=None,
            required_action="NONE",
            plan=plan,
            executed_steps=tuple(executed),
        )

    def _compute_hash(self, content: str) -> str:
        """Compute SHA256 hash of content."""
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]

    async def abort_rollback(self, plan: RollbackPlan) -> RollbackResult:
        """Abort a pending rollback plan."""
        # H-6 Fix: Cleanup snapshots and plan on abort
        self._cleanup_plan_snapshots(plan.plan_id)
        self._plans.pop(plan.plan_id, None)

        return RollbackResult(
            status="ABORTED",
            reason="User requested abort",
            required_action="NONE",
            plan=plan,
            executed_steps=(),
        )

    def _cleanup_plan_snapshots(self, plan_id: str) -> None:
        """Remove all snapshots associated with a plan."""
        keys_to_remove = [key for key in self._snapshots if key.startswith(f"{plan_id}:")]
        for key in keys_to_remove:
            self._snapshots.pop(key, None)
