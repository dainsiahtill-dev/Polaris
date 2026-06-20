"""Canonical role-output quality markers (single source of truth).

These constants are the shared vocabulary used by role-output quality scoring
to flag technical-debt signals in generated text. They previously lived as
duplicated literals in two cells:

- ``polaris/cells/roles/kernel/internal/quality_checker.py``
- ``polaris/cells/llm/dialogue/internal/role_dialogue.py``

Both cells already depend on ``polaris.kernelone`` (a neutral base layer that
never imports from cells), so hosting the constant here removes the duplication
without introducing a cross-cell import cycle.

The markers are exposed as an immutable ``tuple`` to make this a safe
process-wide shared constant: call sites only iterate over it, and the tuple
prevents accidental in-place mutation from one consumer leaking into another.
"""

from __future__ import annotations

from typing import Final

# Technical-debt markers (their presence lowers role-output quality scores).
DEBT_MARKERS: Final[tuple[str, ...]] = ("临时方案", "hack", "TODO", "FIXME")

__all__ = ["DEBT_MARKERS"]
