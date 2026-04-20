"""PATCH_FILE 解析和执行

包含 PATCH_FILE 格式解析、协议操作应用、输出验证等执行逻辑。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.text_ops import write_text_atomic

from .execution_tools import DirectorToolExecutor
from .helpers import (
    looks_like_protocol_patch_response,
)

logger = logging.getLogger(__name__)


class DirectorPatchExecutor:
    """Director PATCH 文件执行器。

    提供 PATCH_FILE 格式解析、协议操作应用、输出验证等功能。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._tool_executor = DirectorToolExecutor(workspace)

    # -------------------------------------------------------------------------
    # LLM Timeout Resolution
    # -------------------------------------------------------------------------

    @staticmethod
    def resolve_llm_call_timeout_seconds(context: dict[str, Any] | None) -> float:
        """解析 LLM 调用超时时间"""
        from .helpers import _DEFAULT_LLM_CALL_TIMEOUT_SECONDS

        raw_candidates: list[Any] = []
        if isinstance(context, dict):
            raw_candidates.append(context.get("llm_call_timeout_seconds"))
        raw_candidates.append(os.environ.get("POLARIS_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS"))
        raw_candidates.append(os.environ.get("POLARIS_DIRECTOR_LLM_TIMEOUT_SECONDS"))

        for raw in raw_candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            return max(0.1, min(value, 900.0))
        return _DEFAULT_LLM_CALL_TIMEOUT_SECONDS

    # -------------------------------------------------------------------------
    # Tool Execution (delegated to DirectorToolExecutor)
    # -------------------------------------------------------------------------

    async def execute_tools(
        self,
        response: str,
        task_id: str,
        update_task_progress_fn: Any,
    ) -> list[dict[str, Any]]:
        """解析并执行工具调用

        支持两种格式:
        1. [工具名]...[/工具名] 格式 (通过 parse_tool_calls)
        2. PATCH_FILE 格式 (通过 parse_file_blocks)
        """
        from polaris.kernelone.llm.toolkit import parse_tool_calls

        tool_calls = parse_tool_calls(
            response,
            allowed_tool_names={
                "write_file",
                "read_file",
                "edit_file",
                "execute_command",
                "run_command",
                "search_code",
            },
        )
        if not tool_calls:
            return await self._execute_patch_file_format(response, task_id, update_task_progress_fn)

        results = []
        for call in tool_calls:
            result = await self._execute_single_tool_call(call, task_id, update_task_progress_fn)
            results.append(result)
        return results

    async def _execute_single_tool_call(
        self,
        call: Any,
        task_id: str,
        update_task_progress_fn: Any,
    ) -> dict[str, Any]:
        """执行单个解析后的工具调用"""
        from polaris.kernelone.llm.toolkit.tool_normalization import normalize_tool_name

        tool_name = normalize_tool_name(call.name.lower())
        args, args_error = self._normalize_tool_arguments(call.arguments)
        if args_error:
            return {"tool": tool_name, "success": False, "error": args_error}
        update_task_progress_fn(
            task_id,
            "executing",
            current_file=args.get("file", args.get("path", "")),
        )
        try:
            result = self._tool_executor.execute_tool(tool_name, args)
            return {"tool": tool_name, "success": result.get("ok", False), "result": result}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"tool": tool_name, "success": False, "error": str(exc)}

    @staticmethod
    def _normalize_tool_arguments(raw_args: Any) -> tuple[dict[str, Any], str | None]:
        """归一化工具参数"""
        if isinstance(raw_args, dict):
            return raw_args, None
        if isinstance(raw_args, list):
            if len(raw_args) == 1 and isinstance(raw_args[0], dict):
                return raw_args[0], None
            return {}, "Invalid tool arguments type: list"
        return {}, f"Invalid tool arguments type: {type(raw_args).__name__}"

    # -------------------------------------------------------------------------
    # PATCH_FILE Format Execution
    # -------------------------------------------------------------------------

    async def _execute_patch_file_format(
        self,
        response: str,
        task_id: str,
        update_task_progress_fn: Any,
    ) -> list[dict[str, Any]]:
        """执行 PATCH_FILE 格式的响应"""
        from polaris.cells.director.execution.public.service import validate_before_apply
        from polaris.kernelone.llm.toolkit import (
            StrictOperationApplier,
            parse_protocol_output,
        )

        workspace_path = Path(self.workspace).resolve()
        protocol_operations = parse_protocol_output(response)
        if protocol_operations:
            return self._apply_protocol_operations(
                protocol_operations,
                workspace_path=workspace_path,
                task_id=task_id,
                update_task_progress_fn=update_task_progress_fn,
                applier=StrictOperationApplier,
            )

        integrity = validate_before_apply(response, {})
        protocol_like_response = looks_like_protocol_patch_response(response)
        parse_errors = list(integrity.errors or []) if not integrity.is_valid else []
        if protocol_like_response:
            error_text = "; ".join(parse_errors[:3]) or "No valid patch format found"
            return [{"tool": "patch_apply", "success": False, "error": error_text}]

        results: list[dict[str, Any]] = []
        for patch in self._extract_markdown_file_blocks(response):
            result = self._apply_single_patch(
                patch,
                workspace_path,
                task_id,
                update_task_progress_fn,
            )
            results.append(result)
        return results

    def _apply_protocol_operations(
        self,
        operations: list[Any],
        *,
        workspace_path: Path,
        task_id: str,
        update_task_progress_fn: Any,
        applier: Any,
    ) -> list[dict[str, Any]]:
        """应用协议操作"""
        from polaris.kernelone.llm.toolkit import EditType

        results: list[dict[str, Any]] = []
        for operation in operations:
            file_path = str(getattr(operation, "path", "") or "").strip()
            if not file_path:
                results.append({"tool": "patch_apply", "success": False, "error": "Missing file path"})
                continue

            update_task_progress_fn(task_id, "executing", current_file=file_path)
            outcome = applier.apply(operation, str(workspace_path))
            if outcome.success:
                edit_type = getattr(operation, "edit_type", None)
                if edit_type == EditType.SEARCH_REPLACE:
                    source_tool = "edit_file"
                elif edit_type == EditType.DELETE:
                    source_tool = "delete_file"
                else:
                    source_tool = "write_file"
                bytes_written = 0
                replace_text = getattr(operation, "replace", None)
                if isinstance(replace_text, str):
                    bytes_written = len(replace_text.encode("utf-8"))
                results.append(
                    {
                        "tool": "patch_apply",
                        "success": True,
                        "result": {
                            "ok": True,
                            "source_tool": source_tool,
                            "file": file_path,
                            "bytes_written": bytes_written,
                            "changed": bool(getattr(outcome, "changed", False)),
                        },
                    }
                )
                continue

            results.append(
                {
                    "tool": "patch_apply",
                    "success": False,
                    "error": str(getattr(outcome, "error_message", "") or "Patch apply failed"),
                }
            )
        return results

    def _apply_single_patch(
        self,
        patch: dict[str, Any],
        workspace_path: Path,
        task_id: str,
        update_task_progress_fn: Any,
    ) -> dict[str, Any]:
        """应用单个补丁块"""
        file_path = str(patch.get("file") or "").strip()
        if not file_path:
            return {"tool": "patch_apply", "success": False, "error": "Missing file path"}
        update_task_progress_fn(task_id, "executing", current_file=file_path)
        try:
            target = (workspace_path / file_path).resolve()
            if workspace_path not in target.parents and target != workspace_path:
                raise RuntimeError(f"Unsafe patch path: {file_path}")
            target.parent.mkdir(parents=True, exist_ok=True)
            search = str(patch.get("search") or "")
            replace = str(patch.get("replace") or "")
            if target.exists():
                original_content = target.read_text(encoding="utf-8")
                if search:
                    if search not in original_content:
                        raise RuntimeError(f"PATCH SEARCH block not found in file: {file_path}")
                    new_content = original_content.replace(search, replace, 1)
                    tool_name = "edit_file"
                else:
                    new_content = replace
                    tool_name = "write_file"
            else:
                new_content = replace
                tool_name = "write_file"
            write_text_atomic(str(target), new_content, encoding="utf-8")
            return {
                "tool": "patch_apply",
                "success": True,
                "result": {
                    "ok": True,
                    "source_tool": tool_name,
                    "file": file_path,
                    "bytes_written": len(new_content.encode("utf-8")),
                },
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"tool": "patch_apply", "success": False, "error": str(exc)}

    @staticmethod
    def _extract_markdown_file_blocks(text: str) -> list[dict[str, Any]]:
        """从 Markdown 代码块中提取"文件名 + 内容"并映射为补丁。"""

        blocks: list[dict[str, Any]] = []
        if not text:
            return blocks

        pattern = re.compile(
            r"(?:^|\n)(?:#{1,6}\s*|[-*]\s*|)\s*([a-zA-Z0-9_./-]+\.[a-zA-Z0-9]+)\s*\n```[a-zA-Z0-9_-]*\n(.*?)\n```",
            re.DOTALL,
        )
        for match in pattern.finditer(text):
            file_path = str(match.group(1) or "").strip()
            content = str(match.group(2) or "")
            if not file_path:
                continue
            if looks_like_protocol_patch_response(content):
                continue
            blocks.append(
                {
                    "file": file_path,
                    "search": "",
                    "replace": content,
                }
            )
        return blocks

    # -------------------------------------------------------------------------
    # Output Validation
    # -------------------------------------------------------------------------

    def validate_generated_output(
        self,
        task: dict[str, Any],
        file_paths: list[str],
    ) -> str | None:
        """检查 Director 输出是否存在模板化/占位化迹象。"""
        from .helpers import _GENERIC_SCAFFOLD_MARKERS, _LOW_QUALITY_PATTERNS, _PATCH_RESIDUE_PATTERNS
        from .state_utils import extract_domain_tokens

        if not file_paths:
            return "Director output validation failed: no changed files to evaluate"
        workspace_path = Path(self.workspace).resolve()
        domain_tokens = extract_domain_tokens(task)
        matched_markers: list[str] = []
        domain_hit = False
        inspected = 0
        for rel_path in file_paths[:40]:
            safety = self._check_file_quality(
                rel_path,
                workspace_path,
                domain_tokens,
                _LOW_QUALITY_PATTERNS,
                _PATCH_RESIDUE_PATTERNS,
                _GENERIC_SCAFFOLD_MARKERS,
            )
            if isinstance(safety, str):
                return safety  # unsafe path error
            marker, hit, readable = safety
            if marker:
                matched_markers.append(marker)
            if hit:
                domain_hit = True
            if readable:
                inspected += 1
        if inspected == 0:
            return "Director output validation failed: changed files are unreadable or non-code"
        if matched_markers:
            return "Director output quality gate failed: generic/placeholder content detected: " + "; ".join(
                matched_markers[:6]
            )
        if domain_tokens and not domain_hit:
            return (
                "Director output quality gate failed: no project-domain signal found in changed files; "
                f"expected one of {domain_tokens[:6]}"
            )
        return None

    def _check_file_quality(
        self,
        rel_path: str,
        workspace_path: Path,
        domain_tokens: list[str],
        low_quality_patterns: tuple,
        patch_residue_patterns: tuple,
        generic_scaffold_markers: tuple,
    ) -> tuple[str | None, bool, bool] | str:
        """检查单个文件的质量问题

        Returns:
            (marker_or_None, domain_hit, readable) or error str.
        """
        target = (workspace_path / rel_path).resolve()
        if workspace_path not in target.parents and target != workspace_path:
            return f"Director output validation failed: unsafe path {rel_path}"
        if not target.exists() or not target.is_file():
            return None, False, False
        if target.stat().st_size > 512 * 1024:
            return None, False, False
        try:
            content = target.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            return None, False, False
        readable = bool(str(content or "").strip())
        lowered = content.lower()
        domain_hit = any(token in lowered for token in domain_tokens if token)
        for pattern in low_quality_patterns:
            if pattern.search(content):
                return f"{rel_path}:{pattern.pattern}", domain_hit, readable
        for pattern in patch_residue_patterns:
            if pattern.search(content):
                return f"{rel_path}:{pattern.pattern}", domain_hit, readable
        for marker in generic_scaffold_markers:
            if marker.lower() in lowered:
                return f"{rel_path}:{marker}", domain_hit, readable
        return None, domain_hit, readable

    # -------------------------------------------------------------------------
    # QA Check
    # -------------------------------------------------------------------------

    async def run_qa_check(
        self,
        task: dict[str, Any],
        director_output: str,
    ) -> dict[str, Any]:
        """运行 QA 检查"""
        # 这里应该调用 QA 适配器
        # 简化实现
        return {
            "passed": True,
            "note": "QA check simulated",
        }
