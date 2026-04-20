"""Benchmark Baseline Database.

This module provides the BenchmarkDB class for storing and loading
benchmark baselines, supporting version tracking and branch comparisons.

Design Principles
----------------
- File-based storage: Each baseline stored as a JSON file
- Branch-aware: Track baselines per branch for parallel development
- Immutable: Baselines are never modified, only new ones added

Example
-------
    from polaris.kernelone.benchmark.reporting import BenchmarkDB

    db = BenchmarkDB("reports/baselines")

    # Save baseline
    db.save_baseline(
        metrics={
            "latency_p50": 120.0,
            "latency_p90": 200.0,
            "score": 0.95,
        },
        branch="main",
        commit="abc1234",
    )

    # Load baseline
    baseline = db.load_baseline("main")
    print(baseline)  # {"latency_p50": 120.0, ...}

    # List all baselines
    baselines = db.list_baselines("main")
    for b in baselines:
        print(f"{b['commit']}: score={b['metrics'].get('score', 0)}")
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# ------------------------------------------------------------------
# Baseline Entry
# ------------------------------------------------------------------


@dataclass(frozen=True, kw_only=True)
class BaselineEntry:
    """A single baseline entry with metrics and metadata.

    Attributes:
        branch: Git branch name.
        commit: Git commit hash.
        timestamp: ISO timestamp when baseline was created.
        metrics: Dictionary of metric_name -> value.
        environment: Optional environment metadata.
        message: Optional commit message or note.
    """

    branch: str
    commit: str
    timestamp: str
    metrics: dict[str, float] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "branch": self.branch,
            "commit": self.commit,
            "timestamp": self.timestamp,
            "metrics": self.metrics,
            "environment": self.environment,
            "message": self.message,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BaselineEntry:
        return cls(
            branch=data.get("branch", ""),
            commit=data.get("commit", ""),
            timestamp=data.get("timestamp", datetime.now(timezone.utc).isoformat()),
            metrics=data.get("metrics", {}),
            environment=data.get("environment", {}),
            message=data.get("message", ""),
        )


# ------------------------------------------------------------------
# BenchmarkDB
# ------------------------------------------------------------------


class BenchmarkDB:
    """File-based benchmark baseline database.

    This class manages benchmark baselines stored as JSON files.
    Each baseline is immutable once written.

    Attributes:
        db_path: Path to the database directory.

    Example
    -------
        db = BenchmarkDB("reports/baselines")

        # Save a baseline
        db.save_baseline(
            metrics={"latency_p50": 120.0, "score": 0.95},
            branch="main",
            commit="abc1234",
        )

        # Load the latest baseline for a branch
        baseline = db.load_baseline("main")

        # Get historical baselines
        history = db.get_baseline_history("main")
    """

    def __init__(self, db_path: str) -> None:
        """Initialize the benchmark database.

        Args:
            db_path: Path to the database directory.
        """
        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

    def save_baseline(
        self,
        metrics: dict[str, float],
        branch: str,
        commit: str,
        environment: dict[str, str] | None = None,
        message: str = "",
    ) -> BaselineEntry:
        """Save a new baseline.

        Args:
            metrics: Dictionary of metric_name -> value.
            branch: Git branch name.
            commit: Git commit hash.
            environment: Optional environment metadata.
            message: Optional note or message.

        Returns:
            The saved BaselineEntry.
        """
        entry = BaselineEntry(
            branch=branch,
            commit=commit,
            timestamp=datetime.now(timezone.utc).isoformat(),
            metrics=dict(metrics),
            environment=environment or self._get_environment(),
            message=message,
        )

        # Create filename: baseline_{branch}_{commit[:8]}_{timestamp}.json
        safe_branch = self._sanitize_filename(branch)
        safe_commit = commit[:8] if len(commit) >= 8 else commit
        timestamp_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        filename = f"baseline_{safe_branch}_{safe_commit}_{timestamp_str}.json"

        path = self.db_path / filename
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        return entry

    def load_baseline(
        self,
        branch: str,
        commit: str | None = None,
    ) -> dict[str, float]:
        """Load the latest or specific baseline for a branch.

        Args:
            branch: Git branch name.
            commit: Optional specific commit hash (loads latest if not provided).

        Returns:
            Dictionary of metric_name -> value.
        """
        if commit:
            entry = self._load_specific(branch, commit)
            if entry:
                return entry.metrics
            return {}

        entry = self._load_latest(branch)
        if entry:
            return entry.metrics
        return {}

    def get_baseline_history(
        self,
        branch: str,
        limit: int = 10,
    ) -> list[BaselineEntry]:
        """Get historical baselines for a branch.

        Args:
            branch: Git branch name.
            limit: Maximum number of entries to return.

        Returns:
            List of BaselineEntry objects, newest first.
        """
        safe_branch = self._sanitize_filename(branch)
        pattern = f"baseline_{safe_branch}_*.json"

        files = sorted(
            self.db_path.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        entries: list[BaselineEntry] = []
        for path in files[:limit]:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                entries.append(BaselineEntry.from_dict(data))
            except (json.JSONDecodeError, OSError):
                continue

        return entries

    def list_baselines(
        self,
        branch: str | None = None,
    ) -> list[dict[str, Any]]:
        """List all baseline files with metadata.

        Args:
            branch: Optional branch filter.

        Returns:
            List of baseline metadata dictionaries.
        """
        if branch:
            safe_branch = self._sanitize_filename(branch)
            pattern = f"baseline_{safe_branch}_*.json"
        else:
            pattern = "baseline_*.json"

        files = sorted(
            self.db_path.glob(pattern),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )

        results: list[dict[str, Any]] = []
        for path in files:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                results.append(
                    {
                        "filename": path.name,
                        "branch": data.get("branch", ""),
                        "commit": data.get("commit", ""),
                        "timestamp": data.get("timestamp", ""),
                        "metrics_count": len(data.get("metrics", {})),
                    }
                )
            except (json.JSONDecodeError, OSError):
                continue

        return results

    def compare_baselines(
        self,
        branch: str,
        commit_a: str,
        commit_b: str,
    ) -> dict[str, dict[str, float]] | None:
        """Compare two baselines for the same branch.

        Args:
            branch: Git branch name.
            commit_a: First commit hash.
            commit_b: Second commit hash.

        Returns:
            Dictionary with comparison data, or None if either baseline not found.
        """
        baseline_a = self._load_specific(branch, commit_a)
        baseline_b = self._load_specific(branch, commit_b)

        if not baseline_a or not baseline_b:
            return None

        comparison: dict[str, dict[str, float]] = {}
        all_metrics = set(baseline_a.metrics.keys()) | set(baseline_b.metrics.keys())

        for metric in all_metrics:
            val_a = baseline_a.metrics.get(metric, 0.0)
            val_b = baseline_b.metrics.get(metric, 0.0)
            diff = val_b - val_a
            pct = (diff / val_a * 100) if val_a != 0 else 0.0

            comparison[metric] = {
                "a": val_a,
                "b": val_b,
                "diff": diff,
                "change_percent": pct,
            }

        return comparison

    def delete_baseline(
        self,
        branch: str,
        commit: str,
    ) -> bool:
        """Delete a specific baseline.

        Args:
            branch: Git branch name.
            commit: Git commit hash.

        Returns:
            True if deleted, False if not found.
        """
        entry = self._load_specific(branch, commit)
        if not entry:
            return False

        # Find and delete the file
        safe_branch = self._sanitize_filename(branch)
        pattern = f"baseline_{safe_branch}_{commit[:8]}_*.json"

        for path in self.db_path.glob(pattern):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("commit") == commit:
                    path.unlink()
                    return True
            except (json.JSONDecodeError, OSError):
                continue

        return False

    def export_baseline(
        self,
        branch: str,
        output_path: str,
    ) -> bool:
        """Export the latest baseline to a file.

        Args:
            branch: Git branch name.
            output_path: Path to write the export.

        Returns:
            True if exported, False if no baseline found.
        """
        entry = self._load_latest(branch)
        if not entry:
            return False

        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(entry.to_dict(), indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        return True

    def _load_latest(self, branch: str) -> BaselineEntry | None:
        """Load the latest baseline for a branch."""
        history = self.get_baseline_history(branch, limit=1)
        return history[0] if history else None

    def _load_specific(self, branch: str, commit: str) -> BaselineEntry | None:
        """Load a specific baseline by commit."""
        safe_branch = self._sanitize_filename(branch)
        pattern = f"baseline_{safe_branch}_{commit[:8]}_*.json"

        for path in self.db_path.glob(pattern):
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                if data.get("commit") == commit:
                    return BaselineEntry.from_dict(data)
            except (json.JSONDecodeError, OSError):
                continue

        return None

    def _sanitize_filename(self, name: str) -> str:
        """Sanitize a string for use in filenames."""
        return name.replace("/", "_").replace("\\", "_").replace(".", "_").replace(" ", "_")

    def _get_environment(self) -> dict[str, str]:
        """Get current environment information."""
        return {
            "python_version": os.environ.get("PYTHON_VERSION", ""),
            "platform": os.environ.get("PLATFORM", ""),
            "polaris_version": (os.environ.get("KERNELONE_VERSION") or os.environ.get("POLARIS_VERSION", "")),
        }
