"""Package late-bind helper for monkeypatch-compatible module attrs."""

from __future__ import annotations

import sys
from types import ModuleType


def _mod() -> ModuleType:
    """Return the package module (``_service_lifecycle``) for late-bound attrs.

    Characterization tests monkeypatch
    ``polaris.cells.runtime.task_market.internal._service_lifecycle.now_epoch``.
    Call sites must resolve via the package object so those patches apply.
    """

    pkg = sys.modules[__package__]  # type: ignore[index]
    return pkg
