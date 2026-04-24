"""PM State Manager - 尚书令数据空间管理器

统一管理尚书令的数据空间，提供原子化的状态读写接口，
维护项目元数据、配置和统计信息。
"""

from __future__ import annotations

import atexit
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from polaris.delivery.cli.pm.utils import read_json_file
from polaris.kernelone.utils import utc_now_str

logger = logging.getLogger(__name__)


def _ensure_parent_dir(path: str) -> None:
    """Ensure parent directory exists."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)


def _write_json_atomic(path: str, data: dict[str, Any]) -> None:
    """Atomically write JSON file."""
    if not path:
        return
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    _write_atomic_text(path, payload + "\n")


def _write_text_atomic(path: str, text: str) -> None:
    """Atomically write text file."""
    if not path:
        return
    _write_atomic_text(path, text or "")


def _write_atomic_text(path: str, content: str) -> None:
    """Atomically write UTF-8 text using per-write temp files.

    A unique temp file avoids writer collisions under concurrent updates.
    """
    _ensure_parent_dir(path)
    parent = os.path.dirname(path) or "."
    base = os.path.basename(path)
    fd, tmp_path = tempfile.mkstemp(prefix=f".{base}.", suffix=".tmp", dir=parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())

        last_error: Exception | None = None
        for _ in range(5):
            try:
                os.replace(tmp_path, path)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.01)
        if last_error is not None:
            raise last_error
    finally:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def _now_iso() -> str:
    """Get current ISO format timestamp."""
    return utc_now_str()


def _generate_id(prefix: str = "ID") -> str:
    """Generate unique ID with prefix."""
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    random_part = hashlib.sha256(os.urandom(32) + str(datetime.now(timezone.utc).timestamp()).encode()).hexdigest()[:8]
    return f"{prefix}-{timestamp}-{random_part}"


@dataclass
class ProjectMetadata:
    """项目元数据"""

    name: str = ""
    description: str = ""
    created_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)
    version: str = "1.0.0"
    pm_version: str = "2.0"  # 尚书令系统版本
    workspace_path: str = ""


@dataclass
class PMStats:
    """PM统计信息"""

    total_iterations: int = 0
    total_tasks_created: int = 0
    total_tasks_completed: int = 0
    total_tasks_failed: int = 0
    total_requirements: int = 0
    total_requirements_implemented: int = 0
    last_iteration_at: str | None = None
    last_task_created_at: str | None = None
    last_task_completed_at: str | None = None


@dataclass
class PMState:
    """PM主状态"""

    version: str = "2.0"
    metadata: ProjectMetadata = field(default_factory=ProjectMetadata)
    stats: PMStats = field(default_factory=PMStats)
    config: dict[str, Any] = field(default_factory=dict)
    active: bool = True
    paused: bool = False
    initialized_at: str = field(default_factory=_now_iso)
    updated_at: str = field(default_factory=_now_iso)


class PMStateManager:
    """尚书令数据空间管理器

    统一管理PM的数据空间，包括：
    - PM数据目录初始化
    - 原子化的状态读写接口
    - 项目元数据管理
    - 配置管理
    - 统计信息追踪

    存储结构:
        workspace/<metadata_dir>/pm_data/
        ├── state.json              # PM主状态
        ├── requirements/           # 需求追踪数据
        ├── tasks/                  # 任务管理数据
        ├── documents/              # 文档管理数据
        └── execution/              # 执行追踪数据
    """

    PM_DATA_DIR = "pm_data"
    STATE_FILE = "state.json"
    REQUIREMENTS_DIR = "requirements"
    TASKS_DIR = "tasks"
    DOCUMENTS_DIR = "documents"
    EXECUTION_DIR = "execution"

    def __init__(self, workspace: str) -> None:
        """Initialize PM State Manager.

        Args:
            workspace: Workspace root path
        """
        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        self.workspace = os.path.abspath(workspace)
        self.pm_data_root = os.path.join(self.workspace, get_workspace_metadata_dir_name(), self.PM_DATA_DIR)
        self._state_cache: PMState | None = None
        self._state_cache_time: float = 0
        self._cache_ttl: float = 1.0  # 1秒缓存

    def _get_path(self, *parts: str) -> str:
        """Get path under pm_data root."""
        return os.path.join(self.pm_data_root, *parts)

    def _ensure_dirs(self) -> None:
        """Ensure all PM data directories exist."""
        dirs = [
            self.pm_data_root,
            self._get_path(self.REQUIREMENTS_DIR),
            self._get_path(self.TASKS_DIR),
            self._get_path(self.DOCUMENTS_DIR),
            self._get_path(self.EXECUTION_DIR),
            self._get_path(self.DOCUMENTS_DIR, "snapshots"),
            self._get_path(self.DOCUMENTS_DIR, "analysis"),
            self._get_path(self.EXECUTION_DIR, "history"),
        ]
        for d in dirs:
            os.makedirs(d, exist_ok=True)

    def initialize(
        self,
        project_name: str = "",
        description: str = "",
        config: dict[str, Any] | None = None,
    ) -> PMState:
        """Initialize PM data space.

        Args:
            project_name: Project name
            description: Project description
            config: Initial configuration

        Returns:
            Initial PM state
        """
        self._ensure_dirs()

        metadata = ProjectMetadata(
            name=project_name or os.path.basename(self.workspace),
            description=description,
            workspace_path=self.workspace,
        )

        state = PMState(
            metadata=metadata,
            config=config or {},
        )

        self._save_state(state)

        # 初始化子系统空文件
        self._init_subsystem_files()

        self._state_cache = state
        return state

    def _init_subsystem_files(self) -> None:
        """Initialize empty subsystem files."""
        # Requirements registry
        requirements_registry = {
            "version": "1.0",
            "created_at": _now_iso(),
            "requirements": {},
            "next_id": 1,
        }
        _write_json_atomic(
            self._get_path(self.REQUIREMENTS_DIR, "registry.json"),
            requirements_registry,
        )

        # Requirements matrix
        matrix = {
            "version": "1.0",
            "created_at": _now_iso(),
            "matrix": {},
            "dependencies": {},
        }
        _write_json_atomic(
            self._get_path(self.REQUIREMENTS_DIR, "matrix.json"),
            matrix,
        )

        # Task registry (truth source)
        task_registry = {
            "version": "1.0",
            "created_at": _now_iso(),
            "tasks": {},
            "stats": {
                "total": 0,
                "completed": 0,
                "in_progress": 0,
                "pending": 0,
                "failed": 0,
                "blocked": 0,
            },
            "next_id": 1,
        }
        _write_json_atomic(
            self._get_path(self.TASKS_DIR, "registry.json"),
            task_registry,
        )

        # Task assignments
        assignments = {
            "version": "1.0",
            "created_at": _now_iso(),
            "assignments": [],
        }
        _write_json_atomic(
            self._get_path(self.TASKS_DIR, "assignments.json"),
            assignments,
        )

        # Document versions
        doc_versions = {
            "version": "1.0",
            "created_at": _now_iso(),
            "documents": {},
        }
        _write_json_atomic(
            self._get_path(self.DOCUMENTS_DIR, "versions.json"),
            doc_versions,
        )

        # Execution stats
        execution_stats = {
            "version": "1.0",
            "created_at": _now_iso(),
            "executors": {},
            "history_summary": {
                "total_runs": 0,
                "successful_runs": 0,
                "failed_runs": 0,
            },
        }
        _write_json_atomic(
            self._get_path(self.EXECUTION_DIR, "stats.json"),
            execution_stats,
        )

    def _save_state(self, state: PMState) -> None:
        """Save PM state to file."""
        state.updated_at = _now_iso()
        state_dict = {
            "version": state.version,
            "metadata": asdict(state.metadata),
            "stats": asdict(state.stats),
            "config": state.config,
            "active": state.active,
            "paused": state.paused,
            "initialized_at": state.initialized_at,
            "updated_at": state.updated_at,
        }
        _write_json_atomic(self._get_path(self.STATE_FILE), state_dict)

    def get_state(self, use_cache: bool = True) -> PMState | None:
        """Get current PM state.

        Args:
            use_cache: Whether to use cached state

        Returns:
            PM state or None if not initialized
        """
        if use_cache and self._state_cache is not None:
            cache_age = datetime.now().timestamp() - self._state_cache_time
            if cache_age < self._cache_ttl:
                return self._state_cache

        state_data = read_json_file(self._get_path(self.STATE_FILE))
        if state_data is None:
            return None

        metadata = ProjectMetadata(**state_data.get("metadata", {}))
        stats = PMStats(**state_data.get("stats", {}))

        state = PMState(
            version=state_data.get("version", "2.0"),
            metadata=metadata,
            stats=stats,
            config=state_data.get("config", {}),
            active=state_data.get("active", True),
            paused=state_data.get("paused", False),
            initialized_at=state_data.get("initialized_at", _now_iso()),
            updated_at=state_data.get("updated_at", _now_iso()),
        )

        self._state_cache = state
        self._state_cache_time = datetime.now().timestamp()
        return state

    def update_state(self, **updates: Any) -> PMState:
        """Update PM state.

        Args:
            **updates: State fields to update

        Returns:
            Updated PM state
        """
        state = self.get_state(use_cache=False)
        if state is None:
            raise RuntimeError("PM state not initialized. Call initialize() first.")

        if "metadata" in updates:
            for key, value in updates["metadata"].items():
                setattr(state.metadata, key, value)
            state.metadata.updated_at = _now_iso()

        if "stats" in updates:
            for key, value in updates["stats"].items():
                setattr(state.stats, key, value)

        if "config" in updates:
            state.config.update(updates["config"])

        for key in ["active", "paused"]:
            if key in updates:
                setattr(state, key, updates[key])

        self._save_state(state)
        self._state_cache = state
        return state

    def update_stats(self, **stats_updates: Any) -> PMStats:
        """Update PM statistics.

        Args:
            **stats_updates: Stats fields to update

        Returns:
            Updated stats
        """
        state = self.get_state(use_cache=False)
        if state is None:
            raise RuntimeError("PM state not initialized")

        for key, value in stats_updates.items():
            if hasattr(state.stats, key):
                setattr(state.stats, key, value)

        self._save_state(state)
        return state.stats

    def increment_stat(self, stat_name: str, increment: int = 1) -> int:
        """Increment a stat counter.

        Args:
            stat_name: Name of the stat to increment
            increment: Amount to increment

        Returns:
            New stat value
        """
        state = self.get_state(use_cache=False)
        if state is None:
            raise RuntimeError("PM state not initialized")

        if hasattr(state.stats, stat_name):
            current = getattr(state.stats, stat_name)
            new_value = current + increment
            setattr(state.stats, stat_name, new_value)
            self._save_state(state)
            return new_value
        return 0

    def is_initialized(self) -> bool:
        """Check if PM data space is initialized."""
        return os.path.exists(self._get_path(self.STATE_FILE))

    def get_config(self, key: str, default: Any = None) -> Any:
        """Get configuration value.

        Args:
            key: Config key
            default: Default value if not found

        Returns:
            Config value
        """
        state = self.get_state()
        if state is None:
            return default
        return state.config.get(key, default)

    def set_config(self, key: str, value: Any) -> None:
        """Set configuration value.

        Args:
            key: Config key
            value: Config value
        """
        self.update_state(config={key: value})

    def batch_set_config(self, config: dict[str, Any]) -> None:
        """Batch set configuration values.

        Args:
            config: Config dictionary
        """
        self.update_state(config=config)

    def get_data_path(self, subsystem: str, filename: str) -> str:
        """Get path to a subsystem data file.

        Args:
            subsystem: Subsystem name (requirements, tasks, documents, execution)
            filename: Filename

        Returns:
            Full path to the file
        """
        return self._get_path(subsystem, filename)

    def read_subsystem_data(self, subsystem: str, filename: str) -> dict[str, Any] | None:
        """Read subsystem data file.

        Args:
            subsystem: Subsystem name
            filename: Filename

        Returns:
            Data dictionary or None
        """
        path = self._get_path(subsystem, filename)
        return read_json_file(path)

    def write_subsystem_data(self, subsystem: str, filename: str, data: dict[str, Any]) -> None:
        """Write subsystem data file atomically.

        Args:
            subsystem: Subsystem name
            filename: Filename
            data: Data to write
        """
        path = self._get_path(subsystem, filename)
        _write_json_atomic(path, data)

    def append_to_history(self, subsystem: str, record: dict[str, Any]) -> str:
        """Append record to history file (jsonl format).

        Args:
            subsystem: Subsystem name
            record: Record to append

        Returns:
            Path to history file
        """
        history_dir = self._get_path(subsystem, "history")
        os.makedirs(history_dir, exist_ok=True)

        # 按日期分文件
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        history_file = os.path.join(history_dir, f"{date_str}.jsonl")

        record["_timestamp"] = _now_iso()
        line = json.dumps(record, ensure_ascii=False) + "\n"

        with open(history_file, "a", encoding="utf-8") as f:
            f.write(line)

        return history_file

    def read_history(self, subsystem: str, date_str: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """Read history records.

        Args:
            subsystem: Subsystem name
            date_str: Date string (YYYY-MM-DD), default today
            limit: Max records to read

        Returns:
            List of history records
        """
        if date_str is None:
            date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        history_file = self._get_path(subsystem, "history", f"{date_str}.jsonl")
        if not os.path.exists(history_file):
            return []

        records = []
        with open(history_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
                if len(records) >= limit:
                    break

        return records

    def clear_cache(self) -> None:
        """Clear state cache."""
        self._state_cache = None
        self._state_cache_time = 0

    def shutdown(self) -> None:
        """Release in-memory references for process shutdown."""
        self.clear_cache()

    def get_storage_summary(self) -> dict[str, Any]:
        """Get storage usage summary.

        Returns:
            Storage summary dictionary
        """
        subsystems: dict[str, dict[str, int]] = {}
        summary: dict[str, Any] = {
            "pm_data_root": self.pm_data_root,
            "subsystems": subsystems,
            "total_size_bytes": 0,
        }

        for subsystem in [self.REQUIREMENTS_DIR, self.TASKS_DIR, self.DOCUMENTS_DIR, self.EXECUTION_DIR]:
            subsystem_path = self._get_path(subsystem)
            if os.path.exists(subsystem_path):
                size = 0
                file_count = 0
                for root, _dirs, files in os.walk(subsystem_path):
                    for f in files:
                        fp = os.path.join(root, f)
                        try:
                            size += os.path.getsize(fp)
                            file_count += 1
                        except OSError:
                            pass
                subsystems[subsystem] = {
                    "size_bytes": size,
                    "file_count": file_count,
                }
                summary["total_size_bytes"] += size

        return summary


# Global instance cache
_state_manager_instances: dict[str, PMStateManager] = {}


def get_state_manager(workspace: str) -> PMStateManager:
    """Get or create PMStateManager instance for workspace.

    Args:
        workspace: Workspace path

    Returns:
        PMStateManager instance
    """
    workspace_abs = os.path.abspath(workspace)
    if workspace_abs not in _state_manager_instances:
        _state_manager_instances[workspace_abs] = PMStateManager(workspace_abs)
    return _state_manager_instances[workspace_abs]


def reset_state_manager(workspace: str) -> None:
    """Reset state manager instance for workspace.

    Args:
        workspace: Workspace path
    """
    workspace_abs = os.path.abspath(workspace)
    _state_manager_instances.pop(workspace_abs, None)


def clear_all_state_managers() -> None:
    """Clear global state manager registry to avoid process-lifetime cache buildup."""
    for manager in list(_state_manager_instances.values()):
        try:
            manager.shutdown()
        except (RuntimeError, ValueError) as e:
            logger.debug(f"State manager shutdown failed: {e}")
    _state_manager_instances.clear()


atexit.register(clear_all_state_managers)
