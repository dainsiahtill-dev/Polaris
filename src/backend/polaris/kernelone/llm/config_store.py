"""
LLM Configuration Store Module

Provides secure storage, validation, and management of LLM provider configurations.
Implements:
- Sensitive value masking/restoration
- Strict Pydantic validation
- Configuration backup mechanism
- Audit logging for changes
- Schema migration framework
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import time
from typing import TYPE_CHECKING, Any

from polaris.kernelone import _runtime_config
from polaris.kernelone.constants import MAX_LLM_PROVIDER_TIMEOUT_SECONDS, MAX_SUPPORTED_SCHEMA_VERSION
from polaris.kernelone.fs.jsonl.locking import file_lock
from polaris.kernelone.fs.text_ops import write_json_atomic
from polaris.kernelone.llm.exceptions import ConfigMigrationError
from polaris.kernelone.llm.runtime_config import MASKED_SECRET
from polaris.kernelone.storage import resolve_global_path
from polaris.kernelone.storage.io_paths import build_cache_root
from polaris.kernelone.utils.time_utils import utc_now_iso
from pydantic import BaseModel, Field, HttpUrl, field_validator

if TYPE_CHECKING:
    from collections.abc import Callable

#: Audit logger for configuration changes
_AUDIT_LOGGER = logging.getLogger("llm.config.audit")

#: Minimal generic policies for KernelOne LLM config.
#: Polaris role-specific policies (role_requirements, required_ready_roles)
#: have been migrated to the roles Cell via RoleProfileRegistry/builtin_profiles.
#: KernelOne only stores generic orchestrator-level policies here.
DEFAULT_POLICIES: dict[str, Any] = {
    "test_required_suites": ["connectivity", "response", "qualification"],
}

# MASKED_SECRET is imported from runtime_config (canonical definition).
_SENSITIVE_CONFIG_KEYS = {
    "api_key",
    "api-key",
    "apikey",
    "authorization",
    "token",
    "password",
    "secret",
    "x-api-key",
}

#: Schema migration registry: (from_version, to_version) -> migrator
_MIGRATIONS: dict[tuple[int, int], Callable[[dict[str, Any]], dict[str, Any]]] = {}


def _is_sensitive_config_key(key: str) -> bool:
    lowered = str(key or "").strip().lower()
    if not lowered:
        return False
    if lowered in _SENSITIVE_CONFIG_KEYS:
        return True
    return (
        lowered.endswith("_api_key")
        or lowered.endswith("-api-key")
        or lowered.endswith("_token")
        or lowered.endswith("-token")
    )


def _setting(settings: Any, field_name: str, default: Any = None) -> Any:
    if settings is None:
        return default
    return getattr(settings, field_name, default)


def _redact_sensitive_values(value: Any, *, key_hint: str = "") -> Any:
    if _is_sensitive_config_key(key_hint):
        if value is None:
            return None
        if isinstance(value, str):
            return MASKED_SECRET if value else ""
        return MASKED_SECRET

    if isinstance(value, dict):
        return {str(key): _redact_sensitive_values(item, key_hint=str(key)) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_sensitive_values(item, key_hint=key_hint) for item in value]
    return value


def _restore_masked_sensitive_values(value: Any, previous: Any, *, key_hint: str = "") -> Any:
    """
    Restore masked sensitive values from previous config.

    Restoration triggers when:
    1. The key is sensitive AND
    2. The new value is either:
       - Exactly equal to MASKED_SECRET ("********")
       - Contains more than 50% asterisks (indicating partial masking)
    """
    if _is_sensitive_config_key(key_hint):
        if isinstance(value, str):
            # Calculate masked ratio
            masked_ratio = value.count("*") / max(len(value), 1)
            # Restore if mostly masked (>= 50%) or exactly matches MASKED_SECRET
            if masked_ratio >= 0.5 or value == MASKED_SECRET:
                return previous
            return value
        return value

    if isinstance(value, dict):
        previous_dict = previous if isinstance(previous, dict) else {}
        # Merge: include keys from both value (incoming) and previous (existing).
        # This preserves migrated fields like 'providers' when incoming still
        # has v1 format ('provider'). Keys only in previous are preserved;
        # keys in both are recursively merged.
        merged_keys = set(value.keys()) | set(previous_dict.keys())
        result: dict[str, Any] = {}
        for key in merged_keys:
            val = value.get(key) if key in value else None
            prev = previous_dict.get(key) if key in previous_dict else None
            if key not in value:
                # Key only exists in previous: preserve it entirely
                result[str(key)] = prev
            else:
                # Key exists in value: recursively merge
                result[str(key)] = _restore_masked_sensitive_values(val, prev, key_hint=str(key))
        return result
    if isinstance(value, list):
        previous_list = previous if isinstance(previous, list) else []
        out = []
        for idx, item in enumerate(value):
            prior = previous_list[idx] if idx < len(previous_list) else None
            out.append(_restore_masked_sensitive_values(item, prior, key_hint=key_hint))
        return out
    return value


# =============================================================================
# Pydantic Validation Models
# =============================================================================


class ProviderCodexConfig(BaseModel):
    """Validation model for Codex CLI provider configuration."""

    sandbox: str = Field(default="safe")
    skip_git_repo_check: bool = Field(default=False)

    @field_validator("sandbox")
    @classmethod
    def validate_sandbox(cls, v: str) -> str:
        allowed = {"safe", "browser", "read-only"}
        if v == "danger-full-access":
            raise ValueError(
                f"sandbox value 'danger-full-access' is PROHIBITED for security reasons. Allowed values: {allowed}"
            )
        if v not in allowed:
            raise ValueError(f"sandbox must be one of {allowed}, got '{v}'")
        return v


class ProviderConfig(BaseModel):
    """Validation model for a single LLM provider configuration."""

    type: str = Field(..., min_length=1)
    name: str | None = None
    base_url: HttpUrl | None = None
    command: str | None = None
    timeout: float = Field(default=60.0, gt=0, le=MAX_LLM_PROVIDER_TIMEOUT_SECONDS)
    temperature: float | None = Field(default=None, ge=0, le=2)
    max_tokens: int | None = Field(default=None, gt=0, le=100000)

    @field_validator("type")
    @classmethod
    def validate_type(cls, v: str) -> str:
        valid_types = {
            "codex_cli",
            "codex_sdk",
            "gemini_cli",
            "gemini_api",
            "ollama",
            "openai_compat",
            "anthropic_compat",
            "kimi",
            "minimax",
            "deepseek",
            "moonshot",
            "stepfun",
            "zhipu",
        }
        # Allow unknown types but warn
        if v not in valid_types:
            pass  # Allow for extensibility
        return v


class RoleConfig(BaseModel):
    """Validation model for a single role configuration."""

    provider_id: str | None = Field(default=None, min_length=1)
    model: str | None = None
    profile: str | None = None


class LLMConfigSchema(BaseModel):
    """Top-level validation model for the entire LLM configuration."""

    schema_version: int = Field(default=1, ge=1)
    providers: dict[str, ProviderConfig] = Field(default_factory=dict)
    roles: dict[str, RoleConfig] = Field(default_factory=dict)
    policies: dict[str, Any] = Field(default_factory=dict)

    @field_validator("schema_version")
    @classmethod
    def validate_schema_version(cls, v: int) -> int:
        if v > MAX_SUPPORTED_SCHEMA_VERSION:
            raise ValueError(f"schema_version {v} exceeds maximum supported version {MAX_SUPPORTED_SCHEMA_VERSION}")
        return v


def validate_llm_config_strict(config: dict[str, Any]) -> LLMConfigSchema:
    """
    Strictly validate LLM configuration using Pydantic.

    Raises:
        ValidationError: If validation fails

    Returns:
        Validated LLMConfigSchema instance
    """
    return LLMConfigSchema(**config)


# =============================================================================
# Schema Migration Framework
# =============================================================================


def register_migration(from_ver: int, to_ver: int) -> Callable:
    """
    Decorator to register a schema migration function.

    Usage:
        @register_migration(1, 2)
        def migrate_v1_to_v2(config: dict) -> dict:
            config["schema_version"] = 2
            ...
            return config
    """

    def decorator(func: Callable[[dict[str, Any]], dict[str, Any]]) -> Callable:
        _MIGRATIONS[(from_ver, to_ver)] = func
        return func

    return decorator


def migrate_config(config: dict[str, Any], target_version: int) -> dict[str, Any]:
    """
    Migrate configuration schema to target version.

    Args:
        config: Configuration dictionary
        target_version: Target schema version

    Returns:
        Migrated configuration dictionary

    Raises:
        ConfigMigrationError: If migration path is missing
    """
    current = config.get("schema_version", 1)
    if current == target_version:
        return config

    if current > target_version:
        raise ConfigMigrationError(f"Cannot migrate from version {current} to lower version {target_version}")

    migrated = config.copy()
    while current < target_version:
        migrator = _MIGRATIONS.get((current, current + 1))
        if not migrator:
            raise ConfigMigrationError(f"No migrator found from schema version {current} to {current + 1}")
        migrated = migrator(migrated)
        current += 1
        migrated["schema_version"] = current

    return migrated


@register_migration(1, 2)
def _migrate_v1_to_v2(config: dict[str, Any]) -> dict[str, Any]:
    """
    Migration from schema v1 to v2:
    - Normalize single 'provider' entry to 'providers' dict
    - Fix dangerous Codex CLI defaults
    """
    if "providers" not in config or not config["providers"]:
        # v1 had single 'provider' field
        old_provider = config.pop("provider", None)
        if old_provider and isinstance(old_provider, dict):
            config["providers"] = {"default": old_provider}

    # Fix dangerous Codex CLI defaults
    providers = config.get("providers", {})
    for _provider_id, provider_cfg in providers.items():
        if isinstance(provider_cfg, dict):
            codex_exec = provider_cfg.get("codex_exec", {})
            if isinstance(codex_exec, dict):
                # Fix dangerous defaults
                if codex_exec.get("sandbox") == "danger-full-access":
                    codex_exec["sandbox"] = "safe"
                if codex_exec.get("skip_git_repo_check") is True:
                    codex_exec["skip_git_repo_check"] = False

    config["schema_version"] = 2
    return config


# =============================================================================
# Configuration Backup Mechanism
# =============================================================================


def _create_config_backup(path: str, max_backups: int = 5) -> str | None:
    """
    Create a timestamped backup of the config file.

    Args:
        path: Path to the config file
        max_backups: Maximum number of backups to retain

    Returns:
        Path to the created backup, or None if no backup was created
    """
    if not os.path.exists(path):
        return None

    backup_path = f"{path}.backup.{int(time.time())}"
    try:
        shutil.copy2(path, backup_path)
        _AUDIT_LOGGER.info(
            "config_backup_created",
            extra={
                "original_path": path,
                "backup_path": backup_path,
                "timestamp": utc_now_iso(),
            },
        )
        # Cleanup old backups
        _cleanup_old_backups(path, max_backups)
        return backup_path
    except OSError as e:
        _AUDIT_LOGGER.warning(
            "config_backup_failed",
            extra={
                "original_path": path,
                "error": str(e),
            },
        )
        return None


def _cleanup_old_backups(path: str, max_backups: int = 5) -> None:
    """
    Remove old backup files, keeping only the most recent ones.

    Args:
        path: Original config file path
        max_backups: Maximum number of backups to retain
    """
    backup_dir = os.path.dirname(path)
    if not backup_dir:
        return

    base_name = os.path.basename(path) + ".backup."
    try:
        backup_files = sorted([f for f in os.listdir(backup_dir) if f.startswith(base_name)], reverse=True)
        for old_backup in backup_files[max_backups:]:
            backup_full_path = os.path.join(backup_dir, old_backup)
            try:
                os.remove(backup_full_path)
                _AUDIT_LOGGER.debug("old_backup_removed", extra={"backup_path": backup_full_path})
            except OSError:
                _AUDIT_LOGGER.debug("Failed to remove old backup %s", backup_full_path)
    except OSError:
        _AUDIT_LOGGER.debug("Failed to list backup directory %s", backup_dir)


# =============================================================================
# Change Detection & Audit Logging
# =============================================================================


def _detect_config_changes(old_config: dict[str, Any], new_config: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Detect and return list of configuration changes.

    Args:
        old_config: Previous configuration
        new_config: New configuration

    Returns:
        List of change records with path, old_value, new_value
    """
    changes: list[dict[str, Any]] = []

    def compare_values(old: Any, new: Any, path: str, sensitive_keys: set[str] | None = None) -> None:
        if sensitive_keys is None:
            sensitive_keys = _SENSITIVE_CONFIG_KEYS

        old_is_dict = isinstance(old, dict)
        new_is_dict = isinstance(new, dict)

        if old_is_dict and new_is_dict:
            all_keys = set(old.keys()) | set(new.keys())
            for key in all_keys:
                key_path = f"{path}.{key}" if path else key
                compare_values(old.get(key), new.get(key), key_path, sensitive_keys)
        elif old_is_dict != new_is_dict:
            changes.append(
                {
                    "path": path,
                    "change_type": "type_change",
                    "old_value_type": type(old).__name__,
                    "new_value_type": type(new).__name__,
                }
            )
        elif old != new:
            # For sensitive keys, redact values in the log
            is_sensitive = any(
                key.lower() in sensitive_keys or key.lower().endswith(("_api_key", "-api-key", "_token", "-token"))
                for key in path.split(".")
            )
            changes.append(
                {
                    "path": path,
                    "change_type": "value_change",
                    "old_value": "[REDACTED]" if is_sensitive else old,
                    "new_value": "[REDACTED]" if is_sensitive else new,
                    "is_sensitive": is_sensitive,
                }
            )

    compare_values(old_config, new_config, "")
    return changes


def _log_config_change(changes: list[dict[str, Any]], source: str = "user_action") -> None:
    """
    Log configuration changes to audit logger.

    Args:
        changes: List of detected changes
        source: Source of the change (e.g., "user_action", "migration", "api")
    """
    if not changes:
        return

    _AUDIT_LOGGER.info(
        "config_changed",
        extra={
            "changed_fields": [c.get("path") for c in changes],
            "total_changes": len(changes),
            "timestamp": utc_now_iso(),
            "source": source,
            "changes_detail": changes,
        },
    )


# =============================================================================
# Default Configuration Builder
# =============================================================================


def build_default_config(settings: Any | None = None) -> dict[str, Any]:
    pm_backend = _setting(settings, "pm_backend", "auto") or "auto"
    pm_provider_id = "codex_cli" if pm_backend.strip().lower() == "codex" else "ollama"
    pm_model = _setting(settings, "pm_model") or _setting(settings, "model") or ""
    director_model = _setting(settings, "director_model") or _setting(settings, "model") or ""
    docs_provider = (
        _setting(settings, "architect_spec_provider") or _setting(settings, "docs_init_provider", "ollama") or "ollama"
    )
    docs_provider_id = "openai_compat"
    if docs_provider.strip().lower() == "ollama":
        docs_provider_id = "ollama"
    elif docs_provider.strip().lower() == "codex":
        docs_provider_id = "codex_cli"
    docs_model = _setting(settings, "architect_spec_model") or _setting(settings, "docs_init_model") or pm_model
    openai_base_url = (
        _setting(settings, "architect_spec_base_url", "") or _setting(settings, "docs_init_base_url", "") or ""
    )

    providers: dict[str, Any] = {
        "codex_cli": {
            "type": "codex_cli",
            "name": "Codex CLI",
            "command": "codex",
            "args": [],
            "cli_mode": "headless",
            # SECURE DEFAULTS: sandbox="safe" and skip_git_repo_check=False
            "codex_exec": {
                "cd": "",
                "color": "never",
                "approvals": "",
                "sandbox": "safe",  # CHANGED: was "danger-full-access"
                "skip_git_repo_check": False,  # CHANGED: was True
                "json": True,
                "experimental_json": False,
                "full_auto": False,
                "yolo": False,
                "oss": False,
                "output_schema": "",
                "output_last_message": "",
                "profile": "",
                "add_dirs": [],
                "config": [],
                "images": [],
                "prompt_from_stdin": False,
            },
            "list_args": [],
            "tui_args": [],
            "timeout": 60,
        },
        "gemini_cli": {
            "type": "gemini_cli",
            "name": "Gemini CLI",
            "command": "gemini",
            "args": ["chat", "--model", "{model}", "--prompt", "{prompt}"],
            "cli_mode": "headless",
            "list_args": ["models", "list"],
            "health_args": ["version"],
            "env": {"GOOGLE_API_KEY": "", "GOOGLE_GENAI_USE_VERTEXAI": "false", "GOOGLE_GENAI_API_KEY": ""},
            "timeout": 60,
            "thinking_extraction": {
                "enabled": True,
                "patterns": [
                    r"<thinking>(.*?)</thinking>",
                    r"```thinking(.*?)```",
                    r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                    r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                ],
                "confidence_threshold": 0.6,
            },
        },
        "minimax": {
            "type": "minimax",
            "name": "MiniMax",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key_ref": "keychain:minimax",
            "api_path": "/text/chatcompletion_v2",
            "models_path": "/v1/models",
            "timeout": 60,
            "retries": 3,
            "temperature": 0.7,
            "max_tokens": 2048,
            "thinking_extraction": {
                "enabled": True,
                "patterns": [
                    r"<思考>(.*?)</思考>",
                    r"<thinking>(.*?)</thinking>",
                    r"```思考(.*?)```",
                    r"让我想想(.*?)(?:\n\n|\n[A-Z\u4e00-\u9fff])",
                    r"我需要考虑(.*?)(?:\n\n|\n[A-Z\u4e00-\u9fff])",
                ],
                "confidence_threshold": 0.6,
            },
            "model_specific": {
                "abab6.5": {"max_tokens": 245760, "supports_thinking": True},
                "abab6.5s": {"max_tokens": 245760, "supports_thinking": True},
                "abab6": {"max_tokens": 8192, "supports_thinking": False},
            },
        },
        "gemini_api": {
            "type": "gemini_api",
            "name": "Gemini API",
            "base_url": "https://generativelanguage.googleapis.com",
            "api_key_ref": "keychain:gemini",
            "api_path": "/v1beta/models/{model}:generateContent",
            "models_path": "/v1beta/models",
            "timeout": 60,
            "retries": 3,
            "temperature": 0.7,
            "max_tokens": 8192,
            "thinking_extraction": {
                "enabled": True,
                "patterns": [
                    r"<thinking>(.*?)</thinking>",
                    r"```thinking(.*?)```",
                    r"Let me think(.*?)(?:\n\n|\n[A-Z])",
                    r"I need to consider(.*?)(?:\n\n|\n[A-Z])",
                    r"Looking at this(.*?)(?:\n\n|\n[A-Z])",
                    r"Step by step(.*?)(?:\n\n|\n[A-Z])",
                ],
                "confidence_threshold": 0.6,
            },
            "model_specific": {
                "gemini-1.5-pro": {"max_tokens": 2097152, "supports_thinking": True, "context_window": 2000000},
                "gemini-1.5-flash": {"max_tokens": 1048576, "supports_thinking": True, "context_window": 1000000},
                "gemini-1.0-pro": {"max_tokens": 32768, "supports_thinking": False, "context_window": 32768},
            },
        },
        "ollama": {
            "type": "ollama",
            "base_url": "http://120.24.117.59:11434",
            "timeout": 60,
        },
        "openai_compat": {
            "type": "openai_compat",
            "base_url": openai_base_url or "https://api.example.com/v1",
            "api_key_ref": "keychain:openai_compat",
            "api_path": "/v1/chat/completions",
            "models_path": "/v1/models",
            "timeout": 60,
            "retries": 0,
        },
        "anthropic_compat": {
            "type": "anthropic_compat",
            "name": "Anthropic Compatible",
            "base_url": "https://api.anthropic.com/v1",
            "api_key_ref": "keychain:anthropic",
            "api_path": "/v1/messages",
            "models_path": "/v1/models",
            "timeout": 60,
            "retries": 3,
            "supports_tools": True,
            "supports_json_schema": True,
            "tokenizer": "cl100k_base",
        },
        "kimi": {
            "type": "kimi",
            "name": "Kimi",
            "base_url": "https://api.moonshot.cn/v1",
            "api_key_ref": "keychain:kimi",
            "api_path": "/chat/completions",
            "models_path": "/v1/models",
            "timeout": 60,
            "retries": 3,
            "supports_tools": True,
            "supports_json_schema": True,
            "tokenizer": "cl100k_base",
        },
    }

    return {
        "schema_version": 2,
        "providers": providers,
        "roles": {
            "pm": {"provider_id": pm_provider_id, "model": pm_model, "profile": "pm-default"},
            "director": {"provider_id": "ollama", "model": director_model, "profile": "director-default"},
            "qa": {"provider_id": "ollama", "model": director_model, "profile": "qa-strict"},
            "architect": {"provider_id": docs_provider_id, "model": docs_model, "profile": "architect-writer"},
        },
        "policies": DEFAULT_POLICIES.copy(),
        "visual_layout": {},
        "visual_node_states": {},
    }


def llm_config_path(workspace: str, cache_root: str) -> str:
    del workspace, cache_root  # Kept for API compatibility; LLM config is global app data.
    return resolve_global_path("config/llm/llm_config.json")


def _load_json_payload(path: str) -> dict[str, Any]:
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        data = {}
    if not isinstance(data, dict):
        return {}
    return data


def _ensure_llm_config_exists(path: str, settings: Any | None = None) -> None:
    if os.path.isfile(path):
        return
    lock_path = f"{path}.lock"
    with file_lock(lock_path, timeout_sec=5.0) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out creating LLM config: {path}")
        if os.path.isfile(path):
            return
        write_json_atomic(path, build_default_config(settings), lock_timeout_sec=None)


def load_llm_config(
    workspace: str,
    cache_root: str,
    settings: Any | None = None,
) -> dict[str, Any]:
    path = llm_config_path(workspace, cache_root)
    _ensure_llm_config_exists(path, settings)
    data = _load_json_payload(path)
    # Read path must be non-destructive: do not rewrite user config during load.
    return normalize_llm_config(data, settings=settings)


def save_llm_config(
    workspace: str,
    cache_root: str,
    payload: dict[str, Any],
    settings: Any | None = None,
) -> dict[str, Any]:
    incoming_payload = payload if isinstance(payload, dict) else {}
    path = llm_config_path(workspace, cache_root)
    lock_path = f"{path}.lock"

    with file_lock(lock_path, timeout_sec=5.0) as acquired:
        if not acquired:
            raise TimeoutError(f"Timed out saving LLM config: {path}")

        # Load existing config
        existing_payload = _load_json_payload(path)
        if not existing_payload:
            existing_payload = build_default_config(settings)

        # Apply schema migration if needed
        current_schema = existing_payload.get("schema_version", 1)
        target_schema = 2  # Current stable schema version
        if current_schema < target_schema:
            _AUDIT_LOGGER.info(
                "config_migration_started",
                extra={
                    "from_version": current_schema,
                    "to_version": target_schema,
                    "path": path,
                },
            )
            try:
                existing_payload = migrate_config(existing_payload, target_schema)
                # Save migrated config immediately
                write_json_atomic(path, existing_payload, lock_timeout_sec=None)
                _AUDIT_LOGGER.info(
                    "config_migration_completed",
                    extra={
                        "from_version": current_schema,
                        "to_version": target_schema,
                    },
                )
            except ConfigMigrationError as e:
                _AUDIT_LOGGER.error("config_migration_failed", extra={"error": str(e)})
                raise

        existing_payload = normalize_llm_config(existing_payload, settings=settings)

        # Restore masked sensitive values
        merged_payload = _restore_masked_sensitive_values(incoming_payload, existing_payload)
        normalized = normalize_llm_config(merged_payload, settings=settings)

        # Ensure schema version is at least the target version (migration result takes precedence)
        if normalized.get("schema_version", 1) < target_schema:
            normalized["schema_version"] = target_schema

        # Create backup before saving
        _create_config_backup(path, max_backups=5)

        # Detect and log changes
        changes = _detect_config_changes(existing_payload, normalized)
        if changes:
            _log_config_change(changes, source="user_action")

        # Validate configuration
        is_valid, errors, warnings = validate_llm_config(normalized)
        if not is_valid:
            error_msg = "; ".join(errors)
            raise ValueError(f"Invalid LLM configuration: {error_msg}")

        if warnings:
            for warning in warnings:
                _AUDIT_LOGGER.warning("llm_config_validation_warning", extra={"warning": warning})

        # Write configuration atomically
        write_json_atomic(path, normalized, lock_timeout_sec=None)

        return normalized


def normalize_llm_config(payload: dict[str, Any], settings: Any | None = None) -> dict[str, Any]:
    base = build_default_config(settings)

    data = payload.copy() if isinstance(payload, dict) else {}
    schema_version = data.get("schema_version", 1)
    known_keys = {
        "schema_version",
        "providers",
        "roles",
        "policies",
        "visual_layout",
        "visual_node_states",
        "visual_viewport",
    }
    passthrough = {key: value for key, value in data.items() if key not in known_keys}

    # Provider logic: if user supplies providers, use them (allows deletion).
    # Otherwise fall back to defaults.
    user_providers = data.get("providers")
    providers = user_providers if isinstance(user_providers, dict) else base.get("providers", {})

    roles = data.get("roles")
    roles = base.get("roles", {}) if not isinstance(roles, dict) else dict(roles)
    if isinstance(roles, dict):
        if "architect" not in roles and isinstance(roles.get("docs"), dict):
            roles["architect"] = roles.get("docs")
        roles.pop("docs", None)

    policies = data.get("policies")
    if not isinstance(policies, dict):
        policies = base.get("policies", {})
    else:
        base_policies = base.get("policies", {})
        role_requirements = policies.get("role_requirements")
        if isinstance(role_requirements, dict):
            normalized_role_requirements = dict(role_requirements)
            if "architect" not in normalized_role_requirements and isinstance(
                normalized_role_requirements.get("docs"), dict
            ):
                normalized_role_requirements["architect"] = normalized_role_requirements.get("docs")
            normalized_role_requirements.pop("docs", None)
            base_role_requirements = base_policies.get("role_requirements", {})
            if isinstance(base_role_requirements, dict):
                policies = {
                    **policies,
                    "role_requirements": {**base_role_requirements, **normalized_role_requirements},
                }
        required_ready_roles = policies.get("required_ready_roles")
        if isinstance(required_ready_roles, list):
            normalized_required: list[str] = []
            for role in required_ready_roles:
                role_id = str(role).strip().lower()
                # Architect (and legacy docs alias) is intentionally excluded from loop-ready gate.
                if not role_id or role_id in ("docs", "architect") or role_id in normalized_required:
                    continue
                normalized_required.append(role_id)
            policies = {
                **policies,
                "required_ready_roles": normalized_required or list(base_policies.get("required_ready_roles", [])),
            }
    visual_layout = data.get("visual_layout")
    if not isinstance(visual_layout, dict):
        visual_layout = base.get("visual_layout", {})

    visual_node_states = data.get("visual_node_states")
    if not isinstance(visual_node_states, dict):
        visual_node_states = base.get("visual_node_states", {})

    visual_viewport = data.get("visual_viewport")
    if not isinstance(visual_viewport, dict):
        visual_viewport = None

    merged = {
        "schema_version": int(schema_version or 1),
        "providers": providers,
        "roles": {**base.get("roles", {}), **roles},
        "policies": {**base.get("policies", {}), **policies},
        "visual_layout": visual_layout,
        "visual_node_states": visual_node_states,
        **passthrough,
    }
    if visual_viewport:
        merged["visual_viewport"] = visual_viewport
    return merged


def validate_llm_config(config: dict[str, Any]) -> tuple[bool, list[str], list[str]]:
    """
    Validate LLM configuration.

    Args:
        config: LLM configuration dictionary

    Returns:
        Tuple of (is_valid, errors, warnings)
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(config, dict):
        errors.append("Config must be a dictionary")
        return False, errors, warnings

    # Try strict Pydantic validation
    try:
        validate_llm_config_strict(config)
    except (ValueError, TypeError) as e:
        errors.append(f"Schema validation failed: {e}")
        return False, errors, warnings

    providers = config.get("providers")
    if isinstance(providers, dict):
        for provider_id, provider_cfg in providers.items():
            if not isinstance(provider_cfg, dict):
                warnings.append(f"Provider '{provider_id}' config is not a dictionary")
                continue
            provider_type = provider_cfg.get("type")
            if not provider_type:
                errors.append(f"Provider '{provider_id}' is missing 'type' field")

            # Security check for Codex CLI
            if provider_type == "codex_cli":
                codex_exec = provider_cfg.get("codex_exec", {})
                if isinstance(codex_exec, dict):
                    sandbox = codex_exec.get("sandbox", "")
                    if sandbox == "danger-full-access":
                        warnings.append(f"Provider '{provider_id}' uses dangerous sandbox mode 'danger-full-access'")

    roles = config.get("roles")
    if isinstance(roles, dict):
        required_roles = config.get("policies", {}).get("required_ready_roles", [])
        for role_id in required_roles:
            role_cfg = roles.get(role_id)
            if role_id not in roles or not isinstance(role_cfg, dict):
                errors.append(f"Required role '{role_id}' not defined in roles")
                continue
            if not role_cfg.get("provider_id"):
                errors.append(f"Required role '{role_id}' is missing 'provider_id'")

        for role_id, role_cfg in roles.items():
            if not isinstance(role_cfg, dict):
                warnings.append(f"Role '{role_id}' config is not a dictionary")
                continue
            provider_id = role_cfg.get("provider_id")
            if provider_id:
                if not isinstance(providers, dict):
                    errors.append(f"Role '{role_id}' references provider '{provider_id}' but providers not defined")
                elif provider_id not in providers:
                    errors.append(f"Role '{role_id}' references non-existent provider '{provider_id}'")

    schema_version = config.get("schema_version")
    if schema_version is not None and not isinstance(schema_version, int):
        warnings.append(f"Invalid schema_version '{schema_version}', expected integer")

    return len(errors) == 0, errors, warnings


def redact_llm_config(payload: dict[str, Any]) -> dict[str, Any]:
    copied = json.loads(json.dumps(payload))
    return _redact_sensitive_values(copied)


def resolve_workspace_cache_root(settings: Any) -> str:
    workspace = str(_setting(settings, "workspace", ".") or ".")
    ramdisk_root = str(_setting(settings, "ramdisk_root", "") or "")
    return build_cache_root(ramdisk_root, workspace)


def resolve_workspace_cache_root_for_workspace(
    workspace: str,
    *,
    ramdisk_root: str | None = None,
) -> str:
    root = str(ramdisk_root).strip() if ramdisk_root is not None else _runtime_config.resolve_env_str("ramdisk_root")
    return build_cache_root(root, str(workspace or "."))
