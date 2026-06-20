"""Errors for the roles.runtime session-artifact persistence path.

Lives in the ``roles.runtime`` internal layer (NOT ``kernelone/errors.py``,
which is frozen) and inherits :class:`KernelOneError` so it participates in the
unified KernelOne error hierarchy.
"""

from __future__ import annotations

from polaris.kernelone.errors import KernelOneError

ARTIFACT_PERSIST_FAILED_CODE = "ARTIFACT_PERSIST_FAILED"


class ArtifactPersistError(KernelOneError):
    """Raised when a session artifact / checkpoint write fails durably.

    Replaces the previous behaviour where a raw :class:`OSError` (or a silently
    swallowed ``ImportError``) could escape the async persistence helpers. The
    failing ``path`` and operation ``mode`` are attached as structured context.
    """

    def __init__(
        self,
        message: str,
        *,
        path: str,
        mode: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(
            message,
            code=ARTIFACT_PERSIST_FAILED_CODE,
            cause=cause,
            details={"path": path, "mode": mode},
            retryable=True,
        )
        self.path = path
        self.mode = mode
