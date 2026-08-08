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


def _truthy_config_flag(provider_cfg: dict[str, Any], name: str) -> bool:
    raw_flag = provider_cfg.get(name)
    if isinstance(raw_flag, bool):
        return raw_flag
    if raw_flag is None:
        return False
    return str(raw_flag).strip().lower() in {"1", "true", "yes", "on", "enabled", "enable"}


def _director_tool_contract_supported(provider_cfg: dict[str, Any]) -> bool:
    if _truthy_config_flag(provider_cfg, "director_tool_contract_supported"):
        return True

    tool_schema_profile = str(provider_cfg.get("tool_schema_profile") or "").strip().lower()
    execution_profile = str(provider_cfg.get("execution_profile") or "").strip().lower()
    return tool_schema_profile == "full" and execution_profile == "full"


def _tool_choice_disabled(provider_cfg: dict[str, Any]) -> bool:
    raw_flag = provider_cfg.get("disable_tool_choice")
    if isinstance(raw_flag, bool):
        return raw_flag
    if raw_flag is not None:
        token = str(raw_flag).strip().lower()
        if token in {"1", "true", "yes", "on", "disabled", "disable"}:
            return True

    return False


def _is_deepseek_anthropic_compat(provider_cfg: dict[str, Any]) -> bool:
    token = " ".join(
        [
            str(provider_cfg.get("base_url") or ""),
            str(provider_cfg.get("api_path") or ""),
            str(provider_cfg.get("name") or ""),
            str(provider_cfg.get("model") or ""),
        ]
    ).lower()
    return _normalize_provider_type(provider_cfg) == "anthropic_compat" and "deepseek" in token


def _codex_exec_sandbox(provider_cfg: dict[str, Any]) -> str:
    opts = provider_cfg.get("codex_exec")
    if not isinstance(opts, dict):
        return "read-only"
    sandbox = str(opts.get("sandbox") or "").strip().lower()
    return sandbox or "read-only"


def role_runtime_support_issue(
    role: str,
    provider_id: str | None,
    provider_cfg: dict[str, Any],
) -> str:
    role_key = str(role or "").strip().lower()
    if role_key != "director":
        return ""

    if is_codex_provider(provider_id, provider_cfg):
        sandbox = _codex_exec_sandbox(provider_cfg)
        if sandbox == "read-only":
            return "director_codex_read_only_sandbox"
        if sandbox not in {"workspace-write", "danger-full-access"}:
            return "director_codex_invalid_sandbox"

    if _tool_choice_disabled(provider_cfg):
        return "director_tool_choice_disabled"

    if _is_deepseek_anthropic_compat(provider_cfg) and not _director_tool_contract_supported(provider_cfg):
        # DeepSeek's Anthropic-compatible surface has varied across deployments.
        # Do not infer support from the provider brand and do not require a hidden
        # process environment escape hatch.  The persisted provider capability
        # contract is authoritative: either an explicit operator verification or
        # both full tool-schema and execution profiles must be present.
        return "director_deepseek_tool_contract_unverified"

    provider_type = _normalize_provider_type(provider_cfg)
    if provider_type == "minimax" and not _director_tool_contract_supported(provider_cfg):
        # MiniMax chat endpoints may be usable for planning roles, but current
        # configured M2.x responses do not expose enforceable native tool_calls.
        # Keep Director blocked unless an operator explicitly marks this
        # provider/model as contract-verified or assigns the full tool/execution
        # profile after a tool-call probe.
        return "director_minimax_tool_contract_unverified"

    return ""


def is_role_runtime_supported(
    role: str,
    provider_id: str | None,
    provider_cfg: dict[str, Any],
) -> bool:
    # Director's transaction kernel relies on enforceable native tool
    # selection for materialized changes. Providers that reject tool_choice
    # may still answer in natural language, but cannot be considered safe
    # for unattended code execution.
    return not bool(role_runtime_support_issue(role, provider_id, provider_cfg))
