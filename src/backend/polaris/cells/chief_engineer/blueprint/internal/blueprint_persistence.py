"""Disk persistence layer for Chief Engineer blueprints.

Provides atomic JSON file storage for construction blueprints so that
blueprint state survives process restarts.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from polaris.kernelone.storage import resolve_logical_path


class BlueprintPersistence:
    """Atomic file-based persistence for blueprint JSON documents.

    Each blueprint is stored as an individual JSON file under
    the KernelOne ``runtime/blueprints/{blueprint_id}.json`` storage domain.
    Writes are atomic (temp-file + replace) to avoid corruption.
    """

    def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
        """Initialize persistence for the given workspace.

        Args:
            workspace: Root workspace path.
            ensure_directory: Create the blueprint directory immediately when true.
        """
        self._dir = Path(resolve_logical_path(workspace, "runtime/blueprints"))
        # D-02/D-17: Also check workspace-local blueprints written by factory bench.
        self._workspace_dir = Path(workspace) / ".polaris" / "blueprints"
        if ensure_directory:
            self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, blueprint_id: str, data: dict[str, Any]) -> None:
        """Atomically persist a blueprint dictionary to disk.

        Args:
            blueprint_id: Unique blueprint identifier.
            data: Blueprint data to serialize.
        """
        p = self._dir / f"{blueprint_id}.json"
        tmp = p.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)

    def _candidate_paths(self, blueprint_id: str) -> list[Path]:
        """Return candidate file paths: runtime dir first, then workspace fallback."""
        paths = [self._dir / f"{blueprint_id}.json"]
        if hasattr(self, "_workspace_dir") and self._workspace_dir.is_dir():
            paths.append(self._workspace_dir / f"{blueprint_id}.json")
        return paths

    def load(self, blueprint_id: str) -> dict[str, Any] | None:
        """Load a blueprint dictionary from disk.

        Checks runtime dir first, then workspace-local fallback (D-02/D-17).

        Args:
            blueprint_id: Unique blueprint identifier.

        Returns:
            The deserialized blueprint, or None if not found or unreadable.
        """
        for p in self._candidate_paths(blueprint_id):
            if not p.exists():
                continue
            try:
                with open(p, encoding="utf-8") as f:
                    data: dict[str, Any] = json.load(f)
                    return data
            except (json.JSONDecodeError, OSError, TypeError):
                continue
        return None

    def delete(self, blueprint_id: str) -> bool:
        """Remove a persisted blueprint from disk.

        Args:
            blueprint_id: Unique blueprint identifier.

        Returns:
            True if the file existed and was removed, False otherwise.
        """
        p = self._dir / f"{blueprint_id}.json"
        if p.exists():
            p.unlink()
            return True
        return False

    def list_all(self) -> list[str]:
        """Return a list of all persisted blueprint IDs.

        Merges IDs from runtime dir and workspace-local fallback (D-02/D-17).
        """
        ids: set[str] = {p.stem for p in self._dir.glob("*.json")}
        if hasattr(self, "_workspace_dir") and self._workspace_dir.is_dir():
            ids.update(p.stem for p in self._workspace_dir.glob("*.json"))
        return sorted(ids)

    def load_all(self) -> list[dict[str, Any]]:
        """Load and return every persisted blueprint.

        Invalid JSON files are silently skipped.

        Returns:
            List of deserialized blueprint dictionaries.
        """
        results: list[dict[str, Any]] = []
        for blueprint_id in self.list_all():
            data = self.load(blueprint_id)
            if data is not None:
                results.append(data)
        return results
