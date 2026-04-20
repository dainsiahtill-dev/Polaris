"""工具执行实现

包含文件读写、命令执行、代码搜索等工具的具体实现。
"""

from __future__ import annotations

import logging
import re
import shlex
from pathlib import Path
from typing import Any

from polaris.kernelone.fs.text_ops import write_text_atomic
from polaris.kernelone.llm.toolkit.tool_normalization import (
    normalize_patch_like_write_content,
)

from .helpers import _MIN_FILES_PATTERN, _MIN_LINES_PATTERN
from .security import (
    ALLOWED_EXECUTION_COMMANDS,
    TOOLING_SECURITY_AVAILABLE,
    CommandInjectionBlocked,
    is_command_allowed,
    is_command_blocked,
)

logger = logging.getLogger(__name__)


class DirectorToolExecutor:
    """Director 工具执行器。

    提供文件读写、命令执行、代码搜索等工具的具体实现。
    """

    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def execute_tool(
        self,
        tool_name: str,
        args: dict[str, Any],
    ) -> dict[str, Any]:
        """执行指定工具"""
        workspace_path = Path(self.workspace).resolve()

        if tool_name == "write_file":
            return self._tool_write_file(args, workspace_path)
        elif tool_name == "read_file":
            return self._tool_read_file(args, workspace_path)
        elif tool_name == "edit_file":
            return self._tool_edit_file(args, workspace_path)
        elif tool_name in {"run_command", "execute_command"}:
            return self._tool_run_command(args, workspace_path)
        elif tool_name == "search_code":
            return self._tool_search_code(args, workspace_path)
        else:
            return {"ok": False, "error": f"Unknown tool: {tool_name}"}

    # -------------------------------------------------------------------------
    # File Tools
    # -------------------------------------------------------------------------

    def _tool_write_file(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """写入文件工具"""
        raw_file_path = args.get("file") or args.get("path") or args.get("filepath")
        file_path = str(raw_file_path or "").strip()
        content = str(args.get("content", ""))

        if not file_path:
            return {"ok": False, "error": "Missing file path"}
        if "\n" in file_path or "\r" in file_path:
            return {"ok": False, "error": f"Invalid file path contains newline: {file_path!r}"}
        if _MIN_FILES_PATTERN.match(file_path) or _MIN_LINES_PATTERN.match(file_path):
            return {"ok": False, "error": f"Invalid file path resembles requirement sentence: {file_path}"}
        if re.match(r"^(table|index)\s+if\s+not\s+exists\b", file_path, re.IGNORECASE):
            return {"ok": False, "error": f"Invalid file path resembles SQL statement: {file_path}"}

        target = (workspace / file_path).resolve()
        if workspace not in target.parents and target != workspace:
            return {"ok": False, "error": f"Unsafe file path outside workspace: {file_path}"}

        allowed_extensionless = {
            "makefile",
            "dockerfile",
            "readme",
            "gitignore",
            "gitattributes",
            "dockerignore",
            "env",
            "editorconfig",
            "prettierrc",
            "eslintrc",
            "bashrc",
            "zshrc",
            "profile",
            "toml",
            "ini",
        }
        suffix = target.suffix.lower()
        # Strip leading dot for comparison (e.g., ".gitignore" -> "gitignore")
        target_name_lower = target.name.lower().lstrip(".")
        if not suffix and target_name_lower not in allowed_extensionless:
            return {"ok": False, "error": f"Invalid file path missing extension: {file_path}"}

        try:
            existing_content: str | None = None
            if target.exists():
                try:
                    existing_content = target.read_text(encoding="utf-8")
                except (OSError, UnicodeError):
                    existing_content = None

            rel_path = target.relative_to(workspace).as_posix()
            normalized = normalize_patch_like_write_content(
                rel_path,
                content,
                existing_content=existing_content,
            )
            if normalized.error:
                return {"ok": False, "error": normalized.error}
            text = str(normalized.content or "")

            # 确保父目录存在
            target.parent.mkdir(parents=True, exist_ok=True)
            # 写入文件（UTF-8）
            write_text_atomic(str(target), text, encoding="utf-8")
            result = {"ok": True, "file": rel_path, "bytes_written": len(text.encode("utf-8"))}
            if normalized.normalized_patch_like:
                result["normalized_patch_like_write"] = True
            return result
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_read_file(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """读取文件工具"""
        file_path = args.get("file") or args.get("path") or args.get("filepath")

        if not file_path:
            return {"ok": False, "error": "Missing file path"}

        target = workspace / file_path
        try:
            if not target.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}
            content = target.read_text(encoding="utf-8")
            return {"ok": True, "file": file_path, "content": content}
        except (OSError, UnicodeError) as exc:
            return {"ok": False, "error": str(exc)}

    def _tool_edit_file(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """编辑文件工具（搜索替换）"""
        file_path = args.get("file") or args.get("path")
        search = args.get("search", "")
        replace = args.get("replace", "")

        if not file_path:
            return {"ok": False, "error": "Missing file path"}

        target = workspace / file_path
        try:
            if not target.exists():
                return {"ok": False, "error": f"File not found: {file_path}"}

            content = target.read_text(encoding="utf-8")
            if search not in content:
                return {"ok": False, "error": f"Search text not found in file: {search[:50]}..."}

            new_content = content.replace(search, replace, 1)
            write_text_atomic(str(target), new_content, encoding="utf-8")
            return {"ok": True, "file": file_path, "replacements": 1}
        except (OSError, RuntimeError, TypeError, UnicodeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Command Tools
    # -------------------------------------------------------------------------

    def _tool_run_command(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """运行命令工具（安全加固版）"""
        command_raw = args.get("command") or args.get("cmd")
        command = str(command_raw or "").strip()
        if not command:
            return {"ok": False, "error": "Missing command"}
        timeout_raw = args.get("timeout", 30)
        try:
            timeout = int(timeout_raw)
        except (TypeError, ValueError):
            timeout = 30
        timeout = max(1, min(timeout, 300))
        security = self._validate_command_security(command)
        if security:
            return security
        use_shell = bool(args.get("shell", False))
        try:
            cmd_args = shlex.split(command, posix=False) if not use_shell else command
        except ValueError as exc:
            logger.warning(
                "Command argument parsing failed; blocking shell=True fallback to prevent "
                "command injection. command=%r ValueError=%s",
                command,
                exc,
            )
            raise CommandInjectionBlocked(
                command=command,
                reason=f"shlex.split.failed:{exc}",
            ) from exc
        return self._run_command_service(cmd_args, workspace, timeout, use_shell)

    def _validate_command_security(self, command: str) -> dict[str, Any] | None:
        """验证命令安全性"""
        if TOOLING_SECURITY_AVAILABLE:
            if is_command_blocked(command):
                return {"ok": False, "error": "Command blocked: matches dangerous pattern"}
            if not is_command_allowed(command, ALLOWED_EXECUTION_COMMANDS):
                return {"ok": False, "error": "Command not in allowed whitelist"}
        else:
            lowered = command.lower()
            for pattern in (
                "rm -rf",
                "del /s",
                "rmdir /s",
                ";",
                "&&",
                "||",
                "|",
                "`",
                "$(",
                ">",
                "<",
            ):
                if pattern in lowered:
                    return {"ok": False, "error": f"Dangerous pattern: {pattern}"}
        return None

    def _run_command_service(
        self,
        cmd_args: Any,
        workspace: Path,
        timeout: int,
        use_shell: bool,
    ) -> dict[str, Any]:
        """通过 CommandExecutionService 执行命令"""
        from polaris.kernelone.process.command_executor import (
            CommandExecutionService,
            CommandRequest,
        )

        try:
            if use_shell or not isinstance(cmd_args, list):
                cmd_args = shlex.split(str(cmd_args)) if not use_shell else []
                if not cmd_args:
                    return {"ok": False, "error": "Cannot parse shell command safely"}
            cmd_svc = CommandExecutionService(str(workspace))
            request = CommandRequest(
                executable=cmd_args[0],
                args=cmd_args[1:] if len(cmd_args) > 1 else [],
                cwd=str(workspace),
                timeout_seconds=timeout,
            )
            result = cmd_svc.run(request)
            return {
                "ok": result.get("ok", False),
                "exit_code": result.get("returncode", -1),
                "stdout": result.get("stdout", ""),
                "stderr": result.get("stderr", ""),
            }
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}

    # -------------------------------------------------------------------------
    # Search Tool
    # -------------------------------------------------------------------------

    def _tool_search_code(
        self,
        args: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        """代码搜索工具（使用 ripgrep）"""
        from polaris.kernelone.process.command_executor import (
            CommandExecutionService,
            CommandRequest,
        )

        query = args.get("query") or args.get("search")
        if not query:
            return {"ok": False, "error": "Missing query"}

        try:
            # 使用 rg 进行搜索
            cmd_svc = CommandExecutionService(str(workspace))
            request = CommandRequest(
                executable="rg",
                args=[
                    "-n",
                    "-i",
                    "--type-add",
                    "code:*.{py,js,ts,jsx,tsx,java,go,rs,c,cpp}",
                    "-tcode",
                    query,
                ],
                cwd=str(workspace),
                timeout_seconds=10,
            )
            result = cmd_svc.run(request)
            stdout = result.get("stdout", "")
            ok = result.get("ok", False)
            return {
                "ok": True,
                "query": query,
                "results": stdout if ok else "",
                "count": len([ln for ln in stdout.split("\n") if ln.strip()]) if ok else 0,
            }
        except FileNotFoundError:
            # rg 未安装，返回模拟结果
            return {"ok": True, "query": query, "results": "", "count": 0, "note": "rg not installed"}
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            return {"ok": False, "error": str(exc)}
