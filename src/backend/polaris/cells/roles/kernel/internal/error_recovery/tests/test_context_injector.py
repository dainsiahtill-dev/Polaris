"""Context Injector 综合测试。"""

from polaris.cells.roles.kernel.internal.error_recovery.context_injector import (
    ErrorContextInjector,
)


class TestErrorContextInjector:
    """测试错误上下文注入器。"""

    def test_inject_error_context(self) -> None:
        history = []
        new_history = ErrorContextInjector.inject_error_context(
            history, "read_file", "File not found", {"path": "test.py"}
        )
        assert len(new_history) == 1
        assert new_history[0]["role"] == "system"
        assert "read_file" in new_history[0]["content"]
        assert "File not found" in new_history[0]["content"]

    def test_inject_preserves_existing_history(self) -> None:
        history = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there"},
        ]
        new_history = ErrorContextInjector.inject_error_context(history, "write", "Permission denied", {"path": "a.py"})
        assert len(new_history) == 3
        assert new_history[0]["content"] == "Hello"
        assert new_history[1]["content"] == "Hi there"

    def test_inject_recovery_hint(self) -> None:
        history = []
        hint = "Try using a different file path"
        new_history = ErrorContextInjector.inject_recovery_hint(history, hint)
        assert len(new_history) == 1
        assert "Recovery Hint" in new_history[0]["content"]
        assert "Try using a different file path" in new_history[0]["content"]
