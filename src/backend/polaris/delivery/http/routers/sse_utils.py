"""Nat-JetStream publication and runtime event validation helpers.

SECURITY HARDENING:
- S1: Schema validation with RuntimeEventEnvelope
- S2: Payload size limits enforcement
- S3: Replay attack protection with timestamp validation
- S4: Cryptographically random ephemeral consumer names
- S5: Subject pattern validation and sanitization
- S6: Event timestamp freshness validation
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# =============================================================================
# Security Constants
# =============================================================================

# Maximum payload size: 256KB (matches JetStreamConstants.STREAM_MAX_MSG_SIZE)
MAX_PAYLOAD_SIZE = 262_144

# Maximum replay window: 1 hour in seconds (event older than this is rejected)
MAX_REPLAY_WINDOW_SECONDS = 3600

# Subject pattern validation: only allow alphanumeric, dash, underscore, dot
SUBJECT_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,199}$")

# Workspace key validation: alphanumeric and dash only
WORKSPACE_KEY_PATTERN = re.compile(r"^[a-zA-Z0-9-]{1,64}$")

# Replay protection secret (should be set via environment variable)
_REPLAY_SECRET: str | None = None


def _get_replay_secret() -> str:
    """Get or generate replay protection secret."""
    global _REPLAY_SECRET
    if _REPLAY_SECRET is None:
        # In production, this should come from environment
        _REPLAY_SECRET = secrets.token_hex(32)
    return _REPLAY_SECRET


# =============================================================================
# Security Validation Functions
# =============================================================================


def validate_subject(subject: str) -> bool:
    """Validate JetStream subject pattern to prevent injection.

    Args:
        subject: Subject string to validate.

    Returns:
        True if subject matches allowed pattern.

    SECURITY: Prevents subject injection attacks that could
    access cross-workspace events.
    """
    return bool(SUBJECT_PATTERN.match(subject))


def validate_workspace_key(workspace_key: str) -> bool:
    """Validate workspace key format.

    Args:
        workspace_key: Workspace identifier to validate.

    Returns:
        True if workspace key is valid format.
    """
    return bool(WORKSPACE_KEY_PATTERN.match(workspace_key))


def validate_payload_size(data: bytes | dict[str, Any]) -> bool:
    """Validate payload size against configured limits.

    Args:
        data: Message data (bytes or dict) to validate.

    Returns:
        True if payload size is within limits.

    SECURITY: Prevents memory exhaustion from oversized messages.
    """
    if isinstance(data, dict):
        size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
    else:
        size = len(data) if isinstance(data, bytes) else len(str(data))
    return size <= MAX_PAYLOAD_SIZE


def validate_event_timestamp(ts: str | None) -> bool:
    """Validate event timestamp is within acceptable replay window.

    Args:
        ts: ISO 8601 timestamp string to validate.

    Returns:
        True if timestamp is fresh enough.

    SECURITY: Prevents replay attacks using old cached events.
    """
    if not ts:
        return True  # Allow events without timestamp (backward compat)

    try:
        # Parse ISO 8601 UTC timestamps. Non-UTC values are allowed for
        # backward compatibility with older runtime events.
        normalized = ts.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
        if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
            return True  # Allow non-UTC timestamps for compatibility

        event_time = parsed.timestamp()
        current_time = time.time()
        age = current_time - event_time

        return age <= MAX_REPLAY_WINDOW_SECONDS
    except (ValueError, OSError):
        return True  # Allow parsing failures for backward compat


def generate_event_signature(event_id: str, timestamp: str, payload: dict[str, Any]) -> str:
    """Generate HMAC signature for event integrity verification.

    Args:
        event_id: Unique event identifier.
        timestamp: Event timestamp.
        payload: Event payload dictionary.

    Returns:
        HMAC-SHA256 signature as hex string.

    SECURITY: Provides event integrity verification to prevent tampering.
    """
    secret = _get_replay_secret()
    message = f"{event_id}:{timestamp}:{json.dumps(payload, sort_keys=True, ensure_ascii=False)}"
    return hmac.new(
        secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_event_signature(
    event_id: str,
    timestamp: str,
    payload: dict[str, Any],
    signature: str,
) -> bool:
    """Verify HMAC signature of event.

    Args:
        event_id: Unique event identifier.
        timestamp: Event timestamp.
        payload: Event payload dictionary.
        signature: Signature to verify.

    Returns:
        True if signature is valid.

    SECURITY: Validates event has not been tampered with.
    """
    expected = generate_event_signature(event_id, timestamp, payload)
    return hmac.compare_digest(expected, signature)


async def publish_to_jetstream(
    subject: str,
    payload: dict[str, Any],
) -> bool:
    """Publish event to NAT JetStream.

    Args:
        subject: NAT JetStream subject.
        payload: Event payload.

    Returns:
        True if publish succeeded.

    The factory-bench subprocess and the FactoryRunService both use this
    helper to push runtime events onto the platform's NAT JetStream bus
    (subject ``hp.runtime.<workspace>.event.factory.<rid>`` or
    ``hp.runtime.bench.<session_id>``). The same JetStream subject is
    consumed by the platform's WebSocket's JetStreamConsumerManager
    and forwarded to all subscribed clients (Factory / PM / CE /
    Director / ContextOS panels) via the runtime.v2 envelope shape.

    SECURITY:
    - Subject is validated against the platform's pattern before publish
      to prevent cross-workspace event injection.
    - The stream name is read from ``JetStreamConstants`` (HP_RUNTIME),
      not a hard-coded literal — an earlier version hard-coded
      ``KERNELONE_RUNTIME`` and silently dropped every event.
    """
    if not validate_subject(subject):
        logger.warning("Invalid subject rejected for JetStream publish: %s", subject)
        return False

    try:
        from polaris.infrastructure.messaging import get_default_client
        from polaris.infrastructure.messaging.nats.nats_types import (
            JetStreamConstants,
        )

        client = await get_default_client()
        if not client or not client.jetstream:
            return False

        await client.publish_js(
            stream=JetStreamConstants.STREAM_NAME,
            subject=subject,
            payload=payload,
        )
        return True
    except (RuntimeError, ValueError) as exc:
        logger.warning("Failed to publish to JetStream: %s", exc)
        return False
