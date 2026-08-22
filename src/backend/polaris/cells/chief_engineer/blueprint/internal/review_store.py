"""Owner-only persistence for Chief Engineer review documents."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from polaris.cells.chief_engineer.blueprint.public.contracts._helpers import _require_safe_filename_token
from polaris.kernelone.fs import KernelFileSystem, get_default_adapter


def persist_chief_engineer_review_document(
    *,
    workspace: str,
    run_id: str,
    payload: Mapping[str, Any],
) -> str:
    """Persist one CE-owned review at its stable compatibility path."""

    safe_run_id = _require_safe_filename_token("run_id", run_id)
    logical_path = f"runtime/state/blueprints/{safe_run_id}.review.json"
    fs = KernelFileSystem(workspace, get_default_adapter())
    fs.write_json_atomic(logical_path, deepcopy(dict(payload)), indent=2, ensure_ascii=False)
    return logical_path


__all__ = ["persist_chief_engineer_review_document"]
