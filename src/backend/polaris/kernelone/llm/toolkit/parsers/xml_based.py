"""XML-based tool parser.

Parses tool calls from XML formats used by MiniMax, Claude, Llama, etc.
"""

from __future__ import annotations

import html
import json
import logging
import re
from typing import TYPE_CHECKING, Any

from polaris.kernelone.llm.toolkit.parsers.utils import (
    ParsedToolCall,
    _normalize_allowed_tool_names,
    parse_value,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


class XMLToolParser:
    """XML tool parser.

    Parses tool calls from XML formats used by various models:
    - MiniMax: <tool_call>...</tool_call>
    - Claude: <function_calls><invoke>...</invoke></function_calls>
    - Llama: <tool name="..."><param name="...">...</param></tool>
    - <function name="..."><arg>...</arg></function>
    - Qwen: <tool_call><name>...</name><arguments>...</arguments></tool_call>
    - ChatGLM: <tool_call name="...">...</tool_call>
    - Baichuan: <invoke name="..."><parameter name="...">...</parameter></invoke>
    - Generic: <tool_call name="...">...</tool_call>
    """

    # MiniMax tool call: <tool_call> or <minimax:tool_call>
    MINIMAX_TOOL_PATTERN = re.compile(
        r"<(?:minimax:)?tool_call[^>]*>(.*?)</(?:minimax:)?tool_call>", re.DOTALL | re.IGNORECASE
    )

    # Standard function_calls format
    FUNCTION_CALLS_PATTERN = re.compile(r"<function_calls?[^>]*>(.*?)</function_calls?>", re.DOTALL | re.IGNORECASE)

    # Attribute-style tool: <tool name="...">...</tool>
    TOOL_WITH_ATTR_PATTERN = re.compile(r'<tool\s+name=["\'](\w+)["\'][^>]*>(.*?)</tool>', re.DOTALL | re.IGNORECASE)

    # Function format: <function name="...">...</function>
    FUNCTION_PATTERN = re.compile(r'<function\s+name=["\'](\w+)["\'][^>]*>(.*?)</function>', re.DOTALL | re.IGNORECASE)

    # Invoke block pattern
    INVOKE_PATTERN = re.compile(r"<invoke[^>]*>(.*?)</invoke>", re.DOTALL | re.IGNORECASE)

    # Qwen format: <tool_call><name>...</name><arguments>...</arguments></tool_call>
    QWEN_TOOL_PATTERN = re.compile(r"<tool_call[^>]*>(.*?)</tool_call>", re.DOTALL | re.IGNORECASE)
    QWEN_NAME_PATTERN = re.compile(r"<name[^>]*>(.*?)</name>", re.DOTALL | re.IGNORECASE)
    QWEN_ARGS_PATTERN = re.compile(r"<arguments[^>]*>(.*?)</arguments>", re.DOTALL | re.IGNORECASE)

    # ChatGLM format: <tool_call name="...">...</tool_call>
    CHATGLM_TOOL_PATTERN = re.compile(
        r'<tool_call\s+name=["\'](\w+)["\'][^>]*>(.*?)</tool_call>', re.DOTALL | re.IGNORECASE
    )

    # Baichuan format: <invoke name="..."><parameter name="...">...</parameter></invoke>
    BAICHUAN_INVOKE_PATTERN = re.compile(
        r'<invoke\s+name=["\'](\w+)["\'][^>]*>(.*?)</invoke>', re.DOTALL | re.IGNORECASE
    )
    BAICHUAN_PARAM_PATTERN = re.compile(
        r'<parameter\s+name=["\'](\w+)["\'][^>]*>(.*?)</parameter>', re.DOTALL | re.IGNORECASE
    )

    # Param with name attribute
    PARAM_WITH_NAME_PATTERN = re.compile(r'<param\s+name=["\'](\w+)["\'][^>]*>(.*?)</param>', re.DOTALL | re.IGNORECASE)

    @classmethod
    def parse(
        cls,
        text: str,
        *,
        allowed_tool_names: Iterable[str] | None = None,
    ) -> list[ParsedToolCall]:
        """Parse XML format tool calls.

        Args:
            text: Text containing XML tool calls
            allowed_tool_names: Optional whitelist

        Returns:
            List of parsed tool calls
        """
        tools: list[ParsedToolCall] = []
        allowed = _normalize_allowed_tool_names(allowed_tool_names)
        counter = 0

        # 1. Parse MiniMax format
        for match in cls.MINIMAX_TOOL_PATTERN.finditer(text):
            content = html.unescape(match.group(1).strip())
            parsed = cls._parse_minimax_content(content, match.group(0), allowed)
            if parsed is not None:
                counter += 1
                result = cls._with_id(parsed, f"xml_minimax_{counter}")
                if result is not None:
                    tools.append(result)

        # 2. Parse function_calls format
        for match in cls.FUNCTION_CALLS_PATTERN.finditer(text):
            content = match.group(1)
            for invoke_match in cls.INVOKE_PATTERN.finditer(content):
                parsed = cls._parse_invoke_block(invoke_match.group(1), match.group(0), allowed)
                if parsed is not None:
                    counter += 1
                    result = cls._with_id(parsed, f"xml_invoke_{counter}")
                    if result is not None:
                        tools.append(result)

        # 3. Parse <tool name="...">
        for match in cls.TOOL_WITH_ATTR_PATTERN.finditer(text):
            tool_name = match.group(1).lower()
            if allowed and tool_name not in allowed:
                continue
            content = match.group(2)
            arguments = cls._parse_xml_params(content)
            counter += 1
            tools.append(
                ParsedToolCall(
                    id=f"xml_tool_{counter}",
                    name=tool_name,
                    arguments=arguments,
                    raw=match.group(0),
                )
            )

        # 4. Parse <function name="...">
        for match in cls.FUNCTION_PATTERN.finditer(text):
            tool_name = match.group(1).lower()
            if allowed and tool_name not in allowed:
                continue
            content = match.group(2)
            arguments = cls._parse_xml_params(content)
            counter += 1
            tools.append(
                ParsedToolCall(
                    id=f"xml_func_{counter}",
                    name=tool_name,
                    arguments=arguments,
                    raw=match.group(0),
                )
            )

        # 5. Parse Qwen format: <tool_call><name>...</name><arguments>...</arguments></tool_call>
        for match in cls.QWEN_TOOL_PATTERN.finditer(text):
            content = html.unescape(match.group(1))
            name_match = cls.QWEN_NAME_PATTERN.search(content)
            args_match = cls.QWEN_ARGS_PATTERN.search(content)

            if name_match:
                tool_name = name_match.group(1).strip().lower()
                if tool_name and (not allowed or tool_name in allowed):
                    args_content = args_match.group(1) if args_match else "{}"
                    try:
                        arguments = json.loads(args_content)
                    except json.JSONDecodeError:
                        arguments = cls._parse_xml_params(args_content)

                    counter += 1
                    tools.append(
                        ParsedToolCall(
                            id=f"xml_qwen_{counter}",
                            name=tool_name,
                            arguments=arguments if isinstance(arguments, dict) else {},
                            raw=match.group(0),
                        )
                    )

        # 6. Parse ChatGLM format: <tool_call name="...">...</tool_call>
        for match in cls.CHATGLM_TOOL_PATTERN.finditer(text):
            tool_name = match.group(1).lower()
            if allowed and tool_name not in allowed:
                continue
            content = match.group(2)
            arguments = cls._parse_xml_params(content)
            counter += 1
            tools.append(
                ParsedToolCall(
                    id=f"xml_chatglm_{counter}",
                    name=tool_name,
                    arguments=arguments,
                    raw=match.group(0),
                )
            )

        # 7. Parse Baichuan format: <invoke name="..."><parameter name="...">...</parameter></invoke>
        for match in cls.BAICHUAN_INVOKE_PATTERN.finditer(text):
            tool_name = match.group(1).lower()
            if allowed and tool_name not in allowed:
                continue
            content = match.group(2)
            arguments = {}
            for param_match in cls.BAICHUAN_PARAM_PATTERN.finditer(content):
                key = param_match.group(1)
                value = parse_value(html.unescape(param_match.group(2).strip()))
                arguments[key] = value
            counter += 1
            tools.append(
                ParsedToolCall(
                    id=f"xml_baichuan_{counter}",
                    name=tool_name,
                    arguments=arguments,
                    raw=match.group(0),
                )
            )

        return tools

    @classmethod
    def _parse_minimax_content(
        cls,
        content: str,
        raw: str,
        allowed: set[str],
    ) -> ParsedToolCall | None:
        """Parse MiniMax tool call content."""
        # Try JSON parsing
        try:
            data = json.loads(content)
            if isinstance(data, dict):
                tool_name = str(data.get("tool") or data.get("name") or data.get("tool_name") or "").strip().lower()
                if not tool_name:
                    return None
                if allowed and tool_name not in allowed:
                    return None

                args: dict[str, Any] = {}
                for key in ("args", "arguments", "params", "parameters", "input"):
                    if key in data and isinstance(data[key], dict):
                        args.update(data[key])
                if not args:
                    for k, v in data.items():
                        if k not in ("tool", "name", "tool_name"):
                            args[k] = v

                return ParsedToolCall(
                    id="",
                    name=tool_name,
                    arguments=args,
                    raw=raw,
                )
        except json.JSONDecodeError:
            pass

        # Try nested XML parsing
        func_match = cls.FUNCTION_PATTERN.search(content)
        if func_match:
            tool_name = func_match.group(1).lower()
            if allowed and tool_name not in allowed:
                return None
            func_content = func_match.group(2)
            arguments = cls._parse_xml_params(func_content)
            return ParsedToolCall(
                id="",
                name=tool_name,
                arguments=arguments,
                raw=raw,
            )

        return None

    @classmethod
    def _parse_invoke_block(
        cls,
        content: str,
        raw: str,
        allowed: set[str],
    ) -> ParsedToolCall | None:
        """Parse <invoke> block content."""
        # Find tool_name
        tool_name_match = re.search(r"<tool_name[^>]*>(.*?)</tool_name>", content, re.DOTALL | re.IGNORECASE)
        if not tool_name_match:
            tool_name_match = re.search(
                r"<function_name[^>]*>(.*?)</function_name>", content, re.DOTALL | re.IGNORECASE
            )

        if not tool_name_match:
            return None

        tool_name = tool_name_match.group(1).strip().lower()
        if not tool_name:
            return None
        if allowed and tool_name not in allowed:
            return None

        # Parse arguments
        arguments: dict[str, Any] = {}

        # Try <parameters> block
        params_match = re.search(r"<parameters?[^>]*>(.*?)</parameters?>", content, re.DOTALL | re.IGNORECASE)
        if params_match:
            params_content = params_match.group(1)
            for param_match in cls.PARAM_WITH_NAME_PATTERN.finditer(params_content):
                key = param_match.group(1)
                value = parse_value(html.unescape(param_match.group(2).strip()))
                arguments[key] = value
            if not arguments:
                arguments = cls._parse_xml_params(params_content)
        else:
            arguments = cls._parse_xml_params(content)

        return ParsedToolCall(
            id="",
            name=tool_name,
            arguments=arguments,
            raw=raw,
        )

    @classmethod
    def _parse_xml_params(cls, content: str) -> dict[str, Any]:
        """Parse XML format parameters.

        Supports:
        - <param name="key">value</param>
        - <key>value</key>
        """
        arguments: dict[str, Any] = {}

        # Parse <param name="...">...</param>
        for match in cls.PARAM_WITH_NAME_PATTERN.finditer(content):
            key = match.group(1)
            value = parse_value(html.unescape(match.group(2).strip()))
            arguments[key] = value

        # Parse <key>value</key> direct elements
        simple_tag_pattern = re.compile(r"<(\w+)>([^<]*?)</\1>", re.DOTALL | re.IGNORECASE)
        for match in simple_tag_pattern.finditer(content):
            key = match.group(1).lower()
            if key in {"param", "invoke", "tool_name", "function_name", "parameters"}:
                continue
            value = parse_value(html.unescape(match.group(2).strip()))
            arguments[key] = value

        return arguments

    @staticmethod
    def _with_id(call: ParsedToolCall | None, id_prefix: str) -> ParsedToolCall | None:
        """Add ID to a tool call."""
        if call is None:
            return None
        return ParsedToolCall(
            id=id_prefix,
            name=call.name,
            arguments=call.arguments,
            raw=call.raw,
        )
