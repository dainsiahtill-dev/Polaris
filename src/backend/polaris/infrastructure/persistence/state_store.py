r"""State machine file persistence for Director v2.

Stores state machine snapshots for recovery and audit trail.
Compatible with original Director lifecycle format.

CRITICAL: All state is stored OUTSIDE the workspace to avoid pollution.
Storage locations (in priority order):
1. Ramdisk (X:\) if available and KERNELONE_STATE_TO_RAMDISK is enabled
2. System cache directory (%LOCALAPPDATA%\Polaris\cache or ~/.cache/polaris)
3. Explicit KERNELONE_RUNTIME_ROOT

Path structure: {runtime_base}/<metadata_dir>/projects/{workspace_key}/runtime/state/{task_id}/
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# No Domain entity imports: this Infrastructure layer accepts Dict[str, Any].
# The caller (task_lifecycle_service) serializes TaskStateMachine.to_dict()
# before calling save_state(), breaking the Infrastructure->Domain dependency.
# Required keys in payload dict: task_id, current_phase, context, is_terminal.
# Architectural note: TaskPhase enum values are serialized as strings in the
# dict (e.g. "PLANNING").


class StateNotFoundError(Exception):
    """Raised when state is not found."""

    pass


class StateStore:
    """File-based state storage for Director v2.

    State files are stored OUTSIDE workspace to avoid pollution.
    Compatible with original Director DIRECTOR_LIFECYCLE.json format.
    """

    LIFECYCLE_FILE = "DIRECTOR_LIFECYCLE.json"
    STATE_FILE = "state.json"
    TRAJECTORY_FILE = "trajectory.jsonl"
    MANIFEST_FILE = "state_manifest.json"

    def __init__(self, runtime_root: str | Path) -> None:
        self.runtime_root = Path(runtime_root).resolve()
        self.state_dir = self.runtime_root / "state"

    def _get_task_state_dir(self, task_id: str) -> Path:
        """Get state directory for a task."""
        path = self.state_dir / task_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)

    @staticmethod
    def _write_text_atomic(path: Path, text: str) -> None:
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        with open(tmp_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        tmp_path.replace(path)

    def _load_manifest(self, task_dir: Path) -> dict[str, Any]:
        manifest_path = task_dir / self.MANIFEST_FILE
        if not manifest_path.exists():
            return {}
        try:
            with open(manifest_path, encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError):
            return {}

    def _resolve_committed_file(self, task_dir: Path, file_name: str, manifest_key: str) -> Path:
        manifest = self._load_manifest(task_dir)
        files_raw = manifest.get("files")
        files: dict[str, Any] = files_raw if isinstance(files_raw, dict) else {}
        candidate_name = str(files.get(manifest_key) or file_name).strip()
        candidate = task_dir / candidate_name
        if candidate.exists():
            return candidate
        return task_dir / file_name

    def save_state(
        self,
        payload: dict[str, Any],
        *,
        run_id: str = "",
        phase: str = "",
        status: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Save state machine to file.

        Args:
            payload: State machine as dictionary. Required keys: ``task_id``,
                     ``current_phase`` (str), ``context`` (dict), ``is_terminal`` (bool).
                     Optional: ``trajectory`` (list of dicts).
                     Duck-typed to accept both plain dicts and TaskStateMachine objects.
            run_id: Optional run identifier
            phase: Current phase name
            status: Current status
            details: Additional details

        Returns:
            Metadata about saved files
        """
        # Duck-type: accept TaskStateMachine or plain dict.
        # ACGA 2.0: Infrastructure accepts Dict[str, Any]; caller serializes.
        to_dict_fn = getattr(payload, "to_dict", None)
        if callable(to_dict_fn):
            # TaskStateMachine object: serialize to dict
            sm_dict: dict[str, Any] = to_dict_fn()
        else:
            sm_dict = dict(payload)  # shallow copy

        # Extract required fields; raise KeyError for missing keys per contract
        task_id: str = sm_dict["task_id"]
        current_phase_str: str = sm_dict["current_phase"]
        context: dict[str, Any] = sm_dict["context"]
        is_terminal: bool = sm_dict["is_terminal"]
        trajectory: list[dict[str, Any]] = sm_dict.get("trajectory", [])

        task_dir = self._get_task_state_dir(task_id)

        # Save current state
        state_file = task_dir / self.STATE_FILE
        state_data = {
            "task_id": task_id,
            "current_phase": current_phase_str,
            "context": {
                "workspace": context.get("workspace", ""),
                "build_round": context.get("build_round", 0),
                "stall_count": context.get("stall_count", 0),
                "changed_files": context.get("changed_files", []),
            },
            "is_terminal": is_terminal,
            "saved_at": datetime.now(timezone.utc).isoformat(),
        }

        trajectory_file = task_dir / self.TRAJECTORY_FILE
        lifecycle_path = task_dir / self.LIFECYCLE_FILE
        lifecycle_payload = self._update_lifecycle(
            lifecycle_path,
            task_id=task_id,
            run_id=run_id,
            phase=phase or current_phase_str.lower(),
            status=status,
            terminal=is_terminal,
            details=details,
            persist=False,
        )

        trajectory_text = "".join(f"{json.dumps(entry, ensure_ascii=False, default=str)}\n" for entry in trajectory)
        self._write_json_atomic(state_file, state_data)
        self._write_text_atomic(trajectory_file, trajectory_text)
        self._write_json_atomic(lifecycle_path, lifecycle_payload)
        self._write_json_atomic(
            task_dir / self.MANIFEST_FILE,
            {
                "schema_version": 1,
                "task_id": task_id,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "files": {
                    "state": self.STATE_FILE,
                    "trajectory": self.TRAJECTORY_FILE,
                    "lifecycle": self.LIFECYCLE_FILE,
                },
            },
        )

        return {
            "state_path": str(state_file),
            "trajectory_path": str(trajectory_file),
            "lifecycle_path": str(lifecycle_path),
            "manifest_path": str(task_dir / self.MANIFEST_FILE),
            "task_id": task_id,
            "phase": current_phase_str,
        }

    def _update_lifecycle(
        self,
        path: Path,
        *,
        task_id: str = "",
        run_id: str = "",
        phase: str = "",
        status: str = "",
        terminal: bool = False,
        details: dict[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        """Update lifecycle file (compatible with old Director format).

        Format:
        {
            "schema_version": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
            "run_id": "...",
            "task_id": "...",
            "phase": "...",
            "startup_completed": true,
            "execution_started": true,
            "terminal": false,
            "status": "...",
            "events": [...],
            "updated_at": "..."
        }
        """
        now_iso = datetime.now(timezone.utc).isoformat()

        # Load existing or create new
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, encoding="utf-8") as f:
                    loaded = json.load(f)
                    if isinstance(loaded, dict):
                        payload = loaded
            except (OSError, json.JSONDecodeError):
                payload = {}  # Reset on error
        # else: payload already initialized as empty dict above

        # Initialize if needed
        if not payload:
            payload = {
                "schema_version": 1,
                "created_at": now_iso,
                "run_id": str(run_id or "").strip(),
                "task_id": str(task_id or "").strip(),
                "phase": "",
                "startup_completed": False,
                "execution_started": False,
                "terminal": False,
                "status": "",
                "events": [],
                "updated_at": now_iso,
            }

        # Update fields
        if run_id:
            payload["run_id"] = str(run_id).strip()
        if task_id:
            payload["task_id"] = str(task_id).strip()
        if phase:
            payload["phase"] = str(phase).strip().lower()
        if status:
            payload["status"] = str(status).strip().lower()
        if terminal is not None:
            payload["terminal"] = bool(terminal)

        # Track phase-based flags
        if payload["phase"] in ("planning", "validation"):
            payload["startup_completed"] = True
            if not payload.get("startup_at"):
                payload["startup_at"] = now_iso
        if payload["phase"] in ("execution", "verification"):
            payload["execution_started"] = True
            if not payload.get("execution_started_at"):
                payload["execution_started_at"] = now_iso
        if payload["terminal"] and not payload.get("terminal_at"):
            payload["terminal_at"] = now_iso

        # Add event
        event = {
            "ts": now_iso,
            "phase": payload["phase"],
        }
        if status:
            event["status"] = str(status).strip().lower()
        if details:
            event["details"] = details

        events = payload.get("events", [])
        events.append(event)
        payload["events"] = events[-50:]  # Keep last 50 events
        payload["updated_at"] = now_iso

        if persist:
            self._write_json_atomic(path, payload)

        return payload

    def load_state(self, task_id: str) -> dict[str, Any]:
        """Load state for a task.

        Args:
            task_id: Task identifier

        Returns:
            State data as dictionary

        Raises:
            StateNotFoundError: If state file doesn't exist
        """
        task_dir = self.state_dir / task_id
        state_file = self._resolve_committed_file(task_dir, self.STATE_FILE, "state")

        if not state_file.exists():
            raise StateNotFoundError(f"State not found for task {task_id}")

        with open(state_file, encoding="utf-8") as f:
            return json.load(f)

    def load_lifecycle(self, task_id: str) -> dict[str, Any]:
        """Load lifecycle for a task (compatible with old Director).

        Args:
            task_id: Task identifier

        Returns:
            Lifecycle data as dictionary
        """
        task_dir = self.state_dir / task_id
        lifecycle_file = self._resolve_committed_file(task_dir, self.LIFECYCLE_FILE, "lifecycle")

        if not lifecycle_file.exists():
            return {}

        try:
            with open(lifecycle_file, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def load_trajectory(self, task_id: str) -> list[dict[str, Any]]:
        """Load trajectory for a task.

        Args:
            task_id: Task identifier

        Returns:
            List of trajectory entries
        """
        task_dir = self.state_dir / task_id
        trajectory_file = self._resolve_committed_file(task_dir, self.TRAJECTORY_FILE, "trajectory")

        if not trajectory_file.exists():
            return []

        entries = []
        with open(trajectory_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

        return entries

    def list_tasks(self) -> list[dict[str, Any]]:
        """List all tasks with saved state.

        Returns:
            List of task metadata
        """
        if not self.state_dir.exists():
            return []

        results = []
        for task_dir in self.state_dir.iterdir():
            if task_dir.is_dir():
                state_file = task_dir / self.STATE_FILE
                manifest_path = task_dir / self.MANIFEST_FILE
                if state_file.exists():
                    try:
                        with open(state_file, encoding="utf-8") as f:
                            data = json.load(f)
                        results.append(
                            {
                                "task_id": data.get("task_id", task_dir.name),
                                "current_phase": data.get("current_phase", "UNKNOWN"),
                                "is_terminal": data.get("is_terminal", False),
                                "saved_at": data.get("saved_at"),
                                "path": str(state_file),
                                "manifest_path": str(manifest_path) if manifest_path.exists() else "",
                            }
                        )
                    except (json.JSONDecodeError, OSError):
                        continue

        return results

    def get_latest_by_run(self, run_id: str) -> dict[str, Any] | None:
        """Get latest state for a run ID.

        Args:
            run_id: Run identifier

        Returns:
            State data or None if not found
        """
        latest = None
        latest_time = None

        for task_info in self.list_tasks():
            lifecycle = self.load_lifecycle(task_info["task_id"])
            if lifecycle.get("run_id") == run_id:
                saved_at = task_info.get("saved_at")
                if saved_at and (latest_time is None or saved_at > latest_time):
                    latest = task_info
                    latest_time = saved_at

        if latest:
            return self.load_state(latest["task_id"])

        return None
