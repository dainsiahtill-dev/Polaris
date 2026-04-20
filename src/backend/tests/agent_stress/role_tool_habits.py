"""Single-role tool habit stress harness.

This module exercises one Polaris role against a fresh seeded workspace,
captures streamed tool calls/results, and classifies common LLM tool-use habits
that still need normalization.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import secrets
import sys
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tests.agent_stress.paths import ensure_backend_root_on_syspath

ensure_backend_root_on_syspath()

import httpx
from tests.agent_stress.backend_bootstrap import (
    BackendBootstrapError,
    ensure_backend_session,
)
from tests.agent_stress.stress_path_policy import (
    default_stress_runtime_root,
    default_stress_workspace_base,
    ensure_stress_runtime_root,
    ensure_stress_workspace_path,
)

SUPPORTED_ROLES = ("architect", "pm", "chief_engineer", "director", "qa")
DEFAULT_ROLE = "qa"
DEFAULT_SCENARIO = "tool_habits"
DEFAULT_STREAM_TIMEOUT_SECONDS = 180.0
DEFAULT_WINDOWS_POLARIS_HOME = Path(r"C:\Users\dains\.polaris")
DEFAULT_RUNTIME_NAME = "tests-agent-stress-runtime"
_PROMPT_SEPARATOR_RE = re.compile(r"\r?\n---+\r?\n")
_SHELL_OPERATOR_RE = re.compile(r"(\|\||&&|[|;])")
_MARKDOWN_NOISE_RE = re.compile(r"(\*\*|```|`[^`]+`|\n\s*[*-]{1,2}\s*$)")
_SEARCH_QUERY_ALIASES = {"key", "needle", "term"}
_READONLY_COMMAND_ALIASES = {"ls", "dir", "tree", "find", "pwd"}
_ALLOWED_TOOL_ARGUMENTS = {
    "search_code": {
        "query",
        "path",
        "recursive",
        "max",
        "max_results",
        "max_lines",
        "case_sensitive",
        "file_pattern",
        "file_patterns",
        "type",
    },
    "read_file": {
        "path",
        "file",
        "file_path",
        "start_line",
        "end_line",
        "max_lines",
        "encoding",
    },
    "execute_command": {
        "command",
        "cmd",
        "timeout",
        "timeout_ms",
        "timeout_milliseconds",
        "working_directory",
        "cwd",
        "path",
        "directory",
        "shell",
        "use_shell",
        "key",
        "value",
        "arguments",
        "params",
        "payload",
    },
}


def _write_utf8(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, payload: Mapping[str, Any] | Sequence[Any]) -> None:
    serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    _write_utf8(path, serialized)


def _timestamp_token() -> str:
    return f"{time.strftime('%Y%m%d_%H%M%S')}_{secrets.token_hex(4)}"


def _default_polaris_home() -> Path:
    configured = str(os.environ.get("POLARIS_HOME") or "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    if os.name == "nt":
        return DEFAULT_WINDOWS_POLARIS_HOME
    return (Path.home() / ".polaris").resolve()


def build_default_prompts(role: str) -> list[str]:
    normalized_role = str(role or DEFAULT_ROLE).strip().lower()
    common = [
        (
            "You are doing read-only reconnaissance. Inspect the project layout, "
            "the src directory, tests, and configuration files before answering. "
            "Do not guess. Use tools first, then summarize the structure."
        ),
        (
            "Continue in read-only mode. Search for TODO or FIXME markers, detect "
            "how tests would likely be executed, and report the tool steps you had "
            "to take before concluding."
        ),
    ]
    role_specific = {
        "qa": (
            "Now do a quick QA review. Read a few core files, confirm whether tests "
            "exist, and explain the top three quality risks. Use tools before every "
            "claim."
        ),
        "director": (
            "Now do an execution preflight. Inspect the files that would matter for "
            "implementation, identify the safest first edit target, and explain the "
            "evidence you collected with tools."
        ),
        "architect": (
            "Now do an architecture pass. Inspect the module boundaries, config "
            "surfaces, and likely dependency entrypoints before proposing a design "
            "summary."
        ),
        "pm": (
            "Now do a planning pass. Inspect the workspace, identify deliverable "
            "areas, and draft a task breakdown backed by tool-based evidence."
        ),
        "chief_engineer": (
            "Now do a technical review. Inspect the codebase, test entrypoints, and "
            "configuration hot spots before listing the main implementation risks."
        ),
    }
    return [*common, role_specific.get(normalized_role, common[-1])]


def load_prompt_corpus(path: str | Path) -> list[str]:
    prompt_path = Path(path).expanduser().resolve()
    raw = prompt_path.read_text(encoding="utf-8")
    stripped = raw.strip()
    if not stripped:
        return []
    if prompt_path.suffix.lower() == ".json" or stripped[:1] in {"[", "{"}:
        payload = json.loads(stripped)
        if isinstance(payload, list):
            return [str(item).strip() for item in payload if str(item).strip()]
        if isinstance(payload, Mapping):
            prompts = payload.get("prompts")
            if isinstance(prompts, list):
                return [str(item).strip() for item in prompts if str(item).strip()]
        raise ValueError(f"Unsupported prompt corpus shape: {prompt_path}")
    return [segment.strip() for segment in _PROMPT_SEPARATOR_RE.split(raw) if segment.strip()]


def seed_demo_workspace(workspace: Path) -> list[str]:
    files: dict[str, str] = {
        "README.md": (
            "# Expense Tracker Demo\n\n"
            "This workspace is seeded for single-role tool habit stress checks.\n"
            "The role should inspect structure, tests, configuration, and TODO markers.\n"
        ),
        "pyproject.toml": (
            "[project]\n"
            'name = "expense-tracker-demo"\n'
            'version = "0.1.0"\n'
            'description = "Demo workspace for role tool habit probing"\n'
            "requires-python = \">=3.11\"\n\n"
            "[tool.pytest.ini_options]\n"
            'pythonpath = ["."]\n'
            'testpaths = ["tests"]\n'
        ),
        "src/config.py": (
            'APP_NAME = "expense-tracker-demo"\n'
            'DEFAULT_CURRENCY = "TWD"\n'
            "ENABLE_AUDIT = True\n"
        ),
        "src/ledger.py": (
            "from __future__ import annotations\n\n"
            "from dataclasses import dataclass\n\n"
            "# TODO: validate negative totals before settlement.\n\n"
            "@dataclass(slots=True)\n"
            "class LedgerEntry:\n"
            "    label: str\n"
            "    amount: int\n\n"
            "def total_amount(entries: list[LedgerEntry]) -> int:\n"
            "    return sum(item.amount for item in entries)\n"
        ),
        "src/service.py": (
            "from __future__ import annotations\n\n"
            "from src.ledger import LedgerEntry, total_amount\n\n"
            "def summarize_expenses(entries: list[LedgerEntry]) -> dict[str, int]:\n"
            "    amount = total_amount(entries)\n"
            '    return {"count": len(entries), "total": amount}\n'
        ),
        "tests/test_service.py": (
            "from src.ledger import LedgerEntry\n"
            "from src.service import summarize_expenses\n\n"
            "def test_summarize_expenses_counts_entries() -> None:\n"
            '    result = summarize_expenses([LedgerEntry(label="lunch", amount=120)])\n'
            '    assert result == {"count": 1, "total": 120}\n'
        ),
        ".env.example": "APP_ENV=development\nENABLE_AUDIT=true\n",
    }
    written: list[str] = []
    for relative_path, content in files.items():
        target = workspace / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative_path)
    return written


def _extract_command_text(args: Mapping[str, Any]) -> str:
    for key in ("command", "cmd", "value"):
        value = str(args.get(key) or "").strip()
        if value:
            return value
    key_value = str(args.get("key") or "").strip()
    if key_value.startswith("command:value:"):
        return key_value.split("command:value:", 1)[1].strip().strip('"')
    return ""


def _extract_result_error(raw_result: Any) -> str:
    if isinstance(raw_result, Mapping):
        direct_error = str(raw_result.get("error") or "").strip()
        if direct_error:
            return direct_error
        nested = raw_result.get("result")
        if isinstance(nested, Mapping):
            nested_error = str(nested.get("error") or "").strip()
            if nested_error:
                return nested_error
    return ""


def _extract_result_success(raw_result: Any) -> bool | None:
    if isinstance(raw_result, Mapping):
        if isinstance(raw_result.get("success"), bool):
            return bool(raw_result.get("success"))
        if isinstance(raw_result.get("ok"), bool):
            return bool(raw_result.get("ok"))
        nested = raw_result.get("result")
        if isinstance(nested, Mapping) and isinstance(nested.get("ok"), bool):
            return bool(nested.get("ok"))
    return None


def _first_command_token(command: str) -> str:
    stripped = str(command or "").strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0].lower()


def _scalar_noise_keys(args: Mapping[str, Any]) -> list[str]:
    noisy: list[str] = []
    for key, value in args.items():
        text = str(value or "")
        if text and _MARKDOWN_NOISE_RE.search(text):
            noisy.append(str(key))
    return noisy


def analyze_tool_interaction(
    *,
    tool: str,
    args: Mapping[str, Any] | None,
    raw_result: Any,
    prompt_index: int,
) -> list[dict[str, Any]]:
    tool_name = str(tool or "").strip()
    tool_args = dict(args or {})
    findings: list[dict[str, Any]] = []
    error = _extract_result_error(raw_result)

    def add(category: str, summary: str, *, status: str) -> None:
        findings.append(
            {
                "category": category,
                "status": status,
                "summary": summary,
                "tool": tool_name,
                "prompt_index": prompt_index,
                "error": error,
                "args": tool_args,
            }
        )

    noise_keys = _scalar_noise_keys(tool_args)
    if noise_keys:
        add(
            "markdown_scalar_noise",
            f"Scalar arguments contain markdown or prompt residue: {', '.join(noise_keys)}",
            status="failed" if error else "observed",
        )

    allowed_args = _ALLOWED_TOOL_ARGUMENTS.get(tool_name, set())
    if allowed_args:
        unknown_keys = sorted(str(key) for key in tool_args if str(key) not in allowed_args)
        if unknown_keys:
            add(
                "unknown_tool_arguments",
                f"Tool call included extra arguments that common LLMs may emit: {', '.join(unknown_keys)}",
                status="failed" if error else "observed",
            )

    if tool_name == "search_code":
        alias_keys = sorted(_SEARCH_QUERY_ALIASES.intersection(tool_args))
        if alias_keys and "query" not in tool_args:
            add(
                "search_query_alias",
                f"search_code used alias keys instead of query: {', '.join(alias_keys)}",
                status="failed" if error else "observed",
            )
        if "Missing required parameter: query" in error:
            add(
                "search_query_validation_failure",
                "search_code still rejected a common alias-style payload because query was missing.",
                status="failed",
            )

    if tool_name == "execute_command":
        command = _extract_command_text(tool_args)
        command_token = _first_command_token(command)
        if command_token in _READONLY_COMMAND_ALIASES:
            add(
                "readonly_shell_alias",
                f"execute_command received a shell-style read-only command: {command_token}",
                status="failed" if error else "tolerated",
            )
        if _SHELL_OPERATOR_RE.search(command):
            add(
                "shell_operator_composition",
                "execute_command received shell operator composition that many LLMs emit by default.",
                status="failed" if error else "observed",
            )
        if not command and "Missing command" in error:
            add(
                "missing_command_payload",
                "execute_command received an empty or malformed payload.",
                status="failed",
            )
        if "Executable is not allowed" in error:
            add(
                "disallowed_executable",
                "execute_command attempted a shell alias or executable outside the allowlist.",
                status="failed",
            )
        if "Shell operators are not allowed" in error:
            add(
                "shell_operator_rejected",
                "execute_command rejected a piped or compound shell command.",
                status="failed",
            )

    if tool_name == "read_file":
        file_path = str(tool_args.get("path") or tool_args.get("file") or "").strip()
        if file_path.startswith("/workspace/") and "包含穿越序列" in error:
            add(
                "workspace_alias_path_rejected",
                "read_file rejected a common /workspace/... alias as traversal instead of mapping it into the workspace.",
                status="failed",
            )

    return findings


def allocate_fresh_workspace(requested: str | Path | None) -> Path:
    if requested is None:
        return ensure_stress_workspace_path(
            default_stress_workspace_base(f"hp-role-tool-habits-{_timestamp_token()}")
        )

    candidate = Path(requested).expanduser().resolve()
    if candidate.exists():
        try:
            next(candidate.iterdir())
        except StopIteration:
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        raise ValueError(f"Workspace is not fresh: {candidate}")
    return ensure_stress_workspace_path(candidate)


@dataclass
class ToolInteraction:
    tool: str
    args: dict[str, Any] = field(default_factory=dict)
    raw_result: Any = None
    success: bool | None = None
    error: str = ""
    result_payload: Any = None
    findings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "success": self.success,
            "error": self.error,
            "result_payload": self.result_payload,
            "raw_result": self.raw_result,
            "findings": self.findings,
        }


@dataclass
class TurnRecord:
    prompt_index: int
    prompt: str
    completed: bool = False
    error: str = ""
    assistant_content: str = ""
    assistant_thinking: str = ""
    stream_events: list[dict[str, Any]] = field(default_factory=list)
    tool_interactions: list[ToolInteraction] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "prompt_index": self.prompt_index,
            "prompt": self.prompt,
            "completed": self.completed,
            "error": self.error,
            "assistant_content": self.assistant_content,
            "assistant_thinking": self.assistant_thinking,
            "stream_events": self.stream_events,
            "tool_interactions": [item.to_dict() for item in self.tool_interactions],
        }


@dataclass
class ToolHabitRunReport:
    role: str
    scenario: str
    workspace: str
    ramdisk_root: str
    polaris_home: str
    backend_url: str
    auto_bootstrapped: bool
    role_status: dict[str, Any]
    seeded_files: list[str]
    prompts: list[str]
    turns: list[TurnRecord]
    messages: list[dict[str, Any]]
    audit_events: list[dict[str, Any]]
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        summary = build_summary(self.turns)
        return {
            "schema_version": "1.0.0",
            "generated_at": self.generated_at,
            "role": self.role,
            "scenario": self.scenario,
            "workspace": self.workspace,
            "ramdisk_root": self.ramdisk_root,
            "polaris_home": self.polaris_home,
            "backend_url": self.backend_url,
            "auto_bootstrapped": self.auto_bootstrapped,
            "role_status": self.role_status,
            "seeded_files": self.seeded_files,
            "prompts": self.prompts,
            "summary": summary,
            "turns": [turn.to_dict() for turn in self.turns],
            "messages": self.messages,
            "audit_events": self.audit_events,
        }

    def to_markdown(self) -> str:
        summary = build_summary(self.turns)
        lines = [
            "# Single Role Tool Habit Report",
            "",
            f"- Role: `{self.role}`",
            f"- Scenario: `{self.scenario}`",
            f"- Workspace: `{self.workspace}`",
            f"- Ramdisk root: `{self.ramdisk_root}`",
            f"- Polaris home: `{self.polaris_home}`",
            f"- Auto-bootstrapped backend: `{self.auto_bootstrapped}`",
            f"- Total tool calls: `{summary['total_tool_calls']}`",
            f"- Failed tool calls: `{summary['failed_tool_calls']}`",
            "",
            "## Failed gaps",
            "",
        ]
        failed_categories = summary["failed_categories"]
        if not failed_categories:
            lines.append("- None")
        else:
            for category, count in failed_categories.items():
                lines.append(f"- `{category}`: {count}")
        lines.extend(["", "## Tolerated habits", ""])
        tolerated_habits = summary["tolerated_habits"]
        if not tolerated_habits:
            lines.append("- None")
        else:
            for category, count in tolerated_habits.items():
                lines.append(f"- `{category}`: {count}")
        lines.extend(["", "## Observed habits", ""])
        observed_habits = summary["observed_habits"]
        if not observed_habits:
            lines.append("- None")
        else:
            for category, count in observed_habits.items():
                lines.append(f"- `{category}`: {count}")
        lines.extend(["", "## Tools seen", ""])
        for tool_name, count in summary["tools_seen"].items():
            lines.append(f"- `{tool_name}`: {count}")
        return "\n".join(lines) + "\n"


def build_summary(turns: Sequence[TurnRecord]) -> dict[str, Any]:
    tool_counter: Counter[str] = Counter()
    failed_counter: Counter[str] = Counter()
    tolerated_counter: Counter[str] = Counter()
    observed_counter: Counter[str] = Counter()
    failed_tool_calls = 0
    total_tool_calls = 0
    for turn in turns:
        for interaction in turn.tool_interactions:
            total_tool_calls += 1
            tool_counter[interaction.tool or "unknown"] += 1
            if interaction.success is False or interaction.error:
                failed_tool_calls += 1
            for finding in interaction.findings:
                category = str(finding.get("category") or "unknown")
                status = str(finding.get("status") or "observed").strip().lower()
                if status == "failed":
                    failed_counter[category] += 1
                elif status == "tolerated":
                    tolerated_counter[category] += 1
                else:
                    observed_counter[category] += 1
    return {
        "turns": len(turns),
        "total_tool_calls": total_tool_calls,
        "failed_tool_calls": failed_tool_calls,
        "tools_seen": dict(tool_counter),
        "failed_categories": dict(failed_counter),
        "tolerated_habits": dict(tolerated_counter),
        "observed_habits": dict(observed_counter),
    }


async def _request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    json_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    response = await client.request(method, url, json=json_payload)
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, Mapping):
        return dict(payload)
    raise RuntimeError(f"Unexpected JSON payload from {url}")


async def _stream_single_prompt(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    session_id: str,
    prompt: str,
    prompt_index: int,
) -> TurnRecord:
    record = TurnRecord(prompt_index=prompt_index, prompt=prompt)
    pending_interactions: list[ToolInteraction] = []
    url = f"{base_url}/v2/roles/sessions/{session_id}/messages/stream"

    def _normalize_sse_payload(event_type: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        normalized_type = str(event_type or payload.get("type") or "").strip()
        if not normalized_type:
            return dict(payload)
        if normalized_type == "content_chunk":
            return {"type": normalized_type, "content": str(payload.get("content") or "")}
        if normalized_type == "thinking_chunk":
            thinking = payload.get("thinking")
            if thinking is None:
                thinking = payload.get("content")
            return {"type": normalized_type, "thinking": str(thinking or "")}
        if normalized_type == "tool_call":
            return {"type": normalized_type, "tool": payload}
        if normalized_type == "tool_result":
            return {"type": normalized_type, "result": payload}
        if normalized_type == "error":
            return {"type": normalized_type, "error": str(payload.get("error") or "")}
        return {"type": normalized_type, **dict(payload)}

    async with client.stream(
        "POST",
        url,
        json={"role": "user", "content": prompt},
    ) as response:
        response.raise_for_status()
        pending_event_type = ""
        async for raw_line in response.aiter_lines():
            line = str(raw_line or "").strip()
            if not line:
                pending_event_type = ""
                continue
            if line.startswith("event:"):
                pending_event_type = line.split("event:", 1)[1].strip()
                continue
            if not line.startswith("data:"):
                continue
            payload_text = line.split("data:", 1)[1].strip()
            if not payload_text:
                continue
            payload = json.loads(payload_text)
            if isinstance(payload, Mapping):
                normalized_payload = _normalize_sse_payload(pending_event_type, payload)
            else:
                normalized_payload = {"type": pending_event_type or "message", "data": payload}
            record.stream_events.append(normalized_payload)
            event_type = str(normalized_payload.get("type") or "").strip()
            if event_type == "content_chunk":
                record.assistant_content += str(normalized_payload.get("content") or "")
                continue
            if event_type == "thinking_chunk":
                record.assistant_thinking += str(normalized_payload.get("thinking") or "")
                continue
            if event_type == "tool_call":
                raw_tool = normalized_payload.get("tool")
                if isinstance(raw_tool, Mapping):
                    interaction = ToolInteraction(
                        tool=str(raw_tool.get("tool") or raw_tool.get("name") or "").strip(),
                        args=dict(raw_tool.get("args") or raw_tool.get("arguments") or {}),
                    )
                else:
                    interaction = ToolInteraction(tool=str(raw_tool or "").strip())
                record.tool_interactions.append(interaction)
                pending_interactions.append(interaction)
                continue
            if event_type == "tool_result":
                raw_result = normalized_payload.get("result")
                target = pending_interactions.pop(0) if pending_interactions else ToolInteraction(tool="unknown")
                target.raw_result = raw_result
                target.success = _extract_result_success(raw_result)
                target.error = _extract_result_error(raw_result)
                if isinstance(raw_result, Mapping):
                    target.result_payload = raw_result.get("result")
                target.findings = analyze_tool_interaction(
                    tool=target.tool,
                    args=target.args,
                    raw_result=raw_result,
                    prompt_index=prompt_index,
                )
                if target not in record.tool_interactions:
                    record.tool_interactions.append(target)
                continue
            if event_type == "error":
                record.error = str(normalized_payload.get("error") or "stream_error")
                continue
            if event_type == "complete":
                record.completed = True
    return record


async def run_single_role_tool_habit_probe(
    *,
    role: str,
    workspace: Path,
    ramdisk_root: Path,
    polaris_home: Path,
    prompts: Sequence[str],
    scenario: str,
    backend_url: str = "",
    token: str = "",
    timeout_seconds: float = DEFAULT_STREAM_TIMEOUT_SECONDS,
) -> ToolHabitRunReport:
    os.environ["POLARIS_HOME"] = str(polaris_home)
    os.environ.setdefault("POLARIS_STATE_TO_RAMDISK", "1")

    seeded_files = seed_demo_workspace(workspace)

    managed_session = await ensure_backend_session(
        backend_url=backend_url,
        token=token,
        auto_bootstrap=True,
        startup_workspace=workspace,
        ramdisk_root=ramdisk_root,
    )

    async with managed_session:
        base_url = managed_session.context.backend_url.rstrip("/")
        auth_headers = {}
        if managed_session.context.token:
            auth_headers["Authorization"] = f"Bearer {managed_session.context.token}"
        timeout = httpx.Timeout(timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(headers=auth_headers, timeout=timeout) as client:
            role_status = await _request_json(
                client,
                "GET",
                f"{base_url}/v2/role/{role}/chat/status",
            )
            session_payload = await _request_json(
                client,
                "POST",
                f"{base_url}/v2/roles/sessions",
                json_payload={
                    "role": role,
                    "workspace": str(workspace),
                    "title": f"{role}-tool-habit-{_timestamp_token()}",
                },
            )
            session_data = session_payload.get("session")
            if not isinstance(session_data, Mapping):
                raise RuntimeError(f"Session creation failed: {session_payload}")
            session_id = str(session_data.get("id") or "").strip()
            if not session_id:
                raise RuntimeError(f"Missing session id: {session_payload}")

            turns: list[TurnRecord] = []
            for prompt_index, prompt in enumerate(prompts, start=1):
                turn = await _stream_single_prompt(
                    client,
                    base_url=base_url,
                    session_id=session_id,
                    prompt=prompt,
                    prompt_index=prompt_index,
                )
                turns.append(turn)

            messages_payload = await _request_json(
                client,
                "GET",
                f"{base_url}/v2/roles/sessions/{session_id}/messages?limit=200&offset=0",
            )
            audit_payload = await _request_json(
                client,
                "GET",
                f"{base_url}/v2/roles/sessions/{session_id}/audit?limit=500&offset=0",
            )

    return ToolHabitRunReport(
        role=role,
        scenario=scenario,
        workspace=str(workspace),
        ramdisk_root=str(ramdisk_root),
        polaris_home=str(polaris_home),
        backend_url=managed_session.context.backend_url,
        auto_bootstrapped=managed_session.auto_bootstrapped,
        role_status=role_status,
        seeded_files=seeded_files,
        prompts=list(prompts),
        turns=turns,
        messages=list(messages_payload.get("messages") or []),
        audit_events=list(audit_payload.get("audit_events") or []),
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Single-role tool habit probe for common LLM tool-use patterns."
    )
    parser.add_argument("--role", choices=SUPPORTED_ROLES, default=DEFAULT_ROLE)
    parser.add_argument("--scenario", default=DEFAULT_SCENARIO)
    parser.add_argument("--workspace", help="Fresh workspace path. Defaults to a unique C:/Temp workspace.")
    parser.add_argument("--ramdisk-root", help="Runtime root. Defaults to the policy-managed stress runtime root.")
    parser.add_argument("--output", help="JSON report output path.")
    parser.add_argument("--prompt", action="append", default=[], help="Inline prompt. Can be passed multiple times.")
    parser.add_argument("--prompt-file", help="UTF-8 text or JSON prompt corpus file.")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_STREAM_TIMEOUT_SECONDS)
    parser.add_argument("--backend-url", default="", help="Optional explicit backend URL.")
    parser.add_argument("--token", default="", help="Optional explicit backend token.")
    parser.add_argument(
        "--polaris-home",
        default=str(_default_polaris_home()),
        help="Global Polaris home path. Defaults to C:\\Users\\dains\\.polaris on Windows.",
    )
    parser.add_argument(
        "--exit-zero",
        action="store_true",
        help="Always exit 0 even when failed tool calls are observed.",
    )
    return parser.parse_args(argv)


def _resolve_prompts(args: argparse.Namespace) -> list[str]:
    prompts = [str(item).strip() for item in list(args.prompt or []) if str(item).strip()]
    if args.prompt_file:
        prompts.extend(load_prompt_corpus(args.prompt_file))
    if prompts:
        return prompts
    return build_default_prompts(args.role)


async def _async_main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    prompts = _resolve_prompts(args)
    workspace = allocate_fresh_workspace(args.workspace)
    ramdisk_root = ensure_stress_runtime_root(
        args.ramdisk_root or default_stress_runtime_root(DEFAULT_RUNTIME_NAME)
    )
    output_path = Path(args.output).expanduser().resolve() if args.output else workspace / "role_tool_habit_report.json"
    polaris_home = Path(args.polaris_home).expanduser().resolve()

    try:
        report = await run_single_role_tool_habit_probe(
            role=args.role,
            workspace=workspace,
            ramdisk_root=ramdisk_root,
            polaris_home=polaris_home,
            prompts=prompts,
            scenario=args.scenario,
            backend_url=args.backend_url,
            token=args.token,
            timeout_seconds=args.timeout_seconds,
        )
        payload = report.to_dict()
        _write_json(output_path, payload)
        _write_utf8(output_path.with_suffix(".md"), report.to_markdown())
        summary = payload["summary"]
        status = "PASS" if int(summary["failed_tool_calls"]) == 0 else "FAIL"
        print(f"STATUS: {status}")
        print(f"ROLE: {args.role}")
        print(f"WORKSPACE: {workspace}")
        print(f"REPORT: {output_path}")
        print(f"TOOL_CALLS: {summary['total_tool_calls']}")
        print(f"FAILED_TOOL_CALLS: {summary['failed_tool_calls']}")
        if summary["failed_categories"]:
            print("FAILED_GAPS:")
            for category, count in summary["failed_categories"].items():
                print(f"- {category}: {count}")
        if summary["tolerated_habits"]:
            print("TOLERATED_HABITS:")
            for category, count in summary["tolerated_habits"].items():
                print(f"- {category}: {count}")
        if summary["observed_habits"]:
            print("OBSERVED_HABITS:")
            for category, count in summary["observed_habits"].items():
                print(f"- {category}: {count}")
        if status == "FAIL" and not args.exit_zero:
            return 1
        return 0
    except (BackendBootstrapError, httpx.HTTPError, OSError, RuntimeError, TimeoutError, ValueError) as exc:
        failure_payload = {
            "schema_version": "1.0.0",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "FAIL",
            "role": args.role,
            "scenario": args.scenario,
            "workspace": str(workspace),
            "ramdisk_root": str(ramdisk_root),
            "polaris_home": str(polaris_home),
            "error": str(exc),
        }
        _write_json(output_path, failure_payload)
        print("STATUS: FAIL")
        print(f"ROLE: {args.role}")
        print(f"WORKSPACE: {workspace}")
        print(f"REPORT: {output_path}")
        print(f"ERROR: {exc}")
        if args.exit_zero:
            return 0
        return 1


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_async_main(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
