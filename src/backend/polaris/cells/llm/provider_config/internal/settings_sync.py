"""Application-layer synchronization between LLM config and process settings.

Architecture: This module has two functions:
1. ``compute_llm_config_sync_updates``: Pure function returning a delta dict.
   Preferred for new code. Does not mutate Settings.
2. ``sync_settings_from_llm``: Applies updates to a Settings object.
   Kept for backward compatibility; calls compute_llm_config_sync_updates internally.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Mapping

    from polaris.bootstrap.config import Settings


def compute_llm_config_sync_updates(config: Mapping[str, Any]) -> dict[str, Any]:
    """Compute the Settings updates implied by an LLM config dict.

    This is a pure function: it returns a delta dict without mutating any object.
    The caller is responsible for applying the updates to a Settings instance.

    Args:
        config: LLM config mapping with optional ``roles`` and ``providers`` keys.

    Returns:
        Dict of attribute_name -> value to be applied to Settings.
        Only keys with non-None values are included.
    """
    roles: dict[str, Any] = config.get("roles", {}) if isinstance(config.get("roles"), dict) else {}
    providers: dict[str, Any] = config.get("providers", {}) if isinstance(config.get("providers"), dict) else {}

    updates: dict[str, Any] = {}

    pm_role = roles.get("pm") if isinstance(roles.get("pm"), dict) else None
    if pm_role:
        updates["pm_backend"] = "auto"
        if pm_role.get("model"):
            updates["pm_model"] = pm_role.get("model")

    director_role = roles.get("director") if isinstance(roles.get("director"), dict) else None
    if director_role and director_role.get("model"):
        updates["director_model"] = director_role.get("model")

    architect_role = roles.get("architect") if isinstance(roles.get("architect"), dict) else None
    docs_role = architect_role or (roles.get("docs") if isinstance(roles.get("docs"), dict) else None)
    if not docs_role:
        return updates

    provider_id = docs_role.get("provider_id")
    provider_cfg: dict[str, Any] = providers.get(provider_id, {}) if isinstance(providers, dict) else {}
    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    command = str(provider_cfg.get("command") or "").strip().lower()

    if provider_type == "ollama":
        updates["architect_spec_provider"] = "ollama"
        updates["docs_init_provider"] = "ollama"
    elif provider_type in ("cli", "codex_cli", "codex_sdk") and (
        "codex" in command or provider_id == "codex_cli" or provider_type == "codex_sdk"
    ):
        updates["architect_spec_provider"] = "codex"
        updates["docs_init_provider"] = "codex"
    elif provider_type == "openai_compat":
        updates["architect_spec_provider"] = "custom"
        updates["docs_init_provider"] = "custom"
        if provider_cfg.get("base_url"):
            updates["architect_spec_base_url"] = provider_cfg.get("base_url")
            updates["docs_init_base_url"] = provider_cfg.get("base_url")
        if provider_cfg.get("api_path"):
            updates["architect_spec_api_path"] = provider_cfg.get("api_path")
            updates["docs_init_api_path"] = provider_cfg.get("api_path")

    if docs_role.get("model"):
        updates["architect_spec_model"] = docs_role.get("model")
        updates["docs_init_model"] = docs_role.get("model")

    return updates


def sync_settings_from_llm(settings: Settings, config: Mapping[str, Any]) -> None:
    """Apply relevant LLM config fields back onto process settings.

    This keeps runtime settings aligned with the canonical role mapping config
    without making the config store itself depend on application settings.

    Args:
        settings: Settings instance to mutate.
        config: LLM config mapping.
    """
    updates = compute_llm_config_sync_updates(config)
    for key, value in updates.items():
        setattr(settings, key, value)


__all__ = ["compute_llm_config_sync_updates", "sync_settings_from_llm"]
