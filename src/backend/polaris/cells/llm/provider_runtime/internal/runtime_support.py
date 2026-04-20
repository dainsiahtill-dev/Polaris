from __future__ import annotations

from typing import Any


def _normalize_provider_type(provider_cfg: dict[str, Any]) -> str:
    return str(provider_cfg.get("type") or "").strip().lower()


def is_codex_provider(provider_id: str | None, provider_cfg: dict[str, Any]) -> bool:
    provider_type = _normalize_provider_type(provider_cfg)
    command = str(provider_cfg.get("command") or "").strip().lower()
    provider_id = str(provider_id or "").strip().lower()
    if provider_type in ("codex_cli", "codex_sdk"):
        return True
    return bool(provider_type == "cli" and ("codex" in command or provider_id == "codex_cli"))


def get_role_runtime_provider_kind(
    role: str,
    provider_id: str | None,
    provider_cfg: dict[str, Any],
) -> str:
    provider_type = _normalize_provider_type(provider_cfg)

    if provider_type == "ollama":
        return "ollama"
    if is_codex_provider(provider_id, provider_cfg):
        return "codex"
    return "generic"


def is_role_runtime_supported(
    role: str,
    provider_id: str | None,
    provider_cfg: dict[str, Any],
) -> bool:
    # Runtime no longer restricts role/provider combinations by provider type.
    # Any configured provider is considered supported for any role.
    del role, provider_id, provider_cfg
    return True
