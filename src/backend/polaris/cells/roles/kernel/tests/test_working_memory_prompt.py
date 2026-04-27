"""Tests for SESSION_PATCH working memory integration in PromptBuilder (ADR-0080)."""

from types import SimpleNamespace

import pytest
from polaris.cells.roles.kernel.internal.prompt_builder import PromptBuilder


class TestWorkingMemoryContractGuide:
    """ADR-0080: SESSION_PATCH 工作记忆契约嵌入验证。"""

    @pytest.fixture
    def profile(self) -> object:
        return SimpleNamespace(
            role_id="director",
            version="1.0.0",
            prompt_policy=SimpleNamespace(
                core_template_id="director",
                tpl_version="1.0",
                persona_id="default",
                quality_checklist=[],
                task_type="default",
            ),
            tool_policy=SimpleNamespace(
                policy_id="director-policy",
                whitelist=["read_file", "write_file", "execute_command"],
                allow_code_write=True,
                allow_command_execution=True,
                allow_file_delete=False,
            ),
            responsibilities=["代码执行"],
            display_name="Director",
            description="代码执行角色",
        )

    def test_session_patch_guide_in_system_prompt(self, profile) -> None:
        """系统提示词必须包含 <SESSION_PATCH> 输出规范（ADR-0080 Step 7）。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        assert "<SESSION_PATCH>" in prompt
        assert "</SESSION_PATCH>" in prompt
        # 核心字段必须出现
        assert "task_progress" in prompt
        assert "error_summary" in prompt
        assert "suspected_files" in prompt
        assert "verified_results" in prompt

    def test_session_patch_guide_in_professional_prompt(self, profile) -> None:
        """三轴模式下的 prompt 也必须包含 SESSION_PATCH 规范。"""
        pb = PromptBuilder()
        prompt = pb.build_professional_prompt(profile, recipe_id="director")

        assert "<SESSION_PATCH>" in prompt
        assert "</SESSION_PATCH>" in prompt

    def test_session_patch_schema_fields_present(self, profile) -> None:
        """SESSION_PATCH 块的 schema 所有字段都必须在 prompt 中出现。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        required_fields = [
            "task_progress",
            "error_summary",
            "suspected_files",
            "patched_files",
            "verified_results",
            "pending_files",
            "action_taken",
        ]
        for field in required_fields:
            assert f'"{field}"' in prompt, f"Field {field} missing from SESSION_PATCH schema"

    def test_session_patch_task_progress_values_present(self, profile) -> None:
        """task_progress 的所有枚举值都必须在 prompt 中说明。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        assert "exploring" in prompt
        assert "investigating" in prompt
        assert "implementing" in prompt
        assert "verifying" in prompt
        assert "done" in prompt

    def test_session_patch_remove_keys_mentioned(self, profile) -> None:
        """remove_keys 机制必须在 prompt 中说明（用于撤销伪线索）。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        assert "remove_keys" in prompt
        assert "伪线索" in prompt or "撤销" in prompt

    def test_session_patch_example_in_prompt(self, profile) -> None:
        """prompt 中必须包含完整的输出示例。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        # SESSION_PATCH 结构块必须存在（<thinking> 是模型输出格式，不在系统提示词中）
        assert "<SESSION_PATCH>" in prompt

    def test_working_memory_contract_in_chunk_assembly(self, profile) -> None:
        """L4 工作记忆契约层必须在 chunk assembly 中被正确注入。"""
        pb = PromptBuilder()
        prompt = pb.build_system_prompt(profile)

        # L4 层应该在 L3（Runtime Contract）之后
        runtime_contract_idx = prompt.index("工具调用、结构化输出")
        session_patch_idx = prompt.index("<SESSION_PATCH>")
        assert session_patch_idx > runtime_contract_idx

    def test_working_memory_prompt_persists_across_builds(self, profile) -> None:
        """SESSION_PATCH 规范在多次 build_system_prompt 调用中保持稳定。"""
        pb = PromptBuilder()
        prompt1 = pb.build_system_prompt(profile)
        prompt2 = pb.build_system_prompt(profile)

        assert "<SESSION_PATCH>" in prompt1
        assert "<SESSION_PATCH>" in prompt2
        # 两次构建的 SESSION_PATCH 块内容应一致
        assert prompt1.count("<SESSION_PATCH>") == prompt2.count("<SESSION_PATCH>")

    def test_working_memory_guide_cached_in_l4(self, profile) -> None:
        """L4 工作记忆契约层应该有缓存机制（60s TTL）。"""
        pb = PromptBuilder()
        assert pb.L4_CACHE_TTL == 60
        # 首次调用后，L4 缓存应有值
        pb.build_system_prompt(profile)
        stats = pb.get_cache_stats()
        assert stats.get("l4_cached") is True

    def test_clear_cache_includes_l4(self, profile) -> None:
        """clear_cache() 必须清除 L4 缓存。"""
        pb = PromptBuilder()
        pb.build_system_prompt(profile)
        assert pb.get_cache_stats().get("l4_cached") is True
        pb.clear_cache()
        assert pb.get_cache_stats().get("l4_cached") is False
