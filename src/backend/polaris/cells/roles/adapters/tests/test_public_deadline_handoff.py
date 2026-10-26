"""Real public adapter construction consumes the RoleKernel deadline port."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.adapters.public import create_role_adapter
from polaris.cells.roles.kernel.public import llm_caller_contracts


def test_public_factory_adapter_uses_float_deadline_port(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    adapter = create_role_adapter("director", str(tmp_path))
    context = {"factory_run_deadline_epoch_seconds": 100.25}

    def public_port(*, timeout_seconds: float, context: Any = None) -> float:
        assert timeout_seconds == 9.5
        assert context is original_context
        return 0.03125

    original_context = context
    monkeypatch.setattr(
        llm_caller_contracts, "clamp_role_execution_timeout_seconds_to_deadline", public_port, raising=False
    )
    assert adapter._execution.clamp_llm_call_timeout_to_factory_deadline(context, 9.5) == 0.03125


@pytest.mark.parametrize(
    "code", ["factory_director_execution_deadline_invalid", "factory_director_execution_deadline_exhausted"]
)
def test_public_factory_adapter_propagates_original_public_port_rejection(
    code: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = create_role_adapter("director", str(tmp_path))
    rejection = RuntimeError(code)

    def public_port(*, timeout_seconds: float, context: Any = None) -> float:
        raise rejection

    monkeypatch.setattr(
        llm_caller_contracts, "clamp_role_execution_timeout_seconds_to_deadline", public_port, raising=False
    )
    with pytest.raises(RuntimeError) as caught:
        adapter._execution.clamp_llm_call_timeout_to_factory_deadline({}, 9.5)
    assert caught.value is rejection


@pytest.mark.parametrize(
    ("context", "expected"),
    [
        (None, 9.5),
        ({"factory_run_deadline_epoch_seconds": 100.25}, 0.25),
        ({"factory_run_deadline_epoch_seconds": 100.05}, 0.04999999999999716),
        ({"factory_run_deadline_epoch_seconds": 102.5, "factory_director_execution_deadline_epoch_seconds": 99.0}, 2.5),
        (
            {"factory_run_deadline_epoch_seconds": 110.0, "metadata": {"factory_run_deadline_epoch_seconds": 100.25}},
            9.5,
        ),
    ],
)
def test_public_factory_adapter_preserves_real_frozen_timeout_values(
    context: Any, expected: float, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    adapter = create_role_adapter("director", str(tmp_path))
    result = adapter._execution.clamp_llm_call_timeout_to_factory_deadline(context, 9.5)
    assert type(result) is float
    assert result.hex() == expected.hex()


def test_public_factory_adapter_resolved_timeout_does_not_expand_subtenth_remainder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(time, "time", lambda: 100.0)
    adapter = create_role_adapter("director", str(tmp_path))
    result = adapter._execution.resolve_llm_call_timeout_seconds({"factory_run_deadline_epoch_seconds": 100.05})
    assert result.hex() == "0x1.9999999999800p-5"


def test_cold_public_factory_deadline_path_rejects_private_kernel_handoff(tmp_path: Path) -> None:
    program = """
import builtins
import sys
import time

original_import = builtins.__import__
def public_boundary(name, globals=None, locals=None, fromlist=(), level=0):
    caller = (globals or {}).get('__name__', '')
    if name == 'polaris.cells.roles.kernel.internal.llm_caller.helpers' and caller.startswith(
        'polaris.cells.roles.adapters.'
    ):
        raise AssertionError('Adapter crossed private Kernel deadline boundary')
    return original_import(name, globals, locals, fromlist, level)
builtins.__import__ = public_boundary
time.time = lambda: 100.0
from polaris.cells.roles.adapters.public import create_role_adapter
adapter = create_role_adapter('director', sys.argv[1])
result = adapter._execution.clamp_llm_call_timeout_to_factory_deadline(
    {'factory_run_deadline_epoch_seconds': 100.05}, 9.5,
)
assert result.hex() == '0x1.9999999999800p-5'
"""
    result = subprocess.run(
        ["rtk", "proxy", sys.executable, "-c", program, str(tmp_path)],
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": os.pathsep.join(sys.path)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
