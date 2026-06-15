"""Shared helpers for AIExecutor and StreamExecutor.

Extracted to eliminate ~80% duplication between executor.py and stream_executor.py.
Do NOT import this module from outside the engine package.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Mapping
from typing import Any

from polaris.kernelone.errors import ErrorCategory, classify_error
from polaris.kernelone.llm.runtime import normalize_provider_type, resolve_provider_api_key
from polaris.kernelone.llm.runtime_config import get_role_model, get_role_provider_override

logger = logging.getLogger(__name__)
_SENSITIVE_OBSERVABILITY_KEYS = frozenset(
    {
        "compressed_input",
        "prompt",
        "input",
        "messages",
        "chat_messages",
        "content",
        "output",
        "reasoning",
        "thinking",
        "thinking_content",
        "notes",
        "system_prompt",
    }
)
_SECRET_OR_PATH_RE = re.compile(r"(?i)(sk-[A-Za-z0-9_-]{4,}|/home/[^\s\"']+)")

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

    Resolution order:
    1. A context-scoped role override (e.g. a Director worker pinned to a specific
       backend in a multi-backend pool) — this MUST win over any ``provider_id``
       baked into a cached role profile, otherwise every pooled worker collapses
       onto the default provider and the extra backends sit idle.
    2. An explicit ``provider_id`` + ``model`` pair passed by the caller.
    3. The role's configured binding (``get_role_model``).

    Returns the resolved pair.  Either or both values may be None on failure.
    """
    if role:
        try:
            override_pid = get_role_provider_override(role)
        except (RuntimeError, ValueError):
            override_pid = None
        if override_pid:
            try:
                ov_pid, ov_model = get_role_model(role)
                if ov_pid:
                    return ov_pid, ov_model or model
            except (RuntimeError, ValueError) as exc:  # get_role_model may raise on config errors
                logger.debug("%s failed to resolve overridden role model: %s", logger_prefix, exc)

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


def normalize_provider_type_constraints(value: Any) -> tuple[str, ...]:
    """Normalize provider type constraints from request options."""
    if value is None:
        return ()
    if isinstance(value, str):
        raw_items: list[Any] = list(value.split(","))
    elif isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    else:
        return ()

    normalized: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        token = normalize_provider_type(str(item or "").strip().lower())
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return tuple(normalized)


def provider_type_policy_error(provider_type: str, options: dict[str, Any]) -> str:
    """Return an error string when a resolved provider violates request policy."""
    resolved_type = normalize_provider_type(str(provider_type or "").strip().lower())
    if not resolved_type:
        return "provider_type_missing"

    policy = options.get("provider_type_policy")
    policy_payload = policy if isinstance(policy, dict) else {}
    allowed_types = normalize_provider_type_constraints(
        options.get("allowed_provider_types")
        or options.get("allow_provider_types")
        or policy_payload.get("allowed_provider_types")
        or policy_payload.get("allow_provider_types")
    )
    blocked_types = normalize_provider_type_constraints(
        options.get("blocked_provider_types") or policy_payload.get("blocked_provider_types")
    )

    if allowed_types and resolved_type not in allowed_types:
        return f"provider_type_not_allowed:{resolved_type}"
    if resolved_type in blocked_types:
        return f"provider_type_blocked:{resolved_type}"
    return ""


def estimate_payload_overhead_tokens(invoke_cfg: dict[str, Any], chat_messages: Any) -> int:
    """Estimate request overhead the prompt text does NOT contain (W1.5c-1).

    The server's chat template renders ``tools`` JSON into the prompt
    (qwen-class: a <tools> block with every schema) and wraps each message in
    ChatML markers — none of which client-side prompt estimation ever counted.
    Live failure (factory-bench 2026-06-12): a director call with 17 tool
    schemas (~16k JSON chars ≈ 4-5.3k true tokens) sailed past the budget
    gate and died server-side at prompt+output > max_model_len. Dense JSON is
    estimated as code (≈3 chars/token) to stay on the safe (high) side.
    """
    overhead = 32  # generation-prompt suffix + misc template constants
    tools = invoke_cfg.get("tools")
    if isinstance(tools, list) and tools:
        try:
            tools_json = json.dumps(tools, ensure_ascii=False)
        except (TypeError, ValueError):
            tools_json = str(tools)
        from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

        overhead += TokenEstimator.estimate(tools_json, content_type="code") + 96
    if isinstance(chat_messages, list):
        overhead += 4 * len(chat_messages)
    return overhead


def build_final_request_observability_fields(
    *,
    context: Any,
    trace_id: str,
    provider_id: str,
    model: str,
    final_max_tokens: int,
    token_budget: dict[str, Any],
    input_sha256: str,
    effective_prompt_sha256: str,
) -> dict[str, Any]:
    """Build prompt-free correlation fields for ContextOS final request receipts."""
    payload = context if isinstance(context, Mapping) else {}
    turn_envelope = payload.get("turn_envelope")
    turn_payload = turn_envelope if isinstance(turn_envelope, Mapping) else {}
    context_os_audit = payload.get("context_os_audit")
    audit_payload = context_os_audit if isinstance(context_os_audit, Mapping) else {}
    capability_ref = _capability_profile_ref(payload)
    turn_id = _non_empty_str(payload.get("turn_id") or turn_payload.get("turn_id"))
    context_projection_id = _non_empty_str(
        payload.get("context_projection_id") or turn_payload.get("projection_id") or audit_payload.get("prompt_digest")
    )
    fields = {
        "turn_id": turn_id,
        "attempt": _coerce_positive_int(payload.get("attempt") or payload.get("attempt_no")),
        "fix_attempt_id": _non_empty_str(payload.get("fix_attempt_id") or payload.get("repair_attempt_id")),
        "context_projection_id": context_projection_id,
        "capability_profile_sha256": capability_ref["sha256"],
        "capability_profile_source": capability_ref["source"],
    }
    fields["budget_admission_id"] = _stable_sha256_json(
        {
            "trace_id": trace_id,
            "turn_id": fields["turn_id"],
            "context_projection_id": fields["context_projection_id"],
            "capability_profile_sha256": fields["capability_profile_sha256"],
            "provider_id": provider_id,
            "model": model,
            "final_max_tokens": final_max_tokens,
            "token_budget": token_budget,
            "input_sha256": input_sha256,
            "effective_prompt_sha256": effective_prompt_sha256,
        }
    )
    return fields


def build_final_request_trace_refs(
    *,
    trace_id: str,
    observability_fields: Mapping[str, Any],
) -> tuple[str, ...]:
    refs = [
        trace_id,
        observability_fields.get("turn_id"),
        observability_fields.get("context_projection_id"),
        observability_fields.get("capability_profile_sha256"),
        observability_fields.get("budget_admission_id"),
    ]
    return tuple(str(item).strip() for item in refs if str(item or "").strip())


def build_safe_token_budget_payload(budget_decision: Any) -> dict[str, Any]:
    """Serialize token budget metadata without prompt-visible compression text."""
    if isinstance(budget_decision, Mapping):
        payload: dict[str, Any] = {str(key): value for key, value in budget_decision.items() if str(key)}
    elif hasattr(budget_decision, "to_dict"):
        raw_payload = budget_decision.to_dict()
        payload = dict(raw_payload) if isinstance(raw_payload, Mapping) else {}
    else:
        payload = {}

    compression_payload = payload.get("compression")
    compression_obj = getattr(budget_decision, "compression", None)
    if isinstance(compression_payload, Mapping) or compression_obj is not None:
        payload["compression"] = _safe_compression_payload(compression_payload, compression_obj)
    return payload


def coerce_required_flag(value: Any) -> bool:
    """Parse required-mode booleans without treating ``'false'`` as true."""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        token = value.strip().lower()
        if token in {"1", "true", "yes", "y", "on", "required"}:
            return True
        if token in {"0", "false", "no", "n", "off", "optional", "disabled", ""}:
            return False
    return False


def safe_observability_payload(value: Any, *, max_depth: int = 4) -> Any:
    """Return provider metadata without prompt-like text, secrets, or host paths."""
    return _safe_observability_value(value, key="", depth=max_depth)


def _safe_observability_value(value: Any, *, key: str, depth: int) -> Any:
    key_token = str(key or "").strip().lower()
    if key_token in _SENSITIVE_OBSERVABILITY_KEYS:
        return _redacted_text_summary(value)
    if depth < 0:
        return _redacted_text_summary(value)
    if isinstance(value, Mapping):
        safe: dict[str, Any] = {}
        redacted_field_count = 0
        for raw_key, raw_value in value.items():
            child_key = str(raw_key or "").strip()
            if not child_key:
                continue
            child_token = child_key.lower()
            if child_token in _SENSITIVE_OBSERVABILITY_KEYS:
                redacted_field_count += 1
                continue
            safe[child_key] = _safe_observability_value(raw_value, key=child_key, depth=depth - 1)
        if redacted_field_count:
            safe["redacted_field_count"] = redacted_field_count
        return safe
    if isinstance(value, (list, tuple)):
        return [_safe_observability_value(item, key=key_token, depth=depth - 1) for item in value[:50]]
    if isinstance(value, str):
        return "[redacted]" if _SECRET_OR_PATH_RE.search(value) else value[:512]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _redacted_text_summary(value)


def _redacted_text_summary(value: Any) -> dict[str, Any]:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True) if isinstance(value, Mapping) else str(value or "")
    except (TypeError, ValueError):
        text = str(value or "")
    return {
        "redacted": True,
        "sha256": _stable_sha256_text(text),
        "chars": len(text),
    }


def _safe_compression_payload(compression_payload: Any, compression_obj: Any) -> dict[str, Any]:
    raw = compression_payload if isinstance(compression_payload, Mapping) else {}
    compressed_input = raw.get("compressed_input")
    if compressed_input is None and compression_obj is not None:
        compressed_input = getattr(compression_obj, "compressed_input", "")
    compressed_text = str(compressed_input or "")

    safe: dict[str, Any] = {}
    for key in (
        "original_tokens",
        "compressed_tokens",
        "strategy",
        "quality_flag",
        "drop_ratio",
    ):
        value = raw.get(key)
        if value is None and compression_obj is not None:
            value = getattr(compression_obj, key, None)
        if value is not None:
            safe[key] = value

    notes = raw.get("notes")
    if notes is None and compression_obj is not None:
        notes = getattr(compression_obj, "notes", None)
    if isinstance(notes, list | tuple):
        safe["notes_count"] = len(notes)

    safe["compressed_text_sha256"] = _stable_sha256_text(compressed_text)
    safe["compressed_text_chars"] = len(compressed_text)
    return safe


def _capability_profile_ref(context: Mapping[str, Any]) -> dict[str, str]:
    raw_ref = context.get("capability_profile_ref")
    if isinstance(raw_ref, Mapping):
        sha256 = _non_empty_str(raw_ref.get("sha256"))
        source = _non_empty_str(raw_ref.get("source"))
        if sha256:
            return {"sha256": sha256, "source": source}

    raw_profile = context.get("capability_profile")
    if isinstance(raw_profile, Mapping):
        return {
            "sha256": _stable_sha256_json(raw_profile),
            "source": _non_empty_str(context.get("capability_profile_source"))
            or "roles.kernel.llm_caller.capability_profile",
        }

    return {"sha256": "", "source": ""}


def _stable_sha256_json(value: Any) -> str:
    try:
        serialized = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
    except (TypeError, ValueError):
        serialized = str(value or "")
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _stable_sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _non_empty_str(value: Any) -> str:
    text = str(value or "").strip()
    safe_text = safe_observability_payload(text)
    return str(safe_text or "").strip()


def _coerce_positive_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def clamp_output_tokens_to_window(
    invoke_cfg: dict[str, Any],
    model_spec: Any,
    prompt_text: str,
    *,
    overhead_tokens: int = 0,
    logger_prefix: str = "[executor]",
) -> None:
    """Joint prompt+output window clamp (belt for token-estimation error).

    Live failure (factory-bench 2026-06-12): an under-estimated CJK prompt
    slipped past the budget gate and the server rejected the request outright
    (vLLM: prompt 8193 + max_tokens 8192 > max_model_len 16384), killing the
    whole planning run. The budget gate compresses the PROMPT; this clamp
    guarantees the OUTPUT request never overdrafts whatever window remains.
    """
    try:
        requested = int(invoke_cfg.get("max_tokens") or 0)
    except (TypeError, ValueError):
        return
    window = int(getattr(model_spec, "max_context_tokens", 0) or 0)
    if requested <= 0 or window <= 0:
        return
    from polaris.kernelone.llm.engine.token_estimator import TokenEstimator

    # The EFFECTIVE payload is the structured chat_messages array when present
    # (W1.5) — measuring only prompt_text under-counts the request and the
    # clamp never fires (live: planning sent ~8k-token messages while
    # prompt_text estimated far smaller).
    chat_messages = invoke_cfg.get("chat_messages")
    if isinstance(chat_messages, list) and chat_messages:
        effective_text = "\n".join(str(m.get("content") or "") for m in chat_messages if isinstance(m, dict))
    else:
        effective_text = prompt_text or ""
    prompt_tokens = TokenEstimator.estimate(effective_text)
    headroom = window - prompt_tokens - max(0, int(overhead_tokens)) - 64
    if headroom >= requested:
        return
    clamped = max(256, headroom)
    logging.getLogger(__name__).warning(
        "%s output budget clamped to window: prompt~%s + requested %s > window %s -> max_tokens=%s",
        logger_prefix,
        prompt_tokens,
        requested,
        window,
        clamped,
    )
    invoke_cfg["max_tokens"] = clamped


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
