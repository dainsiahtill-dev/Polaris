"""Internal provider request context resolution.

Architecture: This module has NO dependency on ``config.Settings``.
The caller (public/service.py) extracts ``workspace`` and ``cache_root`` strings
from the settings object at the cell boundary and passes them as primitive
parameters here. This keeps the internal module free of Settings coupling.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from polaris.cells.llm.control_plane import load_llm_config_port
from polaris.cells.llm.provider_config.public.contracts import ProviderNotFoundError


@dataclass(frozen=True)
class ProviderRequestContext:
    provider_type: str
    provider_cfg: dict[str, Any]
    api_key: str | None


def resolve_provider_request_context(
    workspace: str,
    cache_root: str,
    provider_id: str,
    api_key: str | None,
    headers: dict[str, str] | None,
) -> ProviderRequestContext:
    config = load_llm_config_port(workspace, cache_root)
    providers = config.get("providers") or {}
    provider_cfg = providers.get(provider_id)
    if not isinstance(provider_cfg, dict):
        raise ProviderNotFoundError(provider_id)

    merged_cfg = dict(provider_cfg)
    merged_headers: dict[str, str] = dict(provider_cfg.get("headers") or {})
    if headers:
        merged_headers.update(headers)
        merged_cfg["headers"] = merged_headers

    effective_api_key = api_key or provider_cfg.get("api_key")
    if effective_api_key:
        merged_cfg["api_key"] = effective_api_key

    provider_type = str(provider_cfg.get("type") or "").strip().lower()
    return ProviderRequestContext(
        provider_type=provider_type,
        provider_cfg=merged_cfg,
        api_key=str(effective_api_key) if effective_api_key is not None else None,
    )
