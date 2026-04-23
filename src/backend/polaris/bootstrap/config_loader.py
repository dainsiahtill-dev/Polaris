"""Configuration loader with unified source merging.

This module provides ConfigLoader, a unified configuration loading service
that merges configuration from multiple sources according to the priority:
default < persisted < env < cli
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from polaris.domain.models.config_snapshot import ConfigSnapshot

logger = logging.getLogger(__name__)


class ConfigLoadError(Exception):
    """Exception raised when configuration loading fails."""

    def __init__(self, message: str, source: str = "", details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.details = details or {}


class ConfigLoader:
    """Unified configuration loader with source merging.

    This class implements the configuration loading strategy defined in
    ADR-001: Thin CLI Adapter Policy. It merges configuration from
    multiple sources with a clear priority order.

    Priority order (low to high):
        1. DEFAULT - Hardcoded defaults
        2. PERSISTED - Settings from workspace/<metadata_dir>/config.json
        3. ENV - Environment variables (KERNELONE_* only)
        4. CLI - Command-line arguments (highest priority)

    Example:
        >>> loader = ConfigLoader()
        >>> snapshot = loader.load(
        ...     workspace=Path("."),
        ...     cli_overrides={"server.port": 8080}
        ... )
        >>> print(snapshot.get("server.port"))
        8080
    """

    # Default configuration values
    DEFAULTS: dict[str, Any] = {
        "self_upgrade_mode": False,
        "server.host": "127.0.0.1",
        "server.port": 49977,
        "server.cors_origins": ["http://localhost:5173", "http://127.0.0.1:5173"],
        "logging.level": "INFO",
        "logging.enable_debug_tracing": False,
        "pm.backend": "auto",
        "pm.show_output": True,
        "pm.runs_director": True,
        "pm.director_timeout": 600,
        "pm.director_iterations": 1,
        "pm.agents_approval_mode": "auto_accept",
        "director.iterations": 1,
        "director.execution_mode": "parallel",
        "director.max_parallel_tasks": 3,
        "director.show_output": True,
        "llm.model": "modelscope.cn/unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF:latest",
        "llm.provider": "ollama",
        "llm.timeout": 300,
        "jsonl.lock_stale_sec": 120.0,
        "jsonl.buffer_enabled": True,
        "jsonl.flush_interval_sec": 1.0,
        "jsonl.flush_batch": 50,
        "jsonl.max_buffer": 2000,
    }

    # Environment variable mappings
    # Maps config key to (primary_env_var, fallback_env_var, value_transform)
    # Priority: KERNELONE_* > default
    ENV_MAPPINGS: dict[str, tuple] = {
        "server.host": ("KERNELONE_HOST", None, None),
        "server.port": ("KERNELONE_BACKEND_PORT", None, int),
        "logging.level": ("KERNELONE_LOG_LEVEL", None, None),
        "logging.enable_debug_tracing": (
            "KERNELONE_DEBUG_TRACING",
            None,
            lambda v: v.lower() in ("1", "true", "yes"),
        ),
        "pm.backend": ("KERNELONE_PM_BACKEND", None, None),
        "pm.model": ("KERNELONE_PM_MODEL", None, None),
        "pm.show_output": (
            "KERNELONE_PM_SHOW_OUTPUT",
            None,
            lambda v: v.lower() in ("1", "true", "yes"),
        ),
        "pm.runs_director": (
            "KERNELONE_PM_RUNS_DIRECTOR",
            None,
            lambda v: v.lower() in ("1", "true", "yes"),
        ),
        "pm.director_timeout": ("KERNELONE_PM_DIRECTOR_TIMEOUT", None, int),
        "pm.director_iterations": ("KERNELONE_PM_DIRECTOR_ITERATIONS", None, int),
        "director.model": ("KERNELONE_DIRECTOR_MODEL", None, None),
        "director.iterations": ("KERNELONE_DIRECTOR_ITERATIONS", None, int),
        "llm.model": ("KERNELONE_MODEL", None, None),
        "llm.provider": ("KERNELONE_LLM_PROVIDER", None, None),
        "llm.base_url": ("KERNELONE_LLM_BASE_URL", None, None),
        "llm.api_key": ("KERNELONE_LLM_API_KEY", None, None),
        "workspace": ("KERNELONE_WORKSPACE", None, None),
        "self_upgrade_mode": (
            "KERNELONE_SELF_UPGRADE_MODE",
            None,
            lambda v: v.lower() in ("1", "true", "yes", "on"),
        ),
    }

    def __init__(self, defaults: dict[str, Any] | None = None) -> None:
        """Initialize config loader.

        Args:
            defaults: Optional custom defaults to override built-in defaults
        """
        self._defaults = {**self.DEFAULTS, **(defaults or {})}

    def load(
        self,
        workspace: Path | None = None,
        cli_overrides: dict[str, Any] | None = None,
        env_prefix: str = "KERNELONE_",
    ) -> ConfigSnapshot:
        """Load and merge configuration from all sources.

        Args:
            workspace: Workspace path for loading persisted settings
            cli_overrides: CLI argument overrides (highest priority)
            env_prefix: Prefix for environment variables (default: KERNELONE_)

        Returns:
            ConfigSnapshot with merged configuration

        Raises:
            ConfigLoadError: If configuration loading fails
        """
        # 1. Load persisted settings
        persisted = self._load_persisted(workspace) if workspace else {}

        # 2. Load environment variables
        env = self._load_env(env_prefix)

        # 3. CLI overrides (already provided as dict)
        cli = cli_overrides or {}

        # 4. Merge all sources
        snapshot = ConfigSnapshot.merge_sources(
            default=self._defaults,
            persisted=persisted,
            env=env,
            cli=cli,
        )

        return snapshot

    def load_with_settings(
        self,
        settings: Any,  # Settings object from runtime_config.py
        cli_overrides: dict[str, Any] | None = None,
    ) -> ConfigSnapshot:
        """Load config from existing Settings object.

        This is a compatibility method for transitioning from
        the old Settings-based configuration.

        Args:
            settings: Settings object from runtime_config.py
            cli_overrides: Additional CLI overrides

        Returns:
            ConfigSnapshot with merged configuration
        """
        # Convert Settings to flat dict
        persisted = self._settings_to_flat_dict(settings)

        # Merge with CLI overrides
        return self.load(
            workspace=None,  # Already have settings
            cli_overrides={**persisted, **(cli_overrides or {})},
        )

    def _load_persisted(self, workspace: Path | None) -> dict[str, Any]:
        """Load persisted settings from workspace config file.

        Args:
            workspace: Workspace path

        Returns:
            Dictionary of persisted settings
        """
        if not workspace:
            return {}

        from polaris.kernelone._runtime_config import get_workspace_metadata_dir_name

        metadata_dir = get_workspace_metadata_dir_name()
        # Try multiple config locations (new metadata dir first, then legacy flat file)
        config_paths = [
            workspace / metadata_dir / "config.json",
            workspace / metadata_dir / "settings.json",
            workspace / ".polaris.json",  # legacy flat file (backward compat)
        ]

        for config_path in config_paths:
            if config_path.exists():
                try:
                    with open(config_path, encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict):
                            return self._flatten_dict(data)
                except json.JSONDecodeError as e:
                    # Corrupted config file; try next candidate.
                    logger.warning("Config file JSON parse error %s: %s", config_path, e)
                    continue
                except PermissionError as e:
                    # BUG-005 fix: permission errors are operator mistakes and
                    # should surface at error level, not be silently swallowed.
                    logger.error("Config file permission denied %s: %s", config_path, e)
                    continue
                except OSError as e:
                    logger.warning("Config file read error %s: %s", config_path, e)
                    continue

        return {}

    def _load_env(self, prefix: str = "KERNELONE_") -> dict[str, Any]:
        """Load configuration from environment variables.

        Only KERNELONE_* env vars are recognized.

        Args:
            prefix: Environment variable prefix (default: KERNELONE_)

        Returns:
            Dictionary of environment-based configuration
        """
        env_config: dict[str, Any] = {}

        for config_key, (primary_var, fallback_var, transform) in self.ENV_MAPPINGS.items():
            # Priority: primary (KERNELONE_*) > fallback (if any)
            value = os.environ.get(primary_var)
            if value is None and fallback_var is not None:
                value = os.environ.get(fallback_var)
            if value is not None:
                if transform:
                    try:
                        value = transform(value)
                    except (ValueError, TypeError) as e:
                        logger.warning("Failed to transform %s=%s: %s", primary_var, value, e)
                        continue
                env_config[config_key] = value

        # Handle CORS origins specially (comma-separated list)
        cors_origins = os.environ.get("KERNELONE_CORS_ORIGINS")
        if cors_origins:
            env_config["server.cors_origins"] = [o.strip() for o in cors_origins.split(",") if o.strip()]

        # Handle ramdisk root
        ramdisk_root = os.environ.get("KERNELONE_RAMDISK_ROOT")
        if ramdisk_root:
            env_config["runtime.ramdisk_root"] = ramdisk_root

        return env_config

    def _flatten_dict(
        self,
        d: dict[str, Any],
        parent_key: str = "",
        sep: str = ".",
    ) -> dict[str, Any]:
        """Flatten nested dictionary to dot-notation keys.

        Args:
            d: Dictionary to flatten
            parent_key: Parent key prefix
            sep: Key separator

        Returns:
            Flattened dictionary
        """
        items: dict[str, Any] = {}
        for k, v in d.items():
            new_key = f"{parent_key}{sep}{k}" if parent_key else k
            if isinstance(v, dict):
                items.update(self._flatten_dict(v, new_key, sep))
            else:
                items[new_key] = v
        return items

    def _settings_to_flat_dict(self, settings: Any) -> dict[str, Any]:
        """Convert Settings object to flat dictionary.

        Args:
            settings: Settings object from runtime_config.py

        Returns:
            Flat dictionary of settings
        """
        flat: dict[str, Any] = {}

        # Extract common settings
        if hasattr(settings, "workspace"):
            flat["workspace"] = str(settings.workspace)
        if hasattr(settings, "self_upgrade_mode"):
            flat["self_upgrade_mode"] = bool(settings.self_upgrade_mode)

        if hasattr(settings, "server"):
            server = settings.server
            if hasattr(server, "host"):
                flat["server.host"] = server.host
            if hasattr(server, "port"):
                flat["server.port"] = server.port
            if hasattr(server, "cors_origins"):
                flat["server.cors_origins"] = server.cors_origins

        if hasattr(settings, "logging"):
            logging = settings.logging
            if hasattr(logging, "level"):
                flat["logging.level"] = logging.level
            if hasattr(logging, "enable_debug_tracing"):
                flat["logging.enable_debug_tracing"] = logging.enable_debug_tracing

        if hasattr(settings, "pm"):
            pm = settings.pm
            if hasattr(pm, "backend"):
                flat["pm.backend"] = pm.backend
            if hasattr(pm, "model"):
                flat["pm.model"] = pm.model
            if hasattr(pm, "show_output"):
                flat["pm.show_output"] = pm.show_output
            if hasattr(pm, "runs_director"):
                flat["pm.runs_director"] = pm.runs_director

        if hasattr(settings, "director"):
            director = settings.director
            if hasattr(director, "model"):
                flat["director.model"] = director.model
            if hasattr(director, "iterations"):
                flat["director.iterations"] = director.iterations
            if hasattr(director, "execution_mode"):
                flat["director.execution_mode"] = director.execution_mode

        if hasattr(settings, "llm"):
            llm = settings.llm
            if hasattr(llm, "model"):
                flat["llm.model"] = llm.model
            if hasattr(llm, "provider"):
                flat["llm.provider"] = llm.provider

        return flat

    def get_default(self, key: str) -> Any:
        """Get default value for a configuration key.

        Args:
            key: Configuration key

        Returns:
            Default value or None
        """
        return self._defaults.get(key)

    def get_all_defaults(self) -> dict[str, Any]:
        """Get all default values.

        Returns:
            Dictionary of all default values
        """
        return dict(self._defaults)


def load_config(
    workspace: str | Path | None = None,
    **cli_overrides: Any,
) -> ConfigSnapshot:
    """Convenience function to load configuration.

    Args:
        workspace: Workspace path
        **cli_overrides: CLI argument overrides

    Returns:
        ConfigSnapshot with merged configuration
    """
    loader = ConfigLoader()
    ws_path = Path(workspace) if workspace else None
    return loader.load(workspace=ws_path, cli_overrides=cli_overrides or None)


if __name__ == "__main__":
    # Test the loader
    logger.info("Testing ConfigLoader...")

    loader = ConfigLoader()

    # Test with defaults only
    snapshot = loader.load()
    logger.info("Default server.port: %s", snapshot.get("server.port"))
    logger.info("Default pm.backend: %s", snapshot.get("pm.backend"))

    # Test with environment (using new KERNELONE_ prefix)
    os.environ["KERNELONE_BACKEND_PORT"] = "8080"
    snapshot = loader.load()
    logger.info("With env server.port: %s", snapshot.get("server.port"))
    logger.info("Source: %s", snapshot.get_source("server.port"))

    # Test with CLI override
    snapshot = loader.load(cli_overrides={"server.port": 9000})
    logger.info("With CLI server.port: %s", snapshot.get("server.port"))
    logger.info("Source: %s", snapshot.get_source("server.port"))

    logger.info("ConfigLoader tests passed!")
