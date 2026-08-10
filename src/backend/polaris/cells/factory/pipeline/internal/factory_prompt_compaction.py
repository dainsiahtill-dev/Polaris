"""Pure text/payload compaction helpers for LLM prompt context.

Extracted from ``OrchestrationStageExecutor``. These functions compact raw
JSON quality-check output and blueprint evidence into shorter, parseable
payloads suitable for injection into LLM prompts.
"""

from __future__ import annotations

import json
from typing import Any

from .factory_stage_helpers import compact_text_for_prompt


def compact_workspace_quality_evidence_for_qa(text: str) -> str:
    """Build a short, parseable workspace-quality JSON payload for QA."""

    try:
        payload = json.loads(str(text or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return compact_text_for_prompt(str(text or ""), max_chars=6000)
    if not isinstance(payload, dict):
        return compact_text_for_prompt(str(text or ""), max_chars=6000)

    commands: list[dict[str, Any]] = []
    for item in list(payload.get("commands") or []):
        if not isinstance(item, dict):
            continue
        command = item.get("command")
        if isinstance(command, list):
            command_value: list[str] | str = [str(part) for part in command]
        else:
            command_value = str(command or "")
        row: dict[str, Any] = {
            "command": command_value,
            "phase": str(item.get("phase") or ""),
            "passed": bool(item.get("passed")),
            "exit_code": item.get("exit_code"),
        }
        stdout_tail = str(item.get("stdout_tail") or "").strip()
        stderr_tail = str(item.get("stderr_tail") or "").strip()
        if stdout_tail:
            row["stdout_tail"] = compact_text_for_prompt(stdout_tail, max_chars=700)
        if stderr_tail:
            row["stderr_tail"] = compact_text_for_prompt(stderr_tail, max_chars=700)
        commands.append(row)

    repair = payload.get("repair") if isinstance(payload.get("repair"), dict) else {}
    compact_payload: dict[str, Any] = {
        "schema_version": payload.get("schema_version"),
        "source": payload.get("source"),
        "factory_run_id": payload.get("factory_run_id"),
        "workspace": payload.get("workspace"),
        "passed": bool(payload.get("passed")),
        "commands": commands,
    }
    if isinstance(repair, dict) and repair:
        compact_payload["repair"] = {
            "attempted": bool(repair.get("attempted")),
            "success": bool(repair.get("success")),
            "source_tools": [str(item) for item in list(repair.get("source_tools") or [])[:6]],
            "evidence": [
                compact_text_for_prompt(str(item or ""), max_chars=220)
                for item in list(repair.get("evidence") or [])[:6]
                if str(item or "").strip()
            ],
        }
    return json.dumps(compact_payload, ensure_ascii=False, indent=2)


def compact_blueprint_evidence_for_repair(text: str) -> str:
    try:
        payload = json.loads(str(text or ""))
    except (json.JSONDecodeError, TypeError, ValueError):
        return compact_text_for_prompt(str(text or ""), max_chars=6000)
    if not isinstance(payload, dict):
        return compact_text_for_prompt(str(text or ""), max_chars=6000)

    blueprints: list[dict[str, Any]] = []
    for item in list(payload.get("blueprints") or [])[:12]:
        if not isinstance(item, dict):
            continue
        compact_item: dict[str, Any] = {}
        for key in ("task_id", "status", "blueprint_id", "blueprint_path", "summary", "recommendations", "risks"):
            value = item.get(key)
            if value not in (None, "", [], {}):
                compact_item[key] = value
        if compact_item:
            blueprints.append(compact_item)

    compact_payload: dict[str, Any] = {
        "schema_version": "factory.chief_engineer_review.evidence.v1",
        "generated_blueprints": int(payload.get("generated_blueprints") or len(blueprints)),
        "total_tasks": int(payload.get("total_tasks") or len(blueprints)),
        "blueprints": blueprints,
    }
    signals = [
        {
            key: item.get(key)
            for key in ("code", "severity", "detail", "task_id")
            if isinstance(item, dict) and item.get(key) not in (None, "", [], {})
        }
        for item in list(payload.get("signals") or [])[:8]
        if isinstance(item, dict)
    ]
    if signals:
        compact_payload["signals"] = signals
    return json.dumps(compact_payload, ensure_ascii=False, indent=2)
