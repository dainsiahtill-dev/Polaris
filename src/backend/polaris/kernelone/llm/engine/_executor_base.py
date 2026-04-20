"""Shared helpers for AIExecutor and StreamExecutor.

Extracted to eliminate ~80% duplication between executor.py and stream_executor.py.
Do NOT import this module from outside the engine package.
"""

from __future__ import annotations

import logging
from typing import Any

from polaris.kernelone.errors import ErrorCategory, classify_error
from polaris.kernelone.llm.runtime import resolve_provider_api_key
from polaris.kernelone.llm.runtime_config import get_role_model

logger = logging.getLogger(__name__)

# Keys merged from request.options into provider_cfg for non-streaming invocations.
_INVOKE_OPTION_KEYS = (
    "temperature",
    "max_tokens",
    "timeout",
    "stream",
    "system_prompt",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
    "response_format",
)

# Keys merged from request.options for streaming invocations.
# Native tool calling is now part of the canonical runtime contract, so tools
# and tool_choice must flow through the structured stream path. response_format
# remains disabled for streaming because current providers do not expose stable
# incremental JSON-schema guarantees.
_STREAM_OPTION_KEYS = (
    "temperature",
    "max_tokens",
    "timeout",
    "system_prompt",
    "tools",
    "tool_choice",
    "parallel_tool_calls",
)


def resolve_provider_model(
    *,
    provider_id: str | None,
    model: str | None,
    role: str | None,
    logger_prefix: str = "[executor]",
) -> tuple[str | None, str | None]:
    """Resolve (provider_id, model) pair from explicit values or role binding.

    Returns the resolved pair.  Either or both values may be None on failure.
    """
    if provider_id and model:
        return provider_id, model

    if role:
        try:
            resolved_pid, resolved_model = get_role_model(role)
            if resolved_pid and resolved_model:
                return resolved_pid, resolved_model
        except (RuntimeError, ValueError) as exc:  # get_role_model may raise on config errors
            logger.debug("%s failed to resolve role model: %s", logger_prefix, exc)

    return provider_id, model


def get_provider_config(
    *,
    workspace: str | None,
    provider_id: str,
    logger_prefix: str = "[executor]",
) -> dict[str, Any]:
    """Load provider configuration and resolve its API key.

    Returns an empty dict if the config cannot be loaded.
    """
    try:
        from polaris.kernelone.llm import config_store as llm_config

        cache_root = llm_config.resolve_workspace_cache_root_for_workspace(workspace or ".")
        config = llm_config.load_llm_config(
            workspace or ".",
            cache_root,
            settings=None,
        )
        raw_providers = config.get("providers")
        providers: dict[str, Any] = raw_providers if isinstance(raw_providers, dict) else {}
        raw_cfg = providers.get(provider_id)
        cfg: dict[str, Any] = raw_cfg if isinstance(raw_cfg, dict) else {}

        provider_type = str(cfg.get("type") or "").strip().lower() if isinstance(cfg, dict) else ""
        cfg = resolve_provider_api_key(provider_id, provider_type, cfg)
        return cfg
    except (RuntimeError, ValueError) as exc:
        logger.debug("%s failed to get provider config: %s", logger_prefix, exc)
        return {}


def build_invoke_config(
    provider_cfg: dict[str, Any],
    options: dict[str, Any],
    *,
    streaming: bool = False,
) -> dict[str, Any]:
    """Merge provider config with request options into a final invoke config.

    When ``streaming=True`` a narrower set of keys is merged (tools/tool_choice
    are not forwarded to stream providers that do not support them).
    """
    cfg: dict[str, Any] = dict(provider_cfg)
    keys = _STREAM_OPTION_KEYS if streaming else _INVOKE_OPTION_KEYS
    for key in keys:
        if key in options:
            cfg[key] = options[key]

    cfg.setdefault("temperature", 0.2)
    cfg.setdefault("stream", False)
    cfg.setdefault("max_tokens", 3000)
    return cfg


def resolve_requested_output_tokens(
    options: dict[str, Any],
    invoke_cfg: dict[str, Any],
    model_spec: Any,  # ModelSpec
) -> int:
    """Determine the requested output token count.

    Looks in options first, then invoke_cfg, then model_spec defaults.
    Returns 0 when no valid value is found.
    """
    value = options.get("max_tokens")
    if value is None:
        value = invoke_cfg.get("max_tokens")
    if value is None:
        requested = 0
    else:
        try:
            requested = int(value)
        except (TypeError, ValueError):
            requested = int(model_spec.max_output_tokens or 0)

    if requested <= 0:
        requested = int(model_spec.max_output_tokens or 0)
    if requested <= 0:
        return 0
    return min(requested, int(model_spec.max_output_tokens or requested))
