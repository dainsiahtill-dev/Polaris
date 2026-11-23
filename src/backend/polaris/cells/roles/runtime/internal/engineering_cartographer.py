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


_PHASE_NEXT_ACTIONS = {
    "DIAGNOSE": ("读取相关源码与现有测试，定位实现与目标的差距", "得到可定位的差异清单"),
    "EDIT": ("补齐缺失实现并跑通相关检查", "目标检查通过"),
    "RETEST": ("用 verify_project 对当前候选重新执行声明的检查", "验证结论更新为通过或给出真实失败"),
}


def deterministic_run_card(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    phase: str,
) -> dict[str, Any]:
    """宿主自己的阶段卡：全部由持久事实推导，不调用远程模型。

    真实 bench 上制图师的叙述 574 次里只有 32 次通过校验（5.6%）——38% 连合法
    JSON 都不是，62% 与持久事实冲突。只在"小模型恰好产出合法且自洽的 JSON"时
    才有卡，等于大部分轮次模型看不到任何阶段视图。这张卡永远可用；制图师通过
    校验的叙述仍然覆盖它的 ``narrative`` 字段。
    """

    facts = current_facts(run, events)
    normalized_phase = phase if phase in PHASES else facts["phase"]

    # ``tool_started`` carries the arguments, ``tool_observed`` the verdict; the
    # two have to be joined before a write can be reported as done.
    succeeded = {
        str(event["payload"].get("call_id"))
        for event in events
        if event["kind"] == "tool_observed" and event["payload"].get("ok") is True
    }
    written: list[str] = []
    seen: set[str] = set()
    for event in events:
        payload = event["payload"]
        if payload.get("tool") not in {"write_file", "edit_file"}:
            continue
        if str(payload.get("call_id")) not in succeeded:
            continue
        arguments = payload.get("arguments")
        path = arguments.get("file") if isinstance(arguments, dict) else None
        if isinstance(path, str) and path and path not in seen:
            seen.add(path)
            written.append(path)
    completed = [f"已写入 {path}" for path in written[-5:]]
    if facts["verification"] in {"passed", "failed"}:
        completed.append(f"最近验证结论：{facts['verification']}")

    occurrences: dict[str, int] = {}
    for event in events:
        if event["kind"] != "verification_completed":
            continue
        for check in event["payload"].get("checks") or []:
            text = _text(check.get("content") if isinstance(check, dict) else None)
            for candidate in facts["failure_signatures"]:
                if candidate in text:
                    occurrences[candidate] = occurrences.get(candidate, 0) + 1
    signature = ""
    signature_count = 0
    for candidate in sorted(facts["failure_signatures"]):
        if occurrences.get(candidate, 0) > signature_count:
            signature = candidate
            signature_count = occurrences[candidate]

    action, expected = _PHASE_NEXT_ACTIONS.get(normalized_phase, _PHASE_NEXT_ACTIONS["DIAGNOSE"])
    evidence = sorted(facts["evidence_refs"])[:8]
    return {
        "schema_version": "run_card_deterministic.v1",
        "phase": normalized_phase,
        "intent": f"当前阶段 {normalized_phase}（宿主由持久事实推导，非模型自述）",
        "completed": completed,
        "blocker": (
            {"signature": signature, "occurrences": signature_count, "candidate_revisions": 0} if signature else None
        ),
        "next_actions": [{"action": action, "why": "按当前阶段由宿主推导", "expected": expected}],
        "evidence_refs": evidence,
        "uncertain": False,
        "reason": "确定性阶段卡：字段直接来自工程日志。",
    }


class CartographerPool:
    """Small Kimi client for short, structured RunCard narratives."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()

    def _post(self, messages: list[dict[str, str]]) -> str:
        system = (
            "You are the Cartographer for ContextOS. Draw the current engineering stage as a short map. "
            "Return ONLY one JSON object, with no commentary and no thinking: "
            "schema_version,phase,intent,completed,blocker,next_actions,evidence_refs,uncertain,reason. "
            "phase must be DIAGNOSE, EDIT or RETEST and must match the supplied WorkingState. "
            "completed must be an array of at most 8 strings, each at most 80 characters. "
            "next_actions must be an array of at most 3 objects with action, why and expected. "
            "blocker.signature must be copied from the supplied failure_signatures array; "
            "use null when no failure is known. "
            "evidence_refs must be an array of strings drawn from the supplied evidence_refs. "
            "Do not invent failures or evidence. Keep reason within 160 characters."
        )
        body = {
            "model": self.model,
            "max_tokens": 2400,
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
        content = payload.get("content") or []
        text = "".join(str(item.get("text") or "") for item in content if isinstance(item, dict))
        if not text:
            # Some reasoning models put the answer in a thinking block when the
            # output budget is tight. Recover the JSON object rather than fail.
            thinking = "".join(str(item.get("thinking") or "") for item in content if isinstance(item, dict))
            start, end = thinking.find("{"), thinking.rfind("}")
            if start >= 0 and end > start:
                return thinking[start : end + 1]
            raise ValueError("Cartographer returned no text")
        return text

    async def draw_many(self, requests: list[dict[str, Any]]) -> list[str]:
        results: list[str] = []
        for request in requests:
            async with self._lock:
                messages: list[dict[str, str]] = [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
                text = ""
                for _ in range(2):
                    try:
                        text = await asyncio.to_thread(self._post, messages)
                    except (OSError, ValueError, asyncio.TimeoutError):
                        text = ""
                    if _has_json_object(text):
                        break
                    # The small model frequently answers in prose. One
                    # corrective ask beats losing the whole RunCard draw.
                    messages = [
                        *messages,
                        {"role": "assistant", "content": text or "{}"},
                        {
                            "role": "user",
                            "content": (
                                "The previous response was not a JSON object. Return only the JSON object "
                                "with schema_version,phase,intent,completed,blocker,next_actions,evidence_refs,"
                                "uncertain,reason, and no other text."
                            ),
                        },
                    ]
                results.append(text)
        return results


def cartographer_pool_from_environment() -> CartographerPool | None:
    """Bind the cartographer role, falling back to the advisor endpoint.

    Any instance must be able to draw a RunCard. When the cartographer role is
    absent from the unified config, reuse the advisor provider so Bench and
    regular projects behave identically.
    """

    path = Path(__import__("os").environ.get("KERNELONE_LLM_CONFIG", "/home/dains/.polaris/config/llm/llm_config.json"))
    try:
        config = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        config = {}
    role = (config.get("roles") or {}).get("cartographer")
    if role is None:
        raw = __import__("os").environ.get("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG", "").strip()
        if not raw:
            return None
        try:
            first = json.loads(raw)[0]
            return CartographerPool(
                base_url=str(first["base_url"]),
                api_key="EMPTY",
                model=str(first["model"]),
            )
        except (KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError):
            return None
    try:
        provider = (config.get("providers") or {})[str(role["provider_id"])]
        api_key = str(provider.get("api_key") or "")
        if not api_key:
            return None
        return CartographerPool(
            base_url=str(provider["base_url"]),
            api_key=api_key,
            model=str(role["model"]),
        )
    except (KeyError, TypeError, ValueError):
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
    normalized_phase = phase if phase in PHASES else "DIAGNOSE"
    allowed = {normalized_phase}
    if verification in {"passed", "failed"}:
        allowed.add("RETEST")
    return {
        "verification": verification,
        "workspace_revision": revision,
        "phase": normalized_phase,
        "allowed_phases": allowed,
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


def _extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    return stripped


def _has_json_object(text: str) -> bool:
    try:
        return isinstance(json.loads(_extract_json(text)), dict)
    except (json.JSONDecodeError, TypeError):
        return False


def _normalize_narrative(payload: object) -> dict[str, Any] | None:
    """Accept reasonable model drift without inventing facts."""

    try:
        data = json.loads(_extract_json(payload)) if isinstance(payload, str) else payload
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    normalized = dict(data)
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
    uncertain = normalized.get("uncertain")
    if isinstance(uncertain, str):
        lowered = uncertain.lower()
        normalized["uncertain"] = "unknown" in lowered or "uncertain" in lowered or not lowered
    actions = normalized.get("next_actions")
    if isinstance(actions, list):
        normalized_actions: list[object] = []
        for item in actions:
            if isinstance(item, str):
                normalized_actions.append({"action": item})
            else:
                normalized_actions.append(item)
        normalized["next_actions"] = normalized_actions
    return normalized


class NarrativeRejectedError(Exception):
    """Exact validation failure for diagnostics."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def validate_run_card_narrative(
    payload: object,
    facts: Mapping[str, Any],
    *,
    allowed_refs: set[str],
) -> dict[str, Any] | None:
    """Return a bounded narrative or None when it disagrees with durable facts."""

    data = _normalize_narrative(payload)
    if data is None:
        raise NarrativeRejectedError(
            "unparsable_or_non_object",
            f"payload of type {type(payload).__name__} did not normalize to a JSON object",
        )
    phase = data.get("phase")
    observed_phase = str(facts.get("phase") or "DIAGNOSE")
    allowed_phases = facts.get("allowed_phases") or {observed_phase if observed_phase in PHASES else "DIAGNOSE"}
    if str(facts.get("verification") or "") in {"passed", "failed"}:
        allowed_phases = set(allowed_phases) | {"RETEST"}
    if phase not in PHASES:
        raise NarrativeRejectedError(
            "phase_not_allowed",
            f"phase={phase!r} not in allowed={sorted(PHASES)}",
        )
    if phase not in allowed_phases:
        raise NarrativeRejectedError(
            "phase_not_allowed",
            f"phase={phase!r} not in allowed_phases={sorted(allowed_phases)}",
        )
    completed = data.get("completed")
    if not isinstance(completed, list):
        raise NarrativeRejectedError(
            "completed_out_of_bounds",
            f"completed must be an array, got {type(completed).__name__}",
        )
    if len(completed) > 8:
        raise NarrativeRejectedError(
            "completed_out_of_bounds",
            f"completed has {len(completed)} items, limit is 8",
        )
    completed = [str(item)[:80] for item in completed]
    next_actions = data.get("next_actions")
    if not isinstance(next_actions, list):
        raise NarrativeRejectedError(
            "next_actions_out_of_bounds",
            f"next_actions must be an array, got {type(next_actions).__name__}",
        )
    if len(next_actions) > _MAX_NEXT_ACTIONS:
        raise NarrativeRejectedError(
            "next_actions_out_of_bounds",
            f"next_actions has {len(next_actions)} items, limit is {_MAX_NEXT_ACTIONS}",
        )
    normalized_actions: list[dict[str, str]] = []
    for item in next_actions:
        if not isinstance(item, dict):
            continue
        action = str(item.get("action") or "").strip()
        if not action:
            continue
        why = str(item.get("why") or "").strip()
        expected = str(item.get("expected") or "").strip()
        normalized_actions.append(
            {
                "action": action[:160],
                "why": (why or "推进当前阶段")[:160],
                "expected": (expected or "产生可观察的进展")[:160],
            }
        )
    normalized_actions = normalized_actions[:_MAX_NEXT_ACTIONS]
    blocker = data.get("blocker")
    if blocker is None:
        blocker_payload = None
    else:
        if not isinstance(blocker, dict):
            raise NarrativeRejectedError(
                "blocker_not_object",
                f"blocker must be an object or null, got {type(blocker).__name__}",
            )
        signature = str(blocker.get("signature") or "")
        if signature:
            known = facts.get("failure_signatures") or set()
            match = next(
                (item for item in known if signature in item or item in signature),
                None,
            )
            if match is None:
                raise NarrativeRejectedError(
                    "unknown_blocker_signature",
                    f"blocker.signature={signature!r} not in failure_signatures={sorted(known)[:8]}",
                )
            signature = match
        blocker_payload = {
            "signature": signature[:240],
            "occurrences": int(blocker.get("occurrences") or 0),
            "candidate_revisions": int(blocker.get("candidate_revisions") or 0),
        }
    refs = data.get("evidence_refs")
    if not isinstance(refs, list):
        raise NarrativeRejectedError(
            "evidence_refs_not_array",
            f"evidence_refs must be an array, got {type(refs).__name__}",
        )
    evidence_refs = [str(ref) for ref in refs if str(ref) in allowed_refs][:12]
    reason = str(data.get("reason") or "")
    if len(reason) > 160:
        raise NarrativeRejectedError(
            "reason_too_long",
            f"reason has {len(reason)} characters, limit is 160",
        )
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
        self._rejections: set[str] = set()

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

    def note_rejection(self, code: str, detail: str = "") -> bool:
        """Return True only the first time this fingerprint rejection is seen.

        A completed draw is re-validated on every host poll, so the same
        rejection would otherwise flood the event journal.
        """

        key = f"{self._fingerprint}:{code}:{detail}"
        if key in self._rejections:
            return False
        self._rejections.add(key)
        return True
