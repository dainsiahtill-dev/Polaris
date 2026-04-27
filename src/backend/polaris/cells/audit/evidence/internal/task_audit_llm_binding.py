"""Audit LLM runtime binding helpers.

Connects IndependentAuditService to the existing runtime role/provider
invocation path while keeping audit role ownership explicit.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.llm.provider_runtime.public.service import (
    RuntimeProviderInvokeResult,
    invoke_role_runtime_provider,
    normalize_provider_type,
)

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)


AUDIT_TECH_ROLE_ID = "qa"
AUDIT_COURT_DEPARTMENT = "QA Department"
AUDIT_COURT_ROLE_ID = "menxia_shizhong"
AUDIT_COURT_ROLE_NAME = "QA"

_DEFAULT_NON_LOCAL_PROVIDER_TYPES = frozenset(
    {
        "anthropic_compat",
        "codex_cli",
        "codex_sdk",
        "gemini_api",
        "gemini_cli",
        "kimi",
        "minimax",
        "openai_compat",
    }
)


@dataclass(frozen=True)
class AuditLLMBindingConfig:
    """Runtime binding policy for independent audit LLM calls."""

    enabled: bool = True
    role_id: str = AUDIT_TECH_ROLE_ID
    timeout_seconds: int = 180
    prefer_local_ollama: bool = True
    allow_remote_fallback: bool = True
    fallback_model: str = ""


def get_audit_role_descriptor() -> dict[str, str]:
    """Return canonical audit role mapping used by the runtime."""

    return {
        "tech_role_id": AUDIT_TECH_ROLE_ID,
        "court_department": AUDIT_COURT_DEPARTMENT,
        "court_role_id": AUDIT_COURT_ROLE_ID,
        "court_role_name": AUDIT_COURT_ROLE_NAME,
    }


def build_audit_llm_binding_config(settings: Any) -> AuditLLMBindingConfig:
    """Build typed audit LLM config from Settings-like object."""

    role_id = str(getattr(settings, "audit_llm_role", AUDIT_TECH_ROLE_ID) or "").strip().lower()
    if not role_id:
        role_id = AUDIT_TECH_ROLE_ID

    timeout_raw = getattr(settings, "audit_llm_timeout", 180)
    try:
        timeout_seconds = max(30, int(timeout_raw))
    except (TypeError, ValueError):
        timeout_seconds = 180

    fallback_model = str(getattr(settings, "model", "") or "").strip()

    _enabled = getattr(settings, "audit_llm_enabled", True)
    _prefer_local = getattr(settings, "audit_llm_prefer_local_ollama", True)
    _allow_fallback = getattr(settings, "audit_llm_allow_remote_fallback", True)

    return AuditLLMBindingConfig(
        enabled=True if _enabled is None else bool(_enabled),
        role_id=role_id,
        timeout_seconds=timeout_seconds,
        prefer_local_ollama=True if _prefer_local is None else bool(_prefer_local),
        allow_remote_fallback=True if _allow_fallback is None else bool(_allow_fallback),
        fallback_model=fallback_model,
    )


def _resolve_non_local_provider_types(workspace: str, settings: Any) -> set[str]:
    """Collect all configured provider types except local Ollama."""

    provider_types = set(_DEFAULT_NON_LOCAL_PROVIDER_TYPES)
    try:
        from polaris.kernelone.llm.config_store import load_llm_config
        from polaris.kernelone.storage.io_paths import build_cache_root

        cache_root = build_cache_root(str(getattr(settings, "ramdisk_root", "") or ""), workspace)
        payload = load_llm_config(workspace, cache_root, settings=settings)
        providers = payload.get("providers") if isinstance(payload, dict) else {}
        if not isinstance(providers, dict):
            return provider_types

        discovered: set[str] = set()
        for raw_cfg in providers.values():
            if not isinstance(raw_cfg, dict):
                continue
            token = normalize_provider_type(str(raw_cfg.get("type") or "").strip().lower())
            if token and token != "ollama":
                discovered.add(token)
        if discovered:
            provider_types = discovered
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to load provider map for local audit preference: %s", exc)
    return provider_types


def _result_error_summary(result: RuntimeProviderInvokeResult) -> str:
    if result.ok:
        return ""
    if result.error:
        return str(result.error)
    if not result.attempted and result.provider_type:
        return "provider_blocked_or_unavailable"
    if not result.attempted:
        return "provider_not_attempted"
    return "provider_invocation_failed"


def _build_provider_info(
    *,
    role_id: str,
    strategy: str,
    result: RuntimeProviderInvokeResult,
    note: str = "",
) -> dict[str, str]:
    info: dict[str, str] = {
        "tech_role_id": role_id,
        "court_department": AUDIT_COURT_DEPARTMENT,
        "court_role_id": AUDIT_COURT_ROLE_ID,
        "court_role_name": AUDIT_COURT_ROLE_NAME,
        "llm_strategy": strategy,
        "llm_provider_id": str(result.provider_id or ""),
        "llm_provider_type": str(result.provider_type or ""),
        "llm_model": str(result.model or ""),
        "llm_attempted": "true" if bool(result.attempted) else "false",
        "llm_ok": "true" if bool(result.ok) else "false",
        "llm_latency_ms": str(int(result.latency_ms or 0)),
    }
    if note:
        info["note"] = note
    if result.error:
        info["llm_error"] = str(result.error)
    return info


def make_audit_llm_caller(
    *,
    workspace: str,
    settings: Any,
    config: AuditLLMBindingConfig | None = None,
) -> Callable[[str, str], tuple[str, dict[str, str]]]:
    """Build `IndependentAuditService` compatible LLM caller."""

    binding = config or build_audit_llm_binding_config(settings)
    workspace_value = str(workspace or getattr(settings, "workspace", ".") or ".")
    role_id = str(binding.role_id or AUDIT_TECH_ROLE_ID).strip().lower() or AUDIT_TECH_ROLE_ID
    blocked_types: tuple[str, ...] = ()
    if binding.prefer_local_ollama:
        blocked_types = tuple(sorted(_resolve_non_local_provider_types(workspace_value, settings)))

    def _caller(role: str, prompt: str) -> tuple[str, dict[str, str]]:
        runtime_role = str(role or role_id).strip().lower() or role_id
        local_note = ""

        if binding.prefer_local_ollama:
            local_result = invoke_role_runtime_provider(
                role=runtime_role,
                workspace=workspace_value,
                prompt=prompt,
                fallback_model=binding.fallback_model,
                timeout=binding.timeout_seconds,
                blocked_provider_types=blocked_types,
            )
            if local_result.ok and local_result.output.strip():
                return local_result.output, _build_provider_info(
                    role_id=runtime_role,
                    strategy="local_ollama",
                    result=local_result,
                )

            local_note = _result_error_summary(local_result)
            if not binding.allow_remote_fallback:
                return "", _build_provider_info(
                    role_id=runtime_role,
                    strategy="local_ollama_only",
                    result=local_result,
                    note=local_note,
                )

        runtime_result = invoke_role_runtime_provider(
            role=runtime_role,
            workspace=workspace_value,
            prompt=prompt,
            fallback_model=binding.fallback_model,
            timeout=binding.timeout_seconds,
            blocked_provider_types=None,
        )
        strategy = "role_runtime_fallback" if binding.prefer_local_ollama else "role_runtime"
        runtime_note = local_note
        if runtime_result.error:
            runtime_note = f"{local_note}; {runtime_result.error}" if local_note else str(runtime_result.error)
        info = _build_provider_info(
            role_id=runtime_role,
            strategy=strategy,
            result=runtime_result,
            note=runtime_note,
        )
        if runtime_result.ok and runtime_result.output.strip():
            return runtime_result.output, info
        return "", info

    return _caller


def bind_audit_llm_to_task_service(
    *,
    task_service: Any,
    settings: Any,
    workspace: str,
) -> bool:
    """Configure TaskService independent audit caller from runtime settings."""

    binding = build_audit_llm_binding_config(settings)
    if not binding.enabled:
        return False

    llm_caller = make_audit_llm_caller(
        workspace=workspace,
        settings=settings,
        config=binding,
    )
    task_service.set_audit_llm_caller(llm_caller)
    return True


__all__ = [
    "AUDIT_COURT_DEPARTMENT",
    "AUDIT_COURT_ROLE_ID",
    "AUDIT_COURT_ROLE_NAME",
    "AUDIT_TECH_ROLE_ID",
    "AuditLLMBindingConfig",
    "bind_audit_llm_to_task_service",
    "build_audit_llm_binding_config",
    "get_audit_role_descriptor",
    "make_audit_llm_caller",
]
