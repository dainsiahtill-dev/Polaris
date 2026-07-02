"""Output Parser - 输出解析组件

负责解析LLM输出，包括：
- 思考过程提取
- Native 工具调用解析
- JSON内容提取
- SEARCH/REPLACE块提取

P0-002: ToolCallResult 统一到 canonical ToolCall
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from polaris.cells.roles.kernel.internal.turn_engine.artifacts import (
    AssistantRawContent,
)

# Import canonical ToolCall for P0-002 unification
from polaris.kernelone.llm.contracts.tool import ToolCall

# Import canonical dangerous pattern detection
from polaris.kernelone.security.dangerous_patterns import (
    is_dangerous_command,
    is_path_traversal,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

    from polaris.cells.roles.profile.public.service import RoleProfile

logger = logging.getLogger(__name__)

_VISIBLE_PROTOCOL_OPEN_RE = re.compile(
    r"<(?:output|answer)\b[^>]*>",
    re.IGNORECASE,
)
_VISIBLE_PROTOCOL_CLOSE_RE = re.compile(
    r"</(?:output|answer)\s*>",
    re.IGNORECASE,
)
_TOOL_RESULT_BLOCK_RE = re.compile(
    r"<(?:tool_result|tool_results|function_result|function_results)\b[^>]*>.*?</(?:tool_result|tool_results|function_result|function_results)\s*>",
    re.IGNORECASE | re.DOTALL,
)


def _tool_name_match_keys(tool_name: str) -> set[str]:
    """Return raw and canonical forms for allow-list comparisons."""
    raw = str(tool_name or "").strip().lower()
    if not raw:
        return set()
    try:
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        canonical = str(normalize_tool_name(raw) or "").strip().lower()
    except (ImportError, RuntimeError, ValueError):
        canonical = raw
    return {item for item in (raw, canonical) if item}


def _normalize_allowed_tool_names(allowed_tool_names: Iterable[str] | None) -> set[str]:
    """Normalize allowed tool names while retaining provider-emitted raw aliases."""
    allowed: set[str] = set()
    for name in allowed_tool_names or []:
        allowed.update(_tool_name_match_keys(str(name or "")))
    return allowed


@dataclass
class ThinkingResult:
    """思考过程解析结果"""

    thinking: str | None
    clean_content: str


# P0-002: ToolCallResult统一到canonical ToolCall
# 保留 provider-facing 字段名(tool, args)用于兼容外部调用者，但内部使用ToolCall
@dataclass
class ToolCallResult:
    """工具调用解析结果 (P0-002 统一到 canonical ToolCall)

    P2-018 Intent Separation:
        This class is for the PARSE phase only. Intentional separation from:
        - polaris.kernelone.llm.contracts.tool.ToolExecutionResult
            (Execution phase: has tool_call_id, success, result, blocked)

    内部使用 canonical ToolCall，保留 provider-facing 字段名(tool, args)用于兼容外部调用者。
    所有新代码应直接使用 ToolCall。
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    source: str = "kernel_parser"
    _canonical: ToolCall | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """初始化时创建canonical ToolCall"""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_arguments, normalize_tool_name

        raw_tool_name = str(self.tool or "").strip().lower()
        raw_args = self.args if isinstance(self.args, dict) else {}
        resolved_name = normalize_tool_name(raw_tool_name)
        if resolved_name == "write_file" and raw_tool_name != "write_file":
            self.tool = resolved_name
            self.args = normalize_tool_arguments(resolved_name, raw_args)
        else:
            self.tool = raw_tool_name
            self.args = dict(raw_args)
        existing = self._canonical
        self._canonical = ToolCall(
            id=existing.id if existing is not None else f"kernel_{self.tool}_{uuid.uuid4().hex[:8]}",
            name=self.tool,
            arguments=dict(self.args),
            source=existing.source if existing is not None else str(self.source or "kernel_parser"),
            raw=existing.raw if existing is not None else "",
            parse_error=existing.parse_error if existing is not None else None,
        )

    @property
    def name(self) -> str:
        """Tool name (alias for tool, lowercase)"""
        return self.tool

    def to_canonical(self) -> ToolCall:
        """转换为canonical ToolCall"""
        # self._canonical is always set in __post_init__
        assert self._canonical is not None, "ToolCallResult not properly initialized"
        return self._canonical

    @classmethod
    def from_canonical(cls, canonical: ToolCall) -> ToolCallResult:
        """从canonical ToolCall创建"""
        result = cls(
            tool=canonical.name,
            args=dict(canonical.arguments),
            source=canonical.source,
            _canonical=canonical,
        )
        return result


class OutputParser:
    """输出解析器

    将输出解析逻辑从RoleExecutionKernel中提取出来，实现单一职责。
    """

    @staticmethod
    def strip_visible_protocol_wrappers(content: str) -> str:
        """Strip non-user-visible protocol wrappers while preserving inner text."""
        token = str(content or "")
        if not token.strip():
            return ""
        sanitized = _TOOL_RESULT_BLOCK_RE.sub("", token)
        sanitized = _VISIBLE_PROTOCOL_OPEN_RE.sub("", sanitized)
        sanitized = _VISIBLE_PROTOCOL_CLOSE_RE.sub("", sanitized)
        sanitized = re.sub(r"\n{3,}", "\n\n", sanitized)
        from polaris.cells.roles.kernel.internal.turn_engine.utils import (
            sanitize_assistant_transcript_message,
        )

        return sanitize_assistant_transcript_message(sanitized)

    def parse_execution_tool_calls(
        self,
        content: AssistantRawContent,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[ToolCallResult]:
        """Parse executable tool calls for role-kernel execution.

        Runtime execution is native-tool-only. Textual/JSON fallback parsing is
        intentionally excluded here so assistant prose cannot bypass provider
        tool-call accounting, ToolSpecRegistry normalization, and tool lifecycle
        receipts.

        Args:
            content: Typed raw assistant content from LLM output. Execution
                parsing accepts this explicit raw-content stage so sanitized
                transcript text cannot be accidentally reused as executable
                parser input.
            allowed_tool_names: Optional whitelist of allowed tool names
            native_tool_calls: Native tool calls from provider (OpenAI/Anthropic format)
            native_provider: Provider hint for parsing

        Returns:
            List of parsed tool calls
        """
        normalized: list[ToolCallResult] = []
        seen: set[tuple[str, str, str, str]] = set()
        allowed = _normalize_allowed_tool_names(allowed_tool_names)

        native_calls = self._parse_native_tool_calls(
            native_tool_calls=native_tool_calls,
            native_provider=native_provider,
            allowed_tool_names=allowed_tool_names,
        )
        for call in native_calls:
            self._append_unique_tool_call(
                normalized,
                seen,
                tool_name=call.tool,
                arguments=call.args,
                allowed=allowed,
                source=call.to_canonical().source,
            )

        return normalized

    def parse_thinking(self, content: str) -> ThinkingResult:
        """解析思考过程

        从内容中提取<thinking>标签包裹的思考过程。

        Args:
            content: 原始输出内容

        Returns:
            ThinkingResult: 思考过程和清理后的内容
        """
        token = str(content or "")
        thinking_pattern = r"<thinking>(.*?)</thinking\s*>"
        match = re.search(thinking_pattern, token, re.DOTALL)

        if match:
            thinking = match.group(1).strip()
            clean_content = self.strip_visible_protocol_wrappers(re.sub(thinking_pattern, "", token, flags=re.DOTALL))
            return ThinkingResult(thinking=thinking, clean_content=clean_content)

        # 容错：部分模型会输出不完整结束标签 "</thinking"（缺少 ">"
        # 或被传输截断）。这类输出仍应被视为 thinking，不应泄漏到可见正文。
        open_tag = "<thinking>"
        open_idx = token.find(open_tag)
        if open_idx >= 0:
            head = token[:open_idx]
            remainder = token[open_idx + len(open_tag) :]
            close_marker = "</thinking"
            close_idx = remainder.find(close_marker)
            if close_idx >= 0:
                thinking = remainder[:close_idx].strip()
                tail_idx = close_idx + len(close_marker)
                while tail_idx < len(remainder) and remainder[tail_idx].isspace():
                    tail_idx += 1
                if tail_idx < len(remainder) and remainder[tail_idx] == ">":
                    tail_idx += 1
                tail = remainder[tail_idx:]
                clean_content = self.strip_visible_protocol_wrappers(head + tail)
                return ThinkingResult(thinking=thinking or None, clean_content=clean_content)

            # 未闭合标签：将 opening tag 后续内容视为 thinking。
            thinking = remainder.strip()
            return ThinkingResult(
                thinking=thinking or None,
                clean_content=self.strip_visible_protocol_wrappers(head),
            )

        return ThinkingResult(
            thinking=None,
            clean_content=self.strip_visible_protocol_wrappers(token),
        )

    @staticmethod
    def _append_unique_tool_call(
        normalized: list[ToolCallResult],
        seen: set[tuple[str, str, str, str]],
        *,
        tool_name: str,
        arguments: dict[str, Any],
        allowed: set[str],
        source: str = "kernel_parser",
    ) -> None:
        name = str(tool_name or "").strip().lower()
        if not name:
            return
        if allowed and not (_tool_name_match_keys(name) & allowed):
            return
        if not isinstance(arguments, dict):
            return
        signature = (
            name,
            str(arguments.get("file") or arguments.get("path") or ""),
            str(arguments.get("search") or ""),
            str(arguments.get("replace") or arguments.get("content") or ""),
        )
        if signature in seen:
            return
        seen.add(signature)
        normalized.append(ToolCallResult(tool=name, args=arguments, source=source))

    def _parse_native_tool_calls(
        self,
        *,
        native_tool_calls: list[dict[str, Any]] | None,
        native_provider: str,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCallResult]:
        if not native_tool_calls:
            return []
        normalized: list[ToolCallResult] = []
        try:
            from polaris.infrastructure.llm.tools import LLMToolkitParserAdapter

            parser = LLMToolkitParserAdapter()
            parsed_calls = parser.parse_calls(
                text="",
                native_tool_calls=native_tool_calls or [],
                provider_hint=str(native_provider or "auto").strip().lower() or "auto",
                allowed_tool_names=allowed_tool_names,
            )
            for call in parsed_calls:
                name = str(getattr(call, "name", "")).strip().lower()
                arguments = getattr(call, "arguments", {})
                if not name or not re.match(r"^[a-z][a-z0-9_]{0,63}$", name):
                    continue
                if not isinstance(arguments, dict):
                    continue
                normalized.append(ToolCallResult(tool=name, args=arguments, source="native_tool_call"))
        except ImportError as e:
            logger.warning(
                "LLMToolkitParserAdapter not available, native tool calls will not be parsed: %s",
                e,
            )
        except (RuntimeError, ValueError) as e:
            logger.warning(
                "Native tool-call parsing failed (provider=%s, calls=%d): %s",
                native_provider,
                len(native_tool_calls or []),
                e,
            )
            logger.debug("Native tool-call parsing traceback:", exc_info=True)
        return normalized

    def parse_structured_output(
        self, content: str, profile: RoleProfile
    ) -> dict[str, Any] | list[dict[str, str]] | None:
        """解析结构化输出

        根据角色配置的输出格式解析内容。

        Args:
            content: 输出内容
            profile: 角色配置

        Returns:
            解析后的结构化数据
        """
        output_format = profile.prompt_policy.output_format

        if output_format == "json":
            return self.extract_json(content)
        elif output_format == "search_replace":
            return self.extract_search_replace(content)

        return None

    def extract_json(self, content: str) -> dict[str, Any] | None:
        """提取JSON内容

        从文本中提取JSON对象，支持代码块和<output>标签。

        Args:
            content: 包含JSON的文本

        Returns:
            解析后的字典或None
        """
        # 尝试匹配 ```json ... ``` 与 '''json ... ''' 代码块
        # 同时兼容 ``` json 与 ''' json 的空格写法。
        json_pattern = re.compile(
            r"(?P<fence>```|''')(?:\s*json)?\s*(?P<body>.*?)(?P=fence)",
            re.DOTALL | re.IGNORECASE,
        )
        for match in json_pattern.finditer(content):
            try:
                return json.loads(str(match.group("body") or "").strip())
            except json.JSONDecodeError:
                continue

        # 尝试匹配 <output>...</output>
        output_pattern = r"<output>(.*?)</output>"
        output_match: re.Match[str] | None = re.search(output_pattern, content, re.DOTALL)
        if output_match:
            try:
                return json.loads(output_match.group(1).strip())
            except json.JSONDecodeError:
                pass

        return None

    def extract_search_replace(self, content: str) -> list[dict[str, str]] | None:
        """提取SEARCH/REPLACE块

        委托 KernelOne/Director 协议解析器，避免与执行层规则漂移。
        无文件路径的 SEARCH/REPLACE 文本不会被提取为结构化补丁。

        Args:
            content: 包含补丁的文本

        Returns:
            补丁列表，每个补丁包含search和replace（若可提取则包含file）
        """
        try:
            from polaris.cells.director.execution.public.service import parse_search_replace_blocks
            from polaris.kernelone.editing.editblock_engine import _is_safe_relative_path

            operations = parse_search_replace_blocks(str(content or ""))
            patches: list[dict[str, str]] = []
            for operation in operations:
                search = str(getattr(operation, "search", "") or "")
                replace = str(getattr(operation, "replace", "") or "")
                patch: dict[str, str] = {
                    "search": search,
                    "replace": replace,
                }
                file_path = str(getattr(operation, "path", "") or "").strip()
                if file_path:
                    if not _is_safe_relative_path(file_path):
                        continue
                    patch["file"] = file_path
                patches.append(patch)

            if patches:
                return patches
        except (RuntimeError, ValueError) as exc:
            logger.debug("Unified SEARCH/REPLACE parsing failed: %s", exc)

        return None

    def check_security(self, content: str) -> tuple[bool, list[str]]:
        """安全检查

        检查内容中是否包含危险模式。

        Args:
            content: 要检查的内容

        Returns:
            (是否安全, 发现的问题列表)
        """
        issues = []
        if is_path_traversal(content):
            issues.append("发现路径穿越模式")
        if is_dangerous_command(content):
            issues.append("发现危险命令模式")

        return len(issues) == 0, issues
