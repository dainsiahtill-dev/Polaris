"""Cartographer: narrative RunCard enrichment from current WorkingState.

The host owns every state field. Cartographer only writes the readable layer
(phase intent, completed facts, blocker, next actions) and is discarded when it
disagrees with durable facts.
"""

from __future__ import annotations

import asyncio
import json
import re
import urllib.request
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

SCHEMA_VERSION = "run_card_narrative.v1"
PHASES = {"DIAGNOSE", "EDIT", "RETEST"}
_MAX_NEXT_ACTIONS = 3
_FAILURE_MARK = re.compile(
    r"(?:\bfail(?:ed|ure)?\b|\berror\b|referenceerror|assertionerror|typeerror|syntaxerror|runtimeerror|\[FAIL\]|✗)",
    re.IGNORECASE,
)


class CartographerPool:
    """Small Kimi client for short, structured RunCard narratives."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _post(self, request: dict[str, Any]) -> str:
        system = (
            "You are the Cartographer for ContextOS. Draw the current engineering stage as a short map. "
            "Return only one JSON object with schema_version,phase,intent,completed,blocker,next_actions,evidence_refs,uncertain,reason. "
            "phase must be DIAGNOSE, EDIT or RETEST. State fields must match the supplied WorkingState exactly. "
            "Do not invent failures or evidence. Keep reason within 160 characters and each completed item within 80 characters."
        )
        body = {
            "model": self.model,
            "max_tokens": 900,
            "temperature": 0,
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
        content = payload.get("content") or []
        text = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if not text:
            raise ValueError("Cartographer returned no text")
        return text

    async def draw_many(self, requests: list[dict[str, Any]]) -> list[str]:
        results: list[str] = []
        for request in requests:
            async with self._lock:
                try:
                    results.append(await asyncio.to_thread(self._post, request))
                except (OSError, ValueError, asyncio.TimeoutError):
                    results.append("")
        return results


def cartographer_pool_from_environment() -> CartographerPool | None:
    """Bind the cartographer role from the unified LLM config."""

    path = Path(__import__("os").environ.get("KERNELONE_LLM_CONFIG", "/home/dains/.polaris/config/llm/llm_config.json"))
    if not path.is_file():
        return None
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
        role = config["roles"]["cartographer"]
        provider = config["providers"][str(role["provider_id"])]
        api_key = str(provider.get("api_key") or "")
        if not api_key:
            return None
        return CartographerPool(
            base_url=str(provider["base_url"]),
            api_key=api_key,
            model=str(role["model"]),
        )
    except (KeyError, TypeError, ValueError, OSError, json.JSONDecodeError):
        return None


def _text(content: object) -> str:
    if not isinstance(content, str):
        return ""
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return content
    if not isinstance(payload, dict):
        return content
    card = payload.get("command_card")
    if not isinstance(card, dict):
        return content
    return "\n".join(str(card.get(key) or "") for key in ("stdout_preview", "stderr_preview", "summary"))


def current_facts(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Host-owned facts the narrative must agree with."""

    verification = str(run.get("verification") or "not_run")
    revision = run.get("workspace_revision")
    phase = "DIAGNOSE"
    for event in reversed(events):
        if event["kind"] != "request_prepared":
            continue
        request = event["payload"].get("request") or {}
        context = request.get("context") if isinstance(request, dict) else None
        if isinstance(context, dict) and str(context.get("phase") or "") in PHASES:
            phase = str(context["phase"])
        break
    signatures: set[str] = set()
    evidence: set[str] = set()
    for event in events:
        payload = event["payload"]
        if event["kind"] == "verification_completed":
            for check in payload.get("checks") or []:
                text = _text(check.get("content") if isinstance(check, dict) else None)
                for line in text.splitlines():
                    stripped = line.strip()
                    if stripped and not stripped.startswith("[PASS]") and _FAILURE_MARK.search(stripped):
                        signatures.add(re.sub(r"\s+", " ", stripped).split("——", 1)[0].strip()[:240])
        for key in ("call_id", "content_ref"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                evidence.add(value)
    return {
        "verification": verification,
        "workspace_revision": revision,
        "phase": phase if phase in PHASES else "DIAGNOSE",
        "failure_signatures": signatures,
        "evidence_refs": evidence,
    }


def facts_for_run_card(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], str]:
    facts = current_facts(run, events)
    request = {
        "working_state": {
            "verification": facts["verification"],
            "workspace_revision": facts["workspace_revision"],
            "phase": facts["phase"],
            "status": run.get("status"),
        },
        "recent_activity": [
            {
                "kind": event["kind"],
                "tool": event["payload"].get("tool"),
                "ok": event["payload"].get("ok"),
            }
            for event in events[-40:]
        ],
        "failure_signatures": sorted(facts["failure_signatures"])[:8],
        "evidence_refs": sorted(facts["evidence_refs"])[:24],
    }
    fingerprint = f"{facts['verification']}:{facts['workspace_revision']}:{facts['phase']}:{len(events)}"
    return request, fingerprint


def validate_run_card_narrative(
    payload: object,
    facts: Mapping[str, Any],
    *,
    allowed_refs: set[str],
) -> dict[str, Any] | None:
    """Return a bounded narrative or None when it disagrees with durable facts."""

    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") != SCHEMA_VERSION:
        return None
    phase = data.get("phase")
    if phase not in PHASES or phase != facts["phase"]:
        return None
    completed = data.get("completed")
    if not isinstance(completed, list) or len(completed) > 8:
        return None
    completed = [str(item)[:80] for item in completed]
    next_actions = data.get("next_actions")
    if not isinstance(next_actions, list) or len(next_actions) > _MAX_NEXT_ACTIONS:
        return None
    normalized_actions: list[dict[str, str]] = []
    for item in next_actions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        normalized_actions.append(
            {
                "action": action[:160],
                "why": str(item.get("why") or "")[:160],
                "expected": str(item.get("expected") or "")[:160],
            }
        )
    normalized_actions = normalized_actions[:_MAX_NEXT_ACTIONS]
    blocker = data.get("blocker")
    if blocker is None:
        blocker_payload = None
    else:
        if not isinstance(blocker, dict):
            return None
        signature = str(blocker.get("signature") or "")
        if signature and signature not in facts["failure_signatures"]:
            return None
        blocker_payload = {
            "signature": signature[:240],
            "occurrences": int(blocker.get("occurrences") or 0),
            "candidate_revisions": int(blocker.get("candidate_revisions") or 0),
        }
    refs = data.get("evidence_refs")
    if not isinstance(refs, list):
        return None
    evidence_refs = [str(ref) for ref in refs if str(ref) in allowed_refs][:12]
    reason = str(data.get("reason") or "")
    if len(reason) > 160:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "phase": phase,
        "intent": str(data.get("intent") or "")[:160],
        "completed": completed,
        "blocker": blocker_payload,
        "next_actions": normalized_actions,
        "evidence_refs": evidence_refs,
        "uncertain": bool(data.get("uncertain", True)),
        "reason": reason,
    }


class CartographerRuntime:
    """Coalesce one low-frequency narrative job per stage change."""

    def __init__(self, pool: CartographerPool) -> None:
        self.pool = pool
        self._job: asyncio.Task[list[str]] | None = None
        self._fingerprint = ""
        self._request: dict[str, Any] = {}

    def schedule(self, request: dict[str, Any], *, fingerprint: str) -> None:
        # Never abandon an in-flight draw: the model may take longer than one
        # turn, and cancelling on every fingerprint change would starve it.
        if self._job is not None and not self._job.done():
            return
        if fingerprint == self._fingerprint:
            return
        self._fingerprint = fingerprint
        self._request = request
        self._job = asyncio.create_task(self.pool.draw_many([request]))

    def poll(self) -> str | None:
        if self._job is None or not self._job.done():
            return None
        try:
            results = self._job.result()
        except asyncio.CancelledError:
            return None
        return results[0] if results else None

    @property
    def fingerprint(self) -> str:
        return self._fingerprint
