"""Resident AGI: model-driven project type and capability decisions.

Deterministic code only collects repository signals. The model decides the
project type, capabilities, MCP hints and guidance, and must justify them.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_project_profile import (
    _SIGNATURES,
    _detect_binaries,
    _existing_patterns,
)

SCHEMA_VERSION = "resident_agi_decision.v1"
VALID_TYPES = {kind for _name, kind, _patterns in _SIGNATURES} | {"generic"}
VALID_STAGES = {"DIAGNOSE", "EDIT", "VERIFY", "RETEST", "ANY"}
_VALIDATION_RE = re.compile(r"[^a-z0-9_]+")


def _has_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(text), dict)
    except (json.JSONDecodeError, TypeError):
        return False


class ResidentAGIPool:
    """Kimi client for short structured Resident AGI decisions."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _post(self, messages: list[dict[str, str]]) -> str:
        system = (
            "You are the Resident AGI for Polaris. Decide the project type and the capability binding "
            "for the supplied engineering stage from the repository signals. Return ONLY one JSON object with "
            "schema_version, project_type, stage, capabilities, mcp_hints, guidance, reason. "
            "project_type must be one of: web, node, python, rust, go, java, cpp, embedded, "
            "reverse_engineering, generic. stage must be one of: DIAGNOSE, EDIT, VERIFY, RETEST, ANY. "
            "capabilities must be host tool names relevant to the stage. "
            "Do not invent files or tools. Keep reason within 200 characters and guidance within 200 characters."
        )
        body = {
            "model": self.model,
            "max_tokens": 1200,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "messages": [{"role": "system", "content": system}, *messages],
        }
        http_request = urllib.request.Request(
            self.base_url.rstrip("/") + "/v1/messages",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.api_key,
                "anthropic-version": "2023-06-01",
            },
        )
        with urllib.request.urlopen(http_request, timeout=self.timeout_seconds) as response:
            payload = json.load(response)
        blocks = payload.get("content") or []
        text = "".join(str(item.get("text") or "") for item in blocks if isinstance(item, dict))
        if not text:
            thinking = "".join(str(item.get("thinking") or "") for item in blocks if isinstance(item, dict))
            start, end = thinking.find("{"), thinking.rfind("}")
            if start >= 0 and end > start:
                return thinking[start : end + 1]
            raise ValueError("Resident AGI returned no text")
        return text

    def decide(self, request: dict[str, Any]) -> str:
        """Ask once, then correct once: the small model often answers in prose."""

        messages: list[dict[str, str]] = [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
        text = ""
        for _ in range(2):
            text = self._post(messages)
            if _has_json_object(text):
                return text
            messages = [
                *messages,
                {"role": "assistant", "content": text},
                {
                    "role": "user",
                    "content": (
                        "The previous response was not a JSON object. Return only the JSON object with "
                        "schema_version, project_type, stage, capabilities, mcp_hints, guidance, reason."
                    ),
                },
            ]
        return text


def resident_agi_pool_from_environment() -> ResidentAGIPool | None:
    """Bind the resident_agi role from the unified LLM config."""

    path = Path(os.environ.get("KERNELONE_LLM_CONFIG", "/home/dains/.polaris/config/llm/llm_config.json"))
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        role = config["roles"]["resident_agi"]
        provider = config["providers"][str(role["provider_id"])]
        api_key = str(provider.get("api_key") or "")
        if not api_key:
            return None
        return ResidentAGIPool(
            base_url=str(provider["base_url"]),
            api_key=api_key,
            model=str(role["model"]),
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None


def collect_signals(workspace: Path) -> dict[str, Any]:
    """Deterministic signal collection; the model makes the decision."""

    root = Path(workspace)
    signals: list[str] = []
    for _name, kind, patterns in _SIGNATURES:
        matched = _existing_patterns(root, patterns)
        if matched:
            signals.extend([f"{kind}:{item}" for item in matched[:4]])
    binaries = _detect_binaries(root)
    signals.extend(f"binary:{item}" for item in binaries)
    return {
        "workspace": str(root),
        "signals": signals[:24],
        "has_package_json": (root / "package.json").exists(),
        "has_index_html": (root / "index.html").exists(),
    }


def _normalize_decision(payload: object, signals: Mapping[str, Any]) -> dict[str, Any] | None:
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    normalized = dict(data)
    project_type = _VALIDATION_RE.sub("_", str(normalized.get("project_type") or "").strip().lower()).strip("_")
    if project_type not in VALID_TYPES:
        return None
    normalized["project_type"] = project_type
    normalized["schema_version"] = SCHEMA_VERSION
    normalized["intent"] = str(normalized.get("intent") or "").strip() or "推进当前阶段"
    blocker = normalized.get("blocker")
    if isinstance(blocker, str):
        normalized["blocker"] = {"signature": blocker} if blocker else None
    elif not isinstance(blocker, (dict, type(None))):
        normalized["blocker"] = None
    actions = normalized.get("next_actions")
    if not isinstance(actions, list):
        normalized["next_actions"] = []
    completed = normalized.get("completed")
    if not isinstance(completed, list):
        normalized["completed"] = []
    if not isinstance(normalized.get("evidence_refs"), list):
        normalized["evidence_refs"] = []
    capabilities = normalized.get("capabilities")
    normalized["capabilities"] = _map_capabilities(capabilities) if isinstance(capabilities, list) else []
    normalized["signals"] = [str(item) for item in signals.get("signals") or []][:24]
    return normalized


def facts_for_decision(workspace: Path, events: Sequence[Mapping[str, Any]], run: Mapping[str, Any]) -> dict[str, Any]:
    stage = detect_stage(events, run)
    signals = collect_signals(workspace)
    return {
        "working_state": {
            "verification": run.get("verification"),
            "workspace_revision": run.get("workspace_revision"),
            "status": run.get("status"),
            "stage": stage,
        },
        **signals,
    }


def _map_capabilities(capabilities: Sequence[str]) -> list[str]:
    """Translate model capability names into host tool names."""

    mapped: list[str] = []
    for item in capabilities:
        raw = str(item).strip().lower()
        lowered = _VALIDATION_RE.sub("_", raw).strip("_")
        host = {
            "browser_observe": "browser_observe",
            "browser": "browser_observe",
            "playwright": "browser_observe",
            "web": "browser_observe",
            "web:frontend": "browser_observe",
            "frontend": "browser_observe",
            "node": "",
            "node:runtime": "",
            "python": "",
            "rust": "",
            "go": "",
        }.get(raw) or {
            "browser_observe": "browser_observe",
            "browser": "browser_observe",
            "playwright": "browser_observe",
            "web": "browser_observe",
            "web:frontend": "browser_observe",
            "frontend": "browser_observe",
        }.get(lowered, "")
        if not host:
            host = lowered or ""
        if host and host not in mapped:
            mapped.append(host)
    return mapped


def detect_stage(events: Sequence[Mapping[str, Any]], run: Mapping[str, Any]) -> str:
    """Deterministic stage inference from durable facts."""

    verification = str(run.get("verification") or "not_run")
    recent = events[-12:]
    tools = [event["payload"].get("tool") for event in recent if event.get("kind") == "tool_observed"]
    if "verify_project" in tools:
        return "VERIFY"
    if verification in {"passed", "failed"}:
        return "RETEST"
    if "write_file" in tools or "edit_file" in tools:
        return "EDIT"
    return "DIAGNOSE"


class ResidentAGIRuntime:
    """One asynchronous decision per run, made by the model."""

    def __init__(self, pool: ResidentAGIPool) -> None:
        self.pool = pool
        self._job: asyncio.Task[str] | None = None

    @property
    def done(self) -> bool:
        """A job exists and has finished; it still must be consumed."""

        return self._job is not None and self._job.done()

    @property
    def idle(self) -> bool:
        """True only when no job exists.

        A finished-but-unconsumed job is not idle: treating it as idle lets a new
        start discard a decision the run has already paid for.
        """

        return self._job is None

    def start(self, request: dict[str, Any]) -> bool:
        """Schedule one decision; an existing job is never abandoned or replaced."""

        if self._job is not None:
            return False
        self._job = asyncio.create_task(asyncio.to_thread(self.pool.decide, request))
        return True

    def poll(self) -> str | None:
        """Return a finished decision once, then consume it.

        Returning the same finished job every turn made the caller re-read a
        stale first decision forever instead of asking again with fresh signals.
        """

        if self._job is None or not self._job.done():
            return None
        job, self._job = self._job, None
        try:
            return job.result()
        except asyncio.CancelledError:
            return None
        except (OSError, ValueError, asyncio.TimeoutError):
            return None
