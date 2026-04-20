"""Internal test execution context resolution.

Architecture: This module has NO dependency on ``config.Settings``.
The caller (public/service.py) extracts ``workspace`` and ``cache_root`` strings
from the settings object at the cell boundary and passes them as primitive
parameters here. This keeps the internal module free of Settings coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from polaris.cells.llm import control_plane as llm_control_plane
from polaris.cells.llm.provider_config.public.contracts import (
    ProviderConfigValidationError,
    RoleNotConfiguredError,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

_DEFAULT_PROVIDER_TYPE = "openai_compat"
_DEFAULT_API_PATH = "/v1/chat/completions"
_DEFAULT_TIMEOUT = 30
_DEFAULT_SUITES = ["connectivity", "response"]
_DEFAULT_CONNECTIVITY_SUITES = ["connectivity"]


def load_llm_config_port(workspace: str, cache_root: str) -> dict[str, Any]:
    """Delegate config loading through the control-plane module at call time.

    Keeping this wrapper at module scope preserves a stable monkeypatch seam for
    tests while still honoring patches applied to
    ``polaris.cells.llm.control_plane.load_llm_config_port``.
    """
    return llm_control_plane.load_llm_config_port(workspace, cache_root)


@dataclass(frozen=True)
class LlmTestExecutionContext:
    role: str
    effective_provider_id: str
    model: str
    suites: list[str]
    use_direct_config: bool
    provider_cfg: dict[str, Any] | None


def resolve_llm_test_execution_context(
    workspace: str,
    cache_root: str,
    payload: Mapping[str, Any],
) -> LlmTestExecutionContext:
    role = str(payload.get("role") or "").strip().lower()
    is_connectivity_test = role == "connectivity" or not role

    provider_id = payload.get("provider_id")
    model = payload.get("model")
    use_direct_config = is_connectivity_test and bool(payload.get("base_url"))

    if use_direct_config:
        if not model:
            raise ProviderConfigValidationError(
                "连通性测试需要提供 model",
                details={"field": "model"},
            )
        provider_type = str(payload.get("provider_type") or _DEFAULT_PROVIDER_TYPE)
        provider_cfg: dict[str, Any] | None = {
            "type": provider_type,
            "base_url": payload.get("base_url"),
            "api_path": payload.get("api_path") or _DEFAULT_API_PATH,
            "timeout": payload.get("timeout") or _DEFAULT_TIMEOUT,
        }
        effective_provider_id = str(provider_id or f"direct_{provider_type}")
    else:
        config = load_llm_config_port(workspace, cache_root)

        if is_connectivity_test:
            if not provider_id or not model:
                raise ProviderConfigValidationError(
                    "连通性测试需要提供 provider_id 和 model",
                    details={"missing_fields": [f for f in ("provider_id", "model") if not locals().get(f)]},
                )
        else:
            role_cfg = config.get("roles", {}).get(role)
            if not isinstance(role_cfg, dict):
                raise RoleNotConfiguredError(role)
            if not provider_id:
                provider_id = role_cfg.get("provider_id")
            if not model:
                model = role_cfg.get("model")
            if not provider_id or not model:
                raise ProviderConfigValidationError(
                    "provider_id/model required",
                    details={"role": role},
                )
        provider_cfg = None
        effective_provider_id = str(provider_id)

    suites = list(_DEFAULT_CONNECTIVITY_SUITES) if is_connectivity_test else payload.get("suites") or _DEFAULT_SUITES
    return LlmTestExecutionContext(
        role=role,
        effective_provider_id=effective_provider_id,
        model=str(model),
        suites=list(suites),
        use_direct_config=use_direct_config,
        provider_cfg=provider_cfg,
    )
