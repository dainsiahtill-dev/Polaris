"""Bootstrap binding surface for Factory completion notifications."""

from __future__ import annotations

from .project_completion_notification import (
    FactoryProjectCompletionNotificationPortV1,
    _bind_factory_project_completion_notification_port,
    _clear_factory_project_completion_notification_port,
)


def bind_factory_project_completion_notification_port(
    port: FactoryProjectCompletionNotificationPortV1,
) -> None:
    _bind_factory_project_completion_notification_port(port)


def clear_factory_project_completion_notification_port(
    port: FactoryProjectCompletionNotificationPortV1,
) -> None:
    _clear_factory_project_completion_notification_port(port)


__all__ = [
    "bind_factory_project_completion_notification_port",
    "clear_factory_project_completion_notification_port",
]
