"""Provider-related routes for the LLM router."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from polaris.cells.llm.provider_config.public.contracts import (
    LlmProviderConfigError,
    ProviderConfigValidationError,
    ProviderNotFoundError,
)
from polaris.cells.llm.provider_config.public.service import resolve_provider_request_context
from polaris.cells.llm.provider_runtime.public.contracts import (
    LlmProviderRuntimeError,
    UnsupportedProviderTypeError,
)
from polaris.cells.llm.provider_runtime.public.service import get_provider_manager, run_provider_action
from polaris.delivery.http.routers._shared import get_state, require_auth
from polaris.kernelone.storage.io_paths import build_cache_root

from .llm_models import ProviderActionPayload

# Resolve provider_manager from the Cell layer (which delegates to kernelone)
_provider_manager = get_provider_manager()

router = APIRouter()
logger = logging.getLogger(__name__)


def _map_provider_config_error(exc: LlmProviderConfigError) -> HTTPException:
    """Map domain config errors to HTTP status codes."""
    if isinstance(exc, ProviderNotFoundError):
        logger.error("Provider not found: %s", exc)
        return HTTPException(status_code=404, detail="internal error")
    if isinstance(exc, ProviderConfigValidationError):
        logger.error("Provider config validation failed: %s", exc)
        return HTTPException(status_code=400, detail="internal error")
    logger.error("Provider config error: %s", exc)
    return HTTPException(status_code=400, detail="internal error")


def _map_provider_runtime_error(exc: LlmProviderRuntimeError) -> HTTPException:
    """Map domain runtime errors to HTTP status codes."""
    if isinstance(exc, UnsupportedProviderTypeError):
        logger.error("Unsupported provider type: %s", exc)
        return HTTPException(status_code=400, detail="internal error")
    logger.error("Provider runtime error: %s", exc)
    return HTTPException(status_code=500, detail="internal error")


@router.post("/llm/providers/{provider_id}/health", dependencies=[Depends(require_auth)])
def provider_health(request: Request, provider_id: str, payload: ProviderActionPayload) -> dict[str, Any]:
    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    try:
        provider_context = resolve_provider_request_context(
            workspace=str(state.settings.workspace),
            cache_root=cache_root,
            provider_id=provider_id,
            api_key=payload.api_key,
            headers=payload.headers,
        )
    except LlmProviderConfigError as exc:
        raise _map_provider_config_error(exc) from exc
    provider_cfg = provider_context.provider_cfg
    provider_type = provider_context.provider_type
    api_key = provider_context.api_key

    try:
        return run_provider_action(
            action="health",
            provider_type=provider_type,
            provider_cfg=provider_cfg,
            api_key=api_key,
        )
    except LlmProviderRuntimeError as exc:
        raise _map_provider_runtime_error(exc) from exc


@router.post("/llm/providers/{provider_id}/models", dependencies=[Depends(require_auth)])
def provider_models(request: Request, provider_id: str, payload: ProviderActionPayload) -> dict[str, Any]:
    state = get_state(request)
    cache_root = build_cache_root(state.settings.ramdisk_root or "", str(state.settings.workspace))
    try:
        provider_context = resolve_provider_request_context(
            workspace=str(state.settings.workspace),
            cache_root=cache_root,
            provider_id=provider_id,
            api_key=payload.api_key,
            headers=payload.headers,
        )
    except LlmProviderConfigError as exc:
        raise _map_provider_config_error(exc) from exc
    provider_cfg = provider_context.provider_cfg
    provider_type = provider_context.provider_type
    api_key = provider_context.api_key

    try:
        return run_provider_action(
            action="models",
            provider_type=provider_type,
            provider_cfg=provider_cfg,
            api_key=api_key,
        )
    except LlmProviderRuntimeError as exc:
        raise _map_provider_runtime_error(exc) from exc


@router.get("/llm/providers", dependencies=[Depends(require_auth)])
def list_providers(request: Request) -> dict[str, Any]:
    """List all available providers with their information"""
    try:
        providers_info = _provider_manager.list_provider_info()
        return {
            "providers": [
                info.__dict__
                if hasattr(info, "__dict__")
                else {
                    "name": info.name,
                    "type": info.type,
                    "description": info.description,
                    "version": info.version,
                    "author": info.author,
                    "documentation_url": info.documentation_url,
                    "supported_features": info.supported_features,
                    "cost_class": info.cost_class,
                    "provider_category": getattr(info, "provider_category", "LLM"),
                    "autonomous_file_access": getattr(info, "autonomous_file_access", False),
                    "requires_file_interfaces": getattr(info, "requires_file_interfaces", True),
                    "model_listing_method": getattr(info, "model_listing_method", "API"),
                }
                for info in providers_info
            ]
        }
    except (RuntimeError, ValueError) as exc:
        logger.error("list_providers failed: %s", exc)
        raise HTTPException(status_code=500, detail="internal error")


@router.get("/llm/providers/{provider_type}/info", dependencies=[Depends(require_auth)])
def get_provider_info(request: Request, provider_type: str) -> dict[str, Any]:
    """Get detailed information about a specific provider"""
    try:
        info = _provider_manager.get_provider_info(provider_type)
        if not info:
            raise HTTPException(status_code=404, detail="Provider not found")

        return {
            "name": info.name,
            "type": info.type,
            "description": info.description,
            "version": info.version,
            "author": info.author,
            "documentation_url": info.documentation_url,
            "supported_features": info.supported_features,
            "cost_class": info.cost_class,
        }
    except HTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        logger.error("get_provider_info failed: %s", exc)
        raise HTTPException(status_code=500, detail="internal error")


@router.get("/llm/providers/{provider_type}/config", dependencies=[Depends(require_auth)])
def get_provider_default_config(request: Request, provider_type: str) -> dict[str, Any]:
    """Get default configuration for a provider"""
    try:
        config = _provider_manager.get_provider_default_config(provider_type)
        if config is None:
            raise HTTPException(status_code=404, detail="Provider not found")
        return config
    except HTTPException:
        raise
    except (RuntimeError, ValueError) as exc:
        logger.error("get_provider_default_config failed: %s", exc)
        raise HTTPException(status_code=500, detail="internal error")


@router.post("/llm/providers/{provider_type}/validate", dependencies=[Depends(require_auth)])
def validate_provider_config(request: Request, provider_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Validate provider configuration"""
    try:
        provider_class = _provider_manager.get_provider_class(provider_type)
        if provider_class is None:
            return {
                "valid": False,
                "errors": [f"Provider type '{provider_type}' not found"],
                "warnings": [],
                "normalized_config": None,
            }
        result = provider_class.validate_config(payload)
        return {
            "valid": result.valid,
            "errors": result.errors,
            "warnings": result.warnings,
            "normalized_config": result.normalized_config,
        }
    except (RuntimeError, ValueError) as exc:
        return {"valid": False, "errors": [str(exc)], "warnings": [], "normalized_config": None}


@router.post("/llm/providers/health-all", dependencies=[Depends(require_auth)])
def health_check_all(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
    """Perform health checks on all configured providers"""
    try:
        configs = payload.get("providers", {})
        results = _provider_manager.health_check_all(configs)
        return results
    except (RuntimeError, ValueError) as exc:
        logger.error("health_check_all failed: %s", exc)
        raise HTTPException(status_code=500, detail="internal error")
