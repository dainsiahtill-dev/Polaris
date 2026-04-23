"""Artifact Service - Unified artifact I/O service for Polaris runtime.

This module provides a centralized service for reading and writing canonical
runtime artifacts with consistent UTF-8 encoding, atomic writes, and legacy path support.

Usage:
    from polaris.cells.audit.verdict.internal.artifact_service import ArtifactService

    service = ArtifactService(workspace=".")
    service.write_plan("# Plan content")
    plan = service.read_plan()
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from polaris.kernelone.fs import KernelFileSystem, get_default_adapter

logger = logging.getLogger(__name__)

# Import canonical helpers with narrow fallbacks so an optional import failure
# cannot silently downgrade runtime artifact path resolution.
try:
    from polaris.cells.runtime.artifact_store.public.service import resolve_artifact_path
except ImportError:

    def resolve_artifact_path(workspace_full: str, cache_root_full: str, rel_path: str) -> str:
        """Fallback path resolver that preserves logical storage prefixes."""
        raw = str(rel_path or "").strip()
        if not raw:
            return ""
        normalized = raw.replace("\\", "/").lstrip("/")
        if normalized == "runtime":
            base = cache_root_full or workspace_full
            return os.path.abspath(base)
        if normalized.startswith("runtime/"):
            suffix = normalized[len("runtime/") :]
            # Fallback: use workspace-based runtime under .polaris metadata dir
            from polaris.cells.storage.layout import polaris_home

            base = cache_root_full or os.path.join(polaris_home(), ".polaris-cache", "runtime")
            return os.path.abspath(os.path.join(base, suffix))
        if normalized == "workspace":
            from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

            return os.path.abspath(os.path.join(workspace_full, get_workspace_metadata_dir_name()))
        if normalized.startswith("workspace/"):
            suffix = normalized[len("workspace/") :]
            from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

            return os.path.abspath(os.path.join(workspace_full, get_workspace_metadata_dir_name(), suffix))
        if normalized == "config":
            from polaris.cells.storage.layout import polaris_home

            return os.path.abspath(os.path.join(polaris_home(), "config"))
        if normalized.startswith("config/"):
            suffix = normalized[len("config/") :]
            from polaris.cells.storage.layout import polaris_home

            return os.path.abspath(os.path.join(polaris_home(), "config", suffix))
        return os.path.abspath(os.path.join(workspace_full, normalized))


def _get_fs(workspace: str = ".") -> KernelFileSystem:
    """Get KernelFileSystem instance for the given workspace."""
    return KernelFileSystem(workspace, get_default_adapter())


def _write_text_atomic(path: str, text: str, *, workspace: str = ".") -> None:
    """Write text file atomically using KernelFileSystem.

    Args:
        path: Absolute path or path relative to workspace.
        text: Content to write.
        workspace: Workspace root path.
    """
    if not path:
        return
    fs = _get_fs(workspace)
    # Ensure parent directory exists
    parent = os.path.dirname(path)
    if parent:
        Path(parent).mkdir(parents=True, exist_ok=True)
    fs.write_text(path, text or "")


def _write_json_atomic(path: str, data: dict[str, Any], *, workspace: str = ".") -> None:
    """Write JSON file atomically.

    Args:
        path: Absolute path or path relative to workspace.
        data: JSON-serializable data.
        workspace: Workspace root path.
    """
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    _write_text_atomic(path, payload + "\n", workspace=workspace)


def _read_file_safe(path: str, *, workspace: str = ".") -> str:
    """Read file safely with UTF-8-first fallback handling.

    Args:
        path: Absolute path or path relative to workspace.
        workspace: Workspace root path.

    Returns:
        File content as string, or empty string if file doesn't exist.
    """
    if not path:
        return ""
    fs = _get_fs(workspace)
    try:
        return fs.read_text(path)
    except FileNotFoundError:
        return ""
    except UnicodeDecodeError as exc:
        logger.error("Failed to decode text artifact as UTF-8: %s", path, exc_info=exc)
        raise RuntimeError(f"Artifact is not valid UTF-8: {path}") from exc
    except OSError as exc:
        logger.error("Failed to read text artifact: %s", path, exc_info=exc)
        raise RuntimeError(f"Failed to read artifact file: {path}") from exc


def _read_json_file_strict(path: str, *, workspace: str = ".") -> dict[str, Any]:
    """Read and validate a JSON artifact as a dict.

    Args:
        path: Absolute path or path relative to workspace.
        workspace: Workspace root path.

    Returns:
        Parsed JSON data as dict.
    """
    try:
        fs = _get_fs(workspace)
        payload = fs.read_json(path)
    except UnicodeDecodeError as exc:
        logger.error("Failed to decode JSON artifact as UTF-8: %s", path, exc_info=exc)
        raise RuntimeError(f"JSON artifact is not valid UTF-8: {path}") from exc
    except json.JSONDecodeError as exc:
        logger.error("Failed to parse JSON artifact: %s", path, exc_info=exc)
        raise RuntimeError(f"JSON artifact is invalid: {path}") from exc
    except OSError as exc:
        logger.error("Failed to read JSON artifact: %s", path, exc_info=exc)
        raise RuntimeError(f"Failed to read JSON artifact file: {path}") from exc
    if not isinstance(payload, dict):
        logger.error("JSON artifact must be an object: %s (got %s)", path, type(payload).__name__)
        raise RuntimeError(f"JSON artifact root must be object: {path}")
    return payload


def _read_jsonl_safe(path: str, limit: int) -> list[dict[str, Any]]:
    """Read the last *limit* lines from a JSONL file as a list of dicts.

    Raises RuntimeError on encoding or JSON parse failures so callers can
    surface structured errors instead of swallowing bad data silently.
    """
    if not os.path.isfile(path):
        return []

    events: list[dict[str, Any]] = []
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except UnicodeDecodeError as exc:
        logger.error("Failed to decode JSONL artifact as UTF-8: %s", path, exc_info=exc)
        raise RuntimeError(f"JSONL artifact is not valid UTF-8: {path}") from exc
    except OSError as exc:
        logger.error("Failed to read JSONL artifact: %s", path, exc_info=exc)
        raise RuntimeError(f"Failed to read JSONL artifact file: {path}") from exc

    selected = lines[-limit:] if limit >= 0 else lines
    offset = max(0, len(lines) - len(selected))
    for index, raw_line in enumerate(selected, start=1):
        absolute_line = offset + index
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            logger.error(
                "Invalid JSONL line at %s:%d",
                path,
                absolute_line,
                exc_info=exc,
            )
            raise RuntimeError(f"Invalid JSONL line at {path}:{absolute_line}") from exc
        if not isinstance(payload, dict):
            logger.error(
                "JSONL line must be a JSON object at %s:%d (got %s)",
                path,
                absolute_line,
                type(payload).__name__,
            )
            raise RuntimeError(f"JSONL line must be object at {path}:{absolute_line}")
        events.append(payload)
    return events


try:
    from polaris.kernelone.fs.encoding import enforce_utf8
except ImportError:

    def enforce_utf8() -> None:
        pass


# Enforce UTF-8 on module load
enforce_utf8()


# ═══════════════════════════════════════════════════════════════════════════════
# Polaris Artifact Lifecycle Policy Metadata
# ═══════════════════════════════════════════════════════════════════════════════
# These are Polaris-specific business artifact lifecycle metadata.
# Keys MUST match entries in ARTIFACT_REGISTRY above.
# The canonical path is stored in ARTIFACT_REGISTRY; policy metadata lives here.

KERNELONE_ARTIFACT_POLICY_METADATA: dict[str, dict[str, Any]] = {
    # Plan Artifacts
    "contract.plan": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    "contract.gap_report": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    # PM Contract Artifacts
    "contract.pm_tasks": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": True,
    },
    "runtime.report.pm": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": True,
    },
    "runtime.state.pm": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    "contract.resident_goal": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    "contract.resident_goal_plan": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    # Director Artifacts
    "runtime.result.director": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": True,
    },
    "runtime.status.director": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    "runtime.log.director": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    # Event Artifacts
    "audit.events.runtime": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    "audit.events.pm": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    "audit.events.pm_llm": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    "audit.events.pm_task_history": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    "audit.events.director_llm": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    "audit.transcript": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": True,
        "archive_on_terminal": True,
    },
    # QA Artifacts
    "runtime.result.qa": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": True,
    },
    # Process Logs
    "runtime.log.pm_process": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    "runtime.log.director_process": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    # Memory & State
    "runtime.state.last": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    "runtime.status.engine": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    # Control Flags
    "runtime.control.pm_stop": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    "runtime.control.director_stop": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    "runtime.control.pause": {
        "category": "runtime_current",
        "lifecycle": "ephemeral",
        "compress": False,
        "archive_on_terminal": False,
    },
    # Agents Artifacts
    "contract.agents_draft": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
    "contract.agents_feedback": {
        "category": "runtime_current",
        "lifecycle": "active",
        "compress": False,
        "archive_on_terminal": False,
    },
}


def _resolve_artifact_key(key: str) -> str:
    """Resolve a key to its canonical technical key, supporting legacy aliases."""
    return LEGACY_KEY_MAPPING.get(key, key)


def get_artifact_policy_metadata(key: str) -> dict[str, Any] | None:
    """Get lifecycle policy metadata for a Polaris artifact key.

    Args:
        key: Artifact key (e.g., "contract.pm_tasks", "PLAN") - legacy
            aliases are resolved automatically.

    Returns:
        Policy metadata dict with keys: ``category``, ``lifecycle``,
        ``compress``, ``archive_on_terminal``; or None if the key is not
        registered in the Polaris artifact registry.
    """
    canonical = _resolve_artifact_key(key)
    return KERNELONE_ARTIFACT_POLICY_METADATA.get(canonical)


def should_compress_artifact(key: str) -> bool:
    """Check if a Polaris artifact should be compressed when archived.

    Args:
        key: Artifact key (e.g., "audit.events.runtime", "RUNTIME_EVENTS")

    Returns:
        True if compression is required; False if not registered or
        ``compress`` is not set.
    """
    metadata = get_artifact_policy_metadata(key)
    return metadata.get("compress", False) if metadata else False


def should_archive_artifact(key: str) -> bool:
    """Check if a Polaris artifact should be archived on terminal state.

    Args:
        key: Artifact key (e.g., "contract.pm_tasks", "PM_TASKS_CONTRACT")

    Returns:
        True if archiving is required on terminal status; False if not
        registered or ``archive_on_terminal`` is not set.
    """
    metadata = get_artifact_policy_metadata(key)
    return metadata.get("archive_on_terminal", False) if metadata else False


# ═══════════════════════════════════════════════════════════════════════════════
# Artifact Registry - Technical Key mapping (ACGA 2.0)
# ═══════════════════════════════════════════════════════════════════════════════

ARTIFACT_REGISTRY: dict[str, str] = {
    # Plan Artifacts
    "contract.plan": "runtime/contracts/plan.md",
    "contract.gap_report": "runtime/contracts/gap_report.md",
    # PM Contract Artifacts
    "contract.pm_tasks": "runtime/contracts/pm_tasks.contract.json",
    "runtime.report.pm": "runtime/results/pm.report.md",
    "runtime.state.pm": "runtime/state/pm.state.json",
    "contract.resident_goal": "runtime/contracts/resident.goal.contract.json",
    "contract.resident_goal_plan": "runtime/contracts/resident.goal.plan.md",
    # Director Artifacts
    "runtime.result.director": "runtime/results/director.result.json",
    "runtime.status.director": "runtime/status/director.status.json",
    "runtime.log.director": "runtime/logs/director.runlog.md",
    # Event Artifacts
    "audit.events.runtime": "runtime/events/runtime.events.jsonl",
    "audit.events.pm": "runtime/events/pm.events.jsonl",
    "audit.events.pm_llm": "runtime/events/pm.llm.events.jsonl",
    "audit.events.pm_task_history": "runtime/events/pm.task_history.events.jsonl",
    "audit.events.director_llm": "runtime/events/director.llm.events.jsonl",
    "audit.transcript": "runtime/events/dialogue.transcript.jsonl",
    # QA Artifacts
    "runtime.result.qa": "runtime/results/qa.review.md",
    # Process Logs
    "runtime.log.pm_process": "runtime/logs/pm.process.log",
    "runtime.log.director_process": "runtime/logs/director.process.log",
    # Memory & State
    "runtime.state.last": "runtime/memory/last_state.json",
    "runtime.status.engine": "runtime/status/engine.status.json",
    # Control Flags
    "runtime.control.pm_stop": "runtime/control/pm.stop.flag",
    "runtime.control.director_stop": "runtime/control/director.stop.flag",
    "runtime.control.pause": "runtime/control/pause.flag",
    # Agents Artifacts
    "contract.agents_draft": "runtime/contracts/agents.generated.md",
    "contract.agents_feedback": "runtime/contracts/agents.feedback.md",
}

# Mapping from legacy keys to canonical technical keys for backward compatibility
LEGACY_KEY_MAPPING: dict[str, str] = {
    "PLAN": "contract.plan",
    "GAP_REPORT": "contract.gap_report",
    "PM_TASKS_CONTRACT": "contract.pm_tasks",
    "PM_REPORT": "runtime.report.pm",
    "PM_STATE": "runtime.state.pm",
    "RESIDENT_GOAL_CONTRACT": "contract.resident_goal",
    "RESIDENT_GOAL_PLAN": "contract.resident_goal_plan",
    "DIRECTOR_RESULT": "runtime.result.director",
    "DIRECTOR_STATUS": "runtime.status.director",
    "DIRECTOR_RUNLOG": "runtime.log.director",
    "RUNTIME_EVENTS": "audit.events.runtime",
    "PM_EVENTS": "audit.events.pm",
    "PM_LLM_EVENTS": "audit.events.pm_llm",
    "PM_TASK_HISTORY": "audit.events.pm_task_history",
    "DIRECTOR_LLM_EVENTS": "audit.events.director_llm",
    "DIALOGUE_TRANSCRIPT": "audit.transcript",
    "QA_REVIEW": "runtime.result.qa",
    "PM_SUBPROCESS_LOG": "runtime.log.pm_process",
    "DIRECTOR_SUBPROCESS_LOG": "runtime.log.director_process",
    "LAST_STATE": "runtime.state.last",
    "ENGINE_STATUS": "runtime.status.engine",
    "PM_STOP_FLAG": "runtime.control.pm_stop",
    "DIRECTOR_STOP_FLAG": "runtime.control.director_stop",
    "PAUSE_FLAG": "runtime.control.pause",
    "AGENTS_DRAFT": "contract.agents_draft",
    "AGENTS_FEEDBACK": "contract.agents_feedback",
}

# Legacy path aliases for backward compatibility (path -> key)
LEGACY_PATH_ALIASES: dict[str, str] = {
    "runtime/contracts/pm_tasks.json": "contract.pm_tasks",
    "runtime/contracts/tasks.json": "contract.pm_tasks",
    "runtime/results/director_result.json": "runtime.result.director",
    "runtime/events.jsonl": "audit.events.runtime",
}

# Reverse registry for lookups
_REGISTRY_KEY_TO_CANONICAL: dict[str, str] = {v: k for k, v in ARTIFACT_REGISTRY.items()}


def _resolve_key(key: str) -> str:
    """Resolve a key to its canonical technical key."""
    return LEGACY_KEY_MAPPING.get(key, key)


def get_artifact_path(key: str) -> str:
    """Get canonical path for artifact key.

    Args:
        key: Artifact key (e.g., "contract.pm_tasks", "contract.plan")

    Returns:
        Canonical relative path

    Raises:
        KeyError: If key not found in registry
    """
    canonical_key = _resolve_key(key)
    if canonical_key not in ARTIFACT_REGISTRY:
        raise KeyError(
            f"Unknown artifact key: {key} (canonical: {canonical_key}). Available: {list(ARTIFACT_REGISTRY.keys())}"
        )
    return ARTIFACT_REGISTRY[canonical_key]


def get_artifact_key(path: str) -> str | None:
    """Get artifact key from path.

    Args:
        path: Relative path (e.g., "runtime/contracts/plan.md")

    Returns:
        Artifact key or None if not found
    """
    normalized = path.replace("\\", "/").strip()
    return _REGISTRY_KEY_TO_CANONICAL.get(normalized)


def list_artifact_keys() -> list[str]:
    """List all available artifact keys."""
    return sorted(ARTIFACT_REGISTRY.keys())


# ═══════════════════════════════════════════════════════════════════════════════
# Control Flag Helper (SRP: eliminates triplication of write/clear/check logic)
# ═══════════════════════════════════════════════════════════════════════════════


class _ControlFlag:
    """Encapsulates file-presence-based boolean flag semantics.

    A flag is set by writing an empty file and cleared by deleting it.
    All three operations (set, clear, check) live here so the pattern
    is defined once and never duplicated per-flag.
    """

    __slots__ = ("_key",)

    def __init__(self, registry_key: str) -> None:
        self._key = registry_key

    def resolve(self, service: ArtifactService) -> str:
        """Return the absolute path for this flag given a service instance."""
        return service._resolve(self._key)

    def set(self, service: ArtifactService) -> str:
        """Create the flag file. Returns the absolute path written."""
        path = service._resolve_for_write(self._key)
        _write_text_atomic(path, "", workspace=service.workspace)
        return path

    def clear(self, service: ArtifactService) -> bool:
        """Remove the flag file. Returns True if removed, False if absent."""
        path = self.resolve(service)
        if os.path.isfile(path):
            try:
                os.remove(path)
                return True
            except OSError:
                return False
        return False

    def is_set(self, service: ArtifactService) -> bool:
        """Return True if the flag file exists."""
        return os.path.isfile(self.resolve(service))


_PM_STOP_FLAG = _ControlFlag("runtime.control.pm_stop")
_DIRECTOR_STOP_FLAG = _ControlFlag("runtime.control.director_stop")
_PAUSE_FLAG = _ControlFlag("runtime.control.pause")


# ═══════════════════════════════════════════════════════════════════════════════
# Artifact Service Implementation
# ═══════════════════════════════════════════════════════════════════════════════


class ArtifactService:
    """Unified service for runtime artifact I/O.

    Provides consistent interfaces for reading and writing canonical artifacts
    with automatic UTF-8 encoding, atomic writes, and legacy path support.
    """

    def __init__(
        self,
        workspace: str,
        cache_root: str = "",
    ) -> None:
        """Initialize ArtifactService.

        Args:
            workspace: Workspace root path
            cache_root: Optional cache root (ramdisk path)
        """
        self.workspace = os.path.abspath(workspace)
        self.cache_root = os.path.abspath(cache_root) if cache_root else ""

    def _resolve(self, key: str) -> str:
        """Resolve artifact key to absolute path."""
        rel_path = get_artifact_path(key)
        return resolve_artifact_path(self.workspace, self.cache_root, rel_path)

    def _resolve_for_write(self, key: str) -> str:
        """Resolve artifact key to absolute path for writing.

        Creates parent directories if needed.
        """
        path = self._resolve(key)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        return path

    def _write_text(self, path: str, content: str) -> None:
        _write_text_atomic(path, content, workspace=self.workspace)

    def _write_json(self, path: str, data: dict[str, Any]) -> None:
        _write_json_atomic(path, data, workspace=self.workspace)

    def _read_text(self, path: str) -> str:
        return _read_file_safe(path, workspace=self.workspace)

    def _read_json(self, path: str) -> dict[str, Any]:
        return _read_json_file_strict(path, workspace=self.workspace)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Plan Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_plan(self, content: str) -> str:
        """Write plan.md artifact.

        Args:
            content: Plan content (markdown)

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("contract.plan")
        self._write_text(path, content)
        return path

    def read_plan(self) -> str:
        """Read plan.md artifact.

        Returns:
            Plan content or empty string if not found
        """
        path = self._resolve("contract.plan")
        return self._read_text(path)

    def write_gap_report(self, content: str) -> str:
        """Write gap_report.md artifact.

        Args:
            content: Gap report content (markdown)

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("contract.gap_report")
        self._write_text(path, content)
        return path

    def read_gap_report(self) -> str:
        """Read gap_report.md artifact.

        Returns:
            Gap report content or empty string if not found
        """
        path = self._resolve("contract.gap_report")
        return self._read_text(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # PM Contract Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_task_contract(self, data: dict[str, Any]) -> str:
        """Write PM tasks contract (pm_tasks.contract.json).

        Args:
            data: Task contract data (dict with tasks, overall_goal, etc.)

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("contract.pm_tasks")
        self._write_json(path, data)
        return path

    def read_task_contract(self) -> dict[str, Any] | None:
        """Read PM tasks contract.

        Returns:
            Task contract dict or None if not found
        """
        path = self._resolve("contract.pm_tasks")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    def write_pm_report(self, content: str) -> str:
        """Write PM report (pm.report.md).

        Args:
            content: Report content (markdown)

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("runtime.report.pm")
        self._write_text(path, content)
        return path

    def read_pm_report(self) -> str:
        """Read PM report.

        Returns:
            Report content or empty string
        """
        path = self._resolve("runtime.report.pm")
        return self._read_text(path)

    def write_pm_state(self, data: dict[str, Any]) -> str:
        """Write PM state (pm.state.json).

        Args:
            data: PM state data

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("runtime.state.pm")
        self._write_json(path, data)
        return path

    def read_pm_state(self) -> dict[str, Any] | None:
        """Read PM state.

        Returns:
            PM state dict or None
        """
        path = self._resolve("runtime.state.pm")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Director Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_director_result(self, data: dict[str, Any]) -> str:
        """Write Director result (director.result.json).

        Args:
            data: Director execution result

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("runtime.result.director")
        self._write_json(path, data)
        return path

    def read_director_result(self) -> dict[str, Any] | None:
        """Read Director result.

        Returns:
            Director result dict or None
        """
        path = self._resolve("runtime.result.director")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    def write_director_status(self, data: dict[str, Any]) -> str:
        """Write Director status (director.status.json).

        Args:
            data: Director status data

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("runtime.status.director")
        self._write_json(path, data)
        return path

    def read_director_status(self) -> dict[str, Any] | None:
        """Read Director status.

        Returns:
            Director status dict or None
        """
        path = self._resolve("runtime.status.director")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    def write_director_runlog(self, content: str) -> str:
        """Write Director runlog (director.runlog.md).

        Args:
            content: Runlog content (markdown)

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("runtime.log.director")
        self._write_text(path, content)
        return path

    def read_director_runlog(self) -> str:
        """Read Director runlog.

        Returns:
            Runlog content or empty string
        """
        path = self._resolve("runtime.log.director")
        return self._read_text(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Event Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def get_runtime_events_path(self) -> str:
        """Get path for runtime events (for appending).

        Returns:
            Absolute path to runtime.events.jsonl
        """
        return self._resolve("audit.events.runtime")

    def read_runtime_events(self, limit: int = 100) -> list[dict[str, Any]]:
        """Read recent runtime events.

        Args:
            limit: Maximum number of events to read (most-recent *limit* lines)

        Returns:
            List of event dicts

        Raises:
            RuntimeError: On UTF-8 decode failure or malformed JSON lines
        """
        path = self._resolve("audit.events.runtime")
        return _read_jsonl_safe(path, limit)

    def write_runtime_events(self, events: list[dict[str, Any]]) -> str:
        """Write runtime events (overwrites).

        Args:
            events: List of event dicts

        Returns:
            Absolute path where events were written
        """
        path = self._resolve_for_write("audit.events.runtime")
        lines = [json.dumps(e, ensure_ascii=False) + "\n" for e in events]
        self._write_text(path, "".join(lines))
        return path

    def get_pm_events_path(self) -> str:
        """Get path for PM events (for appending)."""
        return self._resolve("audit.events.pm")

    def get_dialogue_transcript_path(self) -> str:
        """Get path for dialogue transcript (for appending)."""
        return self._resolve("audit.transcript")

    def get_pm_llm_events_path(self) -> str:
        """Get path for PM LLM events (for appending)."""
        return self._resolve("audit.events.pm_llm")

    def get_director_llm_events_path(self) -> str:
        """Get path for Director LLM events (for appending)."""
        return self._resolve("audit.events.director_llm")

    # ═══════════════════════════════════════════════════════════════════════════════
    # QA Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_qa_review(self, content: str) -> str:
        """Write QA review (qa.review.md).

        Args:
            content: QA review content (markdown)

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("runtime.result.qa")
        self._write_text(path, content)
        return path

    def read_qa_review(self) -> str:
        """Read QA review.

        Returns:
            QA review content or empty string
        """
        path = self._resolve("runtime.result.qa")
        return self._read_text(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Memory & State
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_last_state(self, data: dict[str, Any]) -> str:
        """Write last state snapshot.

        Args:
            data: State snapshot data

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("runtime.state.last")
        self._write_json(path, data)
        return path

    def read_last_state(self) -> dict[str, Any] | None:
        """Read last state snapshot.

        Returns:
            State dict or None
        """
        path = self._resolve("runtime.state.last")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    def write_engine_status(self, data: dict[str, Any]) -> str:
        """Write engine status.

        Args:
            data: Engine status data

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write("runtime.status.engine")
        self._write_json(path, data)
        return path

    def read_engine_status(self) -> dict[str, Any] | None:
        """Read engine status.

        Returns:
            Engine status dict or None
        """
        path = self._resolve("runtime.status.engine")
        if not os.path.isfile(path):
            return None
        return self._read_json(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Control Flags
    # All three flags delegate to _ControlFlag helpers — single definition,
    # zero duplication of the set/clear/check pattern.
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_pm_stop_flag(self) -> str:
        """Create PM stop flag."""
        return _PM_STOP_FLAG.set(self)

    def clear_pm_stop_flag(self) -> bool:
        """Clear PM stop flag. Returns True if flag was cleared."""
        return _PM_STOP_FLAG.clear(self)

    def is_pm_stop_requested(self) -> bool:
        """Check if PM stop is requested."""
        return _PM_STOP_FLAG.is_set(self)

    def write_director_stop_flag(self) -> str:
        """Create Director stop flag."""
        return _DIRECTOR_STOP_FLAG.set(self)

    def clear_director_stop_flag(self) -> bool:
        """Clear Director stop flag. Returns True if flag was cleared."""
        return _DIRECTOR_STOP_FLAG.clear(self)

    def is_director_stop_requested(self) -> bool:
        """Check if Director stop is requested."""
        return _DIRECTOR_STOP_FLAG.is_set(self)

    def write_pause_flag(self) -> str:
        """Create pause flag."""
        return _PAUSE_FLAG.set(self)

    def clear_pause_flag(self) -> bool:
        """Clear pause flag. Returns True if flag was cleared."""
        return _PAUSE_FLAG.clear(self)

    def is_paused(self) -> bool:
        """Check if paused."""
        return _PAUSE_FLAG.is_set(self)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Agents Artifacts
    # ═══════════════════════════════════════════════════════════════════════════════

    def write_agents_draft(self, content: str) -> str:
        """Write agents draft (agents.generated.md).

        Args:
            content: AGENTS.md draft content

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("contract.agents_draft")
        self._write_text(path, content)
        return path

    def read_agents_draft(self) -> str:
        """Read agents draft.

        Returns:
            Draft content or empty string
        """
        path = self._resolve("contract.agents_draft")
        return self._read_text(path)

    def write_agents_feedback(self, content: str) -> str:
        """Write agents feedback (agents.feedback.md).

        Args:
            content: AGENTS.md feedback content

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write("contract.agents_feedback")
        self._write_text(path, content)
        return path

    def read_agents_feedback(self) -> str:
        """Read agents feedback.

        Returns:
            Feedback content or empty string
        """
        path = self._resolve("contract.agents_feedback")
        return self._read_text(path)

    # ═══════════════════════════════════════════════════════════════════════════════
    # Generic Operations
    # ═══════════════════════════════════════════════════════════════════════════════

    def get_path(self, key: str) -> str:
        """Get absolute path for artifact key.

        Args:
            key: Artifact key (e.g., "contract.pm_tasks")

        Returns:
            Absolute path
        """
        return self._resolve(key)

    def exists(self, key: str) -> bool:
        """Check if artifact exists.

        Args:
            key: Artifact key

        Returns:
            True if artifact file exists
        """
        path = self._resolve(key)
        return os.path.isfile(path)

    def write_text(self, key: str, content: str) -> str:
        """Generic text write for artifact.

        Args:
            key: Artifact key
            content: Text content

        Returns:
            Absolute path where content was written
        """
        path = self._resolve_for_write(key)
        self._write_text(path, content)
        return path

    def write_json(self, key: str, data: dict[str, Any]) -> str:
        """Generic JSON write for artifact.

        Args:
            key: Artifact key
            data: JSON-serializable data

        Returns:
            Absolute path where data was written
        """
        path = self._resolve_for_write(key)
        self._write_json(path, data)
        return path

    def read_text(self, key: str) -> str:
        """Generic text read for artifact.

        Args:
            key: Artifact key

        Returns:
            Text content or empty string
        """
        path = self._resolve(key)
        return self._read_text(path)

    def read_json(self, key: str) -> dict[str, Any] | None:
        """Generic JSON read for artifact.

        Args:
            key: Artifact key

        Returns:
            Parsed JSON dict or None
        """
        path = self._resolve(key)
        if not os.path.isfile(path):
            return None
        return self._read_json(path)


# ═══════════════════════════════════════════════════════════════════════════════
# Convenience Functions (for backward compatibility)
# ═══════════════════════════════════════════════════════════════════════════════


def create_artifact_service(
    workspace: str,
    cache_root: str = "",
) -> ArtifactService:
    """Create ArtifactService instance.

    Args:
        workspace: Workspace root path
        cache_root: Optional cache root

    Returns:
        Configured ArtifactService instance
    """
    return ArtifactService(workspace=workspace, cache_root=cache_root)


__all__ = [
    "ARTIFACT_REGISTRY",
    "KERNELONE_ARTIFACT_POLICY_METADATA",
    "LEGACY_KEY_MAPPING",
    "LEGACY_PATH_ALIASES",
    "ArtifactService",
    "create_artifact_service",
    "get_artifact_key",
    "get_artifact_path",
    "get_artifact_policy_metadata",
    "list_artifact_keys",
    "should_archive_artifact",
    "should_compress_artifact",
]
