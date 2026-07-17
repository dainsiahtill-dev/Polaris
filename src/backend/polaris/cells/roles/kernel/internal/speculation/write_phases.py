from __future__ import annotations

import hashlib
from typing import Mapping

from polaris.cells.roles.kernel.internal.speculation.contracts import SyntheticShadowToolKeyV1
from polaris.cells.roles.kernel.public.turn_contracts import (
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
_PREPARE_SHADOW_EXECUTION_TOOL = "file_exists"
_CONTENT_DIGEST_DOMAIN = b"polaris.synthetic_shadow.write_content.v1\x00utf8\x00"


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
    def build_prepare_invocation(cls, invocation: ToolInvocation) -> SyntheticShadowToolKeyV1:
        """Build a non-executable Prepare shadow key from a real invocation.

        当前实现：构造一个非注册 sentinel 工具 (__prepare_shadow__) 的只读调用,
        携带 path/content_length 用于校验。sentinel 命名确保 spec_key 不会与
        真实 model-emitted 工具 (例如 file_exists) 冲突 — 见 §6.6 同等约束。
        如果工具原生支持 dry_run, 优先使用 dry_run=True 模式(未来扩展点)。
        """
        return cls._build_write_shadow_key(
            source_tool_call_id=str(invocation.call_id),
            canonical_tool_name=_PREPARE_SHADOW_TOOL,
            arguments=invocation.arguments,
            mode="prepare",
        )

    @classmethod
    def build_validate_invocation(cls, invocation: ToolInvocation) -> SyntheticShadowToolKeyV1:
        """构建 Validate 阶段的只读校验调用.

        Validate 是可选阶段，用于检查 prepare 输出中的语法/schema 错误。
        使用独立的 sentinel 工具名 __validate_shadow__, 与 prepare 隔离.
        """
        return cls._build_write_shadow_key(
            source_tool_call_id=str(invocation.call_id),
            canonical_tool_name=_VALIDATE_SHADOW_TOOL,
            arguments=invocation.arguments,
            mode="validate",
        )

    @classmethod
    def build_prepare_shadow_key(
        cls,
        *,
        source_call_id: str,
        arguments: Mapping[str, object],
    ) -> SyntheticShadowToolKeyV1:
        """Build a prepare shadow key without materializing a ToolInvocation."""
        return cls._build_write_shadow_key(
            source_tool_call_id=source_call_id,
            canonical_tool_name=_PREPARE_SHADOW_TOOL,
            arguments=arguments,
            mode="prepare",
        )

    @classmethod
    def _build_write_shadow_key(
        cls,
        *,
        source_tool_call_id: str,
        canonical_tool_name: str,
        arguments: Mapping[str, object],
        mode: str,
    ) -> SyntheticShadowToolKeyV1:
        """Build the private semantic identity shared by start and resolve paths."""
        semantic_arguments: dict[str, object] = {
            "mode": mode,
            "validate_content": mode == "validate" and "content" in arguments,
            "content": cls._canonical_content_identity(arguments),
        }
        canonical_path = cls.prepare_shadow_normalized_args(arguments)
        if canonical_path:
            semantic_arguments["canonical_path"] = canonical_path

        return SyntheticShadowToolKeyV1.build(
            source_tool_call_id=source_tool_call_id,
            canonical_tool_name=canonical_tool_name,
            shadow_phase="write_phase",
            arguments=semantic_arguments,
        )

    @staticmethod
    def _canonical_content_identity(arguments: Mapping[str, object]) -> dict[str, object]:
        """Return content identity without retaining raw content in the shadow key."""
        if "content" not in arguments:
            return {"presence": "missing"}

        content = arguments["content"]
        if not isinstance(content, str):
            content_type = type(content)
            return {
                "presence": "present",
                "type": f"{content_type.__module__}.{content_type.__qualname__}",
                "content_length": 0,
            }

        content_digest, content_length = WriteToolPhases._canonical_utf8_content_digest(content)
        return {
            "presence": "present",
            "type": "utf8_text",
            "utf8_sha256": content_digest,
            "content_length": content_length,
        }

    @staticmethod
    def _canonical_utf8_content_digest(content: str) -> tuple[str, int]:
        """Digest newline-canonical UTF-8 content in O(n) time and O(1) extra space."""
        digest = hashlib.sha256(_CONTENT_DIGEST_DOMAIN)
        content_length = 0
        index = 0
        while index < len(content):
            character = content[index]
            if character == "\r":
                character = "\n"
                if index + 1 < len(content) and content[index + 1] == "\n":
                    index += 1
            digest.update(character.encode("utf-8", "surrogatepass"))
            content_length += 1
            index += 1
        return digest.hexdigest(), content_length

    @classmethod
    def prepare_shadow_normalized_args(cls, arguments: Mapping[str, object]) -> dict[str, object]:
        """Build execution-local, real read-probe arguments outside the shadow key."""
        from polaris.cells.roles.kernel.internal.speculation.fingerprints import normalize_args

        shadow_args: dict[str, object] = {}
        if "path" in arguments:
            shadow_args["path"] = arguments["path"]
        return normalize_args(_PREPARE_SHADOW_EXECUTION_TOOL, shadow_args)

    @classmethod
    def prepare_shadow_execution_tool_name(cls) -> str:
        """Return the real readonly probe used by the registry runner."""
        return _PREPARE_SHADOW_EXECUTION_TOOL

    @classmethod
    def build_commit_invocation(cls, invocation: ToolInvocation) -> ToolInvocation:
        """构建 Commit 阶段的原始写工具调用(authoritative only).

        Commit 必须走 serial_writes 的 authoritative 路径，不可 speculative。
        """
        return ToolInvocation(
            call_id=invocation.call_id,
            raw_tool_name=invocation.raw_tool_name,
            tool_name=invocation.tool_name,
            arguments=dict(invocation.arguments),
            effect_type=ToolEffectType.WRITE,
            execution_mode=ToolExecutionMode.WRITE_SERIAL,
            classification=invocation.classification,
        )
