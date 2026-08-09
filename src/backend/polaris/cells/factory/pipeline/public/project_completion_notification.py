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


@dataclass(frozen=True, slots=True)
class FactoryProjectCompletionNotificationResultV1:
    """Factory-safe projection of one synchronous durable convergence step."""

    status: str
    reason_codes: tuple[str, ...]
    action_id: str | None = None
    diagnostic_id: str | None = None
    next_action: str | None = None

    def __post_init__(self) -> None:
        status = str(self.status or "").strip()
        if not status:
            raise ValueError("status must be non-empty")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "reason_codes", tuple(str(item).strip() for item in self.reason_codes if str(item).strip()))


@runtime_checkable
class FactoryProjectCompletionNotificationPortV1(Protocol):
    async def notify_project_completion(
        self,
        identity: FactoryProjectCompletionIdentityV1,
    ) -> FactoryProjectCompletionNotificationResultV1: ...


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


async def notify_factory_project_completion(
    identity: FactoryProjectCompletionIdentityV1,
) -> FactoryProjectCompletionNotificationResultV1:
    with _notification_port_lock:
        port = _notification_port
    if port is None:
        raise RuntimeError("factory_project_completion_notification_port_unbound")
    result = await port.notify_project_completion(identity)
    if type(result) is not FactoryProjectCompletionNotificationResultV1:
        raise TypeError("completion notification port must return the exact result contract")
    return result


__all__ = [
    "FactoryProjectCompletionIdentityV1",
    "FactoryProjectCompletionNotificationPortV1",
    "FactoryProjectCompletionNotificationResultV1",
    "notify_factory_project_completion",
]
