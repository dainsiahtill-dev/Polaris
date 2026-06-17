from __future__ import annotations

from polaris.cells.roles.kernel.public.turn_contracts import (
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
)

# Sentinel tool names for the synthetic prepare/validate invocations. These are
# NOT in any registered tool_spec — the resolver uses them only to key the
# speculation shadow registry. Using a registered tool name (e.g. "file_exists")
# here would let a real model-emitted file_exists call collide in the spec_key
# hash and either satisfy the prepare shadow or block a legitimate read.
# The double-underscore prefix marks them as internal-synthetic.
_PREPARE_SHADOW_TOOL = "__prepare_shadow__"
_VALIDATE_SHADOW_TOOL = "__validate_shadow__"


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

        当前实现：构造一个非注册 sentinel 工具 (__prepare_shadow__) 的只读调用,
        携带 path/content_length 用于校验。sentinel 命名确保 spec_key 不会与
        真实 model-emitted 工具 (例如 file_exists) 冲突 — 见 §6.6 同等约束。
        如果工具原生支持 dry_run, 优先使用 dry_run=True 模式(未来扩展点)。
        """
        args = dict(invocation.arguments)
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
            tool_name=_PREPARE_SHADOW_TOOL,
            arguments=prepare_args,
            effect_type=ToolEffectType.READ,
            execution_mode=ToolExecutionMode.READONLY_PARALLEL,
        )

    @classmethod
    def build_validate_invocation(cls, invocation: ToolInvocation) -> ToolInvocation:
        """构建 Validate 阶段的只读校验调用.

        Validate 是可选阶段，用于检查 prepare 输出中的语法/schema 错误。
        使用独立的 sentinel 工具名 __validate_shadow__, 与 prepare 隔离.
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
            tool_name=_VALIDATE_SHADOW_TOOL,
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
