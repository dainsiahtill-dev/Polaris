"""Compact committed engineering facts over the existing runtime.v2 transport."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from polaris.delivery.ws.runtime_subjects import resolve_v2_subject
from polaris.infrastructure.messaging.nats.client import get_default_client, get_default_client_snapshot
from polaris.infrastructure.messaging.nats.nats_types import (
    JetStreamConstants,
    RuntimeEventEnvelope,
    create_runtime_event,
)
from polaris.kernelone.storage import resolve_storage_roots


def compact_engineering_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep original full facts in SQLite; send explicit, bounded previews."""

    def without_pixels(value: Any) -> Any:
        if isinstance(value, dict):
            return {key: without_pixels(item) for key, item in value.items() if key != "data_base64"}
        if isinstance(value, (list, tuple)):
            return [without_pixels(item) for item in value]
        return value

    # Includes nested check observations. The durable input remains untouched.
    selected = without_pixels(payload)
    if kind == "request_prepared":
        selected = {key: payload[key] for key in ("turn_seq", "evidence_kind") if key in payload}
        selected["payload_omitted"] = True
    elif kind == "provider_response":
        selected = {
            key: payload[key]
            for key in ("turn_seq", "ok", "error", "model", "provider_id", "trace_id")
            if key in payload
        }
        selected["payload_omitted"] = True
    elif kind == "message":
        selected = {"payload_omitted": True}
        if "native_turn_rejection" in payload:
            selected["native_turn_rejection"] = payload["native_turn_rejection"]
    elif kind == "tool_started":
        arguments = payload.get("arguments", {})
        if isinstance(arguments, dict):
            selected["arguments"] = {
                key: value for key, value in arguments.items() if key in {"file", "command", "pattern", "role", "entry"}
            }
            if len(selected["arguments"]) != len(arguments):
                selected["arguments_omitted"] = True
    truncated: list[str] = []

    def bounded(value: Any, path: str, depth: int = 0) -> Any:
        if isinstance(value, str) and len(value.encode("utf-8")) > 4096:
            truncated.append(path)
            return value.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        if isinstance(value, dict):
            if depth >= 6:
                truncated.append(path)
                return None
            if len(value) > 80:
                truncated.append(path)
            return {str(key): bounded(item, f"{path}.{key}", depth + 1) for key, item in list(value.items())[:80]}
        if isinstance(value, (tuple, list)):
            if len(value) > 50:
                truncated.append(path)
            return [bounded(item, f"{path}[{index}]", depth + 1) for index, item in enumerate(value[:50])]
        return value

    has_checks = "checks" in selected
    has_required_checks = "required_checks" in selected
    has_verification_commands = "verification_commands" in selected
    checks = selected.pop("checks", None)
    required_checks = selected.pop("required_checks", None)
    verification_commands = selected.pop("verification_commands", None)
    projected: dict[str, Any] = bounded(selected, "payload")
    if has_required_checks:
        # Commands and observation identities are evidence, not text previews.
        projected["required_checks"] = required_checks
    if has_verification_commands:
        projected["verification_commands"] = verification_commands

    def check_previews(maximum: int, *, essential_only: bool = False) -> Any:
        if not isinstance(checks, (tuple, list)):
            return checks
        previews: list[Any] = []
        for index, check in enumerate(checks):
            if not isinstance(check, dict):
                previews.append(check)
                continue
            preview = (
                {
                    key: check[key]
                    for key in ("call_id", "tool", "ok", "content", "exit_code", "interrupted")
                    if key in check
                }
                if essential_only
                else dict(check)
            )
            content = preview.get("content")
            if isinstance(content, str) and len(content.encode("utf-8")) > maximum:
                preview["content"] = content.encode("utf-8")[:maximum].decode("utf-8", errors="ignore")
                preview["content_truncated"] = True
                truncated.append(f"payload.checks[{index}].content")
            previews.append(preview)
        return previews

    if has_checks:
        projected["checks"] = check_previews(4096)
    if truncated:
        projected["truncated_fields"] = truncated[:80]
    if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > 60_000:
        projected = {
            key: projected[key]
            for key in (
                "status",
                "role",
                "workflow",
                "text",
                "verification",
                "require_qa",
                "qa_verdict",
                "project_outcome",
                "acceptance_basis",
                "workspace_revision",
                "roles",
                "active_roles",
                "turn_seq",
                "call_id",
                "tool",
                "ok",
                "exit_code",
                "interrupted",
                "content",
            )
            if key in projected
        }
        if has_required_checks:
            projected["required_checks"] = required_checks
        if has_verification_commands:
            projected["verification_commands"] = verification_commands
        projected["payload_omitted"] = True
        for maximum in (1024, 256, 64):
            if has_checks:
                projected["checks"] = check_previews(maximum, essential_only=True)
            if truncated:
                projected["truncated_fields"] = truncated[:80]
            if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= 60_000:
                return projected
        # No partial verdict/check list: the existing same-run HTTP snapshot
        # owns the complete required shape when it cannot fit this transport.
        return {
            "snapshot_required": True,
            "payload_omitted": True,
            "checks_count": len(checks) if isinstance(checks, (tuple, list)) else None,
            "required_checks_count": len(required_checks) if isinstance(required_checks, (tuple, list)) else None,
            "verification_commands_count": len(verification_commands)
            if isinstance(verification_commands, (tuple, list))
            else None,
            "reason": "required_projection_exceeds_transport_budget",
        }
    return projected


def engineering_event_envelope(event: dict[str, Any]) -> tuple[str, RuntimeEventEnvelope]:
    """SQL session sequence stays in payload; NATS owns envelope cursor."""
    workspace = str(event["workspace"])
    # The existing WS helper uses this same K1 resolver. Store events already
    # carry a validated absolute workspace; no legacy status facade is needed.
    workspace_key = str(resolve_storage_roots(workspace).workspace_key)
    session_id = str(event["session_id"])
    body = {
        "session_id": session_id,
        "seq": int(event["seq"]),
        "workspace": workspace,
        "role": str(event["role"]),
        "kind": str(event["kind"]),
        "payload": compact_engineering_payload(str(event["kind"]), event["payload"]),
    }
    envelope = create_runtime_event(
        workspace_key,
        session_id,
        "event.engineering",
        str(event["kind"]),
        body,
        meta={"engine_version": "engineering_v2"},
    )
    envelope.event_id = f"engineering:{workspace_key}:{session_id}:{event['seq']}"
    envelope.ts = datetime.fromtimestamp(float(event["created_at"]), timezone.utc).isoformat()
    return resolve_v2_subject(workspace_key, "event.engineering"), envelope


async def publish_engineering_event(event: dict[str, Any]) -> bool:
    """Use only an already managed connection; offline callers do not start one."""
    if not get_default_client_snapshot()["is_connected"]:
        raise RuntimeError("engineering realtime publication unavailable: managed NATS client is disconnected")
    subject, envelope = engineering_event_envelope(event)
    client = await get_default_client()
    return cast(
        "bool",
        (await client.publish_js(stream=JetStreamConstants.STREAM_NAME, subject=subject, payload=envelope.to_dict())),
    )
