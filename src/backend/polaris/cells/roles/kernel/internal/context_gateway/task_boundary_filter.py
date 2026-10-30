"""Task-boundary filters for role context payloads.

These helpers keep CE/Director task-specific artifacts from leaking across
logical task boundaries before they are rendered into provider messages.

# -*- coding: utf-8 -*-
UTF-8 编码验证: 本文所有文本使用 UTF-8
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.context.contracts import TurnEngineContextRequest as ContextRequest
from polaris.kernelone.tasks.task_tokens import normalize_task_token as _normalize_task_token_sso

TASK_TOKEN_KEYS = (
    "task_id",
    "pm_task_id",
    "source_task_id",
    "external_task_id",
    "original_task_id",
    "id",
)

_BLUEPRINT_KEYS = frozenset({"ce_blueprint", "chief_engineer_blueprint"})


def normalize_task_token(value: Any) -> str:
    # Delegated to the canonical SSoT in polaris.kernelone.tasks.task_tokens
    # (§9.5). Kept as a thin wrapper so existing call sites + monkeypatch
    # targets resolve unchanged.
    return _normalize_task_token_sso(value)


def context_task_sources(context_override: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    sources: list[Mapping[str, Any]] = [context_override]
    for key in ("task", "task_contract", "metadata"):
        value = context_override.get(key)
        if isinstance(value, Mapping):
            sources.append(value)
            nested_task = value.get("task")
            if isinstance(nested_task, Mapping):
                sources.append(nested_task)
            nested_contract = value.get("task_contract")
            if isinstance(nested_contract, Mapping):
                sources.append(nested_contract)
    return sources


def expected_blueprint_task_tokens(request: ContextRequest, context_override: Mapping[str, Any]) -> set[str]:
    logical_tokens: set[str] = set()
    numeric_tokens: set[str] = set()
    for source in context_task_sources(context_override):
        for key in TASK_TOKEN_KEYS:
            token = normalize_task_token(source.get(key))
            if not token:
                continue
            if token.isdigit():
                numeric_tokens.add(token)
            else:
                logical_tokens.add(token)
    request_token = normalize_task_token(getattr(request, "task_id", ""))
    if request_token:
        if request_token.isdigit():
            numeric_tokens.add(request_token)
        else:
            logical_tokens.add(request_token)
    return logical_tokens or numeric_tokens


def blueprint_matches_request_task(candidate: Mapping[str, Any], expected_tokens: set[str]) -> bool:
    if not expected_tokens:
        return True
    candidate_tokens = {token for key in TASK_TOKEN_KEYS if (token := normalize_task_token(candidate.get(key)))}
    if not candidate_tokens:
        return True
    if candidate_tokens & expected_tokens:
        return True
    return any(
        expected.isdigit() and candidate.startswith(f"{expected}-")
        for expected in expected_tokens
        for candidate in candidate_tokens
    )


def filter_context_override_for_current_task(
    request: ContextRequest,
    context_override: Mapping[str, Any],
) -> tuple[dict[str, Any], int]:
    """Drop task-mismatched CE blueprint payloads before prompt rendering.

    Numeric task IDs remain compatible with logical CE IDs such as
    ``TASK-1-foundation``. When a stronger logical token is present in task
    metadata, it becomes the active boundary and prevents stale numeric sibling
    blueprints from being rendered.
    """

    expected_tokens = expected_blueprint_task_tokens(request, context_override)
    filtered_count = 0

    def _filter(value: Any, *, key: str | None = None) -> Any:
        nonlocal filtered_count
        if isinstance(value, Mapping):
            if key in _BLUEPRINT_KEYS and not blueprint_matches_request_task(value, expected_tokens):
                filtered_count += 1
                return None
            result: dict[str, Any] = {}
            for nested_key, nested_value in value.items():
                if not isinstance(nested_key, str):
                    result[nested_key] = nested_value
                    continue
                filtered_value = _filter(nested_value, key=nested_key)
                if filtered_value is None and nested_key in _BLUEPRINT_KEYS:
                    continue
                result[nested_key] = filtered_value
            return result
        if isinstance(value, list):
            return [_filter(item) for item in value]
        if isinstance(value, tuple):
            return tuple(_filter(item) for item in value)
        return value

    filtered = _filter(context_override)
    return (filtered if isinstance(filtered, dict) else dict(context_override), filtered_count)


__all__ = [
    "blueprint_matches_request_task",
    "expected_blueprint_task_tokens",
    "filter_context_override_for_current_task",
]
