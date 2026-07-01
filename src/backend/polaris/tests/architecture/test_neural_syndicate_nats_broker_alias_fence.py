"""Architecture fence for retired Neural Syndicate NATS broker aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.multi_agent.neural_syndicate.nats_broker as nats_broker

BACKEND_ROOT = Path(__file__).resolve().parents[3]
NATS_BROKER_MODULE = BACKEND_ROOT / "polaris" / "kernelone" / "multi_agent" / "neural_syndicate" / "nats_broker.py"


def test_nats_message_broker_alias_is_retired() -> None:
    """NATSBroker is the single public broker type for syndicate NATS traffic."""
    assert hasattr(nats_broker, "NATSBroker")
    assert not hasattr(nats_broker, "NATSMessageBroker")
    assert "NATSMessageBroker" not in nats_broker.__all__


def test_nats_broker_source_does_not_reintroduce_alias() -> None:
    """Source-level fence blocks the old NATSMessageBroker compatibility export."""
    source = NATS_BROKER_MODULE.read_text(encoding="utf-8")
    assert "NATSMessageBroker = NATSBroker" not in source
    assert "NATSMessageBroker" not in source
