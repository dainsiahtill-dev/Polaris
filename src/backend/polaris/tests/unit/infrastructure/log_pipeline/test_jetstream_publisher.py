from __future__ import annotations

import pytest
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher


def test_jetstream_publisher_skips_queue_when_nats_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "0")

    publisher = JetStreamPublisher()
    accepted = publisher.publish(
        subject="hp.runtime.workspace.system",
        payload={"message": "local disk write already succeeded"},
    )

    assert accepted is True
    assert publisher._thread is None
