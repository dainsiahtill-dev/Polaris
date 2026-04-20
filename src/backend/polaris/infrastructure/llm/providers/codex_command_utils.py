from __future__ import annotations

import os
import shutil
from typing import Any


def _normalize_command(command: str) -> list[str]:
    """Normalize command for different platforms"""
    ext = os.path.splitext(command)[1].lower()
    if ext == ".ps1":
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", command]
    if ext in (".cmd", ".bat"):
        return ["cmd.exe", "/c", command]
    return [command]


def _truncate(text: str, limit: int) -> str:
    """Truncate text to specified limit with ellipsis"""
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def _resolve_command(command: str) -> str | None:
    """Resolve command path"""
    if not command:
        return None
    if os.path.isabs(command) or os.path.exists(command):
        return command
    return shutil.which(command)


def _resolve_output_path(config: dict[str, Any]) -> str | None:
    """Resolve output file path"""
    raw = str(config.get("output_path") or "").strip()
    if not raw:
        codex_exec = config.get("codex_exec")
        if isinstance(codex_exec, dict):
            raw = str(codex_exec.get("output_last_message") or "").strip()
    if not raw:
        return None
    if os.path.isabs(raw):
        return raw
    base = str(config.get("working_dir") or "").strip()
    if base:
        return os.path.join(base, raw)
    return os.path.abspath(raw)


def _render_args(
    args: list[str],
    prompt: str,
    model: str,
    output_path: str | None,
) -> tuple[list[str], bool]:
    """Render arguments with placeholder replacement.

    IMPORTANT: Prompt is ALWAYS sent via stdin, never as CLI argument.
    This ensures Codex CLI treats it as the actual user message rather than
    context/instruction, preventing "ready to interview" deflection responses.
    """
    rendered: list[str] = []
    skip_next = False

    for idx, item in enumerate(args):
        if skip_next:
            skip_next = False
            continue

        if item == "--model" and idx + 1 < len(args) and "{model}" in args[idx + 1] and not model:
            skip_next = True
            continue

        if output_path is None and "{output}" in item:
            continue

        value = item.replace("{model}", model)

        # Skip {prompt} placeholder - prompt will be sent via stdin instead
        if "{prompt}" in value:
            continue

        if output_path and "{output}" in value:
            value = value.replace("{output}", output_path)

        if value == "":
            continue
        rendered.append(value)

    # ALWAYS send prompt via stdin for proper handling by Codex CLI
    return rendered, True
