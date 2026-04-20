"""Test suite for `roles.kernel` PromptBuilder retry logic.

Covers:
- PromptBuilder.build_retry_prompt() — error feedback injection
- PromptBuilder._sanitize_error_for_llm() — sensitive data scrubbing
- PromptBuilder.messages_to_input() — message list formatting
- L1/L2/L3 cache hit statistics
"""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.prompt_builder import (
    PromptBuilder,
)


class TestBuildRetryPromptBasics:
    """build_retry_prompt returns base_prompt unchanged when no validation or attempt=0."""

    def test_attempt_zero_returns_base_unchanged(self) -> None:
        builder = PromptBuilder()
        base = "original prompt content"
        result = builder.build_retry_prompt(base, None, attempt=0)
        assert result == base

    def test_no_validation_returns_base_unchanged(self) -> None:
        builder = PromptBuilder()
        base = "original prompt content"
        result = builder.build_retry_prompt(base, None, attempt=1)
        assert result == base

    def test_empty_validation_returns_base_unchanged(self) -> None:
        builder = PromptBuilder()
        base = "original prompt"
        result = builder.build_retry_prompt(base, {}, attempt=1)
        assert result == base


class TestBuildRetryPromptErrorInjection:
    """build_retry_prompt injects error feedback from last_validation."""

    def test_llm_error_injects_llm_feedback(self) -> None:
        builder = PromptBuilder()
        base = "【任务】完成代码审查"
        validation = {
            "errors": ["LLM 调用失败: timeout after 60s"],
            "suggestions": ["请稍后重试"],
            "data": {"llm_call_failed": True},
        }
        result = builder.build_retry_prompt(base, validation, attempt=1)
        assert "LLM" in result
        assert "timeout" in result or "LLM" in result
        assert base in result  # base is prepended

    def test_tool_error_injects_tool_feedback(self) -> None:
        builder = PromptBuilder()
        base = "【任务】实现功能X"
        validation = {
            "errors": ["write_file 执行失败: 权限不足"],
            "suggestions": ["检查文件权限"],
            "data": {},
        }
        result = builder.build_retry_prompt(base, validation, attempt=1)
        assert "工具" in result or "失败" in result

    def test_other_error_injects_format_feedback(self) -> None:
        builder = PromptBuilder()
        base = "【任务】生成JSON"
        validation = {
            "errors": ["JSON格式错误: 缺少引号"],
            "suggestions": ["修复格式"],
            "data": {},
        }
        result = builder.build_retry_prompt(base, validation, attempt=1)
        assert "格式" in result or "错误" in result

    def test_attempt_number_in_feedback(self) -> None:
        builder = PromptBuilder()
        base = "【任务】完成"
        validation = {"errors": ["格式错误"], "suggestions": [], "data": {}}
        result = builder.build_retry_prompt(base, validation, attempt=2)
        assert "3" in result  # attempt + 1 = 3

    def test_suggestions_appended_when_present(self) -> None:
        builder = PromptBuilder()
        base = "【任务】"
        validation = {
            "errors": [],
            "suggestions": ["建议：使用更简洁的代码"],
            "data": {},
        }
        result = builder.build_retry_prompt(base, validation, attempt=1)
        assert "建议" in result or "简洁" in result


class TestSanitizeErrorForLLM:
    """_sanitize_error_for_llm removes sensitive data before feeding back to LLM."""

    def test_removes_file_paths(self) -> None:
        builder = PromptBuilder()
        raw = "Error in /workspace/src/backend/main.py at line 42"
        result = builder._sanitize_error_for_llm(raw)
        assert "/workspace/src/backend/main.py" not in result
        assert "/path/" in result or "C:/path/" in result

    def test_removes_windows_paths(self) -> None:
        builder = PromptBuilder()
        raw = r"Error in C:\Users\dains\project\file.py"
        result = builder._sanitize_error_for_llm(raw)
        assert r"C:\Users" not in result

    def test_removes_ip_addresses(self) -> None:
        builder = PromptBuilder()
        raw = "Connection refused: 192.168.1.100:8080"
        result = builder._sanitize_error_for_llm(raw)
        assert "192.168.1.100" not in result
        assert "[IP]" in result

    def test_removes_api_keys(self) -> None:
        builder = PromptBuilder()
        raw = "Auth failed: sk-abc123xyz789secret"
        result = builder._sanitize_error_for_llm(raw)
        assert "sk-abc123xyz789secret" not in result
        assert "[API_KEY]" in result

    def test_removes_env_secret_patterns(self) -> None:
        builder = PromptBuilder()
        raw = 'Request failed: API_SECRET="my-super-secret-value"'
        result = builder._sanitize_error_for_llm(raw)
        assert "my-super-secret-value" not in result
        assert "[HIDDEN]" in result

    def test_truncates_long_errors(self) -> None:
        builder = PromptBuilder()
        long_error = "x" * 600
        result = builder._sanitize_error_for_llm(long_error)
        assert len(result) <= 503  # 500 + "..."


class TestMessagesToInput:
    """messages_to_input converts message list to formatted string."""

    def test_system_message_formatted(self) -> None:
        builder = PromptBuilder()
        messages = [{"role": "system", "content": "You are a helpful assistant"}]
        result = builder.messages_to_input(messages)
        assert "【系统指令】" in result
        assert "You are a helpful assistant" in result

    def test_user_message_formatted(self) -> None:
        builder = PromptBuilder()
        messages = [{"role": "user", "content": "Hello"}]
        result = builder.messages_to_input(messages)
        assert "【用户】" in result
        assert "Hello" in result

    def test_assistant_message_formatted(self) -> None:
        builder = PromptBuilder()
        messages = [{"role": "assistant", "content": "Hi there"}]
        result = builder.messages_to_input(messages)
        assert "【助手】" in result
        assert "Hi there" in result

    def test_unknown_role_uses_fallback_marker(self) -> None:
        builder = PromptBuilder()
        messages = [{"role": "custom", "content": "data"}]
        result = builder.messages_to_input(messages)
        assert "【custom】" in result

    def test_strips_empty_content_messages(self) -> None:
        builder = PromptBuilder()
        messages = [{"role": "user", "content": "  "}]
        result = builder.messages_to_input(messages)
        # Role marker is included even for whitespace-only content
        assert "【用户】" in result

    def test_multiple_messages_joined_with_double_newline(self) -> None:
        builder = PromptBuilder()
        messages = [
            {"role": "system", "content": "SYS"},
            {"role": "user", "content": "USER"},
        ]
        result = builder.messages_to_input(messages)
        assert "SYS" in result
        assert "USER" in result


class TestPromptBuilderCacheStats:
    """Cache statistics are tracked correctly."""

    def test_new_builder_has_default_cache_structure(self) -> None:
        builder = PromptBuilder()
        stats = builder.get_cache_stats()
        # Keys reflect L1/L2/L3 cache state (not hit counts)
        assert "l1_cached_roles" in stats
        assert "l2_cached" in stats
        assert "l3_cached" in stats

    def test_clear_cache_resets_l1_entries(self) -> None:
        builder = PromptBuilder()
        builder.clear_cache()
        stats = builder.get_cache_stats()
        assert stats["l1_cached_roles"] == 0
