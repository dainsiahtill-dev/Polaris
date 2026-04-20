"""Polaris AI Platform - Model Catalog.

Model capability resolution intentionally depends only on KernelOne contracts
and environment/bootstrap-injected values, not application Settings imports.
"""

from __future__ import annotations

from typing import Any

from polaris.kernelone._runtime_config import resolve_env_str
from polaris.kernelone.llm import config_store as llm_config
from polaris.kernelone.storage.io_paths import build_cache_root

from .contracts import ModelSpec

# SSOT: All model configuration MUST come from llm_config.json
# Hardcoded defaults are REMOVED - if config is missing, raise error.


def _to_int(value: Any) -> int | None:
    """Convert value to positive int, return None if invalid."""
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def _normalize_model_key(model: str) -> str:
    return str(model or "").strip().lower()


def _model_key_candidates(model_key: str) -> list[str]:
    normalized = _normalize_model_key(model_key)
    if not normalized:
        return []

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(value: str) -> None:
        token = str(value or "").strip().lower()
        if not token or token in seen:
            return
        seen.add(token)
        candidates.append(token)

    _add(normalized)
    _add(normalized.split("?", 1)[0])
    _add(normalized.split(":", 1)[0])

    tail = normalized.rsplit("/", 1)[-1]
    _add(tail)
    _add(tail.split(":", 1)[0])

    for segment in normalized.split("/"):
        _add(segment)
        _add(segment.split(":", 1)[0])

    return candidates


def _iter_longest_prefix_matches(mapping: dict[str, Any]) -> list[tuple[str, Any]]:
    """Return normalized string keys sorted by longest-prefix-first."""
    candidates: list[tuple[str, Any]] = []
    for key, value in mapping.items():
        if not isinstance(key, str):
            continue
        normalized = key.strip().lower()
        if not normalized:
            continue
        candidates.append((normalized, value))
    candidates.sort(key=lambda item: len(item[0]), reverse=True)
    return candidates


class ModelCatalog:
    """Resolve model context window and capabilities from LLM config."""

    def __init__(self, workspace: str, *, ramdisk_root: str | None = None) -> None:
        self.workspace = str(workspace or ".").strip() or "."
        if ramdisk_root is None:
            self.ramdisk_root = resolve_env_str("ramdisk_root")
        else:
            self.ramdisk_root = str(ramdisk_root or "").strip()

    def resolve(
        self,
        provider_id: str,
        model: str,
        provider_cfg: dict[str, Any] | None = None,
    ) -> ModelSpec:
        config_payload = None if provider_cfg is not None else self._load_llm_config_payload()
        cfg = provider_cfg or self._load_provider_cfg(provider_id, config_payload)
        provider_type = str((cfg or {}).get("type") or "").strip().lower()

        model_key = _normalize_model_key(model)
        model_specific = self._extract_model_specific(cfg, model_key)
        model_limit_cfg = (
            self._load_global_model_limits(provider_id, model_key, config_payload) if provider_cfg is None else {}
        )

        max_context_tokens = self._resolve_context_window(cfg, model_specific, model_limit_cfg, model_key)
        max_output_tokens = self._resolve_output_limit(
            cfg, model_specific, model_limit_cfg, model_key, max_context_tokens
        )

        # Resolve capabilities: explicit config > provider config > conservative defaults (False)
        # SSOT: Capabilities must be configured in llm_config.json, no hardcoded fallbacks
        return ModelSpec(
            provider_id=str(provider_id or "").strip(),
            provider_type=provider_type,
            model=str(model or "").strip(),
            max_context_tokens=max_context_tokens,
            max_output_tokens=max_output_tokens,
            tokenizer=str(model_specific.get("tokenizer") or cfg.get("tokenizer") or "char_estimate"),
            supports_tools=bool(
                model_specific.get("supports_tools")
                if "supports_tools" in model_specific
                else cfg.get("supports_tools", False)
            ),
            supports_json_schema=bool(
                model_specific.get("supports_json_schema")
                if "supports_json_schema" in model_specific
                else cfg.get("supports_json_schema", False)
            ),
            supports_vision=bool(
                model_specific.get("supports_vision")
                if "supports_vision" in model_specific
                else cfg.get("supports_vision", False)
            ),
            cost_hint=str(model_specific.get("cost_hint") or cfg.get("cost_hint") or "") or None,
        )

    def _cache_root(self) -> str:
        return build_cache_root(self.ramdisk_root, self.workspace)

    def _load_llm_config_payload(self) -> dict[str, Any]:
        payload = llm_config.load_llm_config(self.workspace, self._cache_root(), settings=None)
        return payload if isinstance(payload, dict) else {}

    def _load_provider_cfg(
        self,
        provider_id: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload_val = payload if isinstance(payload, dict) else self._load_llm_config_payload()
        providers_raw = payload_val.get("providers")
        providers: dict[str, Any] = providers_raw if isinstance(providers_raw, dict) else {}
        provider_cfg = providers.get(provider_id)
        return provider_cfg if isinstance(provider_cfg, dict) else {}

    def _load_global_model_limits(
        self,
        provider_id: str,
        model_key: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = payload if isinstance(payload, dict) else self._load_llm_config_payload()
        model_limits = payload.get("model_limits")
        if not isinstance(model_limits, dict):
            return {}

        provider_limits = model_limits.get(provider_id)
        if isinstance(provider_limits, dict):
            direct = provider_limits.get(model_key) or provider_limits.get(model_key.lower())
            if isinstance(direct, dict):
                return direct

        direct_global = model_limits.get(model_key) or model_limits.get(model_key.lower())
        if isinstance(direct_global, dict):
            return direct_global

        return {}

    def _extract_model_specific(self, provider_cfg: dict[str, Any] | None, model_key: str) -> dict[str, Any]:
        cfg = provider_cfg or {}
        model_specific = cfg.get("model_specific") if isinstance(cfg.get("model_specific"), dict) else {}
        if not model_specific:
            return {}

        # exact match first
        raw_model_entry = model_specific.get(model_key)
        if isinstance(raw_model_entry, dict):
            return dict(raw_model_entry)

        for normalized, value in _iter_longest_prefix_matches(model_specific):
            if not isinstance(value, dict):
                continue
            if model_key == normalized:
                return dict(value)
            # Prefix match allows variants like "gpt-4o-mini".
            if model_key.startswith(normalized):
                return dict(value)
        return {}

    def _resolve_context_window(
        self,
        provider_cfg: dict[str, Any],
        model_specific: dict[str, Any],
        model_limit_cfg: dict[str, Any],
        model_key: str,
    ) -> int:
        """Resolve context window from config. Raises if not found."""
        candidates = [
            model_specific.get("max_context_tokens"),
            model_specific.get("context_window"),
            model_limit_cfg.get("max_context_tokens"),
            model_limit_cfg.get("context_window"),
            provider_cfg.get("max_context_tokens"),
            provider_cfg.get("context_window"),
        ]
        for candidate in candidates:
            if candidate is not None:
                resolved = _to_int(candidate)
                if resolved is not None:
                    return resolved

        raise ValueError(
            f"Context window not configured for model '{model_key}'. "
            f"Please add max_context_tokens to llm_config.json in one of: "
            f"model_specific, model_limits, or provider config."
        )

    def _resolve_output_limit(
        self,
        provider_cfg: dict[str, Any],
        model_specific: dict[str, Any],
        model_limit_cfg: dict[str, Any],
        model_key: str,
        max_context_tokens: int,
    ) -> int:
        """Resolve output limit from config. Raises if not found."""
        candidates = [
            model_specific.get("max_output_tokens"),
            model_specific.get("output_tokens"),
            model_specific.get("max_tokens"),
            model_limit_cfg.get("max_output_tokens"),
            model_limit_cfg.get("output_tokens"),
            model_limit_cfg.get("max_tokens"),
            provider_cfg.get("max_output_tokens"),
            provider_cfg.get("max_tokens"),
        ]
        for candidate in candidates:
            if candidate is not None:
                resolved = _to_int(candidate)
                if resolved is not None:
                    return min(resolved, max_context_tokens)

        raise ValueError(
            f"Output token limit not configured for model '{model_key}'. "
            f"Please add max_output_tokens to llm_config.json in one of: "
            f"model_specific, model_limits, or provider config."
        )


__all__ = ["ModelCatalog"]
