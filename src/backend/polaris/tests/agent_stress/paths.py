"""Shared path resolution for tests.agent_stress."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parents[1]


def ensure_backend_root_on_syspath() -> None:
    """Ensure `src/backend` is importable for stress engine modules."""
    backend_root = str(BACKEND_ROOT)
    if backend_root not in sys.path:
        sys.path.insert(0, backend_root)
    _ensure_polaris_storage_layout()


def _ensure_polaris_storage_layout() -> None:
    """Register Polaris storage roots for stress tooling.

    The stress harness is Polaris-specific. Its runtime/workspace policy
    must therefore resolve `.polaris` roots, not the generic `.kernelone`
    defaults used by bare KernelOne.
    """
    from polaris.cells.storage.layout.public.service import resolve_polaris_roots
    from polaris.kernelone._runtime_config import set_workspace_metadata_dir_name
    from polaris.kernelone.storage import register_business_roots_resolver

    if os.name == "nt":
        os.environ.setdefault("KERNELONE_STATE_TO_RAMDISK", "1")
    set_workspace_metadata_dir_name(".polaris")
    register_business_roots_resolver(resolve_polaris_roots)


__all__ = ["BACKEND_ROOT", "REPO_ROOT", "ensure_backend_root_on_syspath"]
