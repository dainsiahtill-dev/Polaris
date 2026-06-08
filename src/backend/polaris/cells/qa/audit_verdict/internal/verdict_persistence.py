"""Disk persistence layer for QA verdicts.

Provides atomic JSON file storage for QA audit verdicts so that a task's
latest verdict survives process restarts and can be read back as a
read-only context signal (mirrors `chief_engineer.blueprint` persistence).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_logical_path


class VerdictPersistence:
    """Atomic file-based persistence for QA verdict JSON documents.

    Each verdict is stored as an individual JSON file under the KernelOne
    ``runtime/qa_verdicts/{verdict_id}.json`` storage domain. Writes are
    atomic (temp-file + replace) to avoid corruption.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        self._dir = Path(resolve_logical_path(workspace, "runtime/qa_verdicts"))
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, verdict_id: str, data: dict[str, Any]) -> None:
        """Atomically persist a verdict dictionary to disk."""
        self._dir.mkdir(parents=True, exist_ok=True)
        p = self._dir / f"{verdict_id}.json"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    def load(self, verdict_id: str) -> dict[str, Any] | None:
        """Load a verdict dictionary from disk; None if missing/unreadable."""
        p = self._dir / f"{verdict_id}.json"
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                payload = json.load(f)
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def list_all(self) -> list[str]:
        """Return all persisted verdict IDs (sorted)."""
        if not self._dir.exists():
            return []
        return sorted(p.stem for p in self._dir.glob("*.json"))
