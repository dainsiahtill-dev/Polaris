"""Tests for the scout escalation bridge + scout profile resolvability (UTF-8).

No real LLM calls. Escalation covers wiring with a substituted retired runtime;
profile registration uses the real loader with isolated synthetic bindings.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.scout.internal.escalation import escalate_probe
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1


class _FakeResult:
    """Minimal stand-in for ``RoleExecutionResultV1`` (only ``output`` is read)."""

    output = "ESCALATED FINDINGS"
    metadata: dict[str, Any] = {}
    error_message = ""


@pytest.mark.asyncio
async def test_escalate_probe_invokes_role_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    async def fake_exec(self: Any, command: Any) -> _FakeResult:
        captured["role"] = command.role
        captured["session_id"] = command.session_id
        captured["user_message"] = command.user_message
        return _FakeResult()

    from polaris.cells.roles.runtime.public import service as runtime_service

    monkeypatch.setattr(runtime_service.RoleRuntimeService, "execute_role_session", fake_exec)

    out = await escalate_probe(ScoutProbeTargetV1(query="where is payment", allow_escalation=True), workspace=".")

    assert captured["role"] == "scout"
    assert captured["user_message"] == "where is payment"
    assert captured["session_id"].startswith("scout-probe-")
    assert out == "ESCALATED FINDINGS"


def test_scout_profile_resolvable_via_core_roles_yaml() -> None:
    """Scout must be resolvable through the SAME loader path the core roles use.

    ``load_core_roles()`` loads ``config/roles/core_roles.yaml`` via
    ``RoleProfileRegistry.load_from_yaml``. We exercise that loader against the
    canonical config on a fresh registry (order-independent vs. the global
    singleton) and assert scout resolves with a non-empty system-prompt id.
    """
    from pathlib import Path

    from polaris.cells.roles.profile.public.service import RoleProfileRegistry

    config_path = Path(__file__).resolve().parents[3] / "profile" / "config" / "roles" / "core_roles.yaml"
    assert config_path.exists(), f"core_roles.yaml not found at {config_path}"

    reg = RoleProfileRegistry()
    reg.load_from_yaml(config_path)

    assert reg.has_role("scout")
    profile = reg.get_profile("scout")
    assert profile is not None
    # A real, non-empty system-prompt id is required for the kernel to compose a turn.
    assert profile.prompt_policy.core_template_id
    # Scout must stay auxiliary, never promoted into the core-role set.
    assert "scout" not in RoleProfileRegistry.CORE_ROLES


def _run_core_role_loader(tmp_path: Path, *, configured: bool) -> subprocess.CompletedProcess[str]:
    from polaris.cells.roles.profile.public.service import RoleProfileRegistry
    from polaris.infrastructure.storage.local_fs_adapter import LocalFileSystemAdapter
    from polaris.kernelone.fs import KernelFileSystem

    fs = KernelFileSystem(str(tmp_path), LocalFileSystemAdapter())
    bindings = (
        {
            role: {"provider_id": "scout-registration-fixture", "model": "fixture-model"}
            for role in RoleProfileRegistry.CORE_ROLES
        }
        if configured
        else {}
    )
    config = ".polaris/fixture/llm_config.json"
    fs.workspace_write_text(config, json.dumps({"roles": bindings}, ensure_ascii=False), encoding="utf-8")
    for relative in (".polaris/home", ".polaris/cache", ".polaris/tmp"):
        fs.workspace_mkdir(relative, parents=True, exist_ok=True)
    env = dict(os.environ)
    for key in (
        "KERNELONE_RUNTIME_ROOT",
        "KERNELONE_RUNTIME_CACHE_ROOT",
        "KERNELONE_RAMDISK_ROOT",
        "KERNELONE_STATE_ROOT",
    ):
        env.pop(key, None)
    backend = Path(__file__).resolve().parents[6]
    env.update(
        PYTHONPATH=str(backend),
        PYTHONDONTWRITEBYTECODE="1",
        KERNELONE_LLM_CONFIG=str(tmp_path / config),
        KERNELONE_HOME=str(tmp_path / ".polaris/home"),
        XDG_CACHE_HOME=str(tmp_path / ".polaris/cache"),
        TMPDIR=str(tmp_path / ".polaris/tmp"),
    )
    code = """import json
from polaris.cells.roles.profile.public.service import load_core_roles, registry
try:
    load_core_roles()
except ValueError as error:
    print(json.dumps({"error_type": "ValueError", "error": str(error)}))
    raise SystemExit(2)
assert registry.has_role("scout")
profile = registry.get_profile("scout")
assert profile is not None
assert profile.prompt_policy.core_template_id
print(json.dumps({"has_scout": registry.has_role("scout"), "prompt": profile.prompt_policy.core_template_id}))
"""
    return subprocess.run(
        [sys.executable, "-B", "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
        timeout=30,
    )


def test_scout_profile_registered_after_load_core_roles(tmp_path: Path) -> None:
    """The real global loader uses explicit fake bindings in an isolated process.

    This tests profile registration, not provider readiness or model execution;
    the parent registry and host model configuration remain untouched.
    """
    result = _run_core_role_loader(tmp_path, configured=True)

    assert result.returncode == 0, result.stdout + result.stderr
    loaded = json.loads(result.stdout)
    assert loaded["has_scout"] is True
    assert loaded["prompt"]


def test_core_role_loader_still_rejects_missing_model_bindings(tmp_path: Path) -> None:
    result = _run_core_role_loader(tmp_path, configured=False)

    assert result.returncode == 2, result.stdout + result.stderr
    failure = json.loads(result.stdout)
    assert failure["error_type"] == "ValueError"
    assert "model binding" in failure["error"]
