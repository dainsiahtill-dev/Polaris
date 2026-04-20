from __future__ import annotations

from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)


class WriteToolPhases:
    """写工具三阶段语义：Prepare -> Validate -> Commit.

    Prepare 和 Validate 可以 speculative 执行；
    Commit 必须由 authoritative 路径执行，并生成 effect_receipt。
    """

    @classmethod
    def is_write_tool(cls, tool_name: str) -> bool:
        """判断工具是否为写工具."""
        from polaris.cells.roles.kernel.internal.transaction.constants import WRITE_TOOLS

        normalized = tool_name.strip().lower().replace("-", "_")
        return normalized in WRITE_TOOLS

    @classmethod
    def build_prepare_invocation(cls, invocation: ToolInvocation) -> ToolInvocation:
        """从原始写工具调用构建 Prepare 阶段的只读校验调用.

        当前实现：将 write_file/apply_patch 等映射为只读的 file_exists + content_schema 校验。
        如果工具原生支持 dry_run，优先使用 dry_run=True 模式(未来扩展点)。
        """
        args = dict(invocation.arguments)
        # 使用 read_file 作为路径存在性和上下文的只读校验
        # 保留原始调用中的 path 参数用于校验
        prepare_args: dict[str, object] = {}
        if "path" in args:
            prepare_args["path"] = args["path"]
        if "content" in args:
            # 将 content 长度作为 schema 校验的一个信号,但不实际写入
            content = args["content"]
            prepare_args["content_length"] = len(content) if isinstance(content, str) else 0

        return ToolInvocation(
            call_id=ToolCallId(f"prepare_{invocation.call_id}"),
            tool_name="file_exists",
            arguments=prepare_args,
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )

    @classmethod
    def build_validate_invocation(cls, invocation: ToolInvocation) -> ToolInvocation:
        """构建 Validate 阶段的只读校验调用.

        Validate 是可选阶段，用于检查 prepare 输出中的语法/schema 错误。
        """
        args = dict(invocation.arguments)
        validate_args: dict[str, object] = {}
        if "path" in args:
            validate_args["path"] = args["path"]
        if "content" in args:
            content = args["content"]
            validate_args["validate_content"] = True
            validate_args["content_length"] = len(content) if isinstance(content, str) else 0

        return ToolInvocation(
            call_id=ToolCallId(f"validate_{invocation.call_id}"),
            tool_name="file_exists",
            arguments=validate_args,
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )

    @classmethod
    def build_commit_invocation(cls, invocation: ToolInvocation) -> ToolInvocation:
        """构建 Commit 阶段的原始写工具调用(authoritative only).

        Commit 必须走 serial_writes 的 authoritative 路径，不可 speculative。
        """
        return ToolInvocation(
            call_id=invocation.call_id,
            tool_name=invocation.tool_name,
            arguments=dict(invocation.arguments),
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
        )
