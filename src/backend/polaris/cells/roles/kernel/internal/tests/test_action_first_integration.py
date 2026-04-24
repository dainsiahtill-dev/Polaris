"""Action-First 架构 Benchmark 集成测试。"""


class TestActionFirstBenchmark:
    """Benchmark 场景验证。"""

    def test_prompt_contains_action_first_rules(self) -> None:
        """验证 Prompt 包含三条铁律。"""
        from polaris.cells.roles.kernel.internal.prompt_templates import (
            build_action_first_prompt,
            get_persona_registry,
        )
        from polaris.kernelone.storage.persona_store import load_workspace_persona

        registry = get_persona_registry()
        persona_id = load_workspace_persona(".", list(registry.keys()))
        prompt = build_action_first_prompt(persona_id)

        assert "【行动优先】" in prompt
        assert "repo_tree" in prompt or "list_directory" in prompt
        assert "【EAFP强制】" in prompt
        assert "file_exists" not in prompt or "禁止" in prompt
        assert "【闭环交付】" in prompt

    def test_parser_extracts_action_block(self) -> None:
        """验证 Parser 正确提取 Action 块。"""
        from polaris.cells.roles.kernel.internal.output.action_parser import (
            extract_thinking_block,
            parse_action_block,
        )

        text = """<thinking>我需要先看看目录结构</thinking>
[Action]: repo_tree
[Arguments]: {"path": ".", "depth": 1}
[Status]: In Progress
[Marker]: None"""

        block = parse_action_block(text)
        assert block is not None
        assert block.tool_name == "repo_tree"
        assert block.arguments == {"path": ".", "depth": 1}

        thinking = extract_thinking_block(text)
        assert thinking is not None
        assert "目录结构" in thinking

    def test_error_recovery_loop(self) -> None:
        """验证错误恢复循环正常工作。"""
        from polaris.cells.roles.kernel.internal.error_recovery.context_injector import (
            ErrorContextInjector,
        )
        from polaris.cells.roles.kernel.internal.error_recovery.retry_policy import (
            RetryPolicy,
            ToolError,
        )

        policy = RetryPolicy()
        history: list[dict[str, str]] = []

        # Simulate tool error
        error = ToolError("read_file", "File not found: test.py", {"path": "test.py"})
        assert policy.should_retry(error, 0) is True

        # Inject error context
        new_history = ErrorContextInjector.inject_error_context(
            history, "read_file", "File not found: test.py", {"path": "test.py"}
        )
        assert len(new_history) == 1
        assert "File not found" in new_history[0]["content"]

    def test_marker_with_spaces(self) -> None:
        """验证 Marker 可以包含空格。"""
        from polaris.cells.roles.kernel.internal.output.action_parser import (
            parse_action_block,
        )

        text = """[Action]: edit_file
[Arguments]: {"file": "test.py"}
[Status]: Completed
[Marker]: Added by agent"""
        block = parse_action_block(text)
        assert block is not None
        assert block.marker == "Added by agent"
