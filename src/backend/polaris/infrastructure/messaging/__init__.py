"""Polaris messaging infrastructure."""

from __future__ import annotations

from importlib import import_module
from typing import Any

__all__ = [
    "ConnectionState",
    "ConsumerManager",
    "JetStreamAdmin",
    "JetStreamAdminConfig",
    "JetStreamConstants",
    "JetStreamConsumerManager",
    "NATSClient",
    "NATSConfig",
    "RuntimeEventEnvelope",
    "StreamManager",
    "close_default_client",
    "create_jetstream_admin",
    "create_nats_client",
    "create_runtime_event",
    "get_default_client",
]


def __getattr__(name: str) -> Any:
    if name in {
        "ConnectionState",
        "NATSClient",
        "NATSConfig",
        "close_default_client",
        "create_nats_client",
        "get_default_client",
    }:
        module = import_module("polaris.infrastructure.messaging.nats.client")
        return {
            "ConnectionState": module.ConnectionState,
            "NATSClient": module.NATSClient,
            "NATSConfig": module.NATSConfig,
            "close_default_client": module.close_default_client,
            "create_nats_client": module.create_nats_client,
            "get_default_client": module.get_default_client,
        }[name]
    if name in {"ConsumerManager", "JetStreamAdmin", "JetStreamAdminConfig", "StreamManager", "create_jetstream_admin"}:
        module = import_module("polaris.infrastructure.messaging.nats.jetstream_admin")
        return {
            "ConsumerManager": module.ConsumerManager,
            "JetStreamAdmin": module.JetStreamAdmin,
            "JetStreamAdminConfig": module.JetStreamAdminConfig,
            "StreamManager": module.StreamManager,
            "create_jetstream_admin": module.create_jetstream_admin,
        }[name]
    if name in {"JetStreamConstants", "RuntimeEventEnvelope", "create_runtime_event"}:
        module = import_module("polaris.infrastructure.messaging.nats.nats_types")
        return {
            "JetStreamConstants": module.JetStreamConstants,
            "RuntimeEventEnvelope": module.RuntimeEventEnvelope,
            "create_runtime_event": module.create_runtime_event,
        }[name]
    if name == "JetStreamConsumerManager":
        module = import_module("polaris.infrastructure.messaging.nats.ws_consumer_manager")
        return module.JetStreamConsumerManager
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
