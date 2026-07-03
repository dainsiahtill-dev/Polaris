"""Architecture guard for NATS JetStream event-kind ownership."""

from __future__ import annotations

from polaris.infrastructure.messaging.nats.nats_types import JetStreamConstants


def test_jetstream_constants_do_not_republish_event_kind_aliases() -> None:
    """Event kind constants belong to KernelOne event constants, not NATS config."""
    assert not any(name.startswith("EVENT_KIND_") for name in vars(JetStreamConstants))
