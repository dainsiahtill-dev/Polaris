"""Pure module-level helpers for :mod:`gateway`.

Extracted verbatim (behavior-preserving) from ``gateway.py`` during the G8
god-class decomposition (blueprint REMAINING_04_gateway-py.md, step 1). These
are side-effect-free strategy/override/coercion helpers plus the two duck-typed
signal renderers; ``gateway.py`` re-imports every public name so callers and
``render_*`` test imports stay intact.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from typing import Any

from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest

_CONTROL_PLANE_CONTEXT_KEYS = {
    "allowed_provider_ids",
    "allowed_provider_types",
    "blocked_provider_ids",
    "blocked_provider_types",
    "cognitive_runtime_enabled",
    "cognitive_runtime_mode",
    "cognitive_runtime_required",
    "cognitive_guidance",
    "context_os_expected",
    "context_os_snapshot",
    "cognitive_strategy_override",
    # Runtime execution knobs (control plane) — ADR-0071: must NOT enter the data
    # plane. Live (L2-11 2026-06-15) these leaked into the context_override system
    # message and, alongside an uncapped value, were the dominant BudgetExceededError.
    "disable_internal_tool_rounds",
    "llm_call_timeout_seconds",
    "request_timeout_seconds",
    "timeout_seconds",
    # Signal-rendered planes (2026-06-15): these are injected for the Director's
    # BlueprintStepsSignal card (_get_blueprint_step renders them concisely) and must
    # NOT be ALSO serialized verbatim into the context_override message — that was a
    # 2143-token duplicate of construction_step (worsened by the P1 anchor contract)
    # that blew the budget and crashed the Director turn (BudgetExceededError, Director
    # barely ran). The signal reads them directly from context_override, not the message.
    "construction_step",
    "consumed_interfaces",
    "pre_state_verify",
    "last_failure",
    "delivery_mode",
    "director_quality_repair",
    "domain",
    "factory_run_id",
    "host_kind",
    "llm_provider_policy",
    "metadata",
    "model_allowlist",
    "model_blocklist",
    "provider_allowlist",
    "provider_blocklist",
    "provider_policy",
    "role_runtime_required",
    "run_id",
    "runtime_session_id",
    "session_context_config",
    "session_turn_events",
    "session_id",
    "state_first_context_os",
    "strategy_override",
    "stream_options",
    "task_id",
    "target_task_id",
    "pm_task_id",
    "prompt_profile",
    "prompt_profile_id",
    "prompt_profile_ids",
    "prompt_profiles",
    "prompt_profile_appendix",
    "prompt_profile_audit",
    "task_runtime_guard",
    "task_runtime_session_id",
    "selected_prompt_profile_ids",
    "workspace",
    "workspace_root",
}

# Budget guard (order-4, 2026-06-15): a single oversized context_override value
# (a serialized blueprint/payload/guidance) was rendered verbatim into one system
# message — 5588 tokens live (L2-11), the dominant cause of BudgetExceededError
# crashing the Director turn BEFORE any write. Cap each value so no one value can
# blow the window; the weak model cannot use multi-thousand-token metadata anyway.
_DEFAULT_CONTEXT_OVERRIDE_VALUE_CHAR_CAP = 1500


def _context_override_value_char_cap() -> int:
    raw = os.environ.get("KERNELONE_CONTEXT_OVERRIDE_VALUE_CHAR_CAP", "").strip()
    if raw:
        try:
            value = int(raw)
        except ValueError:
            return _DEFAULT_CONTEXT_OVERRIDE_VALUE_CHAR_CAP
        if value > 0:
            return value
    return _DEFAULT_CONTEXT_OVERRIDE_VALUE_CHAR_CAP


def _copy_strategy_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _copy_strategy_value(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_copy_strategy_value(item) for item in value]
    return value


def _deep_merge_strategy_payload(target: dict[str, Any], source: Mapping[str, Any]) -> None:
    for key, value in source.items():
        key_text = str(key)
        if not key_text:
            continue
        existing = target.get(key_text)
        if isinstance(value, Mapping) and isinstance(existing, dict):
            _deep_merge_strategy_payload(existing, value)
            continue
        target[key_text] = _copy_strategy_value(value)


def _capability_profile_ref_from_request(request: ContextRequest) -> dict[str, Any] | None:
    context_override = getattr(request, "context_override", None)
    if not isinstance(context_override, Mapping):
        return None

    metadata = context_override.get("metadata")
    metadata_payload = metadata if isinstance(metadata, Mapping) else {}
    raw_profile = metadata_payload.get("capability_profile")
    if raw_profile is None:
        raw_profile = context_override.get("capability_profile")
    if not isinstance(raw_profile, Mapping):
        return None

    profile = {str(key): _copy_strategy_value(value) for key, value in raw_profile.items() if str(key)}
    digest = hashlib.sha256(
        json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    source = (
        str(profile.get("source") or "").strip()
        or str(metadata_payload.get("capability_profile_source") or "").strip()
        or "unknown"
    )
    return {
        "sha256": digest,
        "source": source,
        "schema_version": _coerce_int(profile.get("schema_version")),
        "role_id": str(profile.get("role_id") or "").strip(),
        "provider_id": str(profile.get("provider_id") or "").strip(),
        "provider_type": str(profile.get("provider_type") or "").strip(),
        "model": str(profile.get("model") or "").strip(),
        "model_window_tokens": _coerce_int(profile.get("model_window_tokens")),
        "model_output_limit_tokens": _coerce_int(profile.get("model_output_limit_tokens")),
        "supports_native_tools": bool(profile.get("supports_native_tools")),
        "supports_json_schema": bool(profile.get("supports_json_schema")),
        "supports_stream_native_tools": bool(profile.get("supports_stream_native_tools")),
        "tool_count": _coerce_int(profile.get("tool_count")),
        "native_tool_mode": str(profile.get("native_tool_mode") or "").strip(),
        "response_format_mode": str(profile.get("response_format_mode") or "").strip(),
    }


def _coerce_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def render_blueprint_overview(result: Any) -> str | None:
    """把蓝图状态结果渲染成简洁概览字符串（纯函数，便于单测）。

    result 期望具备 ok/summary/recommendations/risks 字段（duck-typed）。
    not ok 或无实质内容 → None（→ 不注入信号）。
    """
    if not getattr(result, "ok", False):
        return None
    parts: list[str] = []
    summary = str(getattr(result, "summary", "") or "").strip()
    if summary:
        parts.append(summary)
    recs = tuple(getattr(result, "recommendations", ()) or ())
    if recs:
        parts.append("推荐:\n" + "\n".join(f"- {r}" for r in recs))
    risks = tuple(getattr(result, "risks", ()) or ())
    if risks:
        parts.append("风险:\n" + "\n".join(f"- {k}" for k in risks))
    text = "\n".join(parts).strip()
    return text or None


def render_verdict_history(result: Any) -> str | None:
    """把 QA 判定结果渲染成简洁概览（纯函数，便于单测）。

    result 期望具备 ok/verdict/score/findings/suggestions（duck-typed）。
    not ok（无持久化判定）→ None（→ 不注入）。
    """
    if not getattr(result, "ok", False):
        return None
    verdict = str(getattr(result, "verdict", "") or "").strip()
    if not verdict:
        return None
    parts: list[str] = [f"最新判定: {verdict} (score={float(getattr(result, 'score', 0.0)):.2f})"]
    findings = tuple(getattr(result, "findings", ()) or ())
    if findings:
        parts.append("问题:\n" + "\n".join(f"- {f}" for f in findings))
    suggestions = tuple(getattr(result, "suggestions", ()) or ())
    if suggestions:
        parts.append("建议:\n" + "\n".join(f"- {s}" for s in suggestions))
    return "\n".join(parts).strip() or None


def _coerce_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None
