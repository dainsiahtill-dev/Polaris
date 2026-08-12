"""Payload copy/projection helpers for DirectorAdapter."""

from __future__ import annotations

from typing import Any

_EXECUTION_AUTHORITY_ENVELOPE_KEYS: tuple[str, ...] = (
    "director_execution_envelope",
    "task_execution_envelope",
)


def _copy_mapping_payload(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump()
        if isinstance(dumped, dict):
            return dict(dumped)
    return None


def _is_lower_sha256(value: Any) -> bool:
    token = str(value or "").strip()
    return len(token) == 64 and all(character in "0123456789abcdef" for character in token)


def _project_director_execution_authority_evidence(
    source: dict[str, Any],
    destination: dict[str, Any] | None,
) -> bool:
    """Project only a self-consistent physical execution envelope across context copies."""

    if not isinstance(destination, dict):
        return False
    envelope: dict[str, Any] | None = None
    for key in _EXECUTION_AUTHORITY_ENVELOPE_KEYS:
        candidate = source.get(key)
        if isinstance(candidate, dict):
            envelope = dict(candidate)
            break
    if envelope is None:
        return False
    envelope_hash = str(source.get("execution_envelope_hash") or envelope.get("envelope_hash") or "").strip()
    if not _is_lower_sha256(envelope_hash) or str(envelope.get("envelope_hash") or "").strip() != envelope_hash:
        return False
    authorization = envelope.get("authorization")
    if not isinstance(authorization, dict):
        return False
    capability_token_ref = str(authorization.get("capability_token_ref") or "").strip()
    token_ids: set[str] = set()
    source_containers = [source]
    source_metadata = source.get("metadata")
    if isinstance(source_metadata, dict):
        source_containers.append(source_metadata)
    for container in source_containers:
        for key in ("job_token", "control_plane_job_token", "capability_token"):
            token = container.get(key)
            if isinstance(token, dict) and str(token.get("token_id") or "").strip():
                token_ids.add(str(token["token_id"]).strip())
    if len(token_ids) != 1 or capability_token_ref not in token_ids:
        return False

    destination["director_execution_envelope"] = dict(envelope)
    destination["task_execution_envelope"] = dict(envelope)
    destination["execution_envelope_hash"] = envelope_hash
    metadata = destination.get("metadata")
    projected_metadata = dict(metadata) if isinstance(metadata, dict) else {}
    projected_metadata["director_execution_envelope"] = dict(envelope)
    projected_metadata["task_execution_envelope"] = dict(envelope)
    projected_metadata["execution_envelope_hash"] = envelope_hash
    destination["metadata"] = projected_metadata
    return True


def _copy_dict_list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _first_mapping_payload(*values: Any) -> dict[str, Any] | None:
    for value in values:
        copied = _copy_mapping_payload(value)
        if copied:
            return copied
    return None


def _first_dict_list_payload(*values: Any) -> list[dict[str, Any]]:
    for value in values:
        copied = _copy_dict_list_payload(value)
        if copied:
            return copied
    return []


def _string_list_payload(value: Any, *, limit: int = 8) -> list[str]:
    if isinstance(value, str):
        values = [part.strip() for part in value.split(",") if part.strip()] or (
            [value.strip()] if value.strip() else []
        )
    elif isinstance(value, (list, tuple)):
        values = [str(item or "").strip() for item in value if str(item or "").strip()]
    else:
        values = []
    return values[: max(int(limit), 0)]
