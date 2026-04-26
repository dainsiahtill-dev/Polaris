"""架构守护测试 (Architecture Guard Tests)

确保重构期间不引入新的重复实现，维护统一架构边界。

运行: pytest tests/refactor/test_architecture_guard.py -v
"""

from __future__ import annotations

import ast
import os
from pathlib import Path
from typing import List, Set

import pytest


class TestOrchestratorUniqueness:
    """验证只有一个生产可用的 RuntimeOrchestrator"""

    def test_only_one_production_orchestrator(self):
        """只允许一个生产 RuntimeOrchestrator 实现"""
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"

        # 新版（唯一生产实现）
        new_orchestrator = backend_root / "core" / "orchestration" / "runtime_orchestrator.py"
        # 旧版（应标记为 deprecated）
        old_orchestrator = backend_root / "core" / "runtime_orchestrator.py"

        assert new_orchestrator.exists(), "New orchestrator must exist"
        assert old_orchestrator.exists(), "Old orchestrator must exist (as shim)"

        # 验证旧版包含 deprecation 标记
        old_content = old_orchestrator.read_text(encoding="utf-8")
        assert "DEPRECATED" in old_content or "deprecated" in old_content.lower(), \
            "Old orchestrator must be marked as deprecated"

    def test_no_new_orchestrator_imports(self):
        """禁止新增对旧版 orchestrator 的引用"""
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"

        # 扫描新增文件，确保不导入旧版 orchestrator
        for py_file in backend_root.rglob("*.py"):
            # 跳过旧版本身和测试
            if "runtime_orchestrator.py" in str(py_file):
                continue
            if "test_" in str(py_file):
                continue

            content = py_file.read_text(encoding="utf-8")

            # 检查是否错误地导入旧版
            if "from core.runtime_orchestrator import" in content:
                # 允许在新版中导入以提供兼容
                if "core/orchestration" not in str(py_file):
                    pytest.fail(
                        f"{py_file} imports from deprecated core.runtime_orchestrator. "
                        f"Use core.orchestration instead."
                    )


class TestServiceModuleCompleteness:
    """验证 cli_thin 所需的服务模块存在"""

    def test_pm_service_exists(self):
        """PM 服务模块必须存在"""
        pm_service = Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "pm" / "pm_service.py"
        assert pm_service.exists(), "pm_service.py must exist for cli_thin to work"

    def test_director_service_exists(self):
        """Director 服务模块必须存在"""
        director_service = Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "director" / "director_service.py"
        assert director_service.exists(), "director_service.py must exist for cli_thin to work"


class TestContractTypes:
    """验证统一编排契约类型"""

    def test_orchestration_contracts_exist(self):
        """统一编排契约必须存在"""
        contracts = Path(__file__).parent.parent.parent / "src" / "backend" / "application" / "dto" / "orchestration_contracts.py"
        assert contracts.exists(), "orchestration_contracts.py must exist"

        content = contracts.read_text(encoding="utf-8")

        # 验证关键类型存在
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
        """编排服务端口必须存在"""
        port_file = Path(__file__).parent.parent.parent / "src" / "backend" / "application" / "ports" / "orchestration_service.py"
        assert port_file.exists(), "orchestration_service.py port must exist"

        content = port_file.read_text(encoding="utf-8")

        # 验证关键接口
        required_interfaces = [
            "OrchestrationService",
            "RoleOrchestrationAdapter",
        ]

        for interface in required_interfaces:
            assert interface in content, f"{interface} must be defined in service port"


class TestNoBusinessLogicInCLI:
    """验证 CLI 层不包含业务逻辑"""

    def test_cli_thin_is_thin(self):
        """cli_thin 应该只包含解析和转发"""
        cli_files = [
            Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "pm" / "cli_thin.py",
            Path(__file__).parent.parent.parent / "src" / "backend" / "scripts" / "director" / "cli_thin.py",
        ]

        for cli_file in cli_files:
            if not cli_file.exists():
                continue

            content = cli_file.read_text(encoding="utf-8")

            # 不应该包含业务逻辑关键字
            forbidden_patterns = [
                "subprocess.Popen(",  # 应该使用 RuntimeOrchestrator
                "generate_role_response(",  # 应该在服务层
                "TaskBoard(",  # 应该在服务层
            ]

            for pattern in forbidden_patterns:
                assert pattern not in content, \
                    f"{cli_file} contains business logic ({pattern}), should be in service layer"


class TestUnifiedServiceExport:
    """验证统一编排服务正确导出"""

    def test_unified_service_in_init(self):
        """统一编排服务必须在 __init__ 中导出"""
        init_file = Path(__file__).parent.parent.parent / "src" / "backend" / "core" / "orchestration" / "__init__.py"
        content = init_file.read_text(encoding="utf-8")

        required_exports = [
            "UnifiedOrchestrationService",
            "get_orchestration_service",
        ]

        for export in required_exports:
            assert export in content, f"{export} must be exported in orchestration/__init__.py"


class TestRoleAdapters:
    """验证角色适配器"""

    @pytest.fixture(autouse=True)
    def setup_path(self):
        """添加后端路径到 Python 路径"""
        import sys
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

    def test_all_adapters_exist(self):
        """所有角色适配器必须存在"""
        from app.roles.adapters import (
            PMAdapter,
            DirectorAdapter,
            QAAdapter,
            ChiefEngineerAdapter,
        )

        # 验证可以实例化
        pm = PMAdapter(".")
        director = DirectorAdapter(".")
        qa = QAAdapter(".")
        ce = ChiefEngineerAdapter(".")

        assert pm.role_id == "pm"
        assert director.role_id == "director"
        assert qa.role_id == "qa"
        assert ce.role_id == "chief_engineer"

    def test_adapter_registration(self):
        """适配器注册功能"""
        from app.roles.adapters import get_supported_roles, create_role_adapter

        roles = get_supported_roles()
        assert "pm" in roles
        assert "director" in roles
        assert "qa" in roles
        assert "chief_engineer" in roles


class TestGenericWorkflow:
    """验证通用工作流"""

    def test_generic_pipeline_workflow_exists(self):
        """通用流水线工作流必须存在"""
        workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "app" / "orchestration" / "workflows" / "generic_pipeline_workflow.py"
        assert workflow_file.exists(), "generic_pipeline_workflow.py must exist"

        content = workflow_file.read_text(encoding="utf-8")

        required_classes = [
            "PipelineWorkflowInput",
            "PipelineWorkflowResult",
            "GenericPipelineWorkflow",  # 注意：这个类在 @workflow.defn 装饰器内
        ]

        for cls in required_classes:
            assert cls in content, f"{cls} must be defined in generic pipeline workflow"

    def test_compatibility_wrappers_exist(self):
        """兼容包装器必须存在"""
        workflow_file = Path(__file__).parent.parent.parent / "src" / "backend" / "app" / "orchestration" / "workflows" / "generic_pipeline_workflow.py"
        content = workflow_file.read_text(encoding="utf-8")

        wrappers = ["PMWorkflow", "DirectorWorkflow", "QAWorkflow"]
        for wrapper in wrappers:
            assert wrapper in content, f"{wrapper} wrapper must exist for backward compatibility"


class TestUIStateContract:
    """验证 UI 状态合同"""

    def test_ui_state_contract_exists(self):
        """UI 状态合同必须存在"""
        contract_file = Path(__file__).parent.parent.parent / "src" / "backend" / "core" / "orchestration" / "ui_state_contract.py"
        assert contract_file.exists(), "ui_state_contract.py must exist"

        content = contract_file.read_text(encoding="utf-8")

        required_types = [
            "UIPhase",
            "UITaskStatus",
            "UIFileChangeMetrics",
            "UITaskItem",
            "UIOrchestrationState",
            "UIStateConverter",
        ]

        for t in required_types:
            assert t in content, f"{t} must be defined in UI state contract"


class TestFileChangeTracker:
    """验证文件变更追踪"""

    def test_file_change_tracker_exists(self):
        """文件变更追踪器必须存在"""
        tracker_file = Path(__file__).parent.parent.parent / "src" / "backend" / "core" / "orchestration" / "file_change_tracker.py"
        assert tracker_file.exists(), "file_change_tracker.py must exist"

        content = tracker_file.read_text(encoding="utf-8")

        required_classes = [
            "FileChangeSnapshot",
            "FileChangeTracker",
            "TaskFileChangeTracker",
        ]

        for cls in required_classes:
            assert cls in content, f"{cls} must be defined in file change tracker"


class TestUnifiedOrchestrationAPI:
    """验证统一编排 API"""

    def test_unified_api_exists(self):
        """统一编排 API 必须存在"""
        api_file = Path(__file__).parent.parent.parent / "src" / "backend" / "api" / "v2" / "orchestration.py"
        assert api_file.exists(), "orchestration.py API must exist"

        content = api_file.read_text(encoding="utf-8")

        required_routes = [
            'router = APIRouter(prefix="/orchestration"',
            '@router.post("/runs"',
            '@router.get("/runs/{run_id}"',
            '@router.post("/runs/{run_id}/signal"',
        ]

        for route in required_routes:
            assert route in content, f"Route '{route}' must be defined in API"


class TestRunModeCompatibility:
    """验证 RunMode 兼容映射"""

    @pytest.fixture(autouse=True)
    def setup_path(self):
        """添加后端路径到 Python 路径"""
        import sys
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

    def test_pm_mode_mapping(self):
        """PM 模式映射必须正确"""
        from application.dto.orchestration_contracts import CompatibilityMapper, OrchestrationMode

        assert CompatibilityMapper.pm_mode_to_orchestration("run_once") == OrchestrationMode.WORKFLOW
        assert CompatibilityMapper.pm_mode_to_orchestration("loop") == OrchestrationMode.WORKFLOW
        assert CompatibilityMapper.pm_mode_to_orchestration("chat") == OrchestrationMode.CHAT

    def test_director_mode_mapping(self):
        """Director 模式映射必须正确"""
        from application.dto.orchestration_contracts import CompatibilityMapper, OrchestrationMode

        assert CompatibilityMapper.director_mode_to_orchestration("one_shot") == OrchestrationMode.WORKFLOW
        assert CompatibilityMapper.director_mode_to_orchestration("continuous") == OrchestrationMode.WORKFLOW


class TestStatusCompatibility:
    """验证状态映射兼容"""

    @pytest.fixture(autouse=True)
    def setup_path(self):
        """添加后端路径到 Python 路径"""
        import sys
        backend_root = Path(__file__).parent.parent.parent / "src" / "backend"
        if str(backend_root) not in sys.path:
            sys.path.insert(0, str(backend_root))

    def test_legacy_status_mapping(self):
        """旧状态映射到统一状态"""
        from application.dto.orchestration_contracts import CompatibilityMapper, RunStatus

        # PM 状态
        assert CompatibilityMapper.legacy_status_to_unified("idle") == RunStatus.PENDING
        assert CompatibilityMapper.legacy_status_to_unified("running") == RunStatus.RUNNING
        assert CompatibilityMapper.legacy_status_to_unified("completed") == RunStatus.COMPLETED
        assert CompatibilityMapper.legacy_status_to_unified("error") == RunStatus.FAILED

        # Director 状态
        assert CompatibilityMapper.legacy_status_to_unified("pending") == RunStatus.PENDING
        assert CompatibilityMapper.legacy_status_to_unified("in_progress") == RunStatus.RUNNING
        assert CompatibilityMapper.legacy_status_to_unified("success") == RunStatus.COMPLETED
        assert CompatibilityMapper.legacy_status_to_unified("failure") == RunStatus.FAILED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
