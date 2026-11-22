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
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_project_profile import (
    _SIGNATURES,
    _detect_binaries,
    _existing_patterns,
)

SCHEMA_VERSION = "resident_agi_decision.v1"
VALID_TYPES = {kind for _name, kind, _patterns in _SIGNATURES} | {"generic"}
_VALIDATION_RE = re.compile(r"[^a-z0-9_]+")


class ResidentAGIPool:
    """Kimi client for short structured Resident AGI decisions."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _post(self, request: dict[str, Any]) -> str:
        system = (
            "You are the Resident AGI for Polaris. Decide the project type and the capability binding "
            "from the supplied repository signals. Return ONLY one JSON object with schema_version, "
            "project_type, capabilities, mcp_hints, guidance, reason. "
            "project_type must be one of: web, node, python, rust, go, java, cpp, embedded, "
            "reverse_engineering, generic. State fields must match the signals. "
            "Do not invent files or tools. Keep reason within 200 characters and guidance within 200 characters."
        )
        body = {
            "model": self.model,
            "max_tokens": 1200,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(request, ensure_ascii=False)},
            ],
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


def resident_agi_pool_from_environment() -> ResidentAGIPool | None:
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
    project_type = str(data.get("project_type") or "").strip().lower()
    project_type = _VALIDATION_RE.sub("_", project_type).strip("_")
    if project_type not in VALID_TYPES:
        return None
    capabilities = data.get("capabilities")
    if not isinstance(capabilities, list):
        return None
    capabilities = [str(item)[:80] for item in capabilities][:8]
    mcp_hints = data.get("mcp_hints")
    mcp_hints = [str(item)[:80] for item in mcp_hints][:8] if isinstance(mcp_hints, list) else []
    guidance = str(data.get("guidance") or "").strip()
    reason = str(data.get("reason") or "").strip()
    if len(guidance) > 200 or len(reason) > 200:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "project_type": project_type,
        "capabilities": capabilities,
        "mcp_hints": mcp_hints,
        "guidance": guidance,
        "reason": reason,
        "signals": [str(item) for item in signals.get("signals") or []][:24],
    }


class ResidentAGIRuntime:
    """One asynchronous decision per run, made by the model."""

    def __init__(self, pool: ResidentAGIPool) -> None:
        self.pool = pool
        self._job: asyncio.Task[str] | None = None

    def start(self, request: dict[str, Any]) -> None:
        if self._job is not None and not self._job.done():
            return
        self._job = asyncio.create_task(asyncio.to_thread(self.pool._post, request))

    def poll(self) -> str | None:
        if self._job is None or not self._job.done():
            return None
        try:
            return self._job.result()
        except asyncio.CancelledError:
            return None
        except (OSError, ValueError, asyncio.TimeoutError):
            return None
