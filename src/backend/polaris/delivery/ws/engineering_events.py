"""Compact committed engineering facts over the existing runtime.v2 transport."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

from polaris.delivery.ws.runtime_subjects import resolve_v2_subject
from polaris.infrastructure.messaging.nats.client import get_default_client, get_default_client_snapshot
from polaris.infrastructure.messaging.nats.nats_types import (
    JetStreamConstants,
    RuntimeEventEnvelope,
    create_runtime_event,
)
from polaris.kernelone.storage import resolve_storage_roots


# 截图字节只服务实时 UI 的浏览器证据视图：旧逻辑把它剥掉、把超过 4096 字节的
# 字符串截断、再把超过 60KB 的载荷整体降级，三者叠加让截图永远到不了前端
# （表现为 <img> 的 alt 文案"浏览器截图 1"）。完整事实始终留在 SQLite。
_IMAGE_KEY = "data_base64"
_DEFAULT_PAYLOAD_BUDGET = 60_000
_IMAGE_PAYLOAD_BUDGET = 1_500_000
_MAX_IMAGE_CHARS = 1_400_000


def compact_engineering_payload(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Keep original full facts in SQLite; send explicit, bounded previews."""

    # 只有真实工具观察携带浏览器证据；其余 kind 仍然不带像素。
    keep_pixels = kind == "tool_observed"

    def without_pixels(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                key: without_pixels(item)
                for key, item in value.items()
                if keep_pixels or key != _IMAGE_KEY
            }
        if isinstance(value, (list, tuple)):
            return [without_pixels(item) for item in value]
        return value

    # Includes nested check observations. The durable input remains untouched.
    selected = without_pixels(payload)
    if kind == "request_prepared":
        selected = {key: payload[key] for key in ("turn_seq", "evidence_kind") if key in payload}
        route = payload.get("binding_selection")
        if isinstance(route, dict):
            order = route.get("qualified_order")
            if (
                type(route.get("activated")) is bool
                and all(
                    isinstance(route.get(k), str) and re.fullmatch("[0-9a-f]{64}", route[k])
                    for k in ("qualification_record_sha256", "conditions_sha256")
                )
                and isinstance(order, (tuple, list))
                and len(order) <= 32
                and all(
                    isinstance(pair, (tuple, list))
                    and len(pair) == 2
                    and all(isinstance(part, str) and 0 < len(part) <= 256 for part in pair)
                    for pair in order
                )
            ):
                selected["binding_selection"] = {
                    "activated": route["activated"],
                    "qualification_record_sha256": route["qualification_record_sha256"],
                    "conditions_sha256": route["conditions_sha256"],
                    "qualified_order": [list(pair) for pair in order],
                }
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
        # Surface a bounded human-readable excerpt so live consoles can
        # narrate what the role is doing; the full message stays in SQLite.
        message = payload.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            text = ""
            if isinstance(content, str):
                text = content
            elif isinstance(content, list):
                parts = [
                    str(block.get("text") or "")
                    for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                ]
                text = "\n".join(part for part in parts if part)
            if text:
                selected["text"] = text
            role_value = message.get("role")
            if isinstance(role_value, str) and role_value:
                selected["author_role"] = role_value
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
            if path.rsplit(".", 1)[-1] == _IMAGE_KEY:
                # 截图不是文本预览，截断它只会得到一张破图。
                return value[:_MAX_IMAGE_CHARS]
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

    def has_image_bytes(value: Any) -> bool:
        if isinstance(value, dict):
            return any(
                (key == _IMAGE_KEY and isinstance(item, str) and bool(item)) or has_image_bytes(item)
                for key, item in value.items()
            )
        if isinstance(value, list):
            return any(has_image_bytes(item) for item in value)
        return False

    if has_checks:
        projected["checks"] = check_previews(4096)
    if truncated:
        projected["truncated_fields"] = truncated[:80]
    budget = _IMAGE_PAYLOAD_BUDGET if has_image_bytes(projected) else _DEFAULT_PAYLOAD_BUDGET
    if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) > budget:
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
                "acceptance_dimensions",
                "acceptance_fidelity_missing",
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
            if len(json.dumps(projected, ensure_ascii=False).encode("utf-8")) <= budget:
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
    return await client.publish_js(stream=JetStreamConstants.STREAM_NAME, subject=subject, payload=envelope.to_dict())
