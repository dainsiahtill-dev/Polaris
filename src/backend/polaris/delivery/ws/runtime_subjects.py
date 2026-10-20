"""Pure runtime.v2 channel mapping shared by producers and WS subscribers."""

from __future__ import annotations

V2_CHANNEL_TO_SUBJECT: dict[str, str] = {
    "log.system": "log.system",
    "log.process": "log.process",
    "log.llm": "log.llm",
    "event.file_edit": "event.file_edit",
    "event.task_trace": "event.task_trace",
    "event.engineering": "event.engineering",
    "status.snapshot": "status.snapshot",
    "status.resident": "status.resident",
    "status.instances": "status.instances",
}


def resolve_v2_subject(workspace_key: str, channel: str) -> str:
    """Resolve one logical channel within its trusted workspace namespace."""
    return f"hp.runtime.{workspace_key}.{V2_CHANNEL_TO_SUBJECT.get(channel, channel)}"
