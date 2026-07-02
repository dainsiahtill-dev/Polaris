"""Foundation validation/normalization helpers for roles.runtime contracts.

Pure helper, normalizer, and guard functions shared by the contract
``__post_init__`` blocks. This is the foundation layer of the
``contracts`` package: it imports nothing from the sibling contract
modules so that those modules may depend on it without creating import
cycles.
"""

from __future__ import annotations

import posixpath
from collections.abc import Mapping
from typing import Any

_FORBIDDEN_ROLE_OBJECT_OWNER_CELLS = frozenset(
    {
        "roles.runtime",
        "roles.adapters",
        "roles.kernel",
        "roles.profile",
        "roles.session",
        "kernelone.roles",
        "polaris.kernelone.roles",
    }
)
_TASK_MARKET_TASK_REF_PREFIX = "runtime.task_market:task:"


def _is_forbidden_role_object_owner_cell(owner_cell: str) -> bool:
    token = str(owner_cell or "").strip()
    return token in _FORBIDDEN_ROLE_OBJECT_OWNER_CELLS or token.startswith("polaris.kernelone.roles.")


def _is_forbidden_role_object_ref_namespace(ref: str) -> bool:
    namespace = str(ref or "").strip().split(":", 1)[0]
    return _is_forbidden_role_object_owner_cell(namespace)


def _is_roles_profile_ref_namespace(ref: str) -> bool:
    namespace = str(ref or "").strip().split(":", 1)[0]
    return namespace == "roles.profile"


def _has_ref_namespace(ref: str, namespace: str) -> bool:
    return str(ref or "").strip().split(":", 1)[0] == namespace


def _require_refs_namespace(name: str, refs: tuple[str, ...], namespace: str) -> None:
    if any(not _has_ref_namespace(ref, namespace) for ref in refs):
        raise ValueError(f"{name} must point to {namespace}")


def _normalize_scope_path(path: str) -> str:
    token = str(path or "").strip().replace("\\", "/")
    normalized = posixpath.normpath(token)
    if normalized == ".":
        return ""
    return normalized.lstrip("/")


def _path_is_within_scope(path: str, scopes: tuple[str, ...]) -> bool:
    if str(path or "").strip().replace("\\", "/").startswith("/"):
        return False
    normalized_path = _normalize_scope_path(path)
    if not normalized_path or normalized_path == ".." or normalized_path.startswith("../"):
        return False
    for scope in scopes:
        normalized_scope = _normalize_scope_path(scope).rstrip("/")
        if not normalized_scope or normalized_scope == ".." or normalized_scope.startswith("../"):
            continue
        if normalized_path == normalized_scope or normalized_path.startswith(f"{normalized_scope}/"):
            return True
    return False


def _has_any_ref_namespace(ref: str, namespaces: tuple[str, ...]) -> bool:
    return any(_has_ref_namespace(ref, namespace) for namespace in namespaces)


def _asset_ref_namespace_matches_owner(
    *,
    owner_cell: str,
    ref: str,
    asset_kind: str,
    metadata: Mapping[str, Any],
) -> bool:
    ref_namespace = str(ref or "").strip().split(":", 1)[0]
    if ref_namespace == owner_cell:
        return True

    graph_source_ref = str(metadata.get("graph_source_ref", "")).strip()
    return (
        owner_cell == "context.catalog"
        and asset_kind == "constraint_topology"
        and ref_namespace == "docs.graph"
        and graph_source_ref.startswith("docs/graph/")
    )


def _capability_endpoint_matches_owner(owner_cell: str, endpoint_ref: str) -> bool:
    if not endpoint_ref:
        return True
    if endpoint_ref.startswith(f"{owner_cell}:"):
        return True
    return endpoint_ref.startswith(f"polaris.cells.{owner_cell}.public.")


def _is_task_market_task_ref(ref: str | None) -> bool:
    token = str(ref or "").strip()
    return token.startswith(_TASK_MARKET_TASK_REF_PREFIX) and bool(token[len(_TASK_MARKET_TASK_REF_PREFIX) :])


def _is_retired_task_market_task_ref(ref: str | None) -> bool:
    return str(ref or "").strip().startswith("runtime.task_market:task-")


def _is_hex_sha256(value: str) -> bool:
    token = str(value or "").strip()
    return len(token) == 64 and all(char in "0123456789abcdef" for char in token.lower())


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _normalize_optional_domain(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    if not token:
        return None
    return token


def _normalize_optional_string(value: str | None) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _normalize_history(history: Any) -> tuple[tuple[str, str], ...]:
    if history is None:
        return ()
    if isinstance(history, str | bytes):
        raise ValueError("history must be an iterable of (role, content) entries")

    try:
        iterator = iter(history)
    except TypeError as exc:
        raise ValueError("history must be an iterable of (role, content) entries") from exc

    normalized: list[tuple[str, str]] = []
    for index, item in enumerate(iterator):
        role = ""
        content = ""
        if isinstance(item, Mapping):
            role = str(item.get("role") or "").strip()
            content = str(item.get("content") or item.get("message") or "").strip()
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            role = str(item[0] or "").strip()
            content = str(item[1] or "").strip()

        if not role or not content:
            raise ValueError(f"history entries must provide non-empty role and content (index={index})")
        normalized.append((role, content))

    return tuple(normalized)


def _normalize_string_tuple(name: str, values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes):
        raise ValueError(f"{name} must be an iterable of strings, not a string")

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of strings") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(iterator):
        token = str(item or "").strip()
        if not token:
            raise ValueError(f"{name} entries must be non-empty strings (index={index})")
        if token not in seen:
            normalized.append(token)
            seen.add(token)
    return tuple(normalized)


def _normalize_unique_string_tuple(name: str, values: Any) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str | bytes):
        raise ValueError(f"{name} must be an iterable of strings, not a string")

    try:
        iterator = iter(values)
    except TypeError as exc:
        raise ValueError(f"{name} must be an iterable of strings") from exc

    normalized: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(iterator):
        token = str(item or "").strip()
        if not token:
            raise ValueError(f"{name} entries must be non-empty strings (index={index})")
        if token in seen:
            raise ValueError(f"{name} must not contain duplicate refs")
        normalized.append(token)
        seen.add(token)
    return tuple(normalized)


def _require_ref_superset(message: str, container_refs: tuple[str, ...], required_refs: tuple[str, ...]) -> None:
    if not required_refs:
        return
    container = set(container_refs)
    missing_refs = tuple(ref for ref in required_refs if ref not in container)
    if missing_refs:
        raise ValueError(f"{message}: {', '.join(missing_refs)}")
