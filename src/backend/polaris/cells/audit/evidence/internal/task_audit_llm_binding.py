"""Audit LLM runtime binding helpers.

Connects IndependentAuditService to the existing runtime role/provider
invocation path while keeping audit role ownership explicit.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any

from polaris.kernelone.llm.runtime import normalize_provider_type

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


@dataclass(frozen=True)
class _AuditRoleRuntimeInvokeResult:
    """Audit-facing normalized result for RoleRuntime invocations."""

    attempted: bool
    ok: bool
    output: str = ""
    provider_id: str = ""
    provider_type: str = ""
    model: str = ""
    latency_ms: int = 0
    error: str = ""


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


def _resolve_provider_type_for_provider_id(workspace: str, settings: Any, provider_id: str) -> str:
    provider_token = str(provider_id or "").strip()
    if not provider_token:
        return ""
    try:
        from polaris.kernelone.llm.config_store import load_llm_config
        from polaris.kernelone.storage.io_paths import build_cache_root

        cache_root = build_cache_root(str(getattr(settings, "ramdisk_root", "") or ""), workspace)
        payload = load_llm_config(workspace, cache_root, settings=settings)
        providers = payload.get("providers") if isinstance(payload, dict) else {}
        provider_cfg = providers.get(provider_token) if isinstance(providers, dict) else None
        if not isinstance(provider_cfg, dict):
            return ""
        return normalize_provider_type(str(provider_cfg.get("type") or "").strip().lower())
    except (RuntimeError, ValueError) as exc:
        logger.debug("Failed to resolve audit provider type for %s: %s", provider_token, exc)
        return ""


def _metadata_value(*sources: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value is not None:
                token = str(value).strip()
                if token:
                    return token
    return ""


def _metadata_int(*sources: Mapping[str, Any], keys: tuple[str, ...]) -> int:
    token = _metadata_value(*sources, keys=keys)
    if not token:
        return 0
    try:
        return int(float(token))
    except ValueError:
        return 0


def _run_role_runtime_from_sync(
    coro_factory: Callable[[], Awaitable[_AuditRoleRuntimeInvokeResult]],
    *,
    timeout_seconds: int,
) -> _AuditRoleRuntimeInvokeResult:
    async def _run_with_timeout() -> _AuditRoleRuntimeInvokeResult:
        return await asyncio.wait_for(coro_factory(), timeout=max(1.0, float(timeout_seconds)))

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run_with_timeout())

    result_box: dict[str, _AuditRoleRuntimeInvokeResult | BaseException] = {}

    def _runner() -> None:
        try:
            result_box["value"] = asyncio.run(_run_with_timeout())
        except BaseException as exc:  # noqa: BLE001 - re-raised on the caller thread.
            result_box["exception"] = exc

    thread = threading.Thread(target=_runner, name="polaris-audit-role-runtime", daemon=True)
    thread.start()
    thread.join(max(1.0, float(timeout_seconds)) + 5.0)
    if thread.is_alive():
        return _AuditRoleRuntimeInvokeResult(
            attempted=True,
            ok=False,
            error=f"audit_role_runtime_timeout:{timeout_seconds}s",
            latency_ms=int(max(1.0, float(timeout_seconds)) * 1000),
        )
    exception = result_box.get("exception")
    if isinstance(exception, BaseException):
        raise exception
    result = result_box.get("value")
    if isinstance(result, _AuditRoleRuntimeInvokeResult):
        return result
    return _AuditRoleRuntimeInvokeResult(
        attempted=True,
        ok=False,
        error="audit_role_runtime_missing_result",
    )


def _create_role_runtime_service() -> Any:
    from polaris.cells.roles.runtime.public.service import RoleRuntimeService

    return RoleRuntimeService()


def _build_role_runtime_policy_context(
    *,
    binding: AuditLLMBindingConfig,
    strategy: str,
    blocked_provider_types: tuple[str, ...] = (),
) -> tuple[dict[str, Any], dict[str, Any]]:
    provider_policy: dict[str, Any] = {}
    if blocked_provider_types:
        provider_policy["blocked_provider_types"] = blocked_provider_types

    context: dict[str, Any] = {
        "audit_evidence_llm": True,
        "audit_llm_strategy": strategy,
        "timeout_seconds": binding.timeout_seconds,
    }
    metadata: dict[str, Any] = {
        "source": "audit.evidence.task_audit_llm_binding",
        "role_runtime_required": True,
        "cognitive_runtime_required": True,
        "context_os_expected": True,
        "validate_output": False,
        "max_retries": 0,
        "timeout_seconds": binding.timeout_seconds,
        "audit_llm_strategy": strategy,
        "llm_provider_policy": dict(provider_policy),
    }
    if binding.fallback_model:
        context["fallback_model"] = binding.fallback_model
        metadata["fallback_model"] = binding.fallback_model
    if provider_policy:
        context.update(provider_policy)
        context["llm_provider_policy"] = dict(provider_policy)
        metadata.update(provider_policy)
    return context, metadata


def _result_from_role_runtime(
    *,
    workspace: str,
    settings: Any,
    role_result: Any,
    started_at: float,
) -> _AuditRoleRuntimeInvokeResult:
    metadata = dict(getattr(role_result, "metadata", {}) or {})
    usage = dict(getattr(role_result, "usage", {}) or {})
    llm_invocation = metadata.get("llm_invocation")
    llm_metadata = llm_invocation if isinstance(llm_invocation, Mapping) else {}
    provider_id = _metadata_value(llm_metadata, metadata, usage, keys=("provider_id", "provider", "llm_provider_id"))
    provider_type = _metadata_value(llm_metadata, metadata, usage, keys=("provider_type", "llm_provider_type"))
    if not provider_type:
        provider_type = _resolve_provider_type_for_provider_id(workspace, settings, provider_id)
    model = _metadata_value(llm_metadata, metadata, usage, keys=("model", "llm_model"))
    latency_ms = _metadata_int(llm_metadata, metadata, usage, keys=("latency_ms", "elapsed_ms", "duration_ms"))
    if latency_ms <= 0:
        latency_ms = int((time.perf_counter() - started_at) * 1000)
    error = str(getattr(role_result, "error_message", "") or getattr(role_result, "error_code", "") or "").strip()
    return _AuditRoleRuntimeInvokeResult(
        attempted=True,
        ok=bool(getattr(role_result, "ok", False)),
        output=str(getattr(role_result, "output", "") or ""),
        provider_id=provider_id,
        provider_type=provider_type,
        model=model,
        latency_ms=latency_ms,
        error=error,
    )


async def _invoke_audit_role_runtime(
    *,
    role: str,
    workspace: str,
    settings: Any,
    prompt: str,
    binding: AuditLLMBindingConfig,
    strategy: str,
    blocked_provider_types: tuple[str, ...] = (),
) -> _AuditRoleRuntimeInvokeResult:
    from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1

    started_at = time.perf_counter()
    context, metadata = _build_role_runtime_policy_context(
        binding=binding,
        strategy=strategy,
        blocked_provider_types=blocked_provider_types,
    )
    command = ExecuteRoleSessionCommandV1(
        role=role,
        session_id=f"audit-llm-{uuid.uuid4().hex}",
        workspace=workspace,
        user_message=prompt,
        task_id=f"audit-llm-{uuid.uuid4().hex[:12]}",
        domain="audit",
        context=context,
        metadata=metadata,
        stream=False,
        host_kind="audit_evidence_llm",
    )
    try:
        role_result = await _create_role_runtime_service().execute_role_session(command)
    except (RuntimeError, ValueError, OSError) as exc:
        return _AuditRoleRuntimeInvokeResult(
            attempted=True,
            ok=False,
            error=str(exc),
            latency_ms=int((time.perf_counter() - started_at) * 1000),
        )
    return _result_from_role_runtime(
        workspace=workspace,
        settings=settings,
        role_result=role_result,
        started_at=started_at,
    )


def _result_error_summary(result: _AuditRoleRuntimeInvokeResult) -> str:
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
    result: _AuditRoleRuntimeInvokeResult,
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
            local_result = _run_role_runtime_from_sync(
                lambda: _invoke_audit_role_runtime(
                    role=runtime_role,
                    workspace=workspace_value,
                    prompt=prompt,
                    settings=settings,
                    binding=binding,
                    strategy="local_ollama",
                    blocked_provider_types=blocked_types,
                ),
                timeout_seconds=binding.timeout_seconds,
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

        strategy = "role_runtime_fallback" if binding.prefer_local_ollama else "role_runtime"
        runtime_result = _run_role_runtime_from_sync(
            lambda: _invoke_audit_role_runtime(
                role=runtime_role,
                workspace=workspace_value,
                prompt=prompt,
                settings=settings,
                binding=binding,
                strategy=strategy,
            ),
            timeout_seconds=binding.timeout_seconds,
        )
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
