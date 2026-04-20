"""Command execution tool handlers.

Handles execute_command and related shell alias translations.
"""

from __future__ import annotations

import logging
import re
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from polaris.kernelone.llm.toolkit.executor.core import AgentAccelToolExecutor
    from polaris.kernelone.tool_execution.constants import CommandValidationResult

logger = logging.getLogger(__name__)


def register_handlers() -> dict[str, Any]:
    """Return a dict of handler names to handler methods."""
    return {
        "execute_command": _handle_execute_command,
    }


def _execute_command_base(
    self: AgentAccelToolExecutor,
    command_text: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute a single command (no shell operators).

    This is the shared base for both _handle_execute_command and _execute_single_command.

    Args:
        self: Executor instance
        command_text: Single command string
        timeout_seconds: Timeout

    Returns:
        Execution result dict
    """
    import os

    cmd_lower = command_text.strip().lower()
    _needs_shell_on_windows = any(
        cmd_lower.startswith(prefix) or f" {prefix}" in cmd_lower
        for prefix in ("npm", "npx", "node", "yarn", "pnpm", "cmd", "powershell", "ps")
    )
    if _needs_shell_on_windows and os.name == "nt":
        return _execute_via_shell(self, command_text, timeout_seconds)

    try:
        request = self._command_executor.parse_command(
            command_text,
            cwd=".",
            timeout_seconds=timeout_seconds,
        )
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}

    result = self._command_executor.run(request)
    raw_command = result.get("command")
    command_spec: dict[str, Any] = raw_command if isinstance(raw_command, dict) else {}

    exit_code = int(result.get("returncode", -1))
    timed_out = bool(result.get("timed_out"))
    has_error = bool(result.get("error"))

    if timed_out or has_error:
        ok = False
        err_msg = str(result.get("error") or "")
    else:
        ok = True
        err_msg = ""

    return {
        "ok": ok,
        "exit_code": exit_code,
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "command": command_text,
        "shell": False,
        "timed_out": bool(result.get("timed_out")),
        "executable": str(command_spec.get("executable") or ""),
        "argv": list(command_spec.get("args") or []),
        "cwd": str(command_spec.get("cwd") or self.workspace),
        "error": err_msg,
    }


def _handle_execute_command(self: AgentAccelToolExecutor, **kwargs) -> dict[str, Any]:
    """Handle execute_command tool call.

    Args:
        self: Executor instance
        **kwargs: Tool arguments

    Returns:
        Execution result dict
    """
    command = kwargs.get("command") or kwargs.get("cmd")
    timeout = kwargs.get("timeout", 30)

    command_text = _sanitize_llm_command_text(str(command or ""))
    if not command_text:
        return {"ok": False, "error": "Missing command"}

    timeout_seconds = max(1, min(int(timeout), 120))

    # Validate command against whitelist before execution
    validation_result = _validate_command_whitelist(command_text)
    if not validation_result.allowed:
        logger.warning(
            "execute_command blocked: command=%r reason=%s",
            command_text,
            validation_result.reason,
        )
        return _attach_command_effect_receipt(
            {
                "ok": False,
                "error": f"Command blocked: {validation_result.reason}",
                "blocked": True,
                "command": command_text,
            }
        )

    translated = _translate_readonly_command_alias(self, command_text)
    if translated is not None:
        return _attach_command_effect_receipt(translated)

    if _contains_shell_operators(command_text):
        return _attach_command_effect_receipt(_execute_command_chain(self, command_text, timeout_seconds))

    return _attach_command_effect_receipt(_execute_command_base(self, command_text, timeout_seconds))


def _attach_command_effect_receipt(result: dict[str, Any]) -> dict[str, Any]:
    """Attach a canonical effect receipt to execute_command results."""
    if "effect_receipt" in result:
        return result
    payload = dict(result)
    payload["effect_receipt"] = {
        "operation": "execute_command",
        "command": str(payload.get("command") or ""),
        "exit_code": int(payload.get("exit_code", -1)),
        "cwd": str(payload.get("cwd") or ""),
        "shell": bool(payload.get("shell")),
        "timed_out": bool(payload.get("timed_out")),
        "success": bool(payload.get("ok")),
    }
    return payload


def _execute_command_chain(
    self: AgentAccelToolExecutor,
    command_text: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute a command with shell operators (&&, ||, ;, |, >, <).

    Args:
        self: Executor instance
        command_text: Command string with operators
        timeout_seconds: Timeout for each command

    Returns:
        Execution result dict
    """
    import re

    # Now split by operators while preserving quoted strings
    # Tokenize: split by &&, ||, ;, |
    commands: list[str] = []
    operators: list[str] = []

    # Simple regex-based split that respects quotes
    pattern = r"(\s*(?:&&|\|\||;|\|)\s*)"
    tokens = re.split(pattern, command_text)

    current_cmd = ""
    for token in tokens:
        token = token.strip()
        if not token:
            continue
        if token in ("&&", "||", ";", "|"):
            if current_cmd.strip():
                commands.append(current_cmd.strip())
                operators.append(token)
            current_cmd = ""
        else:
            current_cmd += token

    if current_cmd.strip():
        commands.append(current_cmd.strip())

    if not commands:
        return {"ok": False, "error": "Empty command"}

    # On Windows, commands like npm, npx, node are .cmd files that need shell=True
    # Check if any command might need shell execution
    _needs_shell_on_windows = any(
        cmd.strip().lower().startswith(prefix)
        for cmd in commands
        for prefix in ("npm", "npx", "node", "yarn", "pnpm", "cmd", "powershell", "ps")
    )
    import os

    if _needs_shell_on_windows and os.name == "nt":
        return _execute_via_shell(self, command_text, timeout_seconds)

    # Execute commands sequentially
    all_stdout: list[str] = []
    all_stderr: list[str] = []
    final_exit_code = 0
    last_result: dict[str, Any] = {}
    single_result: dict[str, Any]
    any_timed_out = False

    for idx, cmd in enumerate(commands):
        op = operators[idx] if idx < len(operators) else ";"

        # Handle pipe: pipe not directly supported - execute as shell pipe
        if op == "|" and idx == 0:
            return _execute_via_shell(self, command_text, timeout_seconds)

        # Handle conditional operators
        if op == "&&" and final_exit_code != 0:
            # Previous command failed, skip this one
            continue
        if op == "||" and final_exit_code == 0:
            # Previous command succeeded, skip this one
            continue

        # Handle output redirection
        redirect_match = re.search(r'\s+>\s+["\']?([^"\'\s]+)["\']?\s*$', cmd)
        redirect_file: str | None = None
        if redirect_match:
            redirect_file = redirect_match.group(1).strip()
            cmd = cmd[: redirect_match.start()].strip()

        # Handle input redirection
        input_redirect_match = re.search(r'\s+<\s+["\']?([^"\'\s]+)["\']?\s*$', cmd)
        input_file: str | None = None
        if input_redirect_match:
            input_file = input_redirect_match.group(1).strip()
            cmd = cmd[: input_redirect_match.start()].strip()

        # Handle cd command (shell built-in, not an executable)
        cd_match = re.match(r"^cd\s+[\"']?([^\s\"']+)[\"']?\s*$", cmd, re.IGNORECASE)
        if cd_match:
            target_dir = cd_match.group(1).strip()
            # Resolve relative to current working directory
            import os

            try:
                if os.path.isabs(target_dir):
                    new_cwd = target_dir
                else:
                    new_cwd = os.path.normpath(os.path.join(self.workspace, target_dir))
                if os.path.isdir(new_cwd):
                    self.workspace = new_cwd
                    single_result = {
                        "ok": True,
                        "exit_code": 0,
                        "stdout": new_cwd,
                        "stderr": "",
                        "command": cmd,
                        "shell": False,
                        "timed_out": False,
                        "executable": "cd",
                        "argv": [target_dir],
                        "cwd": new_cwd,
                        "error": "",
                    }
                else:
                    single_result = {
                        "ok": False,
                        "exit_code": 1,
                        "stdout": "",
                        "stderr": f"cd: {target_dir}: No such directory",
                        "command": cmd,
                        "shell": False,
                        "timed_out": False,
                        "executable": "cd",
                        "argv": [target_dir],
                        "cwd": self.workspace,
                        "error": f"cd: {target_dir}: No such directory",
                    }
            except OSError as e:
                single_result = {
                    "ok": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": str(e),
                    "command": cmd,
                    "shell": False,
                    "timed_out": False,
                    "executable": "cd",
                    "argv": [target_dir],
                    "cwd": self.workspace,
                    "error": str(e),
                }
            last_result = single_result
            if single_result.get("timed_out"):
                any_timed_out = True
            all_stdout.append(str(single_result.get("stdout", "")))
            all_stderr.append(str(single_result.get("stderr", "")))
            final_exit_code = int(single_result.get("exit_code", 0))
            continue

        # Execute single command
        single_result = _execute_single_command(self, cmd, timeout_seconds)

        if redirect_file and single_result.get("ok"):
            # Redirect stdout to file
            try:
                with open(redirect_file, "w", encoding="utf-8") as f:
                    f.write(single_result.get("stdout", ""))
            except OSError as e:
                single_result["ok"] = False
                single_result["error"] = f"Failed to write to {redirect_file}: {e}"

        if input_file:
            # Read stdin from file for next command
            try:
                with open(input_file, encoding="utf-8") as f:
                    stdin_content = f.read()
                single_result["stdin_content"] = stdin_content
            except OSError as e:
                single_result["ok"] = False
                single_result["error"] = f"Failed to read from {input_file}: {e}"

        all_stdout.append(single_result.get("stdout", ""))
        all_stderr.append(single_result.get("stderr", ""))
        final_exit_code = single_result.get("exit_code", 0)
        last_result = single_result
        if single_result.get("timed_out"):
            any_timed_out = True

        # For && and ||, check exit code
        if op in ("&&", "||") and idx < len(commands) - 1:
            if op == "&&" and final_exit_code != 0:
                # && chain failed
                break
            if op == "||" and final_exit_code == 0:
                # || chain succeeded (skip rest)
                break

    # Build error message if command failed but error is empty
    chain_err_msg = last_result.get("error", "")
    if not chain_err_msg and final_exit_code != 0:
        last_stderr = str(last_result.get("stderr") or "").strip()
        last_stdout = str(last_result.get("stdout") or "").strip()
        if last_stderr:
            chain_err_msg = f"Exit code {final_exit_code}: {last_stderr}"
        elif last_stdout:
            # pytest outputs collection errors to stdout, not stderr
            chain_err_msg = f"Exit code {final_exit_code}: {last_stdout}"
        else:
            chain_err_msg = f"Command chain failed with exit code {final_exit_code}"

    return {
        "ok": final_exit_code == 0,
        "exit_code": final_exit_code,
        "stdout": "\n".join(all_stdout).strip(),
        "stderr": "\n".join(all_stderr).strip(),
        "command": command_text,
        "shell": False,
        "timed_out": any_timed_out,
        "error": chain_err_msg,
    }


def _execute_single_command(
    self: AgentAccelToolExecutor,
    command_text: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute a single command (no shell operators).

    Args:
        self: Executor instance
        command_text: Single command string
        timeout_seconds: Timeout

    Returns:
        Execution result dict
    """
    validation_result = _validate_command_whitelist(command_text)
    if not validation_result.allowed:
        return {
            "ok": False,
            "error": f"Command blocked: {validation_result.reason}",
            "blocked": True,
            "command": command_text,
        }

    translated = _translate_readonly_command_alias(self, command_text)
    if translated is not None:
        return translated

    return _execute_command_base(self, command_text, timeout_seconds)


def _execute_via_shell(
    self: AgentAccelToolExecutor,
    command_text: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Execute command chain via shell (fallback for complex pipes).

    Args:
        self: Executor instance
        command_text: Full command with operators
        timeout_seconds: Timeout

    Returns:
        Execution result dict
    """
    import os
    import subprocess

    # Validate full command
    validation_result = _validate_command_whitelist(command_text)
    if not validation_result.allowed:
        return {
            "ok": False,
            "error": f"Command blocked: {validation_result.reason}",
            "blocked": True,
            "command": command_text,
        }

    # Detect long-running server processes that should run in background
    cmd_lower = command_text.lower().strip()
    # Check both full chain and individual commands for server start patterns
    _is_server_start = any(
        cmd_lower.startswith(prefix) or f" {prefix}" in cmd_lower
        for prefix in (
            "npm start",
            "npm run dev",
            "npm run serve",
            "yarn start",
            "yarn dev",
            "yarn serve",
            "pnpm start",
            "pnpm dev",
            "pnpm serve",
            "python manage.py runserver",
            "python -m uvicorn",
            "node dist/",
            "node build/",
            "node server",
            "node index",
            "node app",
            "node src/",
            "npx ts-node",
            "npx nodemon",
        )
    )

    if _is_server_start and os.name == "nt":
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "/b", "", command_text],
                shell=False,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": "Server started in background",
                "stderr": "",
                "command": command_text,
                "shell": True,
                "timed_out": False,
                "error": "",
                "background": True,
            }
        except OSError as e:
            return {
                "ok": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command_text,
                "shell": True,
                "timed_out": False,
                "error": str(e),
            }
    elif _is_server_start:
        try:
            subprocess.run(
                ["nohup", "bash", "-c", command_text],
                shell=False,
                cwd=self.workspace,
                timeout=5,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": "Server started in background",
                "stderr": "",
                "command": command_text,
                "shell": True,
                "timed_out": False,
                "error": "",
                "background": True,
            }
        except subprocess.TimeoutExpired:
            return {
                "ok": True,
                "exit_code": 0,
                "stdout": "Server started in background (timeout on check)",
                "stderr": "",
                "command": command_text,
                "shell": True,
                "timed_out": False,
                "error": "",
                "background": True,
            }
        except OSError as e:
            return {
                "ok": False,
                "exit_code": -1,
                "stdout": "",
                "stderr": str(e),
                "command": command_text,
                "shell": True,
                "timed_out": False,
                "error": str(e),
            }

    try:
        if os.name == "nt":
            run_argv = ["cmd", "/c", command_text]
        else:
            parsed_args = shlex.split(command_text, posix=True)
            if not parsed_args:
                return {
                    "ok": False,
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": "Empty command after parsing",
                    "command": command_text,
                    "shell": False,
                    "timed_out": False,
                    "error": "Empty command",
                }
            run_argv = parsed_args
        proc = subprocess.run(
            run_argv,
            shell=False,
            cwd=self.workspace,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            encoding="utf-8",
            errors="replace",
        )
        # Build error message if command failed but error is empty
        shell_err_msg = ""
        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            if stderr:
                shell_err_msg = f"Exit code {proc.returncode}: {stderr}"
            elif stdout:
                # pytest outputs collection errors to stdout, not stderr
                shell_err_msg = f"Exit code {proc.returncode}: {stdout}"
            else:
                shell_err_msg = f"Command failed with exit code {proc.returncode}"
        return {
            "ok": proc.returncode == 0,
            "exit_code": proc.returncode,
            "stdout": proc.stdout or "",
            "stderr": proc.stderr or "",
            "command": command_text,
            "shell": True,
            "timed_out": False,
            "error": shell_err_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": "",
            "command": command_text,
            "shell": True,
            "timed_out": True,
            "error": f"Command timed out after {timeout_seconds}s",
        }
    except OSError as e:
        return {
            "ok": False,
            "exit_code": -1,
            "stdout": "",
            "stderr": str(e),
            "command": command_text,
            "shell": True,
            "timed_out": False,
            "error": str(e),
        }


def _validate_command_whitelist(command: str) -> CommandValidationResult:
    """Validate a command against the whitelist.

    This function imports and uses CommandWhitelistValidator from
    polaris.kernelone.tool_execution.constants.

    Args:
        command: The command string to validate.

    Returns:
        CommandValidationResult with allowed status and reason.
    """
    from polaris.kernelone.tool_execution.constants import CommandWhitelistValidator

    return CommandWhitelistValidator.validate(command)


def _contains_shell_operators(command_text: str) -> bool:
    """Check if command contains shell operators.

    Args:
        command_text: Command to check

    Returns:
        True if shell operators are present
    """
    token = str(command_text or "")
    markers = ("|", "&&", "||", ";", ">", "<")
    return any(marker in token for marker in markers)


def _sanitize_llm_command_text(command_text: str) -> str:
    """Sanitize LLM-generated command text.

    Args:
        command_text: Raw command text

    Returns:
        Sanitized command
    """
    token = str(command_text or "").strip()
    if not token:
        return ""
    token = re.sub(r"\*\*(.*?)\*\*", r"\1", token, flags=re.DOTALL)
    token = token.replace("`", "")
    token = token.replace("\r\n", "\n").replace("\r", "\n")
    token = re.sub(r"\n+\*+\s*$", "", token).strip()
    token = re.sub(r"\s+\*+\s*$", "", token).strip()
    token = token.replace("\n", " ").strip()
    token = re.sub(r"\s{2,}", " ", token)
    return token


def _translate_readonly_command_alias(
    self: AgentAccelToolExecutor,
    command_text: str,
) -> dict[str, Any] | None:
    """Translate read-only shell command aliases to tool calls.

    Args:
        self: Executor instance
        command_text: Command text to translate

    Returns:
        Translated result dict or None if not translatable
    """
    token = str(command_text or "").strip()
    if not token:
        return None

    base_path = "."
    cd_match = re.match(r"^cd\s+(?P<path>.+?)\s*&&\s*(?P<rest>.+)$", token, flags=re.IGNORECASE)
    if cd_match is not None:
        base_path = _normalize_command_path(cd_match.group("path"), base_path=".")
        token = str(cd_match.group("rest") or "").strip()

    try:
        argv = shlex.split(token, posix=True)
    except ValueError:
        argv = [part for part in token.split(" ") if part]
    if not argv:
        return None

    executable = str(argv[0] or "").strip().lower()

    if executable in {"ls", "dir"}:
        return _translate_list_directory_command(self, token, argv, base_path=base_path)
    if executable == "tree":
        return _translate_tree_command(self, token, argv, base_path=base_path)
    if executable == "find":
        return _translate_find_command(self, token, argv, base_path=base_path)
    if executable == "pwd":
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": str(Path(self.workspace).resolve()),
            "stderr": "",
            "command": command_text,
            "shell": False,
            "timed_out": False,
            "executable": executable,
            "argv": argv,
            "cwd": self.workspace,
            "translated_tool": "pwd",
            "translation_mode": "readonly_shell_alias",
            "error": "",
        }

    return None


def _translate_list_directory_command(
    self: AgentAccelToolExecutor,
    command_text: str,
    argv: list[str],
    *,
    base_path: str,
) -> dict[str, Any]:
    """Translate ls/dir command to list_directory tool call."""
    flags = [item for item in argv[1:] if item.startswith("-") or item.startswith("/")]
    targets = [item for item in argv[1:] if not (item.startswith("-") or item.startswith("/"))]
    path = _normalize_command_path(targets[-1] if targets else ".", base_path=base_path)
    include_hidden = any("a" in flag.lower() for flag in flags)
    recursive = any("r" in flag.lower() for flag in flags)

    # Import handler dynamically to avoid circular import
    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_list_directory

    listing = _handle_list_directory(
        self,
        path=path,
        recursive=recursive,
        include_hidden=include_hidden,
        max_entries=500,
    )
    return _translated_command_result(
        self,
        command_text=command_text,
        executable=str(argv[0] or ""),
        argv=argv,
        translated_tool="list_directory",
        payload=listing,
    )


def _translate_tree_command(
    self: AgentAccelToolExecutor,
    command_text: str,
    argv: list[str],
    *,
    base_path: str,
) -> dict[str, Any]:
    """Translate tree command to list_directory tool call."""
    targets: list[str] = []
    skip_next = False
    for item in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if item == "-L":
            skip_next = True
            continue
        if item.startswith("-") or item.startswith("/"):
            continue
        targets.append(item)

    path = _normalize_command_path(targets[-1] if targets else ".", base_path=base_path)

    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_list_directory

    listing = _handle_list_directory(
        self,
        path=path,
        recursive=True,
        include_hidden=False,
        max_entries=500,
    )
    return _translated_command_result(
        self,
        command_text=command_text,
        executable=str(argv[0] or ""),
        argv=argv,
        translated_tool="list_directory",
        payload=listing,
    )


def _translate_find_command(
    self: AgentAccelToolExecutor,
    command_text: str,
    argv: list[str],
    *,
    base_path: str,
) -> dict[str, Any] | None:
    """Translate find command to glob tool call."""
    search_path = _normalize_command_path(argv[1] if len(argv) > 1 else ".", base_path=base_path)
    pattern = ""
    for index, item in enumerate(argv):
        if item.lower() in {"-name", "-iname"} and index + 1 < len(argv):
            pattern = str(argv[index + 1] or "").strip()
            break

    if not pattern:
        return None

    from polaris.kernelone.llm.toolkit.executor.handlers.navigation import _handle_glob

    glob_result = _handle_glob(
        self,
        pattern=f"**/{pattern}",
        path=search_path,
        recursive=True,
        include_hidden=False,
        max_results=500,
    )
    return _translated_command_result(
        self,
        command_text=command_text,
        executable=str(argv[0] or ""),
        argv=argv,
        translated_tool="glob",
        payload=glob_result,
    )


def _translated_command_result(
    self: AgentAccelToolExecutor,
    *,
    command_text: str,
    executable: str,
    argv: list[str],
    translated_tool: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Build a translated command result dict."""
    if not isinstance(payload, dict):
        return {"ok": False, "error": f"Translated {translated_tool} payload is invalid"}
    if not bool(payload.get("ok")):
        return payload

    stdout = _format_translated_command_stdout(translated_tool=translated_tool, payload=payload)

    return {
        "ok": True,
        "exit_code": 0,
        "stdout": stdout,
        "stderr": "",
        "command": command_text,
        "shell": False,
        "timed_out": False,
        "executable": executable,
        "argv": list(argv),
        "cwd": self.workspace,
        "translated_tool": translated_tool,
        "translation_mode": "readonly_shell_alias",
        "error": "",
    }


def _format_translated_command_stdout(
    *,
    translated_tool: str,
    payload: dict[str, Any],
) -> str:
    """Format translated tool output for command stdout."""
    if translated_tool == "list_directory":
        raw_entries = payload.get("entries")
        entries: list[Any] = raw_entries if isinstance(raw_entries, list) else []
        lines: list[str] = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            rel_path = str(item.get("path") or item.get("name") or "").strip()
            if not rel_path:
                continue
            if str(item.get("type") or "").strip().lower() == "dir" and not rel_path.endswith("/"):
                rel_path = f"{rel_path}/"
            lines.append(rel_path)
        return "\n".join(lines)

    if translated_tool == "glob":
        raw_results = payload.get("results")
        results: list[Any] = raw_results if isinstance(raw_results, list) else []
        return "\n".join(str(item).strip() for item in results if str(item).strip())

    return ""


def _normalize_command_path(path_token: str, *, base_path: str) -> str:
    """Normalize a command path argument.

    Args:
        path_token: Path token to normalize
        base_path: Base path for relative paths

    Returns:
        Normalized path
    """
    token = str(path_token or "").strip().strip('"').strip("'")
    if not token or token == ".":
        return str(base_path or ".")

    normalized = token.replace("\\", "/")
    lowered = normalized.lower()

    if lowered in {"/workspace", "/workspace/"}:
        return "."
    if lowered.startswith("/workspace/"):
        suffix = normalized[len("/workspace/") :].strip("/")
        return suffix or "."
    if base_path not in {"", "."} and not normalized.startswith("/"):
        prefix = str(base_path or ".").strip().strip("/")
        suffix = normalized.lstrip("./")
        return f"{prefix}/{suffix}" if prefix else suffix or "."

    return normalized
