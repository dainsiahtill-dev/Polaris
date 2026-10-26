"""Controlled test-only config scenarios; never a product entry point."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.llm.control_plane.internal import llm_config_agent as core
from polaris.kernelone.fs import KernelFileSystem


@contextmanager
def private_llm_config_scenario(workspace: Path, *, fail_config_reads: bool = False) -> Iterator[Path]:
    """Yield only an explicit private directory with real KFS/cache behavior.

    Replaces the legacy first-ancestor workspace inference in this owner only.
    This synthetic binding does not establish default global-path correctness.
    The optional fixed failure reproduces Store.get unavailability for consumer
    error classification; no internal module/object or generic setter escapes.
    """
    patch = pytest.MonkeyPatch()
    home = workspace / "private-home"
    try:
        for key, value in {
            "KERNELONE_HOME": str(home),
            "KERNELONE_RUNTIME_ROOT": "",
            "KERNELONE_RUNTIME_CACHE_ROOT": "",
            "KERNELONE_STATE_TO_RAMDISK": "0",
            "KERNELONE_RAMDISK_ROOT": "",
        }.items():
            patch.setenv(key, value)
        patch.setattr(core, "KernelFileSystem", lambda _inferred, adapter: KernelFileSystem(str(workspace), adapter))
        if fail_config_reads:

            def unavailable(self: Any, role: str) -> Any:
                raise RuntimeError("store failure")

            patch.setattr(core.LLMConfigStore, "get", unavailable)
        yield home / "config" / "llm"
    finally:
        patch.undo()
