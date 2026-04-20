"""Helper functions for runtime WebSocket endpoint (Facade).

This module imports and re-exports helper functions from specialized modules:
- channel_utils: Channel classification and path resolution
- signature_utils: Signature computation and tracking
- protocol_utils: v2 Protocol helpers
- json_utils: JSON parsing utilities

This allows backward compatibility for imports from helpers.py.
"""

from __future__ import annotations

# Re-export from specialized modules
from polaris.delivery.ws.endpoints.channel_utils import (
    channel_max_chars,
    is_llm_channel,
    is_process_channel,
    normalize_roles,
    resolve_channel_path,
    resolve_current_run_id,
    wants_role,
)
from polaris.delivery.ws.endpoints.json_utils import (
    parse_json_line,
    resolve_journal_event_channel,
    sanitize_snapshot_lines,
)
from polaris.delivery.ws.endpoints.models import (
    JOURNAL_CHANNELS,
    LEGACY_LLM_CHANNELS,
    V2_CHANNEL_TO_SUBJECT,
)
from polaris.delivery.ws.endpoints.protocol_utils import (
    build_v2_subscription_subjects,
    resolve_runtime_v2_workspace_key,
    resolve_v2_subject,
)
from polaris.delivery.ws.endpoints.signature_utils import (
    filter_status_payload_by_roles,
    remember_stream_signature,
    status_signature,
    stream_seen,
    stream_signature,
)

__all__ = [
    # models
    "JOURNAL_CHANNELS",
    "LEGACY_LLM_CHANNELS",
    "V2_CHANNEL_TO_SUBJECT",
    # protocol_utils
    "build_v2_subscription_subjects",
    # channel_utils
    "channel_max_chars",
    # signature_utils
    "filter_status_payload_by_roles",
    "is_llm_channel",
    "is_process_channel",
    "normalize_roles",
    # json_utils
    "parse_json_line",
    "remember_stream_signature",
    "resolve_channel_path",
    "resolve_current_run_id",
    "resolve_journal_event_channel",
    "resolve_runtime_v2_workspace_key",
    "resolve_v2_subject",
    "sanitize_snapshot_lines",
    "status_signature",
    "stream_seen",
    "stream_signature",
    "wants_role",
]
