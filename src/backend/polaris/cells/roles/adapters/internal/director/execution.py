"""PATCH_FILE 解析和执行

包含 PATCH_FILE 格式解析、协议操作应用、输出验证等执行逻辑。
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from polaris.cells.control_plane.run_ledger.public import FailureClassV1

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
_TEXTUAL_TOOL_PROTOCOL_PATTERN = re.compile(
    r"\[/?TOOL_CALLS?\]"
    r"|<\s*/?\s*tool_calls?\s*>"
    r"|\[(?:READ_FILE|WRITE_FILE|APPEND_TO_FILE|REPLACE_IN_FILE|LIST_FILES|RUN_COMMAND|"
    r"SEARCH_CODE|GLOB|LIST_DIRECTORY|FILE_EXISTS|EDIT_FILE|SEARCH_REPLACE|EXECUTE_COMMAND)\]",
    re.IGNORECASE,
)


class DirectorPatchExecutor:
    """Director PATCH 文件执行器。

    提供 PATCH_FILE 格式解析、协议操作应用、输出验证等功能。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def set_message_bus(self, message_bus: Any | None) -> None:
        """Retain the compatibility hook; text fallback owns no physical bus."""

        del message_bus

    # -------------------------------------------------------------------------
    # LLM Timeout Resolution
    # -------------------------------------------------------------------------

    @staticmethod
    def _llm_call_timeout_max_seconds() -> float:
        raw = os.environ.get("KERNELONE_DIRECTOR_LLM_TIMEOUT_MAX_SECONDS")
        try:
            value = float(str(raw).strip()) if raw is not None and str(raw).strip() else 1800.0
        except (TypeError, ValueError):
            value = 1800.0
        return max(900.0, value)

    @staticmethod
    def clamp_llm_call_timeout_to_factory_deadline(
        context: dict[str, Any] | None,
        timeout_seconds: float,
    ) -> float:
        """Apply the live Factory execution deadline as a non-expanding ceiling."""

        resolved_timeout = max(0.1, float(timeout_seconds))
        if not isinstance(context, dict):
            return resolved_timeout
        from polaris.cells.roles.kernel.internal.llm_caller.helpers import (
            _factory_execution_deadline_epochs,
            _select_viable_factory_deadline_epoch,
        )

        candidates = _factory_execution_deadline_epochs(context)
        if not candidates:
            return resolved_timeout
        now = time.time()
        selected = _select_viable_factory_deadline_epoch(
            context,
            now=now,
            minimum_remaining_seconds=0.0,
        )
        if selected is None:
            raise RuntimeError("factory_director_execution_deadline_exhausted")
        _source, deadline_epoch = selected
        remaining_seconds = deadline_epoch - now
        if remaining_seconds <= 0:
            raise RuntimeError("factory_director_execution_deadline_exhausted")
        return min(resolved_timeout, remaining_seconds)

    @staticmethod
    def resolve_llm_call_timeout_seconds(context: dict[str, Any] | None) -> float:
        """Resolve the call timeout under the live Factory execution deadline."""
        from .helpers import _DEFAULT_LLM_CALL_TIMEOUT_SECONDS

        timeout_max = DirectorPatchExecutor._llm_call_timeout_max_seconds()
        explicit_default = float(_DEFAULT_LLM_CALL_TIMEOUT_SECONDS)
        env_candidates = (
            os.environ.get("KERNELONE_DIRECTOR_LLM_CALL_TIMEOUT_SECONDS"),
            os.environ.get("KERNELONE_DIRECTOR_LLM_TIMEOUT_SECONDS"),
        )
        for raw in env_candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value > 0:
                explicit_default = max(0.1, min(value, timeout_max))
                break

        raw_candidates: list[Any] = []
        if isinstance(context, dict):
            raw_candidates.append(context.get("llm_call_timeout_seconds"))
            raw_candidates.append(context.get("director_llm_timeout_seconds"))
            raw_candidates.append(context.get("director_dispatch_timeout_seconds"))

        resolved_timeout = explicit_default
        for raw in raw_candidates:
            if raw is None:
                continue
            try:
                value = float(raw)
            except (TypeError, ValueError):
                continue
            if value <= 0:
                continue
            resolved_timeout = max(explicit_default, min(value, timeout_max))
            break

        # Factory admission owns an absolute execution deadline.  The child can
        # spend significant time in TaskRuntime claim/projection work before it
        # reaches the Provider boundary, so replaying the original relative
        # timeout here would let the physical request outlive its parent stage.
        # Clamp at the actual call start; an exhausted deadline is a control-
        # plane rejection and must not produce a Provider request.
        return DirectorPatchExecutor.clamp_llm_call_timeout_to_factory_deadline(context, resolved_timeout)

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
        """Reject non-native fallback formats with audit evidence.

        Native provider tool calls are handled before this adapter path. Textual
        tool protocols such as ``[TOOL_CALL]`` and ``[WRITE_FILE]`` are no
        longer executable because they bypass the tool lifecycle receipt chain.
        Markdown/PATCH_FILE patch fallbacks are also non-authoritative and must
        not write files. Repair attempts must enter native tool execution or
        the director.runtime repair kernel.
        """
        del allowed_tool_names  # Textual tool parsing is disabled in this path.
        del update_task_progress_fn  # Text patch execution is intentionally disabled.
        response_text = str(response or "")
        if _TEXTUAL_TOOL_PROTOCOL_PATTERN.search(response_text):
            error_code = "text_tool_protocol_disabled"
            return [
                {
                    "tool": "text_tool_protocol",
                    "success": False,
                    "ok": False,
                    "error": error_code,
                    "failure_class": FailureClassV1.TEXT_TOOL_PROTOCOL_DISABLED.value,
                    "protocol_violation": error_code,
                    "task_id": task_id,
                }
            ]
        if looks_like_protocol_patch_response(response_text) or self._extract_markdown_file_blocks(response_text):
            return [self._patch_file_protocol_disabled_result(task_id, allow_patch_fallback=allow_patch_fallback)]
        return []

    @staticmethod
    def _patch_file_protocol_disabled_result(
        task_id: str,
        *,
        allow_patch_fallback: bool,
    ) -> dict[str, Any]:
        error_code = "patch_file_protocol_disabled"
        return {
            "tool": "patch_apply",
            "tool_name": "patch_apply",
            "success": False,
            "ok": False,
            "status": "blocked",
            "error": error_code,
            "failure_class": FailureClassV1.PATCH_FILE_PROTOCOL_DISABLED.value,
            "protocol_violation": error_code,
            "task_id": task_id,
            "writes_allowed": False,
            "allow_patch_fallback_requested": bool(allow_patch_fallback),
            "result": {
                "ok": False,
                "source_tool": "patch_apply",
                "error": error_code,
                "writes_allowed": False,
                "authoritative_receipt": False,
                "repair_path": "native_tools_or_director_runtime_repair_required",
            },
        }

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
        if any(ch.isspace() for ch in token):
            return f"Invalid patch path: {file_path}"
        if any(ch in token for ch in ("└", "├", "│", "─", "•", "…")):
            return f"Invalid patch path: {file_path}"
        if re.search(r"^[a-zA-Z]:", token):
            return f"Absolute patch paths are not allowed: {file_path}"
        path = Path(token)
        if path.is_absolute() or any(part == ".." for part in path.parts):
            return f"Unsafe patch path: {file_path}"
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
        from .helpers import (
            _GENERIC_SCAFFOLD_MARKERS,
            _LOW_QUALITY_PATTERNS,
            _PATCH_RESIDUE_PATTERNS,
        )
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
        from .helpers import low_quality_pattern_match

        for pattern in low_quality_patterns:
            if low_quality_pattern_match(pattern, content, rel_path=rel_path):
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
