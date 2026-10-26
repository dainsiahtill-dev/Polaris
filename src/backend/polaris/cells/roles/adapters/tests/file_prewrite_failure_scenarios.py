"""Adapter-owned physical-result fault injection; never production proof.

The canonical mutation port still owns fence consumption and recovery. Only
this owner's executor method is replaced, for the original four negative cases.
No private executor or class escapes through the scenario interface.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

from polaris.cells.roles.adapters.internal.director.execution_tools import DirectorToolExecutor
from polaris.kernelone.editing.contracts import EditPrewriteRejectionV1
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


@contextmanager
def inject_file_prewrite_failure(
    root: Path,
    *,
    case: str,
) -> Iterator[dict[str, Any]]:
    """Yield ordinary call observations while injecting one explicit negative input."""
    if case not in {"generic_error", "lookalike_dict", "partial_io", "foreign_typed_arguments"}:
        raise ValueError("unsupported synthetic file failure")
    observation: dict[str, Any] = {"case": case, "calls": 0, "synthetic_fault": True}
    original = DirectorToolExecutor._tool_edit_blocks

    def controlled(self: Any, *args: Any, **kwargs: Any) -> Any:
        observation["calls"] += 1
        if case == "foreign_typed_arguments":
            proof = original(self, *args, **kwargs)
            assert type(proof) is EditPrewriteRejectionV1
            object.__setattr__(proof, "arguments_hash", "f" * 64)
            return proof
        if case == "partial_io":
            KernelFileSystem(str(root), get_default_adapter()).workspace_write_text(
                "src/a.txt",
                "partial physical write\n",
                encoding="utf-8",
            )
        if case == "lookalike_dict":
            return {
                "ok": True,
                "no_op": True,
                "schema_version": "kernelone.editing.prewrite_rejection.v1",
                "write_started": False,
                "reason": "untrusted dict",
            }
        return {"ok": False, "error": "opaque physical failure"}

    with patch.object(DirectorToolExecutor, "_tool_edit_blocks", controlled):
        yield observation
