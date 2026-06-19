"""UTF-8 runtime JSON persistence for agentic-eval audit artifacts.

Writes audit / report payloads under the workspace runtime root using
the kernel filesystem so writes remain auditable. All text is written
explicitly as UTF-8.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.infrastructure.storage import LocalFileSystemAdapter
from polaris.kernelone.fs.runtime import KernelFileSystem

__all__ = [
    "_persist_audit_package",
    "_persist_runtime_json",
]


def _persist_audit_package(
    *,
    workspace: str,
    output_path: str,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    return _persist_runtime_json(workspace=workspace, output_path=output_path, payload=payload)


def _persist_runtime_json(
    *,
    workspace: str,
    output_path: str,
    payload: Mapping[str, Any],
) -> dict[str, str]:
    fs = KernelFileSystem(str(Path(workspace).resolve()), LocalFileSystemAdapter())
    # Convert runtime-relative path to absolute path for workspace_write_text
    # output_path is like "runtime/llm_evaluations/<run_id>/AGENTIC_EVAL_AUDIT.json"
    # Use direct workspace-relative resolution to avoid cross-drive .polaris runtime path
    # rejection on Windows (workspace on C: vs .polaris on X:).
    absolute_output_path = str(Path(workspace).resolve() / Path(output_path))
    receipt = fs.workspace_write_text(
        absolute_output_path,
        json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "relative_path": str(receipt.logical_path),
        "absolute_path": str(receipt.absolute_path),
    }
