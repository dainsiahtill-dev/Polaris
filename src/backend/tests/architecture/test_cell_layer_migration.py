"""Tests for K1-PURIFY Phase 2: Cell Layer Role Integration Migration.

【K1-PURIFY Phase 2】
验证角色工具集成已正确迁移到 Cell 层。
这些测试验证迁移的完整性和正确性。
"""

import pytest


class TestCellLayerMigration:
    """验证角色集成已正确迁移到 Cell 层."""

    def test_cell_role_integrations_exist(self):
        """验证 Cell 层包含所有角色集成类."""
        from polaris.cells.llm.tool_runtime.internal import (
            ArchitectToolIntegration,
            ChiefEngineerToolIntegration,
            DirectorToolIntegration,
            PMToolIntegration,
            QAToolIntegration,
            ScoutToolIntegration,
        )

        assert PMToolIntegration is not None
        assert ArchitectToolIntegration is not None
        assert ChiefEngineerToolIntegration is not None
        assert DirectorToolIntegration is not None
        assert QAToolIntegration is not None
        assert ScoutToolIntegration is not None

    def test_cell_role_registry(self):
        """验证 Cell 层包含角色注册表."""
        from polaris.cells.llm.tool_runtime.internal import (
            ROLE_TOOL_INTEGRATIONS,
            PMToolIntegration,
            get_role_tool_integration,
        )

        assert isinstance(ROLE_TOOL_INTEGRATIONS, dict)
        assert len(ROLE_TOOL_INTEGRATIONS) == 6
        assert "pm" in ROLE_TOOL_INTEGRATIONS
        assert "architect" in ROLE_TOOL_INTEGRATIONS
        assert "chief_engineer" in ROLE_TOOL_INTEGRATIONS
        assert "director" in ROLE_TOOL_INTEGRATIONS
        assert "qa" in ROLE_TOOL_INTEGRATIONS
        assert "scout" in ROLE_TOOL_INTEGRATIONS

        # 测试工厂函数
        integration = get_role_tool_integration("pm", "/tmp")
        assert isinstance(integration, PMToolIntegration)

    def test_cell_layer_prompt_generation(self):
        """验证 Cell 层能正确生成角色提示."""
        from polaris.cells.llm.tool_runtime.internal import (
            ChiefEngineerToolIntegration,
            DirectorToolIntegration,
        )

        # ChiefEngineer
        ce_integration = ChiefEngineerToolIntegration("/tmp")
        ce_prompt = ce_integration.get_system_prompt()
        assert "repo_rg" in ce_prompt
        assert "read_file" in ce_prompt
        assert "write_file" in ce_prompt

        # Director
        dir_integration = DirectorToolIntegration("/tmp")
        dir_prompt = dir_integration.get_system_prompt()
        assert "repo_rg" in dir_prompt
        assert "execute_command" in dir_prompt

    def test_cell_layer_native_function_calling(self):
        """验证 Cell 层支持原生 Function Calling 格式."""
        from polaris.cells.llm.tool_runtime.internal import DirectorToolIntegration
        from polaris.kernelone.tool_execution.tool_spec_registry import (
            migrate_from_contracts_specs,
        )

        # 测试自动重置了单例，需要重新初始化工具注册表
        migrate_from_contracts_specs()

        integration = DirectorToolIntegration("/tmp")
        tools = integration.format_tools_for_native_calling()

        assert len(tools) > 0
        assert all(t["type"] == "function" for t in tools)
        for tool in tools:
            assert "function" in tool
            assert "name" in tool["function"]
            assert "description" in tool["function"]
            assert "parameters" in tool["function"]

    def test_cell_layer_context_manager(self):
        """验证 Cell 层支持上下文管理器."""
        from polaris.cells.llm.tool_runtime.internal import PMToolIntegration

        # 测试上下文管理器
        with PMToolIntegration("/tmp") as integration:
            assert integration is not None
            prompt = integration.get_system_prompt()
            assert "PM" in prompt or "尚书令" in prompt

    def test_kernelone_toolkit_no_role_exports(self):
        """验证 KernelOne toolkit 不再导出角色集成."""
        from polaris.kernelone.llm import toolkit

        toolkit_all = toolkit.__all__

        role_items = [
            "PMToolIntegration",
            "ArchitectToolIntegration",
            "ChiefEngineerToolIntegration",
            "DirectorToolIntegration",
            "QAToolIntegration",
            "ScoutToolIntegration",
            "ToolEnabledLLMClient",
            "enhance_chief_engineer_prompt",
            "enhance_director_prompt",
            "ROLE_TOOL_INTEGRATIONS",
            "get_role_tool_integration",
        ]

        for item in role_items:
            assert item not in toolkit_all, f"{item} should not be in KernelOne toolkit __all__"


class TestCellLayerBackwardCompatibility:
    """验证 Cell 层与旧代码的兼容性."""

    def test_cell_integration_basic_usage(self):
        """验证基本使用模式."""
        from polaris.cells.llm.tool_runtime.internal import (
            ROLE_TOOL_INTEGRATIONS,
            PMToolIntegration,
        )

        # 直接实例化
        integration = PMToolIntegration("/tmp")
        prompt = integration.get_system_prompt()
        assert isinstance(prompt, str)
        assert len(prompt) > 0

        # 通过注册表获取
        ce_class = ROLE_TOOL_INTEGRATIONS["chief_engineer"]
        ce_integration = ce_class("/tmp")
        assert ce_integration.get_system_prompt()

    def test_cell_layer_protocol_violation(self):
        """验证遗留文本协议被正确拒绝."""
        from polaris.cells.llm.tool_runtime.internal import DirectorToolIntegration

        integration = DirectorToolIntegration("/tmp")

        # 遗留协议应被拒绝
        legacy_response = """
        [WRITE_FILE]
        file: "test.py"
        content: "hello"
        [/WRITE_FILE]
        """

        result = integration.process_llm_response(legacy_response)
        assert result["has_tools"] is False
        assert "protocol_violation" in result


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
