from __future__ import annotations

import json
from typing import Any

from polaris.kernelone.llm.types import ModelInfo


def _extract_cli_error_message(output: str) -> str | None:
    """Extract error text from parsed Codex CLI JSON output."""
    if not output:
        return None
    first_line = output.strip().splitlines()[0].strip()
    lowered = first_line.lower()
    if lowered.startswith("error:") or lowered.startswith("turn failed:"):
        return first_line
    return None


def _parse_codex_json_output(raw_output: str) -> str:
    """Parse JSON Lines output from Codex CLI exec --json mode

    Based on official documentation: https://docs.openai.com/codex/cli/non-interactive

    Event types include:
    - thread.started, turn.started, turn.completed, turn.failed
    - item.* (agent_message, reasoning, command_execution, file_change, mcp_tool_call, web_search, plan_update)
    - error

    Args:
        raw_output: Raw stdout from codex exec --json command

    Returns:
        Parsed output with thinking and agent messages extracted
    """
    lines = (raw_output or "").splitlines()
    reasoning_parts: list[str] = []
    message_parts: list[str] = []
    usage_data: dict[str, Any] | None = None
    command_executions: list[str] = []
    file_changes: list[str] = []
    errors: list[str] = []

    for line in lines:
        trimmed = line.strip()
        if not trimmed or not trimmed.startswith("{"):
            continue

        try:
            payload = json.loads(trimmed)
        except (RuntimeError, ValueError):
            continue

        if not isinstance(payload, dict):
            continue

        event_type = payload.get("type")

        # Thread/Turn lifecycle events
        if event_type in ("thread.started", "turn.started"):
            continue
        elif event_type == "turn.completed":
            usage_data = payload.get("usage")
            continue
        elif event_type == "turn.failed":
            error_info = payload.get("error", "Turn failed")
            if isinstance(error_info, str):
                errors.append(f"Turn failed: {error_info}")
            continue
        elif event_type == "error":
            error_text = payload.get("error", "")
            if isinstance(error_text, str):
                errors.append(f"Error: {error_text}")
            continue

        # Item events (the actual content)
        elif event_type == "item.started":
            item = payload.get("item")
            if isinstance(item, dict):
                pass  # Could track item start if needed
            continue

        elif event_type == "item.completed":
            item = payload.get("item")
            if not isinstance(item, dict):
                continue

            item_type = str(item.get("type") or "")
            text = item.get("text")

            # Extract content based on item type
            if item_type in ("reasoning", "thinking", "analysis"):
                if isinstance(text, str) and text.strip():
                    reasoning_parts.append(text.strip())

            elif item_type in ("agent_message", "message", "response"):
                if isinstance(text, str) and text.strip():
                    message_parts.append(text.strip())

            elif item_type == "command_execution":
                if isinstance(text, str) and text.strip():
                    command_executions.append(f"Command: {text.strip()}")
                elif isinstance(item.get("command"), str):
                    command_executions.append(f"Command: {item['command'].strip()}")

            elif item_type == "file_change":
                if isinstance(text, str) and text.strip():
                    file_changes.append(f"File change: {text.strip()}")

            elif item_type in ("mcp_tool_call", "web_search", "plan_update") and isinstance(text, str) and text.strip():
                reasoning_parts.append(f"[{item_type.replace('_', ' ').title()}]: {text.strip()}")

    # Build the final output
    output_chunks: list[str] = []

    if errors:
        output_chunks.append("\n".join(errors))

    if reasoning_parts:
        reasoning_text = "\n\n".join(reasoning_parts).strip()
        output_chunks.append(f"<thinking>{reasoning_text}</thinking>")

    if command_executions:
        output_chunks.append("\n".join(command_executions))

    if file_changes:
        output_chunks.append("\n".join(file_changes))

    if message_parts:
        message_text = "\n\n".join(message_parts).strip()
        output_chunks.append(message_text)

    if not output_chunks:
        return raw_output.strip()

    final_output = "\n\n".join(output_chunks).strip()

    if usage_data and isinstance(usage_data, dict):
        input_tokens = usage_data.get("input_tokens", 0)
        output_tokens = usage_data.get("output_tokens", 0)
        cached_tokens = usage_data.get("cached_input_tokens", 0)

        if input_tokens or output_tokens:
            usage_comment = f"<!-- Usage: {input_tokens} input, {output_tokens} output"
            if cached_tokens:
                usage_comment += f", {cached_tokens} cached"
            usage_comment += " -->"
            final_output += f"\n\n{usage_comment}"

    return final_output


def _parse_model_output(output: str) -> list[ModelInfo]:
    """Parse model listing output"""
    text = (output or "").strip()
    if not text:
        return []
    models: list[ModelInfo] = []

    # Try JSON first
    if text.startswith("{") or text.startswith("["):
        try:
            payload = json.loads(text)
            if isinstance(payload, dict):
                payload = payload.get("models") or payload.get("data") or payload.get("items") or []
            if isinstance(payload, list):
                for item in payload:
                    if isinstance(item, dict):
                        model_id = str(item.get("id") or item.get("name") or "").strip()
                        if model_id:
                            models.append(ModelInfo(id=model_id, raw=item))
                    elif isinstance(item, str):
                        models.append(ModelInfo(id=item.strip()))
            return models
        except (RuntimeError, ValueError):
            models = []

    # Parse text output (fallback)
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate:
            continue
        model_id = candidate.split()[0].strip()
        if model_id:
            models.append(ModelInfo(id=model_id, label=candidate))

    return models
