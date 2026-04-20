"""Output Parser - 输出解析组件

负责解析LLM输出，包括：
- 思考过程提取
- 工具调用解析
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


@dataclass
class ThinkingResult:
    """思考过程解析结果"""

    thinking: str | None
    clean_content: str


# P0-002: ToolCallResult统一到canonical ToolCall
# 保留旧字段名(tool, args)用于向后兼容，但内部使用ToolCall
@dataclass
class ToolCallResult:
    """工具调用解析结果 (P0-002 统一到 canonical ToolCall)

    P2-018 Intent Separation:
        This class is for the PARSE phase only. Intentional separation from:
        - polaris.kernelone.benchmark.llm.tool_accuracy.ToolCallResult
            (Benchmark phase: has case_id, execution_time_ms, error)
        - polaris.kernelone.llm.contracts.tool.ToolExecutionResult
            (Execution phase: has tool_call_id, success, result, blocked)

    内部使用 canonical ToolCall，保留旧字段名(tool, args)用于向后兼容。
    所有新代码应直接使用 ToolCall。
    """

    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    _canonical: ToolCall | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        """初始化时创建canonical ToolCall"""
        if self._canonical is None:
            self._canonical = ToolCall(
                id=f"kernel_{self.tool}_{uuid.uuid4().hex[:8]}",
                name=str(self.tool or "").strip().lower(),
                arguments=dict(self.args) if isinstance(self.args, dict) else {},
                source="kernel_parser",
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
            _canonical=canonical,
        )
        return result


# Backward compatibility: ParsedToolCall also maps to ToolCall
ParsedToolCall = ToolCall


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
        return sanitized.strip()

    def parse_execution_tool_calls(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[ToolCallResult]:
        """Parse executable tool calls for role-kernel execution.

        Parsing order (fallback chain):
        1. Native tool calling protocol (OpenAI/Anthropic format)
        2. JSON tool call text (fallback for models returning JSON as text)

        Args:
            content: Raw text content from LLM output
            allowed_tool_names: Optional whitelist of allowed tool names
            native_tool_calls: Native tool calls from provider (OpenAI/Anthropic format)
            native_provider: Provider hint for parsing

        Returns:
            List of parsed tool calls
        """
        normalized: list[ToolCallResult] = []
        seen: set[tuple[str, str, str, str]] = set()
        allowed = {str(name).strip().lower() for name in (allowed_tool_names or []) if str(name).strip()}

        # Layer 1: Try native tool calling protocol
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
            )

        # Layer 2: Fallback to JSON tool call parsing
        # This handles cases where LLM returns tool calls as raw JSON text
        # instead of using native tool calling protocols
        if not native_calls and content:
            json_calls = self._parse_json_tool_calls(
                content=content,
                allowed_tool_names=allowed_tool_names,
            )
            for call in json_calls:
                self._append_unique_tool_call(
                    normalized,
                    seen,
                    tool_name=call.tool,
                    arguments=call.args,
                    allowed=allowed,
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

    def parse_tool_calls(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[ToolCallResult]:
        """统一工具调用解析入口，运行时只接受 native tool_calls。"""
        return self.parse_execution_tool_calls(
            content,
            allowed_tool_names=allowed_tool_names,
            native_tool_calls=native_tool_calls,
            native_provider=native_provider,
        )

    @staticmethod
    def _append_unique_tool_call(
        normalized: list[ToolCallResult],
        seen: set[tuple[str, str, str, str]],
        *,
        tool_name: str,
        arguments: dict[str, Any],
        allowed: set[str],
    ) -> None:
        name = str(tool_name or "").strip().lower()
        if not name:
            return
        if allowed and name not in allowed:
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
        normalized.append(ToolCallResult(tool=name, args=arguments))

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
                normalized.append(ToolCallResult(tool=name, args=arguments))
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

    def _parse_json_tool_calls(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCallResult]:
        """Parse tool calls from JSON text format.

        This is a fallback for cases where LLM returns tool calls as raw JSON
        text instead of using native tool calling protocols.

        Args:
            content: Text content that may contain JSON tool calls
            allowed_tool_names: Optional whitelist of allowed tool names

        Returns:
            List of parsed tool calls from JSON text
        """
        from polaris.kernelone.llm.toolkit.parsers.json_based import JSONToolParser

        normalized: list[ToolCallResult] = []
        try:
            parsed_calls = JSONToolParser.parse(
                str(content or ""),
                allowed_tool_names=allowed_tool_names,
            )
            for call in parsed_calls:
                name = str(getattr(call, "name", "")).strip().lower()
                arguments = getattr(call, "arguments", {})
                if not name or not re.match(r"^[a-z][a-z0-9_]{0,63}$", name):
                    continue
                if not isinstance(arguments, dict):
                    continue
                normalized.append(ToolCallResult(tool=name, args=arguments))
        except (RuntimeError, ValueError) as e:
            logger.debug(
                "JSON tool-call parsing failed: %s",
                e,
            )
        return normalized

    @staticmethod
    def _has_explicit_patch_markers(content: str) -> bool:
        text = str(content or "")
        lowered = text.lower()
        if not lowered.strip():
            return False

        if "patch_file" in lowered:
            return True
        if "delete_file" in lowered:
            return True
        if "<<<<<<< search" in lowered and ">>>>>>> replace" in lowered:
            return True
        if re.search(r"(?:^|\n)\s*search:?\s*\n", text, flags=re.IGNORECASE) and re.search(
            r"\n\s*replace:?\s*\n", text, flags=re.IGNORECASE
        ):
            return True
        # FILE/CREATE 协议要求出现 END FILE/END CREATE，避免误匹配工具参数 `file: ...`
        if re.search(r"(?:^|\n)\s*(?:file|create)\s*[:\s]+\S+", text, flags=re.IGNORECASE) and re.search(
            r"\n\s*end\s+(?:file|create)\s*(?:\n|$)", text, flags=re.IGNORECASE
        ):
            return True
        # 兼容无 END 的简写，但必须以协议头开头，避免匹配到 [read_file] 内部参数行。
        return bool(re.match(r"^\s*(?:patch_file|file|create|delete(?:_file)?)\b", text, flags=re.IGNORECASE))

    @staticmethod
    def _is_safe_relative_path(path: str) -> bool:
        token = str(path or "").strip().replace("\\", "/")
        if not token:
            return False
        if token.startswith("/") or token.startswith("\\"):
            return False
        if re.match(r"^[a-zA-Z]:[/\\]", token):
            return False
        if "\x00" in token:
            return False
        parts = [part for part in token.split("/") if part]
        return not any(part in {".", ".."} for part in parts)

    def _parse_patch_file_format(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCallResult]:
        """解析 PATCH_FILE 格式为工具调用

        委托统一解析器（application.unified_apply）处理 PATCH_FILE / SEARCH-REPLACE
        的所有兼容方言，避免此处与应用层解析规则漂移。
        """
        results: list[ToolCallResult] = []
        allowed = {str(name).strip().lower() for name in (allowed_tool_names or []) if str(name).strip()}
        try:
            from polaris.cells.director.execution.public.service import (
                EditType,
                parse_full_file_blocks,
                parse_search_replace_blocks,
            )
        except (RuntimeError, ValueError) as exc:
            logger.debug(f"无法加载 unified_apply 解析器: {exc}")
            return results

        operations = parse_search_replace_blocks(content)
        operations.extend(parse_full_file_blocks(content))

        for operation in operations:
            path = str(getattr(operation, "path", "") or "").strip()
            if not self._is_safe_relative_path(path):
                continue

            edit_type = getattr(operation, "edit_type", None)
            search = str(getattr(operation, "search", "") or "")
            replace = str(getattr(operation, "replace", "") or "")

            if edit_type == EditType.SEARCH_REPLACE:
                if search.strip():
                    tool_name = "edit_file"
                    args = {
                        "file": path,
                        "search": search,
                        "replace": replace,
                    }
                else:
                    tool_name = "write_file"
                    args = {
                        "file": path,
                        "content": replace,
                    }
            elif edit_type in {EditType.FULL_FILE, EditType.CREATE}:
                tool_name = "write_file"
                args = {
                    "file": path,
                    "content": replace,
                }
            else:
                continue

            if allowed and tool_name not in allowed:
                continue
            results.append(ToolCallResult(tool=tool_name, args=args))

        return results

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

        优先委托 unified_apply 的统一协议解析器，避免与执行层规则漂移。
        若统一解析器不可用或未命中，再回退到 legacy 正则提取。

        Args:
            content: 包含补丁的文本

        Returns:
            补丁列表，每个补丁包含search和replace（若可提取则包含file）
        """
        try:
            from polaris.cells.director.execution.public.service import parse_search_replace_blocks

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
                    patch["file"] = file_path
                patches.append(patch)

            if patches:
                return patches
        except (RuntimeError, ValueError) as exc:
            logger.debug(f"统一SEARCH/REPLACE解析失败，回退legacy正则: {exc}")

        # legacy 回退：兼容无 FILE/PATCH_FILE 包装的旧输出
        # DEPRECATED: 此正则回退路径仅用于保持向后兼容，将在 v2.0 中移除。
        # 新输出必须使用 canonical patch/search-replace envelope。
        logger.warning(
            "Legacy regex SEARCH/REPLACE fallback triggered — "
            "output is not using canonical patch envelope. "
            "This fallback is deprecated and will be removed in v2.0. "
            "Please emit PATCH_FILE or canonical SEARCH-REPLACE blocks instead."
        )
        pattern = r"<<<<<<< SEARCH\s*(.*?)\s*=======\s*(.*?)\s*>>>>>>> REPLACE"
        matches = re.findall(pattern, str(content or ""), re.DOTALL)
        if matches:
            return [{"search": s.strip(), "replace": r.strip()} for s, r in matches]

        return None

    def extract_edit_blocks(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ToolCallResult]:
        """提取 SEARCH/REPLACE 编辑块并转换为工具调用。

        从 LLM 输出中提取 edit_blocks 格式的编辑块，并转换为 ToolCallResult 列表。
        支持从自然语言中连续提取多个编辑块。

        Args:
            content: 包含编辑块的文本
            allowed_tool_names: 可选的工具白名单

        Returns:
            ToolCallResult 列表
        """
        from polaris.kernelone.editing.editblock_engine import parse_edit_blocks

        results: list[ToolCallResult] = []
        allowed = {str(name).strip().lower() for name in (allowed_tool_names or []) if str(name).strip()}

        if not content or not content.strip():
            return results

        try:
            blocks = parse_edit_blocks(content)
        except Exception as e:
            logger.debug("Failed to parse edit blocks: %s", e)
            return results

        if not blocks:
            return results

        for block in blocks:
            if not block.filepath:
                continue

            # 安全检查
            if not self._is_safe_relative_path(block.filepath):
                logger.warning("Unsafe path in edit block: %s", block.filepath)
                continue

            tool_name = "edit_blocks"
            if allowed and tool_name not in allowed:
                continue

            args = {
                "file": block.filepath,
                "blocks": f"<<<< SEARCH:{block.filepath}\n{block.search_text}====\n{block.replace_text}>>>> REPLACE",
            }

            results.append(ToolCallResult(tool=tool_name, args=args))

        return results

    def parse_with_edit_blocks(
        self,
        content: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
        native_tool_calls: list[dict[str, Any]] | None = None,
        native_provider: str = "auto",
    ) -> list[ToolCallResult]:
        """综合解析工具调用，包括 edit_blocks。

        解析顺序（回退链）：
        1. Native tool calling protocol (OpenAI/Anthropic format)
        2. JSON tool call text
        3. edit_blocks (SEARCH/REPLACE format)

        Args:
            content: Raw text content from LLM output
            allowed_tool_names: Optional whitelist of allowed tool names
            native_tool_calls: Native tool calls from provider
            native_provider: Provider hint for parsing

        Returns:
            List of parsed tool calls
        """
        # 首先尝试标准解析
        results = self.parse_execution_tool_calls(
            content,
            allowed_tool_names=allowed_tool_names,
            native_tool_calls=native_tool_calls,
            native_provider=native_provider,
        )

        if results:
            return results

        # 尝试 edit_blocks 解析
        block_results = self.extract_edit_blocks(content, allowed_tool_names=allowed_tool_names)
        if block_results:
            return block_results

        return []

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
