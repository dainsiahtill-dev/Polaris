"""Cell, audit, and event subsystem errors.

Internal submodule of :mod:`polaris.kernelone.errors`.
Public symbols are re-exported from the package ``__init__``.
"""

from __future__ import annotations

from polaris.kernelone.errors._base import KernelOneError


class CellError(KernelOneError):
    """Cell-level errors.

    Base class for all Cell-specific errors.
    Each Cell can define its own error subclass that inherits from this.

    Attributes:
        cell_name: Name of the Cell that raised the error.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "CELL_ERROR",
        cell_name: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        self.cell_name = cell_name
        if cell_name:
            self.details["cell_name"] = cell_name


class AuditError(KernelOneError):
    """Audit-related errors.

    Base class for audit subsystem errors.

    Attributes:
        audit_id: Identifier for the audit context.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "AUDIT_ERROR",
        audit_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if audit_id:
            self.details["audit_id"] = audit_id


class KernelAuditWriteError(AuditError):
    """Kernel audit write failed."""

    def __init__(
        self,
        message: str,
        *,
        event_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="KERNEL_AUDIT_WRITE_ERROR",
            **kwargs,
        )
        if event_id:
            self.details["event_id"] = event_id


class AuditFieldError(AuditError):
    """Audit field type error."""

    def __init__(
        self,
        message: str,
        *,
        field_path: str = "",
        expected_type: str = "",
        actual_type: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="AUDIT_FIELD_ERROR",
            **kwargs,
        )
        if field_path:
            self.details["field_path"] = field_path
        if expected_type:
            self.details["expected_type"] = expected_type
        if actual_type:
            self.details["actual_type"] = actual_type


class EventError(KernelOneError):
    """Event-related errors.

    Base class for event system errors.

    Attributes:
        event_name: Name of the event.
        event_id: Identifier of the event.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = "EVENT_ERROR",
        event_name: str = "",
        event_id: str = "",
        **kwargs,
    ) -> None:
        super().__init__(message, code=code, **kwargs)
        if event_name:
            self.details["event_name"] = event_name
        if event_id:
            self.details["event_id"] = event_id


class EventPublishError(EventError):
    """Event publishing failed."""

    def __init__(
        self,
        message: str,
        *,
        event_name: str = "",
        failed_side: str = "",
        left_error: Exception | None = None,
        right_error: Exception | None = None,
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVENT_PUBLISH_ERROR",
            event_name=event_name,
            **kwargs,
        )
        self.failed_side = failed_side
        self.left_error = left_error
        self.right_error = right_error
        self.details.update(
            {
                "failed_side": failed_side,
                "registry_failed": left_error is not None,
                "message_bus_failed": right_error is not None,
            }
        )


class EventSourcingError(EventError):
    """Event sourcing error."""

    def __init__(
        self,
        message: str,
        *,
        stream: str = "",
        **kwargs,
    ) -> None:
        super().__init__(
            message,
            code="EVENT_SOURCING_ERROR",
            **kwargs,
        )
        if stream:
            self.details["stream"] = stream
