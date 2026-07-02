"""Coded exception for the typed ``CapabilityHandler`` dispatch seam.

``CapabilityInvocationError`` is the single exception a capability handler may
raise from :meth:`CapabilityHandler.validate` (and may be raised internally by
:meth:`CapabilityHandler.invoke`) to signal a payload/precondition rejection.
Its ``code`` mirrors — byte-for-byte — the ``error_code`` string literals that
``execute_role_capability_invocation`` already emits on its failure paths
(captured in :mod:`polaris.cells.roles.runtime.internal.capability._oracle`).
Mirroring those literals keeps the on-the-wire ``RoleCapabilityInvocationResultV1``
``error_code`` byte-identical when the dispatcher migrates a branch onto a handler.

The exception lives in this consumer-owned internal package and inherits the
platform root :class:`polaris.kernelone.errors.KernelOneError`; ``errors.py``
under ``kernelone`` is intentionally NOT modified (governance exclusion).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from polaris.kernelone.errors import KernelOneError


class CapabilityInvocationError(KernelOneError):
    """Coded rejection raised by a capability handler's ``validate``/``invoke``.

    The ``code`` attribute MUST be one of the dispatcher's existing
    ``error_code`` literals so that the failure result rendered by
    ``execute_role_capability_invocation`` stays byte-identical to the pre-extraction
    ``if/elif`` arm it replaces. ``owner_cell`` carries the rejecting
    capability's owner cell so the dispatcher can attach it to the structured
    failure result without re-deriving it.

    ``evidence_refs`` and ``metadata`` carry the optional structured payload that
    a handler's failure path attaches to its ``_capability_invocation_failure``
    result. Most validate/invoke rejections leave them at their empty defaults
    (the dispatcher then renders an empty ``evidence_refs`` / ``metadata``), but
    branches that emit a richer failure result (Phase 3 families) populate them
    so the dispatcher's single catch can forward them byte-identically.
    """

    def __init__(
        self,
        message: str,
        *,
        code: str,
        owner_cell: str = "",
        capability_available: bool = False,
        evidence_refs: tuple[str, ...] = (),
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message, code=code)
        self.owner_cell = owner_cell
        self.capability_available = capability_available
        self.evidence_refs = evidence_refs
        self.metadata = metadata
