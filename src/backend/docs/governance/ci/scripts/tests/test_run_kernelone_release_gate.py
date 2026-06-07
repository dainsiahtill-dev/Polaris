from __future__ import annotations

from pathlib import Path

import pytest
from docs.governance.ci.scripts import run_kernelone_release_gate as gate


def test_role_runtime_public_import_boundary_allows_own_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    public_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "public"
    public_root.mkdir(parents=True)
    (public_root / "service.py").write_text(
        "from polaris.cells.roles.runtime.internal.session_orchestrator import SessionOrchestrator\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_role_runtime_public_import_boundaries()

    assert result.ok is True
    assert result.stage == "roles_runtime_public_import_boundary"


def test_role_runtime_public_import_boundary_blocks_cross_cell_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    public_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "public"
    public_root.mkdir(parents=True)
    (public_root / "service.py").write_text(
        "from polaris.cells.qa.audit_verdict.internal.quality_service import QualityService\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_role_runtime_public_import_boundaries()

    assert result.ok is False
    assert result.stage == "roles_runtime_public_import_boundary"
    assert "qa.audit_verdict.internal" in result.stderr
