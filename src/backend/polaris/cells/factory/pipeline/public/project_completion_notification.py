"""Factory-owned notification port for committed CE completion identity."""

from __future__ import annotations

from dataclasses import dataclass
from threading import Lock
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class FactoryProjectCompletionIdentityV1:
    workspace: str
    project_id: str
    run_id: str
    completion_contract_hash: str

    def __post_init__(self) -> None:
        for name in ("workspace", "project_id", "run_id", "completion_contract_hash"):
            value = str(getattr(self, name) or "").strip()
            if not value:
                raise ValueError(f"{name} must be non-empty")
            object.__setattr__(self, name, value)


@runtime_checkable
class FactoryProjectCompletionNotificationPortV1(Protocol):
    async def notify_project_completion(
        self,
        identity: FactoryProjectCompletionIdentityV1,
    ) -> None: ...


_notification_port: FactoryProjectCompletionNotificationPortV1 | None = None
_notification_port_lock = Lock()


def _bind_factory_project_completion_notification_port(
    port: FactoryProjectCompletionNotificationPortV1,
) -> None:
    if not isinstance(port, FactoryProjectCompletionNotificationPortV1):
        raise TypeError("port must implement FactoryProjectCompletionNotificationPortV1")
    global _notification_port
    with _notification_port_lock:
        if _notification_port is None:
            _notification_port = port
        elif _notification_port is not port:
            raise RuntimeError("factory_project_completion_notification_port_conflicting_rebind")


def _clear_factory_project_completion_notification_port(
    port: FactoryProjectCompletionNotificationPortV1,
) -> None:
    global _notification_port
    with _notification_port_lock:
        if _notification_port is port:
            _notification_port = None


async def notify_factory_project_completion(identity: FactoryProjectCompletionIdentityV1) -> None:
    with _notification_port_lock:
        port = _notification_port
    if port is None:
        raise RuntimeError("factory_project_completion_notification_port_unbound")
    await port.notify_project_completion(identity)


__all__ = [
    "FactoryProjectCompletionIdentityV1",
    "FactoryProjectCompletionNotificationPortV1",
    "notify_factory_project_completion",
]
