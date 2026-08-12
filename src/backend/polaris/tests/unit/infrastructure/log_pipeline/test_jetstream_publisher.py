from __future__ import annotations

import pytest
from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublisher
from polaris.infrastructure.messaging.nats.client import NATSPayloadTooLargeError


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


@pytest.mark.asyncio
async def test_oversized_payload_is_not_retried_or_reconnected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KERNELONE_NATS_ENABLED", "1")
    publisher = JetStreamPublisher(max_attempts=6, retry_base_sec=0.05)
    publish_calls = 0
    reset_calls = 0

    class FakeClient:
        async def publish(self, subject: str, payload: dict[str, object]) -> bool:
            nonlocal publish_calls
            del subject, payload
            publish_calls += 1
            raise NATSPayloadTooLargeError("payload exceeds max_payload")

    async def fake_get_client() -> FakeClient:
        return FakeClient()

    async def fake_reset_client() -> None:
        nonlocal reset_calls
        reset_calls += 1

    monkeypatch.setattr(publisher, "_get_client", fake_get_client)
    monkeypatch.setattr(publisher, "_reset_client", fake_reset_client)

    from polaris.infrastructure.log_pipeline.jetstream_publisher import JetStreamPublishRequest

    await publisher._publish_with_retry(JetStreamPublishRequest(subject="hp.runtime.test", payload={"x": "y"}))

    assert publish_calls == 1
    assert reset_calls == 0
