"""F32 (2026-06-16): F24 read-loop bound silently disabled by a "." workspace.

``config.workspace`` is sometimes the literal CWD marker ``"."`` (truthy), which
short-circuited the ``or KERNELONE_WORKSPACE`` fallback. ``"."`` is unmeasurable
(``_workspace_materialization_fingerprint`` -> None), so ``_read_bootstrap_makes_no_progress``
returned False forever and the read-only-bootstrap escalation never engaged
(factory-bench L4-23: 6 consecutive read-only bootstraps, same turn_id, no
interspersed writes, F24 never fired). ``_resolve_materialization_workspace``
treats "."/empty as unset so the real workspace is used and measurable.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.transaction.retry_orchestrator import (
    _resolve_materialization_workspace,
    _workspace_materialization_fingerprint,
)


class TestResolveMaterializationWorkspace:
    def test_dot_workspace_falls_through_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/real/project")
        assert _resolve_materialization_workspace(SimpleNamespace(workspace=".")) == "/real/project"

    def test_empty_workspace_falls_through_to_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/real/project")
        assert _resolve_materialization_workspace(SimpleNamespace(workspace="")) == "/real/project"

    def test_real_config_workspace_wins_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/env/project")
        assert _resolve_materialization_workspace(SimpleNamespace(workspace="/cfg/project")) == "/cfg/project"

    def test_dot_workspace_no_env_stays_dot(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("KERNELONE_WORKSPACE", raising=False)
        assert _resolve_materialization_workspace(SimpleNamespace(workspace=".")) == "."

    def test_missing_workspace_attr_uses_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_WORKSPACE", "/real/project")
        assert _resolve_materialization_workspace(SimpleNamespace()) == "/real/project"

    def test_closed_loop_dot_is_unmeasurable_real_dir_is_measurable(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The load-bearing proof: "." -> None (F24 disabled); the resolved real
        # dir -> a concrete fingerprint (F24 can measure -> can fire).
        (tmp_path / "main.py").write_text("print('x')\n", encoding="utf-8")
        assert _workspace_materialization_fingerprint(".") is None
        monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
        resolved = _resolve_materialization_workspace(SimpleNamespace(workspace="."))
        assert resolved == str(tmp_path)
        assert _workspace_materialization_fingerprint(resolved) is not None
