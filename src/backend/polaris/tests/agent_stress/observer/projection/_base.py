"""Shared attribute/type declarations for RuntimeProjection mixins."""

from __future__ import annotations

from typing import Any


class RuntimeProjectionBase:
    """Instance attributes initialized on RuntimeProjection.__init__.

    Mixins inherit this base so mypy can resolve ``self.<attr>`` across
    domain modules without re-declaring state on every mixin.
    """

    _active_taskboard_task: dict[str, Any] | None
    _local_offsets: dict[str, int]
    _local_output_signatures: dict[str, str]
    _max_dialogue_chars: int
    _max_llm_content_chars: int
    _max_panel_items: int
    _running: bool
    _runtime_v2_client_id: str
    _runtime_v2_cursor: int
    _runtime_v2_enabled: bool
    _runtime_v2_jetstream: bool
    _runtime_v2_last_acked_cursor: int
    _runtime_v2_tail: int
    _task: Any | None
    _taskboard_has_non_empty_snapshot: bool
    backend_url: str
    connected: bool
    connection_error: str
    focus: str
    panels: dict[str, list[dict[str, Any]]]
    runtime_root: Any
    token: str
    transport: str
    transport_used: str
    workspace: str
    ws: Any | None
    ws_url: str
