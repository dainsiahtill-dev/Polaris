"""Foundation validation/normalization helpers for task_runtime contracts.

Pure helper and evidence-normalization functions shared by contract
``__post_init__`` blocks. Foundation layer with no domain-type dependencies.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from types import MappingProxyType
from typing import Any, Final, cast


def _require_non_empty(name: str, value: str) -> str:
    normalized = str(value).strip()
    if not normalized:
        raise ValueError(f"{name} must be a non-empty string")
    return normalized


def _to_dict_copy(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    return dict(payload or {})


def _to_detached_dict(payload: Mapping[str, Any] | None) -> dict[str, Any]:
    """Copy nested evidence so a verdict cannot retain caller-owned state."""

    return deepcopy(dict(payload or {}))


_SUCCESS_READINESS_EVIDENCE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "parent_registry_source_head_seq",
        "operation_source_head_seq",
    }
)


def _readiness_evidence_key(key: object) -> str:
    """Require stable string keys while preserving typed failure diagnostics."""

    if not isinstance(key, str):
        raise TypeError("readiness evidence keys must be strings")
    return key


def _freeze_readiness_evidence(value: Any, *, active_object_ids: set[int]) -> Any:
    """Detach and recursively freeze one readiness-evidence value."""

    if isinstance(value, (Mapping, list, tuple, set, frozenset)):
        object_id = id(value)
        if object_id in active_object_ids:
            raise ValueError("readiness evidence must not contain cycles")
        active_object_ids.add(object_id)
        try:
            if isinstance(value, Mapping):
                return MappingProxyType(
                    {
                        _readiness_evidence_key(key): _freeze_readiness_evidence(
                            item,
                            active_object_ids=active_object_ids,
                        )
                        for key, item in value.items()
                    }
                )
            if isinstance(value, (list, tuple)):
                return tuple(_freeze_readiness_evidence(item, active_object_ids=active_object_ids) for item in value)
            return frozenset(_freeze_readiness_evidence(item, active_object_ids=active_object_ids) for item in value)
        finally:
            active_object_ids.remove(object_id)
    if isinstance(value, bytearray):
        return bytes(value)
    if value is None or isinstance(value, (str, int, float, bool, bytes)):
        return value
    raise TypeError("readiness evidence values must be immutable data")


def _to_immutable_evidence(payload: Mapping[str, Any] | None) -> Mapping[str, Any]:
    """Return deeply detached, immutable readiness evidence."""

    source = {} if payload is None else payload
    frozen = _freeze_readiness_evidence(source, active_object_ids=set())
    return cast(Mapping[str, Any], frozen)


def _task_row_fact(row: Mapping[str, Any]) -> Mapping[str, Any]:
    """Return detached execution-fact metadata from a projected task row."""

    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    fact = metadata_map.get("task_runtime_execution_fact")
    return fact if isinstance(fact, Mapping) else {}


def _workflow_run_id_from_task_row(row: Mapping[str, Any]) -> str:
    """Return canonical workflow run identity from a projected task row."""

    fact_map = _task_row_fact(row)
    return str(row.get("workflow_run_id") or row.get("run_id") or fact_map.get("run_id") or "").strip()


def _factory_run_id_from_task_row(row: Mapping[str, Any]) -> str:
    """Return canonical Factory portfolio-run identity from a task row."""

    metadata = row.get("metadata")
    metadata_map = metadata if isinstance(metadata, Mapping) else {}
    fact_map = _task_row_fact(row)
    return str(
        row.get("factory_run_id") or metadata_map.get("factory_run_id") or fact_map.get("factory_run_id") or ""
    ).strip()
