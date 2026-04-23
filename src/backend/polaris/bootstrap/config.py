"""Centralized configuration management for Polaris backend."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

from polaris.config.director_config import DirectorConfig
from polaris.config.llm_config import LLMConfig
from polaris.config.nats_config import NATSConfig
from polaris.config.pm_config import PMConfig
from pydantic import BaseModel, Field, field_validator, model_validator


def find_workspace_root(start: str | Path) -> Path:
    """Find workspace root by scanning parent directories for `docs/`."""
    current = Path(start).expanduser().resolve()
    while True:
        if (current / "docs").is_dir():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return Path(start).expanduser().resolve()

def get_backend_root() -> Path:
    """Get backend root directory (`src/backend`)."""
    return Path(__file__).resolve().parent

def get_project_root() -> Path:
    """Get repository root directory."""
    return get_backend_root().parents[1]

def default_system_cache_base() -> Path:
    """Get default system cache directory."""
    if os.name == "nt":
        cache_base = os.environ.get("LOCALAPPDATA", "")
        if cache_base:
            return Path(cache_base) / "Polaris" / "cache"
    else:
        cache_base = os.environ.get("XDG_CACHE_HOME", "")
        if cache_base:
            return Path(cache_base) / "polaris"
    return Path.home() / ".cache" / "polaris"

def resolve_ramdisk_root(configured_root: str | None = None) -> Path | None:
    """Resolve a usable ramdisk root path."""
    if configured_root:
        path = Path(configured_root).expanduser().resolve()
        if path.exists():
            return path

    if os.name == "nt":
        x_drive = Path("X:/")
        if x_drive.exists():
            return x_drive

    shm_path = Path("/dev/shm")
    if shm_path.exists():
        return shm_path

    return None

# ═══════════════════════════════════════════════════════════════════════════════
# Default Constants - Single Source of Truth
# ═══════════════════════════════════════════════════════════════════════════════
DEFAULT_BACKEND_PORT: int = 49977
DEFAULT_RENDERER_PORT: int = 5173

class JSONLConfig(BaseModel):
    """JSONL I/O configuration for Polaris Loop.

    All configuration values can be overridden via environment variables.
    See field descriptions for corresponding environment variable names.
    """

    lock_stale_sec: float = Field(
        default=120.0,
        description="Lock file stale timeout in seconds (KERNELONE_JSONL_LOCK_STALE_SEC)",
    )
    buffer_enabled: bool = Field(
        default=True,
        description="Enable buffered writes (KERNELONE_JSONL_BUFFERED)",
    )
    flush_interval_sec: float = Field(
        default=1.0,
        description="Buffer flush interval in seconds (KERNELONE_JSONL_FLUSH_INTERVAL)",
    )
    flush_batch: int = Field(
        default=50,
        description="Number of lines to trigger flush (KERNELONE_JSONL_FLUSH_BATCH)",
    )
    max_buffer: int = Field(
        default=2000,
        description="Maximum buffer size per file (KERNELONE_JSONL_MAX_BUFFER)",
    )
    buffer_ttl_sec: float = Field(
        default=300.0,
        description="Buffer entry TTL in seconds (KERNELONE_JSONL_BUFFER_TTL)",
    )
    max_paths: int = Field(
        default=100,
        description="Maximum number of tracked file paths (KERNELONE_JSONL_MAX_PATHS)",
    )
    cleanup_interval_sec: float = Field(
        default=60.0,
        description="Cleanup timer interval in seconds (KERNELONE_JSONL_CLEANUP_INTERVAL)",
    )

    @field_validator("lock_stale_sec", "flush_interval_sec", "buffer_ttl_sec", "cleanup_interval_sec", mode="before")
    @classmethod
    def validate_positive_float(cls, value: Any) -> float:
        try:
            return max(0.0, float(value))
        except (ValueError, TypeError):
            return 0.0

    @field_validator("flush_batch", "max_buffer", "max_paths", mode="before")
    @classmethod
    def validate_positive_int(cls, value: Any) -> int:
        try:
            return max(1, int(value))
        except (ValueError, TypeError):
            return 1

    @field_validator("buffer_enabled", mode="before")
    @classmethod
    def validate_bool(cls, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() not in ("0", "false", "no", "off", "disabled")
        return bool(value)

    @classmethod
    def from_env(cls) -> JSONLConfig:
        """Create JSONLConfig from environment variables."""
        return cls(
            lock_stale_sec=os.environ.get("KERNELONE_JSONL_LOCK_STALE_SEC", 120.0),
            buffer_enabled=os.environ.get("KERNELONE_JSONL_BUFFERED", "1"),
            flush_interval_sec=os.environ.get("KERNELONE_JSONL_FLUSH_INTERVAL", 1.0),
            flush_batch=os.environ.get("KERNELONE_JSONL_FLUSH_BATCH", 50),
            max_buffer=os.environ.get("KERNELONE_JSONL_MAX_BUFFER", 2000),
            buffer_ttl_sec=os.environ.get("KERNELONE_JSONL_BUFFER_TTL", 300.0),
            max_paths=os.environ.get("KERNELONE_JSONL_MAX_PATHS", 100),
            cleanup_interval_sec=os.environ.get("KERNELONE_JSONL_CLEANUP_INTERVAL", 60.0),
        )

class RuntimeConfig(BaseModel):
    """Runtime paths and storage behavior."""

    root: Path | None = Field(default=None, description="Explicit runtime root")
    cache_root: Path | None = Field(default=None, description="Cache directory")
    use_ramdisk: bool = Field(default=True, description="Use ramdisk if available")
    ramdisk_root: Path | None = Field(default=None, description="Configured ramdisk path")

    @field_validator("root", "cache_root", "ramdisk_root")
    @classmethod
    def validate_path(cls, value: str | Path | None) -> Path | None:
        if value is None:
            return None
        return Path(value).expanduser().resolve()

class LoggingConfig(BaseModel):
    """Logging configuration."""

    level: str = Field(default="DEBUG", description="Log level")
    json_path: Path | None = Field(default=None, description="JSON log file path")
    enable_debug_tracing: bool = Field(default=True, description="Enable debug tracing")

    @field_validator("level")
    @classmethod
    def validate_level(cls, value: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        normalized = value.upper()
        if normalized not in valid:
            raise ValueError(f"Invalid log level: {value}. Must be one of {valid}")
        return normalized

class ServerConfig(BaseModel):
    """Server/network configuration."""

    host: str = Field(default="127.0.0.1", description="Server bind host")
    port: int = Field(default=DEFAULT_BACKEND_PORT, description="Server port")
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            f"http://localhost:{DEFAULT_RENDERER_PORT}",
            f"http://127.0.0.1:{DEFAULT_RENDERER_PORT}",
        ],
        description="CORS allowed origins",
    )

class SettingsUpdate(BaseModel):
    """Partial settings update payload."""

    model_config = {"extra": "ignore"}

    self_upgrade_mode: bool | None = None
    workspace: str | None = None
    timeout: int | None = None
    json_log_path: str | None = None
    ramdisk_root: str | None = None

    model: str | None = None
    pm_backend: str | None = None
    pm_model: str | None = None
    director_model: str | None = None

    pm_show_output: bool | None = None
    pm_runs_director: bool | None = None
    pm_director_show_output: bool | None = None
    pm_director_timeout: int | None = None
    pm_director_iterations: int | None = None
    pm_director_match_mode: str | None = None
    pm_agents_approval_mode: str | None = None
    pm_agents_approval_timeout: int | None = None
    pm_max_failures: int | None = None
    pm_max_blocked: int | None = None
    pm_max_same: int | None = None
    pm_blocked_strategy: str | None = None
    pm_blocked_degrade_max_retries: int | None = None

    director_iterations: int | None = None
    director_execution_mode: str | None = None
    director_max_parallel_tasks: int | None = None
    director_ready_timeout_seconds: int | None = None
    director_claim_timeout_seconds: int | None = None
    director_phase_timeout_seconds: int | None = None
    director_complete_timeout_seconds: int | None = None
    director_task_timeout_seconds: int | None = None
    director_forever: bool | None = None
    director_show_output: bool | None = None

    slm_enabled: bool | None = None
    qa_enabled: bool | None = None
    audit_llm_enabled: bool | None = None
    audit_llm_role: str | None = None
    audit_llm_timeout: int | None = None
    audit_llm_prefer_local_ollama: bool | None = None
    audit_llm_allow_remote_fallback: bool | None = None
    debug_tracing: bool | None = None

    architect_spec_provider: str | None = None
    architect_spec_model: str | None = None
    architect_spec_base_url: str | None = None
    architect_spec_api_key: str | None = None
    architect_spec_api_path: str | None = None
    architect_spec_timeout: int | None = None

    docs_init_provider: str | None = None
    docs_init_model: str | None = None
    docs_init_base_url: str | None = None
    docs_init_api_key: str | None = None
    docs_init_api_path: str | None = None
    docs_init_timeout: int | None = None

    # JSONL configuration
    jsonl_lock_stale_sec: float | None = None
    jsonl_buffer_enabled: bool | None = None
    jsonl_flush_interval_sec: float | None = None
    jsonl_flush_batch: int | None = None
    jsonl_max_buffer: int | None = None
    jsonl_buffer_ttl_sec: float | None = None
    jsonl_max_paths: int | None = None
    jsonl_cleanup_interval_sec: float | None = None

    # NATS configuration
    nats_enabled: bool | None = None
    nats_required: bool | None = None
    nats_url: str | None = None
    nats_user: str | None = None
    nats_password: str | None = None
    nats_connect_timeout_sec: float | None = None
    nats_reconnect_wait_sec: float | None = None
    nats_max_reconnect_attempts: int | None = None
    nats_stream_name: str | None = None

class Settings(BaseModel):
    """Unified Polaris settings."""

    workspace: Path = Field(default_factory=lambda: find_workspace_root(os.getcwd()))
    project_root: Path = Field(default_factory=get_project_root)
    backend_root: Path = Field(default_factory=get_backend_root)
    self_upgrade_mode: bool = Field(
        default=False,
        description="Allow Polaris meta-project to be used as the target workspace.",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    pm: PMConfig = Field(default_factory=PMConfig)
    director: DirectorConfig = Field(default_factory=DirectorConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    jsonl: JSONLConfig = Field(default_factory=JSONLConfig.from_env)
    nats: NATSConfig = Field(default_factory=NATSConfig)

    timeout: int = Field(default=0, description="PM orchestration timeout in seconds")
    json_log_path: str | None = Field(default=None, description="Runtime PM JSON log path")
    slm_enabled: bool = Field(default=False, description="Enable SLM features")
    qa_enabled: bool = Field(default=True, description="Enable QA agent")
    audit_llm_enabled: bool = Field(default=True, description="Enable independent audit LLM reviewer")
    audit_llm_role: str = Field(default="qa", description="Audit role id used for runtime model binding")
    audit_llm_timeout: int = Field(default=180, description="Independent audit LLM timeout (seconds)")
    audit_llm_prefer_local_ollama: bool = Field(
        default=True,
        description="Prefer local Ollama for independent audit when role binding permits",
    )
    audit_llm_allow_remote_fallback: bool = Field(
        default=True,
        description="Fallback to role runtime provider when local-only attempt is unavailable",
    )

    architect_spec_provider: str | None = None
    architect_spec_model: str | None = None
    architect_spec_base_url: str | None = None
    architect_spec_api_key: str | None = None
    architect_spec_api_path: str | None = None
    architect_spec_timeout: int | None = None

    docs_init_provider: str | None = None
    docs_init_model: str | None = None
    docs_init_base_url: str | None = None
    docs_init_api_key: str | None = None
    docs_init_api_path: str | None = None
    docs_init_timeout: int | None = None

    model_config = {
        "validate_assignment": True,
        "extra": "ignore",
        "arbitrary_types_allowed": True,
    }

    @model_validator(mode="before")
    @classmethod
    def migrate_legacy_inputs(cls, raw: Any) -> Any:
        """Map legacy flat keys to unified nested structures."""
        if not isinstance(raw, dict):
            return raw

        data = dict(raw)

        def _as_dict(value: Any) -> dict[str, Any]:
            if isinstance(value, dict):
                return dict(value)
            if isinstance(value, BaseModel):
                return value.model_dump(mode="python")
            return {}

        if data.get("model") is not None:
            llm = _as_dict(data.get("llm"))
            llm.setdefault("model", data.pop("model"))
            data["llm"] = llm

        pm = _as_dict(data.get("pm"))
        pm_mapped = False
        for legacy_key, pm_key in (
            ("pm_backend", "backend"),
            ("pm_model", "model"),
            ("pm_show_output", "show_output"),
            ("pm_runs_director", "runs_director"),
            ("pm_director_show_output", "director_show_output"),
            ("pm_director_timeout", "director_timeout"),
            ("pm_director_iterations", "director_iterations"),
            ("pm_director_match_mode", "director_match_mode"),
            ("pm_agents_approval_mode", "agents_approval_mode"),
            ("pm_agents_approval_timeout", "agents_approval_timeout"),
            ("pm_max_failures", "max_failures"),
            ("pm_max_blocked", "max_blocked"),
            ("pm_max_same", "max_same"),
            ("pm_blocked_strategy", "blocked_strategy"),
            ("pm_blocked_degrade_max_retries", "blocked_degrade_max_retries"),
        ):
            if data.get(legacy_key) is not None:
                pm_mapped = True
                pm.setdefault(pm_key, data.pop(legacy_key))
        if pm_mapped:
            data["pm"] = pm

        director = _as_dict(data.get("director"))
        director_mapped = False
        for legacy_key, director_key in (
            ("director_model", "model"),
            ("director_iterations", "iterations"),
            ("director_execution_mode", "execution_mode"),
            ("director_max_parallel_tasks", "max_parallel_tasks"),
            ("director_ready_timeout_seconds", "ready_timeout_seconds"),
            ("director_claim_timeout_seconds", "claim_timeout_seconds"),
            ("director_phase_timeout_seconds", "phase_timeout_seconds"),
            ("director_complete_timeout_seconds", "complete_timeout_seconds"),
            ("director_task_timeout_seconds", "task_timeout_seconds"),
            ("director_forever", "forever"),
            ("director_show_output", "show_output"),
        ):
            if data.get(legacy_key) is not None:
                director_mapped = True
                director.setdefault(director_key, data.pop(legacy_key))
        if director_mapped:
            data["director"] = director

        runtime_mapped = "ramdisk_root" in data
        runtime = _as_dict(data.get("runtime"))
        if data.get("ramdisk_root") not in (None, ""):
            runtime.setdefault("ramdisk_root", data.pop("ramdisk_root"))
        if data.get("ramdisk_root") == "":
            data.pop("ramdisk_root")
            runtime["ramdisk_root"] = None
        if runtime_mapped:
            data["runtime"] = runtime

        if "debug_tracing" in data:
            logging_cfg = _as_dict(data.get("logging"))
            logging_cfg.setdefault("enable_debug_tracing", data.pop("debug_tracing"))
            data["logging"] = logging_cfg

        return data

    @field_validator("workspace", "project_root", "backend_root")
    @classmethod
    def validate_directory(cls, value: Path) -> Path:
        return value.resolve()

    @field_validator("audit_llm_role", mode="before")
    @classmethod
    def normalize_audit_llm_role(cls, value: Any) -> str:
        token = str(value or "").strip().lower()
        return token or "qa"

    @field_validator("audit_llm_timeout", mode="before")
    @classmethod
    def normalize_audit_llm_timeout(cls, value: Any) -> int:
        try:
            return max(30, int(value))
        except (TypeError, ValueError):
            return 180

    @field_validator("json_log_path", mode="before")
    @classmethod
    def normalize_json_log_path(cls, value: Any) -> str | None:
        if value is None:
            return None
        raw = str(value).strip()
        if not raw:
            return None
        normalized = raw.replace("\\", "/")
        # Normalize legacy .polaris/runtime paths → runtime
        for legacy in (".polaris/runtime", ".polaris/runtime"):
            if normalized == legacy:
                return "runtime"
            legacy_prefix = legacy + "/"
            if normalized.startswith(legacy_prefix):
                suffix = normalized[len(legacy_prefix) :].lstrip("/")
                return f"runtime/{suffix}" if suffix else "runtime"
        return normalized

    @property
    def model(self) -> str:
        return self.llm.model

    @model.setter
    def model(self, value: str) -> None:
        self.llm.model = str(value)

    @property
    def pm_backend(self) -> str:
        return self.pm.backend

    @pm_backend.setter
    def pm_backend(self, value: str) -> None:
        self.pm.backend = str(value or "auto")

    @property
    def pm_model(self) -> str | None:
        return self.pm.model

    @pm_model.setter
    def pm_model(self, value: str | None) -> None:
        self.pm.model = str(value) if value else None

    @property
    def director_model(self) -> str | None:
        return self.director.model

    @director_model.setter
    def director_model(self, value: str | None) -> None:
        self.director.model = str(value) if value else None

    @property
    def pm_show_output(self) -> bool:
        return self.pm.show_output

    @pm_show_output.setter
    def pm_show_output(self, value: bool) -> None:
        self.pm.show_output = bool(value)

    @property
    def pm_runs_director(self) -> bool:
        return self.pm.runs_director

    @pm_runs_director.setter
    def pm_runs_director(self, value: bool) -> None:
        self.pm.runs_director = bool(value)

    @property
    def pm_director_show_output(self) -> bool:
        return self.pm.director_show_output

    @pm_director_show_output.setter
    def pm_director_show_output(self, value: bool) -> None:
        self.pm.director_show_output = bool(value)

    @property
    def pm_director_timeout(self) -> int:
        return self.pm.director_timeout

    @pm_director_timeout.setter
    def pm_director_timeout(self, value: int) -> None:
        self.pm.director_timeout = int(value)

    @property
    def pm_director_iterations(self) -> int:
        return self.pm.director_iterations

    @pm_director_iterations.setter
    def pm_director_iterations(self, value: int) -> None:
        self.pm.director_iterations = int(value)

    @property
    def pm_director_match_mode(self) -> str:
        return self.pm.director_match_mode

    @pm_director_match_mode.setter
    def pm_director_match_mode(self, value: str) -> None:
        self.pm.director_match_mode = str(value or "run_id")

    @property
    def pm_agents_approval_mode(self) -> str:
        return self.pm.agents_approval_mode

    @pm_agents_approval_mode.setter
    def pm_agents_approval_mode(self, value: str) -> None:
        self.pm.agents_approval_mode = str(value or "auto_accept")

    @property
    def pm_agents_approval_timeout(self) -> int:
        return self.pm.agents_approval_timeout

    @pm_agents_approval_timeout.setter
    def pm_agents_approval_timeout(self, value: int) -> None:
        self.pm.agents_approval_timeout = int(value)

    @property
    def pm_max_failures(self) -> int:
        return self.pm.max_failures

    @pm_max_failures.setter
    def pm_max_failures(self, value: int) -> None:
        self.pm.max_failures = int(value)

    @property
    def pm_max_blocked(self) -> int:
        return self.pm.max_blocked

    @pm_max_blocked.setter
    def pm_max_blocked(self, value: int) -> None:
        self.pm.max_blocked = int(value)

    @property
    def pm_max_same(self) -> int:
        return self.pm.max_same

    @pm_max_same.setter
    def pm_max_same(self, value: int) -> None:
        self.pm.max_same = int(value)

    @property
    def pm_blocked_strategy(self) -> str:
        return self.pm.blocked_strategy

    @pm_blocked_strategy.setter
    def pm_blocked_strategy(self, value: str) -> None:
        self.pm.blocked_strategy = str(value or "auto").strip().lower()

    @property
    def pm_blocked_degrade_max_retries(self) -> int:
        return self.pm.blocked_degrade_max_retries

    @pm_blocked_degrade_max_retries.setter
    def pm_blocked_degrade_max_retries(self, value: int) -> None:
        self.pm.blocked_degrade_max_retries = max(0, int(value))

    @property
    def director_iterations(self) -> int:
        return self.director.iterations

    @director_iterations.setter
    def director_iterations(self, value: int) -> None:
        self.director.iterations = int(value)

    @property
    def director_execution_mode(self) -> str:
        return self.director.execution_mode

    @director_execution_mode.setter
    def director_execution_mode(self, value: str) -> None:
        token = str(value or "").strip().lower()
        self.director.execution_mode = token if token in {"serial", "parallel"} else "parallel"

    @property
    def director_max_parallel_tasks(self) -> int:
        return self.director.max_parallel_tasks

    @director_max_parallel_tasks.setter
    def director_max_parallel_tasks(self, value: int) -> None:
        self.director.max_parallel_tasks = max(1, int(value))

    @property
    def director_ready_timeout_seconds(self) -> int:
        return self.director.ready_timeout_seconds

    @director_ready_timeout_seconds.setter
    def director_ready_timeout_seconds(self, value: int) -> None:
        self.director.ready_timeout_seconds = max(1, int(value))

    @property
    def director_claim_timeout_seconds(self) -> int:
        return self.director.claim_timeout_seconds

    @director_claim_timeout_seconds.setter
    def director_claim_timeout_seconds(self, value: int) -> None:
        self.director.claim_timeout_seconds = max(1, int(value))

    @property
    def director_phase_timeout_seconds(self) -> int:
        return self.director.phase_timeout_seconds

    @director_phase_timeout_seconds.setter
    def director_phase_timeout_seconds(self, value: int) -> None:
        self.director.phase_timeout_seconds = max(1, int(value))

    @property
    def director_complete_timeout_seconds(self) -> int:
        return self.director.complete_timeout_seconds

    @director_complete_timeout_seconds.setter
    def director_complete_timeout_seconds(self, value: int) -> None:
        self.director.complete_timeout_seconds = max(1, int(value))

    @property
    def director_task_timeout_seconds(self) -> int:
        return self.director.task_timeout_seconds

    @director_task_timeout_seconds.setter
    def director_task_timeout_seconds(self, value: int) -> None:
        self.director.task_timeout_seconds = max(1, int(value))

    @property
    def director_forever(self) -> bool:
        return self.director.forever

    @director_forever.setter
    def director_forever(self, value: bool) -> None:
        self.director.forever = bool(value)

    @property
    def director_show_output(self) -> bool:
        return self.director.show_output

    @director_show_output.setter
    def director_show_output(self, value: bool) -> None:
        self.director.show_output = bool(value)

    @property
    def debug_tracing(self) -> bool:
        return self.logging.enable_debug_tracing

    @debug_tracing.setter
    def debug_tracing(self, value: bool) -> None:
        self.logging.enable_debug_tracing = bool(value)

    @property
    def ramdisk_root(self) -> str:
        if self.runtime.ramdisk_root is None:
            return ""
        return str(self.runtime.ramdisk_root)

    @ramdisk_root.setter
    def ramdisk_root(self, value: str) -> None:
        raw = str(value or "").strip()
        self.runtime.ramdisk_root = Path(raw).expanduser().resolve() if raw else None

    def apply_update(self, update: SettingsUpdate) -> None:
        """Apply partial update payload."""
        from polaris.cells.policy.workspace_guard.public.service import ensure_workspace_target_allowed
        data = update.model_dump(exclude_unset=True)
        target_self_upgrade_mode = bool(data.get("self_upgrade_mode", self.self_upgrade_mode))
        target_workspace_value = data.get("workspace", self.workspace)
        target_workspace = None
        if target_workspace_value:
            target_workspace = ensure_workspace_target_allowed(
                target_workspace_value,
                self_upgrade_mode=target_self_upgrade_mode,
            )
        for key, value in data.items():
            if key == "llm" and isinstance(value, dict):
                self.llm = LLMConfig(**value)
            elif key == "pm" and isinstance(value, dict):
                self.pm = PMConfig(**value)
            elif key == "director" and isinstance(value, dict):
                self.director = DirectorConfig(**value)
            elif key == "runtime" and isinstance(value, dict):
                self.runtime = RuntimeConfig(**value)
            elif key == "logging" and isinstance(value, dict):
                self.logging = LoggingConfig(**value)
            elif key == "server" and isinstance(value, dict):
                self.server = ServerConfig(**value)
            elif key == "jsonl" and isinstance(value, dict):
                self.jsonl = JSONLConfig(**value)
            elif key == "jsonl_lock_stale_sec":
                self.jsonl.lock_stale_sec = float(value or 120.0)
            elif key == "jsonl_buffer_enabled":
                self.jsonl.buffer_enabled = bool(value)
            elif key == "jsonl_flush_interval_sec":
                self.jsonl.flush_interval_sec = float(value or 1.0)
            elif key == "jsonl_flush_batch":
                self.jsonl.flush_batch = int(value or 50)
            elif key == "jsonl_max_buffer":
                self.jsonl.max_buffer = int(value or 2000)
            elif key == "jsonl_buffer_ttl_sec":
                self.jsonl.buffer_ttl_sec = float(value or 300.0)
            elif key == "jsonl_max_paths":
                self.jsonl.max_paths = int(value or 100)
            elif key == "jsonl_cleanup_interval_sec":
                self.jsonl.cleanup_interval_sec = float(value or 60.0)
            elif key == "self_upgrade_mode":
                self.self_upgrade_mode = bool(value)
            elif key == "workspace":
                self.workspace = target_workspace if target_workspace is not None else self.workspace
            elif key == "timeout":
                self.timeout = int(value or 0)
            elif key == "json_log_path":
                self.json_log_path = str(value).strip() if value else None
            elif key == "ramdisk_root":
                self.ramdisk_root = str(value or "")
            elif key == "model":
                self.model = str(value)
            elif key == "pm_backend":
                self.pm_backend = str(value)
            elif key == "pm_model":
                self.pm_model = str(value) if value else None
            elif key == "director_model":
                self.director_model = str(value) if value else None
            elif key == "pm_show_output":
                self.pm_show_output = bool(value)
            elif key == "pm_runs_director":
                self.pm_runs_director = bool(value)
            elif key == "pm_director_show_output":
                self.pm_director_show_output = bool(value)
            elif key == "pm_director_timeout":
                self.pm_director_timeout = int(value)
            elif key == "pm_director_iterations":
                self.pm_director_iterations = int(value)
            elif key == "pm_director_match_mode":
                self.pm_director_match_mode = str(value)
            elif key == "pm_agents_approval_mode":
                self.pm_agents_approval_mode = str(value)
            elif key == "pm_agents_approval_timeout":
                self.pm_agents_approval_timeout = int(value)
            elif key == "pm_max_failures":
                self.pm_max_failures = int(value)
            elif key == "pm_max_blocked":
                self.pm_max_blocked = int(value)
            elif key == "pm_max_same":
                self.pm_max_same = int(value)
            elif key == "pm_blocked_strategy":
                self.pm_blocked_strategy = str(value)
            elif key == "pm_blocked_degrade_max_retries":
                self.pm_blocked_degrade_max_retries = int(value)
            elif key == "director_iterations":
                self.director_iterations = int(value)
            elif key == "director_execution_mode":
                self.director_execution_mode = str(value)
            elif key == "director_max_parallel_tasks":
                self.director_max_parallel_tasks = int(value)
            elif key == "director_ready_timeout_seconds":
                self.director_ready_timeout_seconds = int(value)
            elif key == "director_claim_timeout_seconds":
                self.director_claim_timeout_seconds = int(value)
            elif key == "director_phase_timeout_seconds":
                self.director_phase_timeout_seconds = int(value)
            elif key == "director_complete_timeout_seconds":
                self.director_complete_timeout_seconds = int(value)
            elif key == "director_task_timeout_seconds":
                self.director_task_timeout_seconds = int(value)
            elif key == "director_forever":
                self.director_forever = bool(value)
            elif key == "director_show_output":
                self.director_show_output = bool(value)
            elif key == "slm_enabled":
                self.slm_enabled = bool(value)
            elif key == "qa_enabled":
                self.qa_enabled = bool(value)
            elif key == "audit_llm_enabled":
                self.audit_llm_enabled = bool(value)
            elif key == "audit_llm_role":
                token = str(value or "").strip().lower()
                self.audit_llm_role = token or "qa"
            elif key == "audit_llm_timeout":
                try:
                    self.audit_llm_timeout = max(30, int(value))
                except (TypeError, ValueError):
                    self.audit_llm_timeout = 180
            elif key == "audit_llm_prefer_local_ollama":
                self.audit_llm_prefer_local_ollama = bool(value)
            elif key == "audit_llm_allow_remote_fallback":
                self.audit_llm_allow_remote_fallback = bool(value)
            elif key == "debug_tracing":
                self.debug_tracing = bool(value)
            elif key == "nats" and isinstance(value, dict):
                self.nats = NATSConfig(**value)
            elif key == "nats_enabled":
                self.nats.enabled = bool(value)
            elif key == "nats_required":
                self.nats.required = bool(value)
            elif key == "nats_url":
                self.nats.url = str(value)
            elif key == "nats_user":
                self.nats.user = str(value)
            elif key == "nats_password":
                self.nats.password = str(value)
            elif key == "nats_connect_timeout_sec":
                self.nats.connect_timeout_sec = float(value or 3.0)
            elif key == "nats_reconnect_wait_sec":
                self.nats.reconnect_wait_sec = float(value or 1.0)
            elif key == "nats_max_reconnect_attempts":
                self.nats.max_reconnect_attempts = int(value or -1)
            elif key == "nats_stream_name":
                self.nats.stream_name = str(value)
            elif hasattr(self, key):
                setattr(self, key, value)

    @property
    def runtime_base(self) -> Path:
        """Resolve effective runtime base directory."""
        if self.runtime.root:
            return self.runtime.root
        if self.runtime.use_ramdisk:
            ramdisk = resolve_ramdisk_root(str(self.runtime.ramdisk_root) if self.runtime.ramdisk_root else None)
            if ramdisk:
                return ramdisk
        if self.runtime.cache_root:
            return self.runtime.cache_root
        return default_system_cache_base()

    @property
    def pm_script_path(self) -> Path:
        """Path to PM CLI script."""
        return self.backend_root / "scripts" / "pm" / "cli.py"

    @property
    def director_script_path(self) -> Path:
        """Path to Director CLI script."""
        return self.backend_root / "scripts" / "loop-director.py"

    @property
    def loop_module_dir(self) -> Path:
        """Path to loop core module directory."""
        return self.backend_root / "core" / "polaris_loop"

    def to_payload(self) -> dict[str, Any]:
        """Build JSON-safe payload including compatibility keys."""
        payload = super().model_dump(mode="json")
        payload["model"] = self.model
        payload["pm_backend"] = self.pm_backend
        payload["pm_model"] = self.pm_model
        payload["director_model"] = self.director_model
        payload["pm_show_output"] = self.pm_show_output
        payload["pm_runs_director"] = self.pm_runs_director
        payload["pm_director_show_output"] = self.pm_director_show_output
        payload["pm_director_timeout"] = self.pm_director_timeout
        payload["pm_director_iterations"] = self.pm_director_iterations
        payload["pm_director_match_mode"] = self.pm_director_match_mode
        payload["pm_agents_approval_mode"] = self.pm_agents_approval_mode
        payload["pm_agents_approval_timeout"] = self.pm_agents_approval_timeout
        payload["pm_max_failures"] = self.pm_max_failures
        payload["pm_max_blocked"] = self.pm_max_blocked
        payload["pm_max_same"] = self.pm_max_same
        payload["pm_blocked_strategy"] = self.pm_blocked_strategy
        payload["pm_blocked_degrade_max_retries"] = self.pm_blocked_degrade_max_retries
        payload["director_iterations"] = self.director_iterations
        payload["director_execution_mode"] = self.director_execution_mode
        payload["director_max_parallel_tasks"] = self.director_max_parallel_tasks
        payload["director_ready_timeout_seconds"] = self.director_ready_timeout_seconds
        payload["director_claim_timeout_seconds"] = self.director_claim_timeout_seconds
        payload["director_phase_timeout_seconds"] = self.director_phase_timeout_seconds
        payload["director_complete_timeout_seconds"] = self.director_complete_timeout_seconds
        payload["director_task_timeout_seconds"] = self.director_task_timeout_seconds
        payload["director_forever"] = self.director_forever
        payload["director_show_output"] = self.director_show_output
        payload["audit_llm_enabled"] = self.audit_llm_enabled
        payload["audit_llm_role"] = self.audit_llm_role
        payload["audit_llm_timeout"] = self.audit_llm_timeout
        payload["audit_llm_prefer_local_ollama"] = self.audit_llm_prefer_local_ollama
        payload["audit_llm_allow_remote_fallback"] = self.audit_llm_allow_remote_fallback
        payload["debug_tracing"] = self.debug_tracing
        payload["ramdisk_root"] = self.ramdisk_root
        payload["nats_enabled"] = self.nats.enabled
        payload["nats_required"] = self.nats.required
        payload["nats_url"] = self.nats.url
        payload["nats_stream_name"] = self.nats.stream_name
        return payload

    @classmethod
    def from_env(cls) -> Settings:
        """Create settings from environment variables."""
        from polaris.cells.policy.workspace_guard.public.service import (
            SELF_UPGRADE_MODE_ENV,
            ensure_workspace_target_allowed,
        )
        kwargs: dict[str, Any] = {}
        self_upgrade_mode = _parse_bool(os.environ.get(SELF_UPGRADE_MODE_ENV, "0"))
        kwargs["self_upgrade_mode"] = self_upgrade_mode

        workspace = os.environ.get("KERNELONE_WORKSPACE")
        if workspace:
            kwargs["workspace"] = str(
                ensure_workspace_target_allowed(
                    workspace,
                    self_upgrade_mode=self_upgrade_mode,
                )
            )

        timeout = os.environ.get("KERNELONE_TIMEOUT")
        if timeout is not None:
            kwargs["timeout"] = _parse_value(timeout)

        json_log = os.environ.get("KERNELONE_JSON_LOG_PATH")
        if json_log:
            kwargs["json_log_path"] = json_log

        for flag_key, env_key in (
            ("slm_enabled", "KERNELONE_SLM_ENABLED"),
            ("qa_enabled", "KERNELONE_QA_ENABLED"),
            ("audit_llm_enabled", "KERNELONE_AUDIT_LLM_ENABLED"),
            ("audit_llm_prefer_local_ollama", "KERNELONE_AUDIT_LLM_PREFER_LOCAL_OLLAMA"),
            ("audit_llm_allow_remote_fallback", "KERNELONE_AUDIT_LLM_ALLOW_REMOTE_FALLBACK"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                kwargs[flag_key] = _parse_value(raw)

        audit_role = os.environ.get("KERNELONE_AUDIT_LLM_ROLE")
        if audit_role is not None:
            kwargs["audit_llm_role"] = str(audit_role).strip().lower() or "qa"

        audit_timeout = os.environ.get("KERNELONE_AUDIT_LLM_TIMEOUT")
        if audit_timeout is not None:
            kwargs["audit_llm_timeout"] = _parse_value(audit_timeout)

        llm_config: dict[str, Any] = {}
        for key, env_key in (
            ("model", "KERNELONE_MODEL"),
            ("provider", "KERNELONE_LLM_PROVIDER"),
            ("base_url", "KERNELONE_LLM_BASE_URL"),
            ("api_key", "KERNELONE_LLM_API_KEY"),
            ("api_path", "KERNELONE_LLM_API_PATH"),
            ("timeout", "KERNELONE_LLM_TIMEOUT"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                llm_config[key] = _parse_value(raw)

        if os.environ.get("KERNELONE_PM_MODEL"):
            llm_config["model"] = os.environ.get("KERNELONE_PM_MODEL")
        elif os.environ.get("KERNELONE_DIRECTOR_MODEL"):
            llm_config["model"] = os.environ.get("KERNELONE_DIRECTOR_MODEL")
        if llm_config:
            kwargs["llm"] = LLMConfig(**llm_config)

        pm_config: dict[str, Any] = {}
        for key, env_key in (
            ("model", "KERNELONE_PM_MODEL"),
            ("backend", "KERNELONE_PM_BACKEND"),
            ("director_timeout", "KERNELONE_PM_DIRECTOR_TIMEOUT"),
            ("director_iterations", "KERNELONE_PM_DIRECTOR_ITERATIONS"),
            ("director_match_mode", "KERNELONE_PM_DIRECTOR_MATCH_MODE"),
            ("agents_approval_mode", "KERNELONE_PM_AGENTS_APPROVAL_MODE"),
            ("agents_approval_timeout", "KERNELONE_PM_AGENTS_APPROVAL_TIMEOUT"),
            ("max_failures", "KERNELONE_PM_MAX_FAILURES"),
            ("max_blocked", "KERNELONE_PM_MAX_BLOCKED"),
            ("max_same", "KERNELONE_PM_MAX_SAME"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                pm_config[key] = _parse_value(raw)
        for key, env_key in (
            ("show_output", "KERNELONE_PM_SHOW_OUTPUT"),
            ("runs_director", "KERNELONE_PM_RUNS_DIRECTOR"),
            ("director_show_output", "KERNELONE_PM_DIRECTOR_SHOW_OUTPUT"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                pm_config[key] = _parse_bool(raw)
        if pm_config:
            kwargs["pm"] = PMConfig(**pm_config)

        director_config: dict[str, Any] = {}
        for key, env_key in (
            ("model", "KERNELONE_DIRECTOR_MODEL"),
            ("iterations", "KERNELONE_DIRECTOR_ITERATIONS"),
            ("execution_mode", "KERNELONE_DIRECTOR_WORKFLOW_EXECUTION_MODE"),
            ("max_parallel_tasks", "KERNELONE_DIRECTOR_MAX_PARALLEL_TASKS"),
            ("ready_timeout_seconds", "KERNELONE_DIRECTOR_READY_TIMEOUT_SECONDS"),
            ("claim_timeout_seconds", "KERNELONE_DIRECTOR_CLAIM_TIMEOUT_SECONDS"),
            ("phase_timeout_seconds", "KERNELONE_DIRECTOR_PHASE_TIMEOUT_SECONDS"),
            ("complete_timeout_seconds", "KERNELONE_DIRECTOR_COMPLETE_TIMEOUT_SECONDS"),
            ("task_timeout_seconds", "KERNELONE_DIRECTOR_TASK_TIMEOUT_SECONDS"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                director_config[key] = _parse_value(raw)
        for key, env_key in (
            ("forever", "KERNELONE_DIRECTOR_FOREVER"),
            ("show_output", "KERNELONE_DIRECTOR_SHOW_OUTPUT"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                director_config[key] = _parse_bool(raw)
        if director_config:
            kwargs["director"] = DirectorConfig(**director_config)

        runtime_config: dict[str, Any] = {}
        for key, env_key in (
            ("root", "KERNELONE_RUNTIME_ROOT"),
            ("cache_root", "KERNELONE_RUNTIME_CACHE_ROOT"),
            ("ramdisk_root", "KERNELONE_RAMDISK_ROOT"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                runtime_config[key] = raw
        state_to_ramdisk = os.environ.get("KERNELONE_STATE_TO_RAMDISK")
        if state_to_ramdisk is not None:
            runtime_config["use_ramdisk"] = _parse_bool(state_to_ramdisk)
        if runtime_config:
            kwargs["runtime"] = RuntimeConfig(**runtime_config)

        logging_config: dict[str, Any] = {}
        log_level = os.environ.get("KERNELONE_LOG_LEVEL")
        if log_level:
            logging_config["level"] = log_level
        debug_tracing = os.environ.get("KERNELONE_DEBUG_TRACING")
        if debug_tracing is not None:
            logging_config["enable_debug_tracing"] = _parse_bool(debug_tracing)
        if kwargs.get("json_log_path"):
            logging_config["json_path"] = kwargs["json_log_path"]
        if logging_config:
            kwargs["logging"] = LoggingConfig(**logging_config)

        server_config: dict[str, Any] = {}
        backend_port = os.environ.get("KERNELONE_BACKEND_PORT")
        if backend_port and backend_port.isdigit():
            server_config["port"] = int(backend_port)
        cors_origins = os.environ.get("KERNELONE_CORS_ORIGINS")
        if cors_origins:
            parsed = [item.strip() for item in cors_origins.split(",") if item.strip()]
            if parsed:
                server_config["cors_origins"] = parsed
        if server_config:
            kwargs["server"] = ServerConfig(**server_config)

        nats_config: dict[str, Any] = {}
        for key, env_key in (
            ("enabled", "KERNELONE_NATS_ENABLED"),
            ("required", "KERNELONE_NATS_REQUIRED"),
            ("url", "KERNELONE_NATS_URL"),
            ("user", "KERNELONE_NATS_USER"),
            ("password", "KERNELONE_NATS_PASSWORD"),
            ("connect_timeout_sec", "KERNELONE_NATS_CONNECT_TIMEOUT"),
            ("reconnect_wait_sec", "KERNELONE_NATS_RECONNECT_WAIT"),
            ("max_reconnect_attempts", "KERNELONE_NATS_MAX_RECONNECT"),
            ("stream_name", "KERNELONE_NATS_STREAM_NAME"),
        ):
            raw = os.environ.get(env_key)
            if raw is not None:
                nats_config[key] = _parse_value(raw)
        if nats_config:
            kwargs["nats"] = NATSConfig(**nats_config)

        return cls(**kwargs)

def _parse_value(value: str) -> str | int | bool:
    normalized = value.lower()
    if normalized in ("true", "1", "yes"):
        return True
    if normalized in ("false", "0", "no"):
        return False
    if value.isdigit():
        return int(value)
    return value

def _parse_bool(value: str) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "on")

@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings.from_env()

def reload_settings() -> Settings:
    """Reload settings from environment."""
    get_settings.cache_clear()
    return get_settings()
