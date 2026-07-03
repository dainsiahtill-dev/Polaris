"""Fences for retired KernelOne runtime default-constant aliases."""

from __future__ import annotations

from pathlib import Path

import polaris.kernelone.runtime as runtime
import polaris.kernelone.runtime.execution_facade as execution_facade
from polaris.kernelone.constants import EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS

BACKEND_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_INIT = BACKEND_ROOT / "polaris" / "kernelone" / "runtime" / "__init__.py"
_RETIRED_RUNTIME_DEFAULT_ALIASES = {
    "DEFAULT_ASYNC_CONCURRENCY",
    "DEFAULT_BLOCKING_CONCURRENCY",
    "DEFAULT_PROCESS_CONCURRENCY",
    "DEFAULT_PROCESS_TIMEOUT_SECONDS",
}


def test_runtime_package_root_does_not_reexport_default_constant_aliases() -> None:
    """Runtime defaults belong to polaris.kernelone.constants."""
    for name in _RETIRED_RUNTIME_DEFAULT_ALIASES:
        assert not hasattr(runtime, name), name
        assert name not in runtime.__all__


def test_execution_facade_does_not_publish_process_timeout_alias() -> None:
    """ProcessSpec may use the canonical constant without creating a second export."""
    assert not hasattr(execution_facade, "DEFAULT_PROCESS_TIMEOUT_SECONDS")
    assert "DEFAULT_PROCESS_TIMEOUT_SECONDS" not in execution_facade.__all__
    assert execution_facade.ProcessSpec(name="probe", args=["echo"]).timeout_seconds == (
        EXECUTION_DEFAULT_PROCESS_TIMEOUT_SECONDS
    )

def test_runtime_package_root_does_not_label_active_exports_as_compat_aliases() -> None:
    """Runtime package-root comments should not imply current helpers are old aliases."""
    source = RUNTIME_INIT.read_text(encoding="utf-8")
    retired_comment = " ".join(("Backward", "compatibility", "aliases"))
    assert retired_comment not in source
