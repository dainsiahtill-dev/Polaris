from __future__ import annotations

from pathlib import Path

import pytest
from docs.governance.ci.scripts import run_kernelone_release_gate as gate


def _write_roles_runtime_cell_yaml(backend_root: Path, owned_paths: list[str]) -> None:
    cell_yaml = backend_root / "polaris" / "cells" / "roles" / "runtime" / "cell.yaml"
    cell_yaml.parent.mkdir(parents=True, exist_ok=True)
    cell_yaml.write_text(
        "id: roles.runtime\nowned_paths:\n" + "".join(f"  - {owned_path}\n" for owned_path in owned_paths),
        encoding="utf-8",
    )


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


def test_roles_runtime_cell_import_boundary_allows_own_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    _write_roles_runtime_cell_yaml(
        backend_root,
        ["polaris/cells/roles/runtime/internal/**"],
    )
    internal_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "internal"
    internal_root.mkdir(parents=True)
    (internal_root / "orchestrator.py").write_text(
        "from polaris.cells.roles.runtime.internal.session_orchestrator import SessionOrchestrator\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_roles_runtime_cell_import_boundaries()

    assert result.ok is True
    assert result.stage == "roles_runtime_cell_import_boundary"


def test_roles_runtime_cell_import_boundary_blocks_internal_cross_cell_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    _write_roles_runtime_cell_yaml(
        backend_root,
        ["polaris/cells/roles/runtime/internal/**"],
    )
    internal_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "internal"
    internal_root.mkdir(parents=True)
    (internal_root / "architect_port.py").write_text(
        "from polaris.cells.architect.design.internal.graph_validator import GraphValidator\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_roles_runtime_cell_import_boundaries()

    assert result.ok is False
    assert result.stage == "roles_runtime_cell_import_boundary"
    assert "architect.design.internal" in result.stderr
    assert "polaris/cells/roles/runtime/internal/architect_port.py" in result.stderr


def test_roles_runtime_cell_import_boundary_blocks_owned_router_cross_cell_internal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    _write_roles_runtime_cell_yaml(
        backend_root,
        ["polaris/delivery/http/routers/role_chat.py"],
    )
    router_root = backend_root / "polaris" / "delivery" / "http" / "routers"
    router_root.mkdir(parents=True)
    (router_root / "role_chat.py").write_text(
        "from polaris.cells.roles.kernel.internal.turn_runner import TurnRunner\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_roles_runtime_cell_import_boundaries()

    assert result.ok is False
    assert result.stage == "roles_runtime_cell_import_boundary"
    assert "roles.kernel.internal" in result.stderr
    assert "polaris/delivery/http/routers/role_chat.py" in result.stderr


def test_roles_runtime_cell_import_boundary_uses_cell_yaml_owned_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    _write_roles_runtime_cell_yaml(
        backend_root,
        ["polaris/cells/roles/runtime/owned_extra.py"],
    )
    owned_path = backend_root / "polaris" / "cells" / "roles" / "runtime" / "owned_extra.py"
    owned_path.parent.mkdir(parents=True, exist_ok=True)
    owned_path.write_text(
        "from polaris.cells.qa.audit_verdict.internal.quality_service import QualityService\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_roles_runtime_cell_import_boundaries()

    assert result.ok is False
    assert result.stage == "roles_runtime_cell_import_boundary"
    assert "qa.audit_verdict.internal" in result.stderr
    assert "polaris/cells/roles/runtime/owned_extra.py" in result.stderr


def test_role_runtime_capability_result_sandbox_allows_failed_allowed_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    public_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "public"
    public_root.mkdir(parents=True)
    (public_root / "service.py").write_text(
        "from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1\n"
        "\n"
        "def denied_result():\n"
        "    return RoleCapabilityInvocationResultV1(\n"
        "        ok=False,\n"
        "        allowed=False,\n"
        "        invocation_id='i',\n"
        "        role_id='qa',\n"
        "        capability_id='issue_audit_verdict',\n"
        "        command_contract='RunQaAuditCommandV1',\n"
        "        error_code='denied',\n"
        "    )\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_role_runtime_capability_result_sandbox()

    assert result.ok is True
    assert result.stage == "roles_runtime_capability_result_sandbox"


def test_role_runtime_capability_result_sandbox_blocks_failed_allowed_true(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    public_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "public"
    public_root.mkdir(parents=True)
    (public_root / "service.py").write_text(
        "from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1\n"
        "\n"
        "def unsafe_result():\n"
        "    return RoleCapabilityInvocationResultV1(\n"
        "        ok=False,\n"
        "        allowed=True,\n"
        "        invocation_id='i',\n"
        "        role_id='qa',\n"
        "        capability_id='issue_audit_verdict',\n"
        "        command_contract='RunQaAuditCommandV1',\n"
        "        error_code='denied',\n"
        "    )\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_role_runtime_capability_result_sandbox()

    assert result.ok is False
    assert result.stage == "roles_runtime_capability_result_sandbox"
    assert "RoleCapabilityInvocationResultV1(ok=False, allowed=True)" in result.stderr


def test_role_runtime_capability_result_sandbox_allows_negative_pytest_raises(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    public_root = backend_root / "polaris" / "cells" / "roles" / "runtime" / "public" / "tests"
    public_root.mkdir(parents=True)
    (public_root / "test_contract.py").write_text(
        "import pytest\n"
        "from polaris.cells.roles.runtime.public.contracts import RoleCapabilityInvocationResultV1\n"
        "\n"
        "def test_negative_contract():\n"
        "    with pytest.raises(ValueError):\n"
        "        RoleCapabilityInvocationResultV1(\n"
        "            ok=False,\n"
        "            allowed=True,\n"
        "            invocation_id='i',\n"
        "            role_id='qa',\n"
        "            capability_id='issue_audit_verdict',\n"
        "            command_contract='RunQaAuditCommandV1',\n"
        "            error_code='denied',\n"
        "        )\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_role_runtime_capability_result_sandbox()

    assert result.ok is True
    assert result.stage == "roles_runtime_capability_result_sandbox"


def test_kernelone_roles_business_boundary_allows_shared_contracts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    roles_root = backend_root / "polaris" / "kernelone" / "roles"
    roles_root.mkdir(parents=True)
    (roles_root / "shared_contracts.py").write_text(
        "from dataclasses import dataclass\n"
        "\n"
        "@dataclass(frozen=True)\n"
        "class AgentMessage:\n"
        "    sender: str\n"
        "    receiver: str\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_kernelone_roles_business_boundary()

    assert result.ok is True
    assert result.stage == "kernelone_roles_business_boundary"


def test_kernelone_roles_business_boundary_blocks_business_role_filename(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    roles_root = backend_root / "polaris" / "kernelone" / "roles"
    roles_root.mkdir(parents=True)
    (roles_root / "project_manager_role.py").write_text(
        "class SharedTemplate:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_kernelone_roles_business_boundary()

    assert result.ok is False
    assert result.stage == "kernelone_roles_business_boundary"
    assert "business role filename" in result.stderr
    assert "project_manager_role.py" in result.stderr


def test_kernelone_roles_business_boundary_blocks_business_role_definition(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_root = tmp_path / "backend"
    roles_root = backend_root / "polaris" / "kernelone" / "roles"
    roles_root.mkdir(parents=True)
    (roles_root / "templates.py").write_text(
        "class ArchitectAgent:\n    pass\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(gate, "BACKEND_ROOT", backend_root)

    result = gate._check_kernelone_roles_business_boundary()

    assert result.ok is False
    assert result.stage == "kernelone_roles_business_boundary"
    assert "business role definition" in result.stderr
    assert "ArchitectAgent" in result.stderr
