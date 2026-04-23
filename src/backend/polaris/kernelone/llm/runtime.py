from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING, Any

from polaris.kernelone.constants import DEFAULT_OPERATION_TIMEOUT_SECONDS
from polaris.kernelone.llm.runtime_config import _ROLE_BINDING_MODE_ENV_KEYS, MASKED_SECRET

from .provider_contract import KernelLLMRuntimeAdapter, RuntimeProviderInvokeResult

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

logger = logging.getLogger(__name__)


class KernelLLM:
    """KernelOne LLM runtime facade."""

    def __init__(
        self,
        adapter: KernelLLMRuntimeAdapter,
        *,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        if adapter is None:
            raise ValueError("adapter is required")
        self._adapter = adapter
        self._environ = environ

    def invoke_role_provider(
        self,
        *,
        role: str,
        workspace: str,
        prompt: str,
        fallback_model: str,
        timeout: int,
        blocked_provider_types: Iterable[str] | None = None,
    ) -> RuntimeProviderInvokeResult:
        return invoke_role_runtime_provider(
            role=role,
            workspace=workspace,
            prompt=prompt,
            fallback_model=fallback_model,
            timeout=timeout,
            adapter=self._adapter,
            blocked_provider_types=blocked_provider_types,
            environ=self._environ,
        )


def normalize_provider_type(provider_type: str) -> str:
    token = str(provider_type or "").strip().lower()
    if token == "cli":
        return "codex_cli"
    return token


def _provider_kind_from_provider_id(provider_id: str) -> str:
    token = str(provider_id or "").strip().lower()
    if "ollama" in token:
        return "ollama"
    if "codex" in token:
        return "codex_cli"
    return ""


def _strict_role_binding_enabled(environ: Mapping[str, str] | None = None) -> bool:
    env = environ or os.environ
    for key in _ROLE_BINDING_MODE_ENV_KEYS:
        raw = str(env.get(key) or "").strip().lower()
        if raw:
            return raw == "strict"
    return False


def resolve_provider_api_key(
    provider_id: str,
    provider_type: str,
    provider_cfg: dict[str, Any],
    *,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    cfg = dict(provider_cfg or {})
    current = str(cfg.get("api_key") or "").strip()
    if current and current != MASKED_SECRET:
        return cfg

    candidates: list[str] = []
    api_key_ref = str(cfg.get("api_key_ref") or "").strip().lower()
    if api_key_ref.startswith("keychain:"):
        key_name = api_key_ref.split(":", 1)[1].strip()
        if key_name:
            key_upper = key_name.upper().replace("-", "_")
            candidates.extend(
                [
                    f"KERNELONE_{key_upper}_API_KEY",
                    f"KERNELONE_{key_upper}_API_KEY",
                    f"{key_upper}_API_KEY",
                ]
            )

    provider_token = str(provider_type or "").strip().lower()
    provider_id_token = str(provider_id or "").strip().lower()
    if provider_token == "minimax" or "minimax" in provider_id_token:
        candidates.extend(["KERNELONE_MINIMAX_API_KEY", "MINIMAX_API_KEY"])
    if provider_token in {"openai_compat", "openai"} or "openai" in provider_id_token:
        candidates.append("OPENAI_API_KEY")
    if provider_token == "anthropic_compat" or "anthropic" in provider_id_token:
        candidates.append("ANTHROPIC_API_KEY")
    if provider_token == "gemini_api" or "gemini" in provider_id_token:
        candidates.extend(["GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_API_KEY"])
    if provider_token == "kimi" or "kimi" in provider_id_token:
        candidates.extend(["KIMI_API_KEY", "MOONSHOT_API_KEY"])

    env = os.environ if environ is None else environ
    seen: set[str] = set()
    for env_key in candidates:
        key = str(env_key or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        value = str(env.get(key) or "").strip()
        if value:
            cfg["api_key"] = value
            break
    return cfg


def invoke_role_runtime_provider(
    *,
    role: str,
    workspace: str,
    prompt: str,
    fallback_model: str,
    timeout: int,
    adapter: KernelLLMRuntimeAdapter,
    blocked_provider_types: Iterable[str] | None = None,
    environ: Mapping[str, str] | None = None,
) -> RuntimeProviderInvokeResult:
    if adapter is None:
        raise ValueError("adapter is required")

    try:
        provider_id, configured_model = adapter.get_role_model(role)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id="",
            provider_type="",
            model="",
            error=f"role_model_resolution_failed:{exc}",
        )

    provider_id = str(provider_id or "").strip()
    model = str(configured_model or "").strip()
    strict_binding = _strict_role_binding_enabled(environ=environ)
    if not provider_id or not model:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type="",
            model=model,
            error="role_model_not_configured",
        )

    try:
        provider_cfg = adapter.load_provider_config(
            workspace=workspace,
            provider_id=provider_id,
        )
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type="",
            model=model,
            error=f"provider_config_resolution_failed:{exc}",
        )

    if strict_binding and not isinstance(provider_cfg, dict):
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type="",
            model=model,
            error="strict_role_model_binding_invalid_provider_config",
        )
    resolved_type = normalize_provider_type(str(provider_cfg.get("type") or "").strip().lower())
    if strict_binding and not resolved_type:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type="",
            model=model,
            error="strict_role_model_binding_missing_provider_type",
        )
    if not resolved_type:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type="",
            model=model,
            error="provider_type_missing",
        )

    blocked = {str(item or "").strip().lower() for item in (blocked_provider_types or []) if str(item or "").strip()}
    if not resolved_type or resolved_type in blocked:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type=resolved_type,
            model=model,
            error="provider_type_blocked_or_missing",
        )

    provider_instance = adapter.get_provider_instance(resolved_type)
    if provider_instance is None:
        return RuntimeProviderInvokeResult(
            attempted=False,
            ok=False,
            output="",
            provider_id=provider_id,
            provider_type=resolved_type,
            model=model,
            error="provider_instance_not_found",
        )

    invoke_cfg: dict[str, Any] = dict(provider_cfg)
    invoke_cfg["type"] = resolved_type
    effective_timeout = int(timeout or 0)
    if effective_timeout <= 0:
        effective_timeout = DEFAULT_OPERATION_TIMEOUT_SECONDS
    invoke_cfg["timeout"] = effective_timeout
    invoke_cfg["streaming"] = False
    invoke_cfg["stream"] = False
    invoke_cfg = resolve_provider_api_key(
        provider_id,
        resolved_type,
        invoke_cfg,
        environ=environ,
    )

    def _invoke_with_model(
        invoke_model: str,
        invoke_cfg: dict[str, Any],
    ) -> tuple[bool, Any, str]:
        """Invoke provider and return (ok, result, error)."""
        try:
            result = provider_instance.invoke(str(prompt or ""), invoke_model, invoke_cfg)
            return (
                bool(getattr(result, "ok", False)),
                result,
                str(getattr(result, "error", "") or ""),
            )
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return False, None, str(exc)

    # Primary invocation
    ok, result, error = _invoke_with_model(model, invoke_cfg)

    # Fallback: retry with fallback_model on failure (no infinite fallback loop)
    if not ok and fallback_model and fallback_model != model:
        logger.debug(
            "invoke_role_runtime_provider fallback: role=%s primary=%s failed, retrying with fallback_model=%s",
            role,
            model,
            fallback_model,
        )
        adapter.record_provider_failure(resolved_type)
        ok, result, error = _invoke_with_model(fallback_model, invoke_cfg)
        if ok:
            model = fallback_model  # use fallback model in result

    if not ok:
        adapter.record_provider_failure(resolved_type)

    return RuntimeProviderInvokeResult(
        attempted=True,
        ok=ok,
        output=str(getattr(result, "output", "") or "") if result else "",
        provider_id=provider_id,
        provider_type=resolved_type,
        model=model,
        latency_ms=int(getattr(result, "latency_ms", 0) or 0) if result else 0,
        error=error,
        usage=getattr(result, "usage", None) if result else None,
    )


__all__ = [
    "KernelLLM",
    "RuntimeProviderInvokeResult",
    "invoke_role_runtime_provider",
    "normalize_provider_type",
    "resolve_provider_api_key",
]
