"""PATCH_FILE 解析和执行

包含 PATCH_FILE 格式解析、协议操作应用、输出验证等执行逻辑。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

from polaris.kernelone.events.file_event_broadcaster import (
    broadcast_file_written,
    calculate_patch,
)

from .execution_tools import DirectorToolExecutor
from .helpers import (
    extract_kernel_tool_results,
    looks_like_protocol_patch_response,
)

logger = logging.getLogger(__name__)

_STRUCTURAL_CONFIG_FILE_NAMES = {
    "package.json",
    "tsconfig.json",
    "jsconfig.json",
    "pyproject.toml",
    "cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "makefile",
    "cmakelists.txt",
    "dockerfile",
}
_STRUCTURAL_CONFIG_SUFFIXES = (
    ".config.js",
    ".config.cjs",
    ".config.mjs",
    ".config.ts",
    ".config.mts",
    ".config.cts",
    ".toml",
    ".yaml",
    ".yml",
)
_GENERIC_ENTRYPOINT_STEMS = {"main", "index", "app", "server", "cli"}
_CODE_ENTRYPOINT_SUFFIXES = {
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".py",
    ".go",
    ".rs",
    ".cpp",
    ".cc",
    ".cxx",
    ".c",
    ".h",
    ".hpp",
    ".java",
}
_ENTRYPOINT_CONTENT_SIGNALS = (
    "import ",
    "export ",
    "function ",
    "class ",
    "def ",
    "package ",
    "fn ",
    "#include",
    "public class",
    "const ",
    "let ",
    "var ",
)


class DirectorPatchExecutor:
    """Director PATCH 文件执行器。

    提供 PATCH_FILE 格式解析、协议操作应用、输出验证等功能。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self._message_bus: Any | None = None
        self._worker_id = "director"
        self._tool_executor = DirectorToolExecutor(workspace)

    def set_message_bus(self, message_bus: Any | None) -> None:
        self._message_bus = message_bus
        self._tool_executor.set_message_bus(message_bus)

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
        raw_candidates.append(os.environ.get("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS"))
        raw_candidates.append(os.environ.get("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"))

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

    @staticmethod
    def resolve_direct_fallback_timeout_seconds(
        context: dict[str, Any] | None,
        primary_timeout_seconds: float,
    ) -> float:
        """Resolve bounded timeout for direct text-patch fallback calls."""
        raw_candidates: list[Any] = []
        if isinstance(context, dict):
            raw_candidates.append(context.get("direct_fallback_timeout_seconds"))
            raw_candidates.append(context.get("director_direct_fallback_timeout_seconds"))
        raw_candidates.append(os.environ.get("KERNELONE_DIRECTOR_DIRECT_FALLBACK_TIMEOUT_SECONDS"))

        primary_timeout = max(0.1, float(primary_timeout_seconds or 0.0))
        for raw in raw_candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            return max(0.1, min(value, primary_timeout, 300.0))

        return max(0.1, min(primary_timeout, 60.0))

    # -------------------------------------------------------------------------
    # Tool Execution (delegated to DirectorToolExecutor)
    # -------------------------------------------------------------------------

    @staticmethod
    def extract_kernel_tool_results(role_response: dict[str, Any]) -> list[dict[str, Any]]:
        """Extract normalized tool results from a role-kernel response."""
        return extract_kernel_tool_results(role_response)

    async def execute_tools(
        self,
        response: str,
        task_id: str,
        update_task_progress_fn: Any,
        *,
        allowed_tool_names: set[str] | None = None,
        allow_patch_fallback: bool = True,
    ) -> list[dict[str, Any]]:
        """解析并执行工具调用

        支持两种格式:
        1. [工具名]...[/工具名] 格式 (通过 parse_tool_calls)
        2. PATCH_FILE 格式 (通过 parse_file_blocks)
        """
        from polaris.kernelone.llm.toolkit import parse_tool_calls

        tool_calls = parse_tool_calls(
            response,
            allowed_tool_names=allowed_tool_names
            if allowed_tool_names is not None
            else {
                "write_file",
                "read_file",
                "edit_file",
                "execute_command",
                "run_command",
                "search_code",
            },
        )
        if not tool_calls:
            if not allow_patch_fallback:
                return []
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
            result = self._tool_executor.execute_tool(tool_name, args, task_id=task_id)
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
            path_error = self._validate_relative_patch_path(file_path)
            if path_error:
                results.append({"tool": "patch_apply", "success": False, "error": path_error})
                continue

            update_task_progress_fn(task_id, "executing", current_file=file_path)
            try:
                target = (workspace_path / file_path).resolve()
                if workspace_path not in target.parents and target != workspace_path:
                    raise RuntimeError(f"Unsafe patch path: {file_path}")
                existed_before = target.exists()
                old_content = ""
                if existed_before and target.is_file():
                    old_content = target.read_text(encoding="utf-8")
                outcome = applier.apply(operation, str(workspace_path))
            except (OSError, RuntimeError, TypeError, ValueError) as exc:
                results.append({"tool": "patch_apply", "success": False, "error": str(exc)})
                continue
            if outcome.success:
                changed = bool(getattr(outcome, "changed", False))
                operation_kind = "modify"
                bytes_written = 0
                broadcast_ok = False
                if changed:
                    if target.exists() and target.is_file():
                        new_content = target.read_text(encoding="utf-8")
                        operation_kind = "modify" if existed_before else "create"
                        bytes_written = len(new_content.encode("utf-8"))
                    else:
                        new_content = ""
                        operation_kind = "delete"
                    broadcast_ok = self._emit_realtime_file_change(
                        file_path=file_path,
                        operation=operation_kind,
                        old_content=old_content,
                        new_content=new_content,
                        task_id=task_id,
                    )
                edit_type = getattr(operation, "edit_type", None)
                if edit_type == EditType.SEARCH_REPLACE:
                    source_tool = "edit_file"
                elif edit_type == EditType.DELETE:
                    source_tool = "delete_file"
                else:
                    source_tool = "write_file"
                effect_receipt = {
                    "path": file_path,
                    "file": file_path,
                    "bytes_written": bytes_written,
                    "changed": changed,
                    "operation": operation_kind,
                    "broadcast_ok": broadcast_ok,
                }
                results.append(
                    {
                        "tool": "patch_apply",
                        "tool_name": "patch_apply",
                        "success": True,
                        "status": "success",
                        "file": file_path,
                        "path": file_path,
                        "effect_receipt": effect_receipt,
                        "result": {
                            "ok": True,
                            "source_tool": source_tool,
                            "file": file_path,
                            "bytes_written": bytes_written,
                            "changed": changed,
                            "operation": operation_kind,
                            "broadcast_ok": broadcast_ok,
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
        path_error = self._validate_relative_patch_path(file_path)
        if path_error:
            return {"tool": "patch_apply", "success": False, "error": path_error}
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
            if tool_name == "edit_file":
                tool_result = self._tool_executor.execute_tool(
                    "edit_file",
                    {"file": file_path, "search": search, "replace": replace},
                    task_id=task_id,
                )
            else:
                tool_result = self._tool_executor.execute_tool(
                    "write_file",
                    {"file": file_path, "content": new_content},
                    task_id=task_id,
                )
            if not bool(tool_result.get("ok")):
                raise RuntimeError(str(tool_result.get("error") or "Patch apply failed"))
            operation = str(tool_result.get("operation") or "modify")
            bytes_written = int(tool_result.get("bytes_written") or len(new_content.encode("utf-8")))
            effect_receipt = {
                "path": file_path,
                "file": file_path,
                "bytes_written": bytes_written,
                "changed": True,
                "operation": operation,
                "broadcast_ok": bool(tool_result.get("broadcast_ok")),
            }
            return {
                "tool": "patch_apply",
                "tool_name": "patch_apply",
                "success": True,
                "status": "success",
                "file": file_path,
                "path": file_path,
                "effect_receipt": effect_receipt,
                "result": {
                    "ok": True,
                    "source_tool": tool_name,
                    "file": file_path,
                    "bytes_written": bytes_written,
                    "operation": operation,
                    "broadcast_ok": bool(tool_result.get("broadcast_ok")),
                },
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"tool": "patch_apply", "success": False, "error": str(exc)}

    def _emit_realtime_file_change(
        self,
        *,
        file_path: str,
        operation: str,
        old_content: str,
        new_content: str,
        task_id: str,
    ) -> bool:
        """Broadcast a FILE_WRITTEN event for patch paths applied outside tool executor."""
        patch = calculate_patch(old_content, new_content)
        return broadcast_file_written(
            file_path=file_path,
            operation=operation,
            content_size=len(new_content.encode("utf-8")),
            task_id=task_id,
            patch=patch,
            message_bus=self._message_bus,
            worker_id=self._worker_id,
            event_log_workspace=self.workspace,
        )

    @staticmethod
    def _validate_relative_patch_path(file_path: str) -> str | None:
        """Return an error string when a generated patch path is unsafe."""
        token = str(file_path or "").strip().replace("\\", "/")
        if not token:
            return "Missing file path"
        if re.match(r"(?i)^(?:PATCH_FILE|END PATCH_FILE)(?:\s|:|$)", token):
            return f"Invalid patch path: {file_path}"
        if token.endswith(":"):
            return f"Invalid patch path: {file_path}"
        if any(ch in token for ch in ('"', "'", "`", "<", ">", "|", "\0")):
            return f"Invalid patch path: {file_path}"
        if re.search(r"^[a-zA-Z]:", token):
            return f"Absolute patch paths are not allowed: {file_path}"
        path = Path(token)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            return f"Unsafe patch path: {file_path}"
        if not Path(token).suffix and any(ch.isspace() for ch in token):
            return f"Invalid patch path: {file_path}"
        return None

    @staticmethod
    def _looks_like_unified_diff_content(content: str) -> bool:
        """Return true when markdown block content is a diff, not final file text."""
        token = str(content or "").lstrip()
        if not token:
            return False
        if token.startswith("@@") or token.startswith(("--- ", "+++ ", "diff --git ")):
            return True
        return bool(re.search(r"(?m)^@@\s", token) or re.search(r"(?m)^--- .*\n\+\+\+ ", token))

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
            if DirectorPatchExecutor._validate_relative_patch_path(file_path):
                continue
            if looks_like_protocol_patch_response(content):
                continue
            if DirectorPatchExecutor._looks_like_unified_diff_content(content):
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
        domain_hit = any(
            token in lowered for token in domain_tokens if token
        ) or self._has_structural_path_domain_signal(
            rel_path,
            lowered,
            domain_tokens,
        )
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

    @staticmethod
    def _has_structural_path_domain_signal(
        rel_path: str,
        lowered_content: str,
        domain_tokens: list[str],
    ) -> bool:
        """Allow path-based signal only for structural files.

        Business source files must carry the project/domain vocabulary in their
        content. Config files and generic entrypoints are often intentionally
        vocabulary-light, so their target path can be a weak domain signal.
        """
        lowered_path = rel_path.replace("\\", "/").lower()
        if not any(token and token in lowered_path for token in domain_tokens):
            return False
        path = Path(lowered_path)
        name = path.name
        suffix = path.suffix
        stem = path.stem
        if name in _STRUCTURAL_CONFIG_FILE_NAMES or name.endswith(_STRUCTURAL_CONFIG_SUFFIXES):
            return True
        if suffix in _CODE_ENTRYPOINT_SUFFIXES and stem in _GENERIC_ENTRYPOINT_STEMS:
            return any(signal in lowered_content for signal in _ENTRYPOINT_CONTENT_SIGNALS)
        return False

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
