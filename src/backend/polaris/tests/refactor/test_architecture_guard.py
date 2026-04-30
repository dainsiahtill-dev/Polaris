"""Architecture Guard Tests

Ensure no duplicate implementations are introduced during refactoring
and maintain unified architecture boundaries.

Note: This file tests legacy architecture invariants. Many paths reference
the old root (core/, app/, api/) which have been migrated to polaris/.*.
These tests verify that the migration shims and compatibility layers exist.

运行: pytest tests/refactor/test_architecture_guard.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestOrchestratorUniqueness:
    """Verify only one production RuntimeOrchestrator exists"""

    def test_only_one_production_orchestrator(self):
        """Only one production RuntimeOrchestrator implementation is allowed"""
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"

        # New version (only production implementation)
        new_orchestrator = backend_root / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "internal" / "runtime_orchestrator.py"
        # Old version (should be marked as deprecated)
        old_orchestrator = backend_root / "core" / "runtime_orchestrator.py"

        assert new_orchestrator.exists(), "New orchestrator must exist"
        if old_orchestrator.exists():
            # Verify old version contains deprecation marker
            old_content = old_orchestrator.read_text(encoding="utf-8")
            assert "DEPRECATED" in old_content or "deprecated" in old_content.lower(), \
                "Old orchestrator must be marked as deprecated"

    def test_no_new_orchestrator_imports(self):
        """Prohibit new references to the old orchestrator"""
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"

        # Scan new files to ensure they don't import the old orchestrator
        for py_file in backend_root.rglob("*.py"):
            # Skip old version itself and tests
            if "runtime_orchestrator.py" in str(py_file):
                continue
            if "test_" in str(py_file):
                continue

            content = py_file.read_text(encoding="utf-8")

            # Check if old version is incorrectly imported.
            # The literal string "from core.runtime_orchestrator import" is an
            # intentional search pattern to detect legacy imports in production code.
            if "from core.runtime_orchestrator import" in content:
                # Allow imports in new version for compatibility
                if "core/orchestration" not in str(py_file):
                    pytest.fail(
                        f"{py_file} imports from deprecated core.runtime_orchestrator. "
                        f"Use polaris.cells.orchestration.workflow_runtime instead."
                    )


class TestServiceModuleCompleteness:
    """Verify required service modules exist for cli_thin"""

    def test_pm_service_exists(self):
        """PM service module must exist"""
        pm_service = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "delivery" / "cli" / "pm" / "pm_service.py"
        legacy_pm_service = Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "pm" / "pm_service.py"
        assert pm_service.exists() or legacy_pm_service.exists(), "pm_service.py must exist for cli_thin to work"

    def test_director_service_exists(self):
        """Director service module must exist"""
        director_service = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "delivery" / "cli" / "director" / "director_service.py"
        legacy_director_service = Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "director" / "director_service.py"
        assert director_service.exists() or legacy_director_service.exists(), "director_service.py must exist for cli_thin to work"


class TestContractTypes:
    """Verify unified orchestration contract types"""

    def test_orchestration_contracts_exist(self):
        """Unified orchestration contracts must exist"""
        contracts = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "public" / "contracts.py"
        legacy_contracts = Path(__file__).parent.parent.parent / "src" / "backend" / "application" / "dto" / "orchestration_contracts.py"
        target = contracts if contracts.exists() else legacy_contracts
        assert target.exists(), "orchestration contracts must exist"

        content = target.read_text(encoding="utf-8")

        # Verify key types exist
        required_types = [
            "OrchestrationRunRequest",
            "OrchestrationSnapshot",
            "RunStatus",
            "TaskSnapshot",
            "FileChangeStats",
        ]

        for type_name in required_types:
            assert type_name in content, f"{type_name} must be defined in contracts"

    def test_service_port_exists(self):
        """Orchestration service port must exist"""
        port_file = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "public" / "service.py"
        legacy_port_file = Path(__file__).parent.parent.parent / "src" / "backend" / "application" / "ports" / "orchestration_service.py"
        target = port_file if port_file.exists() else legacy_port_file
        assert target.exists(), "orchestration service port must exist"

        content = target.read_text(encoding="utf-8")

        # Verify key interfaces
        required_interfaces = [
            "OrchestrationService",
            "RoleOrchestrationAdapter",
        ]

        for interface in required_interfaces:
            assert interface in content, f"{interface} must be defined in service port"


class TestNoBusinessLogicInCLI:
    """Verify CLI layer does not contain business logic"""

    def test_cli_thin_is_thin(self):
        """cli_thin should only contain parsing and forwarding"""
        cli_files = [
            Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "delivery" / "cli" / "pm" / "cli_thin.py",
            Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "delivery" / "cli" / "director" / "cli_thin.py",
            Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "pm" / "cli_thin.py",
            Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "director" / "cli_thin.py",
        ]

        for cli_file in cli_files:
            if not cli_file.exists():
                continue

            content = cli_file.read_text(encoding="utf-8")

            # Should not contain business logic keywords
            forbidden_patterns = [
                "subprocess.Popen(",  # Should use RuntimeOrchestrator
                "generate_role_response(",  # Should be in service layer
                "TaskBoard(",  # Should be in service layer
            ]

            for pattern in forbidden_patterns:
                assert pattern not in content, \
                    f"{cli_file} contains business logic ({pattern}), should be in service layer"


class TestUnifiedServiceExport:
    """Verify unified orchestration service is correctly exported"""

    def test_unified_service_in_init(self):
        """Unified orchestration service must be exported in __init__"""
        init_file = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "public" / "__init__.py"
        legacy_init_file = Path(__file__).parent.parent.parent / "src" / "backend" / "core" / "orchestration" / "__init__.py"
        target = init_file if init_file.exists() else legacy_init_file
        assert target.exists(), "orchestration __init__.py must exist"

        content = target.read_text(encoding="utf-8")

        required_exports = [
            "UnifiedOrchestrationService",
            "get_orchestration_service",
        ]

        for export in required_exports:
            assert export in content, f"{export} must be exported in orchestration/__init__.py"


class TestRoleAdapters:
    """Verify role adapters"""

    @pytest.fixture(autouse=True)
    def setup_path(self):
        """Add backend path to Python path"""
        import sys
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

    def test_all_adapters_exist(self):
        """All role adapters must exist"""
        from polaris.cells.roles.adapters.public import (
            ChiefEngineerAdapter,
            PMAdapter,
            QAAdapter,
        )

        # Verify instantiation
        pm = PMAdapter(".")
        qa = QAAdapter(".")
        ce = ChiefEngineerAdapter(".")

        assert pm.role_id == "pm"
        assert qa.role_id == "qa"
        assert ce.role_id == "chief_engineer"

    def test_adapter_registration(self):
        """Adapter registration functionality"""
        from polaris.cells.roles.adapters.public import get_supported_roles

        roles = get_supported_roles()
        assert "pm" in roles
        assert "director" in roles
        assert "qa" in roles
        assert "chief_engineer" in roles


class TestGenericWorkflow:
    """Verify generic workflow"""

    def test_generic_pipeline_workflow_exists(self):
        """Generic pipeline workflow must exist"""
        workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_orchestration" / "__init__.py"
        legacy_workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "app" / "orchestration" / "workflows" / "generic_pipeline_workflow.py"
        target = workflow_file if workflow_file.exists() else legacy_workflow_file
        assert target.exists(), "generic pipeline workflow must exist"

        content = target.read_text(encoding="utf-8")

        required_classes = [
            "PipelineWorkflowInput",
            "PipelineWorkflowResult",
            "GenericPipelineWorkflow",
        ]

        for cls in required_classes:
            assert cls in content, f"{cls} must be defined in generic pipeline workflow"

    def test_compatibility_wrappers_exist(self):
        """Compatibility wrappers must exist"""
        workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_orchestration" / "__init__.py"
        legacy_workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "app" / "orchestration" / "workflows" / "generic_pipeline_workflow.py"
        target = workflow_file if workflow_file.exists() else legacy_workflow_file
        if not target.exists():
            pytest.skip("Workflow file not found")

        content = target.read_text(encoding="utf-8")

        wrappers = ["PMWorkflow", "DirectorWorkflow", "QAWorkflow"]
        for wrapper in wrappers:
            assert wrapper in content, f"{wrapper} wrapper must exist for backward compatibility"


class TestUIStateContract:
    """Verify UI state contract"""

    def test_ui_state_contract_exists(self):
        """UI state contract must exist"""
        contract_file = Path(__file__).parent.parent.parent / "src" / "backend" / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "internal" / "ui_state_contract.py"
        legacy_contract_file = Path(__file__).parent.parent.parent / "src" / "backend" / "core" / "orchestration" / "ui_state_contract.py"
        target = contract_file if contract_file.exists() else legacy_contract_file
        assert target.exists(), "ui_state_contract.py must exist"
