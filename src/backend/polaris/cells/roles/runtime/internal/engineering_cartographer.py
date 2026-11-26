"""Cartographer: narrative RunCard enrichment from current WorkingState.

The host owns every state field. Cartographer only writes the readable layer
(phase intent, completed facts, blocker, next actions) and is discarded when it
disagrees with durable facts.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shlex
import urllib.request
from collections import Counter
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_acceptance import extract_goal_clauses
from polaris.cells.roles.runtime.internal.engineering_phase_policy import (
    EngineeringPhase,
    allowed_tools as phase_allowed_tools,
)
from polaris.cells.roles.runtime.internal.engineering_validation_fingerprint import (
    active_failure_revisions,
    observed_tool_failure,
)

SCHEMA_VERSION = "run_card_narrative.v1"
PHASES = {"DIAGNOSE", "EDIT", "RETEST"}
_MAX_NEXT_ACTIONS = 3
_PROBE_PROMPT = (
    "You are the Cartographer advising a Director on one unresolved observation. Return exactly one JSON object "
    "with four string fields: check_tool, question, expected, evidence_ref. Select one check_tool from "
    "decision.available_checks, preferring a direct source observation. Ask one concrete observational question "
    "(at most 140 characters). In expected, state what observation would distinguish the alternatives (at most "
    "160 characters). Copy one reference from decision.evidence_refs. Do not propose a write, repair or plan. "
    "The host already supplies stage and outcomes. If already_visible_source is present, use that expression. "
    "A retained_historical_write is what was previously written; a retained_current_read was checked at this candidate "
    "revision. Do not ask to read the same lines again unless freshness or an omitted declaration is the actual unknown. "
    "If the evidence answers the question and there is no useful distinct observation, return exactly "
    '{"abstain":true,"reason":"why no further check is useful","evidence_ref":"one triggering reference"}. '
    "Both response forms are advisory."
)


_PHASE_NEXT_ACTIONS = {
    "DIAGNOSE": ("读取相关源码与现有测试，定位实现与目标的差距", "得到可定位的差异清单"),
    "EDIT": ("补齐缺失实现并跑通相关检查", "目标检查通过"),
    "RETEST": ("用 verify_project 对当前候选重新执行声明的检查", "验证结论更新为通过或给出真实失败"),
    "build": ("按当前目标完成下一项实现或检查", "得到可复核的源码或工具观察"),
    "verify": ("对当前候选执行已声明的验证", "取得验证回执"),
    "prepare": ("准备验证所需的依赖或构建产物", "获得可供只读验证使用的候选"),
    "repair": ("阅读失败回执并修复当前候选", "得到可重新验证的变更"),
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
    normalized_phase = facts["execution_phase"] or (phase if phase in PHASES else facts["phase"])

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

    recent_failures = facts["failure_revisions"]
    signature = max(sorted(recent_failures), key=lambda key: len(recent_failures[key]), default="")
    revisions = recent_failures.get(signature, [])

    action, expected = _PHASE_NEXT_ACTIONS.get(normalized_phase, _PHASE_NEXT_ACTIONS["DIAGNOSE"])
    # 阶段模板对每一条卡都一样（实测三条卡逐字相同），等于零信息量，模型没有任何
    # 可照做的具体动作。有持久事实时改成 run 专属的一步。
    if signature and facts["verification"] == "passed":
        action = f"复测此前失败且尚未覆盖的检查：{signature[:80]}"
        expected = "该检查得到当前候选的实际结果"
    elif facts["verification"] == "failed" and signature:
        action = f"先修最近一次失败：{signature[:80]}"
        expected = "该失败签名不再出现"
    elif facts["verification"] == "passed":
        action = "保持当前候选不再改源码，收尾并交独立 QA 复核"
        expected = "候选通过独立 QA"
    elif facts["verification"] == "not_run" and written:
        action = "用 verify_project 跑任务声明的检查（当前候选还没验证过）"
        expected = "得到 passed 或真实失败"
    evidence = sorted(facts["evidence_refs"])[:8]
    return {
        "schema_version": "run_card_deterministic.v1",
        "phase": normalized_phase,
        "execution_phase": facts["execution_phase"],
        "intent": f"当前阶段 {normalized_phase}（宿主由持久事实推导，非模型自述）",
        "completed": completed,
        "blocker": (
            {
                "signature": signature,
                "occurrences": len(revisions),
                "candidate_revisions": len(set(revisions) - {None}),
            }
            if signature
            else None
        ),
        "next_actions": [{"action": action, "why": "按当前阶段由宿主推导", "expected": expected}],
        "evidence_refs": evidence,
        "uncertain": False,
        "reason": "确定性阶段卡：字段直接来自工程日志。",
    }


class CartographerNoVisibleAnswerError(ValueError):
    """The provider stopped without returning visible answer text."""


class CartographerPool:
    """Small Kimi client for short, structured RunCard narratives."""

    def __init__(self, *, base_url: str, api_key: str, model: str, timeout_seconds: float = 45.0) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.model = model
        self.timeout_seconds = timeout_seconds
        self._lock = asyncio.Lock()
        self._last_error = ""
        self.prior_raw_responses: list[str] = []

    def _post(self, messages: list[dict[str, str]]) -> str:
        system = (
            "You are the Cartographer for ContextOS. Draw the current engineering stage as a short map. "
            "Return ONLY one JSON object, with no commentary and no thinking: "
            "schema_version,phase,intent,completed,blocker,next_actions,evidence_refs,uncertain,reason. "
            "The host owns working_state.execution_phase. The response phase field is your suggested cognitive focus: "
            "choose DIAGNOSE, EDIT or RETEST. This suggestion cannot change the host's execution phase. "
            "completed must be an array of at most 8 strings, each at most 80 characters. "
            "next_actions must be an array of at most 3 objects with action, why and expected. "
            "blocker.signature must be copied from the supplied failure_signatures array; "
            "use null when no failure is known. "
            "evidence_refs must be an array of strings drawn from the supplied evidence_refs. "
            "Do not invent failures or evidence. Keep reason within 160 characters."
            " When decision is supplied, address its specific uncertainty. Propose one discriminating observation "
            "using an available check and cite the triggering evidence. A proposal never authorizes execution."
        )
        demand = isinstance(messages, list) and bool(messages) and messages[0].get("role") == "system"
        body = {
            "model": self.model,
            "max_tokens": 480 if demand else 2400,
            "temperature": 0,
            "thinking": {"type": "disabled"},
            "messages": messages if demand else [{"role": "system", "content": system}, *messages],
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
        text = "".join(
            item["text"]
            for item in content
            if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str)
        )
        if not text.strip():
            raise CartographerNoVisibleAnswerError(
                f"no_visible_answer: stop_reason={payload.get('stop_reason') or 'unknown'}"
            )
        return text

    @property
    def last_error(self) -> str:
        return self._last_error

    async def draw_many(self, requests: list[dict[str, Any]]) -> list[str]:
        results: list[str] = []
        for request in requests:
            async with self._lock:
                self.prior_raw_responses = []
                self._last_error = ""
                messages: list[dict[str, str]] = [{"role": "user", "content": json.dumps(request, ensure_ascii=False)}]
                if request.get("decision"):
                    messages.insert(0, {"role": "system", "content": _PROBE_PROMPT})
                text = ""
                for attempt in range(2):
                    try:
                        text = await asyncio.to_thread(self._post, messages)
                    except CartographerNoVisibleAnswerError as exc:
                        self._last_error = str(exc)
                        text = ""
                        break
                    except (OSError, ValueError, asyncio.TimeoutError) as error:
                        # 保留最后一次真实原因（超时 / 空响应 / HTTP），交给宿主记录。
                        self._last_error = f"{type(error).__name__}: {error}"[:200]
                        text = ""
                    parsed = parse_cartographer_response(text)
                    feedback = "Expected exactly one JSON object."
                    if parsed is not None:
                        if not request.get("decision"):
                            break
                        try:
                            _decision_probe(parsed, request["decision"])
                        except NarrativeRejectedError as exc:
                            feedback = exc.detail
                        else:
                            break
                    if attempt == 0 and text:
                        self.prior_raw_responses.append(text)
                    # The small model frequently answers in prose. One
                    # corrective ask beats losing the whole RunCard draw.
                    messages = [
                        *messages,
                        {"role": "assistant", "content": text or "{}"},
                        {
                            "role": "user",
                            "content": feedback
                            + " Return one complete JSON object using the system instruction's fields.",
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


def current_facts(run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Host-owned facts the narrative must agree with."""

    verification = str(run.get("verification") or "not_run")
    revision = run.get("workspace_revision")
    supplied_phase = run.get("execution_phase")
    execution_phase = supplied_phase if supplied_phase in {p.value for p in EngineeringPhase} else None
    phase = execution_phase or "DIAGNOSE"
    # Legacy fixtures and records may contain an explicitly named cognitive phase.
    # Live Director frames always supply their current phase from phase_for().
    if execution_phase is None:
        for event in reversed(events):
            if event["kind"] != "request_prepared" or (
                event.get("role") and run.get("role") and event["role"] != run["role"]
            ):
                continue
            request = event["payload"].get("request") or {}
            context = request.get("context") if isinstance(request, dict) else None
            previous = context.get("phase") if isinstance(context, dict) else None
            phase = previous if previous in PHASES else "unknown"
            break
    failures = active_failure_revisions(events)
    evidence: set[str] = set()
    operations: dict[tuple[Any, Any, Any], tuple[Any, ...]] = {}
    observed: dict[str, tuple[tuple[Any, ...], int]] = {}
    latest: dict[tuple[Any, ...], int] = {}
    read_observed_at: dict[str, int] = {}
    latest_source_change: dict[str, int] = {}
    call_counts: Counter[str] = Counter()
    for event in events:
        payload = event["payload"]
        tool = payload.get("tool")
        call_id = payload.get("call_id")
        identity = (payload.get("turn_seq"), call_id, tool)
        if event["kind"] == "tool_started" and isinstance(identity[0], int) and isinstance(call_id, str):
            args = payload.get("arguments")
            if isinstance(args, Mapping):
                if tool == "read_file":
                    # A different snapshot or slice does not settle a prior read.
                    key = tuple(
                        args.get(k)
                        for k in ("file", "content_ref", "start_line", "end_line", "max_bytes", "range_required")
                    )
                    if (isinstance(key[0], str) or isinstance(key[1], str)) and all(
                        isinstance(v, (str, int, type(None))) for v in key
                    ):
                        operations[identity] = (tool, *key)
                elif tool in {"edit_file", "write_file"} and isinstance(args.get("file"), str):
                    operations[identity] = (tool, args["file"])
                    if isinstance(event.get("seq"), int):
                        latest_source_change[args["file"]] = event["seq"]
                elif args:
                    # Preserve arguments for operation identity only, never render them as evidence.
                    operations[identity] = (tool, json.dumps(args, sort_keys=True, ensure_ascii=False))
        if event["kind"] not in {"tool_observed", "verification_completed"}:
            continue
        seq = event.get("seq")
        if isinstance(seq, int):
            event_ref = f"event:{seq}"
            evidence.add(event_ref)
            op = operations.pop(identity, None)
            if event["kind"] == "verification_completed" and isinstance(payload.get("workspace_revision"), int):
                op = ("verification_completed", payload["workspace_revision"])
            if op:
                latest[op] = seq
                if (
                    op[0] == "read_file"
                    and isinstance(op[1], str)
                    and not any(op[2:5])
                    and payload.get("ok") is True
                    and not payload.get("timed_out")
                    and not payload.get("interrupted")
                ):
                    read_observed_at[op[1]] = seq
                observed[event_ref] = (op, seq)
                if isinstance(call_id, str) and not call_id.startswith("event:"):
                    observed[call_id] = (op, seq)
        if isinstance(call_id, str):
            call_counts[call_id] += 1
        for ref_field in ("call_id", "content_ref"):
            value = payload.get(ref_field)
            if isinstance(value, str) and value:
                evidence.add(value)
    evidence.difference_update(ref for ref, count in call_counts.items() if count > 1 and not ref.startswith("event:"))
    allowed = {phase} if phase in PHASES else set(PHASES)
    if verification in {"passed", "failed"}:
        allowed.add("RETEST")
    return {
        "verification": verification,
        "workspace_revision": revision,
        "phase": phase,
        "execution_phase": execution_phase,
        "allowed_phases": allowed,
        "failure_signatures": set(failures),
        "failure_revisions": failures,
        "superseded_evidence": {ref: latest[op] for ref, (op, seq) in observed.items() if latest[op] > seq},
        "read_observed_at": read_observed_at,
        "latest_source_change": latest_source_change,
        "evidence_refs": evidence,
    }


def _bound_object(raw: object) -> Mapping[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (ValueError, RecursionError):
            return {}
    return raw if isinstance(raw, Mapping) else {}


def _retained_source_pairs(
    messages: Sequence[Mapping[str, Any]], wanted: set[str]
) -> dict[str, tuple[str, Mapping[str, Any], Mapping[str, Any]]]:
    """Index matching native IDs only. Unrelated payloads, including large file bodies, stay opaque."""
    calls: dict[str, list[tuple[int, str, Mapping[str, Any]]]] = {}
    results: dict[str, list[tuple[int, Mapping[str, Any]]]] = {}
    for index, message in enumerate(messages):
        role, content = message.get("role"), message.get("content")
        blocks = content if isinstance(content, list) else []
        if role == "assistant":
            for call in message.get("tool_calls") or []:
                if not isinstance(call, Mapping) or call.get("id") not in wanted:
                    continue
                fn = call.get("function")
                if isinstance(fn, Mapping) and isinstance(fn.get("name"), str):
                    calls.setdefault(call["id"], []).append((index, fn["name"], _bound_object(fn.get("arguments"))))
            for block in blocks:
                if (
                    isinstance(block, Mapping)
                    and block.get("type") == "tool_use"
                    and block.get("id") in wanted
                    and isinstance(block.get("name"), str)
                ):
                    calls.setdefault(block["id"], []).append((index, block["name"], _bound_object(block.get("input"))))
        if role == "tool" and message.get("tool_call_id") in wanted:
            results.setdefault(message["tool_call_id"], []).append((index, _bound_object(content)))
        elif role == "user":
            for block in blocks:
                if (
                    isinstance(block, Mapping)
                    and block.get("type") == "tool_result"
                    and block.get("tool_use_id") in wanted
                    and not block.get("is_error")
                ):
                    results.setdefault(block["tool_use_id"], []).append((index, _bound_object(block.get("content"))))
    return {
        cid: (matches[0][1], matches[0][2], results[cid][0][1])
        for cid, matches in calls.items()
        if len(matches) == 1 and len(results.get(cid, [])) == 1 and matches[0][0] < results[cid][0][0]
    }


def _visible_diagnostic_source(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    messages: Sequence[Mapping[str, Any]],
    failure: Mapping[str, Any],
    *,
    turn_seq: int,
    frame_seq: int,
) -> dict[str, Any] | None:
    """Locate the reported line in an exact successful read/write retained by this Director frame."""
    if run.get("role") != "director" or failure.get("role") != "director" or failure.get("tool") != "execute_command":
        return None
    match = re.match(r"^([\w@./ -]+?)(?:\((\d+)(?:,\d+)?\)|:(\d+)(?::\d+)?):\s*\S", str(failure.get("failure") or ""))
    if not match:
        return None
    path = match[1]
    line = int(match[2] or match[3])
    if line < 1 or path.startswith("/") or any(piece in {"", ".", ".."} for piece in path.split("/")):
        return None
    frame = next(
        (e for e in reversed(events) if e.get("role") == "director" and e["kind"] in {"role_started", "role_finished"}),
        None,
    )
    if frame is None or frame["kind"] != "role_started" or frame.get("seq") != frame_seq:
        return None
    starts: dict[tuple[Any, Any, Any], Mapping[str, Any]] = {}
    candidates = []
    last_change = 0
    latest_read_hash = ""
    for e in events:
        seq = e.get("seq")
        if not isinstance(seq, int) or seq <= frame_seq:
            continue
        data = e["payload"]
        key = (data.get("turn_seq"), data.get("call_id"), data.get("tool"))
        if e["kind"] == "tool_started" and (data.get("arguments") or {}).get("file") == path:
            if key[2] in {"write_file", "edit_file"}:
                last_change = seq
                latest_read_hash = ""
            if (
                e.get("role") == "director"
                and isinstance(key[0], int)
                and frame_seq <= key[0] < turn_seq
                and isinstance(key[1], str)
                and key[2] in {"read_file", "write_file"}
            ):
                starts[key] = data["arguments"]
        elif (
            e["kind"] == "tool_observed"
            and key in starts
            and e.get("role") == "director"
            and data.get("ok") is True
            and not data.get("interrupted")
            and not data.get("timed_out")
        ):
            candidates.append((seq, key, starts[key], data))
            if key[2] == "read_file":
                known = _bound_object(data.get("content")).get("file_card") or {}
                if known.get("path") == path and not known.get("stale"):
                    latest_read_hash = str(known.get("sha256") or "")
    pairs = _retained_source_pairs(messages, {c[1][1] for c in candidates if c[0] > last_change})
    counts = Counter(e["payload"].get("call_id") for e in events if e["kind"] == "tool_started")
    for seq, key, args, observed in reversed(candidates):
        if seq <= last_change or key[1] not in pairs or counts[key[1]] != 1:
            continue
        name, native, result = pairs[key[1]]
        if (
            name != key[2]
            or native.get("file") != path
            or result.get("tool") != name
            or result.get("call_id") != key[1]
            or result.get("ok") is not True
            or result.get("content") != observed.get("content")
        ):
            continue
        if name == "write_file":
            text = native.get("content")
            if (
                not isinstance(text, str)
                or args.get("content_ref")
                or native.get("content_ref")
                or text != args.get("content")
                or observed["content"] != f"Wrote {len(text.encode('utf-8'))} UTF-8 bytes to {path}."
            ):
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            source_kind = "retained_historical_write"
        else:
            card = _bound_object(result.get("content")).get("file_card") or {}
            text = card.get("content_preview")
            if (
                not isinstance(text, str)
                or args.get("content_ref")
                or native.get("content_ref")
                or card.get("path") != path
                or card.get("stale") is not False
                or card.get("revision") != run.get("workspace_revision")
                or not isinstance(card.get("revision"), int)
            ):
                continue
            digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
            if card.get("sha256") != digest or card.get("content_ref") != f"filecard:{digest}":
                continue
            source_kind = "retained_current_read"
        if latest_read_hash and latest_read_hash != digest:
            continue
        lines = text.splitlines()
        if line > len(lines) or not lines[line - 1].strip() or len(lines[line - 1].encode("utf-8")) > 384:
            continue
        start, end = max(1, line - 1), min(len(lines), line + 1)
        excerpt = "\n".join(lines[start - 1 : end])
        if len(excerpt.encode("utf-8")) > 512:
            start = end = line
            excerpt = lines[line - 1]
        return {
            "source_kind": source_kind,
            "path": path,
            "line": line,
            "observed_lines": [start, end],
            "excerpt": excerpt,
            "content_sha256": digest,
            "source_call_id": key[1],
            "source_turn_seq": key[0],
            "observed_seq": seq,
            "retained_for_turn_seq": turn_seq,
            "frame_seq": frame_seq,
            "session_id": run.get("session_id"),
            "candidate_revision": run.get("workspace_revision"),
            "diagnostic_refs": failure["evidence_refs"],
        }
    return None


def facts_for_run_card(
    run: Mapping[str, Any],
    events: Sequence[Mapping[str, Any]],
    *,
    retained_messages: Sequence[Mapping[str, Any]] = (),
    turn_seq: int | None = None,
    frame_seq: int | None = None,
) -> tuple[dict[str, Any], str]:
    facts = current_facts(run, events)
    observations = [event for event in events if event["kind"] in {"tool_observed", "verification_completed"}]
    recent = observations[-6:]
    call_counts = Counter(e["payload"].get("call_id") for e in observations)

    def call_key(p: Mapping[str, Any]) -> tuple[Any, Any, Any]:
        return p.get("turn_seq"), p.get("call_id"), p.get("tool")

    wanted = {call_key(e["payload"]) for e in recent if e["kind"] == "tool_observed"}
    starts = {
        call_key(e["payload"]): e["payload"].get("arguments")
        for e in events
        if e["kind"] == "tool_started" and call_key(e["payload"]) in wanted
    }
    rows: list[dict[str, Any]] = []
    for event in recent:
        p = event["payload"]
        refs = [f"event:{event['seq']}"] if isinstance(event.get("seq"), int) else []
        call_id = p.get("call_id")
        if (
            isinstance(call_id, str)
            and not call_id.startswith("event:")
            and call_counts[call_id] == 1
            and len(call_id) <= 160
        ):
            refs.append(call_id)
        row: dict[str, Any] = {"kind": event["kind"], "evidence_refs": refs}
        rows.append(row)
        if event["kind"] == "verification_completed":
            row.update(verification=p.get("verification"), workspace_revision=p.get("workspace_revision"))
            continue
        tool = p.get("tool")
        row.update(tool=tool, turn_seq=p.get("turn_seq"), role=event.get("role"))
        arguments = starts.get(call_key(p)) if isinstance(p.get("turn_seq"), int) else None
        args = arguments if isinstance(arguments, Mapping) else {}
        path = args.get("file") or args.get("entry") or args.get("path")
        if isinstance(path, str):
            row["subject"] = path[:140]
            if tool == "read_file" and any(k in args for k in ("start_line", "end_line")):
                row["line_range"] = [args.get("start_line"), args.get("end_line")]
        elif tool == "execute_command" and isinstance(args.get("command"), str):
            # Describe the executable and named subcommand only. Script bodies and argv payloads stay in the journal.
            command_line = args["command"].partition("\n")[0][:512]
            try:
                tokens = shlex.split(command_line)
            except ValueError:
                tokens = []
            if tokens[:1] == ["cd"] and tokens[2:3] == ["&&"]:
                tokens = tokens[3:]
            if tokens:
                executable = Path(tokens[0]).name[:48]
                subject = [executable] if re.fullmatch(r"[\w.+-]+", executable) else []
                if subject and executable in {"npm", "pnpm", "yarn", "go", "cargo", "git", "dotnet", "gradle", "mvn"}:
                    for token in tokens[1 : 3 if tokens[1:2] == ["run"] else 2]:
                        if re.fullmatch(r"[\w.:-]{1,44}", token):
                            subject.append(token)
                if subject:
                    row["subject"] = " ".join(subject)
        ok = p.get("ok")
        exit_code = p.get("exit_code")
        if type(exit_code) is int:
            row["exit_code"] = exit_code
        if p.get("interrupted") or p.get("timed_out"):
            row["outcome"] = "unknown"
        elif ok is False or (type(exit_code) is int and exit_code != 0):
            row["outcome"] = "failed"
            if isinstance(arguments, Mapping) and tool in {"write_file", "edit_file"}:
                receipt = p.get("content")
                for field in ("file", "content") if tool == "write_file" else ("file", "search", "replace"):
                    if args.get(field) is not None or not isinstance(receipt, str):
                        continue
                    if (
                        (
                            tool == "write_file"
                            and field == "content"
                            and receipt.startswith(
                                "ValueError: write_file requires a content field with the complete file body for "
                            )
                        )
                        or (
                            tool == "write_file"
                            and field == "file"
                            and receipt.startswith("ValueError: write_file requires a nonempty relative file path;")
                        )
                        or receipt
                        == f"ValueError: {field} must be a {'possibly empty' if field == 'replace' else 'nonempty'} string without NUL"
                    ):
                        row["missing_argument"] = field
                        break
            failure = observed_tool_failure(p, limit=140)
            if failure:
                row["failure"] = failure
        else:
            row["outcome"] = "succeeded" if ok is True else "unknown"
    instruction = str(run.get("instruction") or "")
    clauses = extract_goal_clauses(instruction)
    request = {
        "goal_excerpt": ("\n".join(clauses[:2]) or instruction)[:360],
        "working_state": {
            "verification": facts["verification"],
            "workspace_revision": facts["workspace_revision"],
            "phase": facts["phase"],
            "execution_phase": facts["execution_phase"],
            "status": run.get("status"),
            "role": run.get("role"),
        },
        "recent_activity": rows,
        "failure_signatures": sorted(facts["failure_signatures"])[:8],
        "evidence_refs": [ref for row in rows for ref in row["evidence_refs"]],
    }
    if retained_messages and isinstance(turn_seq, int) and isinstance(frame_seq, int):
        failed_row = next(
            (row for row in reversed(rows) if row.get("outcome") == "failed" or row.get("verification") == "failed"),
            None,
        )
        if failed_row:
            source = _visible_diagnostic_source(
                run, events, retained_messages, failed_row, turn_seq=turn_seq, frame_seq=frame_seq
            )
            if source:
                request["already_visible_source"] = source
    fingerprint = hashlib.sha256(json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return request, fingerprint


def cartographer_decision(
    request: Mapping[str, Any], enabled_tools: frozenset[str]
) -> tuple[dict[str, Any], str] | None:
    """Offer one evidence-bound diagnostic question while a capable Director can use it."""
    state = request["working_state"]
    phase = state.get("execution_phase")
    if state.get("role") != "director" or phase not in {"build", "repair"} or state.get("verification") == "passed":
        return None
    checks = enabled_tools & {
        "read_file",
        "repo_tree",
        "repo_rg",
        "verify_project",
        "execute_command",
        "browser_observe",
    }
    narrowed = phase_allowed_tools(EngineeringPhase(phase))
    if narrowed is not None:
        checks = checks & narrowed
    if not checks:
        return None

    # Inspect only the bounded observed frontier since the last concrete progress.
    # A new successful edit/writing turn or passing check invalidates the prior diagnostic need.
    readonly = {"read_file", "read_command_result", "repo_tree", "repo_rg", "browser_observe"}
    reads: list[dict[str, Any]] = []
    anchor = None
    reason = ""
    for row in reversed(request["recent_activity"]):
        if row.get("outcome") == "failed" or row.get("verification") == "failed":
            anchor, reason = row, "failed_observation"
            break
        if row.get("tool") not in readonly or row.get("outcome") != "succeeded":
            break
        reads.append(row)
    if anchor is None:
        if len({r["turn_seq"] for r in reads if isinstance(r.get("turn_seq"), int)}) < 3:
            return None
        seen = set()
        for read in reads:
            subject = read.get("subject")
            if not subject:
                continue
            key = (read["tool"], subject, tuple(read.get("line_range", [])))
            if key in seen:
                anchor = next(r for r in reads if (r["tool"], r.get("subject"), tuple(r.get("line_range", []))) == key)
                break
            seen.add(key)
        if anchor is None:
            return None
        reason = "repeated_read_without_progress"
        checks = checks - {anchor["tool"]}
    if anchor.get("tool") != "browser_observe" and not any(r.get("tool") == "browser_observe" for r in reads):
        checks = checks - {"browser_observe"}
    if anchor.get("tool") in {"write_file", "edit_file"} and checks & {"read_file", "repo_tree", "repo_rg"}:
        checks = checks & {"read_file", "repo_tree", "repo_rg"}
    if not checks or not anchor.get("evidence_refs"):
        return None
    if reason == "failed_observation" and any(
        read.get("tool") == anchor.get("tool")
        and read.get("subject") == anchor.get("subject")
        and read.get("line_range") == anchor.get("line_range")
        for read in reads
    ):
        return None
    if anchor.get("missing_argument"):
        previous_turns = set()
        for prior in reversed(request["recent_activity"]):
            if prior.get("tool") not in {"write_file", "edit_file"} or prior.get("subject") != anchor.get("subject"):
                continue
            if prior.get("outcome") == "succeeded":
                break
            if (
                prior["tool"] == anchor["tool"]
                and prior.get("missing_argument") == anchor["missing_argument"]
                and isinstance(prior.get("turn_seq"), int)
            ):
                previous_turns.add(prior["turn_seq"])
        if len(previous_turns) < 2:
            return None
        reason = "repeated_invalid_arguments"
    subject_info = {
        k: anchor[k]
        for k in ("tool", "subject", "line_range", "failure", "exit_code", "verification", "missing_argument")
        if k in anchor
    }
    decision = {"reason": reason, "observation": subject_info, "available_checks": sorted(checks)}
    # Call IDs prove which occurrence was observed, but repeat occurrences alone do not create a new question.
    source = request.get("already_visible_source")
    source_key = (
        {k: source[k] for k in ("source_kind", "source_call_id", "content_sha256", "line", "observed_lines")}
        if isinstance(source, Mapping) and set(source["diagnostic_refs"]).intersection(anchor["evidence_refs"])
        else None
    )
    decision_key = hashlib.sha256(
        json.dumps(
            [state, decision, request["failure_signatures"], source_key], sort_keys=True, ensure_ascii=False
        ).encode("utf-8")
    ).hexdigest()
    return {**decision, "evidence_refs": anchor["evidence_refs"]}, decision_key


def parse_cartographer_response(text: object) -> dict[str, Any] | None:
    """Decode exactly one object, optionally inside one complete JSON Markdown fence."""
    if not isinstance(text, str):
        return None
    raw = text.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if len(lines) < 3 or lines[0].strip().lower() not in {"```", "```json"} or lines[-1].strip() != "```":
            return None
        raw = "\n".join(lines[1:-1])

    def unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("ambiguous duplicate JSON field")
        return result

    try:
        value = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, RecursionError):
        return None
    return value if isinstance(value, dict) else None


def _normalize_narrative(payload: object) -> dict[str, Any] | None:
    """Accept reasonable model drift without inventing facts."""
    data = parse_cartographer_response(payload) if isinstance(payload, str) else payload
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
    # 与蒸馏器/节点摘要共用同一道"这不是裁决"闸：小模型常用一句占位话
    # 交差，而叙述卡是低通过率的那道门（实测 5.6%），不能只有长度限制。
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
        summary_is_informative,
    )

    # 空 reason 是允许的（纯阶段切换卡），但填了字就必须是一句观察。
    if reason.strip() and not summary_is_informative(reason):
        raise NarrativeRejectedError(
            "reason_is_placeholder",
            f"reason must state what was observed, got {reason[:80]!r}",
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


def _decision_probe(payload: object, decision: Mapping[str, Any]) -> dict[str, Any]:
    """Admit one descriptive check, keeping model proposals separate from host facts and authorization."""
    if not isinstance(payload, Mapping):
        raise NarrativeRejectedError("unparsable_or_non_object", "Expected one Cartographer JSON object.")
    if "abstain" in payload:
        if set(payload) != {"abstain", "reason", "evidence_ref"} or payload["abstain"] is not True:
            raise NarrativeRejectedError("invalid_abstention", "Expected a single explicit abstention.")
        reason, ref = payload["reason"], payload["evidence_ref"]
        if (
            not isinstance(reason, str)
            or not reason.strip()
            or not reason.isprintable()
            or len(reason) > 160
            or not isinstance(ref, str)
            or ref not in decision["evidence_refs"]
        ):
            raise NarrativeRejectedError(
                "invalid_abstention", "The abstention needs a short reason and one triggering reference."
            )
        return {
            "schema_version": "cartographer.abstention.v1",
            "outcome": "abstained",
            "reason": reason,
            "evidence_refs": [ref],
            "next_actions": [],
        }
    tools = set(decision["available_checks"])
    if "next_actions" in payload:
        actions = payload["next_actions"]
        if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], Mapping):
            raise NarrativeRejectedError("missing_decision_probe", "Legacy proposals need one unambiguous check.")
        step = actions[0]
        question, expected = step.get("action"), step.get("expected")
        if not isinstance(question, str):
            raise NarrativeRejectedError("missing_decision_probe", "Missing legacy check.")
        # A legacy check may name a tool or use its ordinary read/search verb.
        names = set(re.findall(r"\b[a-z]+_[a-z_]+\b", question.lower())) & {
            "read_file",
            "repo_tree",
            "repo_rg",
            "execute_command",
            "browser_observe",
            "verify_project",
            "write_file",
            "edit_file",
            "read_command_result",
        }
        if not names:
            if re.match(r"^(?:search\b|locate\b|搜索|查找)", question, re.I):
                names.add("repo_rg")
            elif re.match(r"^(?:read\b|inspect\b|读取|查看)", question, re.I):
                names.add("read_file")
        tool = next(iter(names)) if len(names) == 1 else None
        refs = payload.get("evidence_refs")
    else:
        if set(payload) != {"check_tool", "question", "expected", "evidence_ref"}:
            raise NarrativeRejectedError(
                "invalid_decision_probe", "Expected check_tool, question, expected and evidence_ref."
            )
        tool, question, expected = payload["check_tool"], payload["question"], payload["expected"]
        refs = [payload["evidence_ref"]]
    if not isinstance(tool, str) or tool not in tools:
        raise NarrativeRejectedError("unavailable_check", "The check must name one offered observation capability.")
    if not isinstance(refs, list) or not refs or not all(isinstance(ref, str) for ref in refs):
        raise NarrativeRejectedError("missing_decision_probe", "The probe lacks a triggering reference.")
    if any(ref not in decision["evidence_refs"] for ref in refs):
        raise NarrativeRejectedError(
            "unbound_evidence_ref", "The probe cites evidence outside the triggering observation."
        )
    if (
        not isinstance(question, str)
        or not isinstance(expected, str)
        or not all(t.strip() and t.isprintable() and len(t) <= limit for t, limit in ((question, 140), (expected, 160)))
    ):
        raise NarrativeRejectedError(
            "missing_decision_probe", "The check needs one bounded question and expected observation."
        )
    if re.search(
        r"(?:^|\b(?:use|invoke|call|(?:re-?)?run|execute|then|and|to)\s+)(?:write_file|edit_file|apply_patch|delete_file)\b"
        r"|(?:^|\b(?:then|and|to)\s+)(?:overwrite|rewrite|write|edit|delete|remove|patch)\b"
        r"|(?:然后|再|并)(?:调用\s*(?:write_file|edit_file)|写入|覆盖|删除|修改)",
        question + " " + expected,
        re.I,
    ):
        raise NarrativeRejectedError("mutating_probe", "The proposal includes a mutation.")
    return {
        "schema_version": "cartographer.probe.v1",
        "check_tool": tool,
        "question": question,
        "expected": expected,
        "evidence_refs": list(dict.fromkeys(refs)),
        "next_actions": [{"action": f"{tool}: {question}", "expected": expected}],
    }


class CartographerRuntime:
    """Coalesce one low-frequency narrative job per stage change."""

    def __init__(self, pool: CartographerPool) -> None:
        self.pool = pool
        self._job: asyncio.Task[list[str]] | None = None
        self._fingerprint = ""
        self._request: dict[str, Any] = {}
        self._rejections: set[str] = set()
        self._offered_decisions: set[str] = set()
        self._decision_receipt: dict[str, Any] = {}
        self._consumed = ""
        self._checked_delivery_refs: set[str] = set()
        self._last_error = ""

    def schedule_for_decision(
        self,
        request: dict[str, Any],
        *,
        fingerprint: str,
        enabled_tools: frozenset[str],
        recorded_keys: set[str] | None = None,
    ) -> bool:
        demand = cartographer_decision(request, enabled_tools)
        if demand is None:
            return False
        decision, key = demand
        source = request.get("already_visible_source")
        if isinstance(source, Mapping) and not set(source["diagnostic_refs"]).intersection(decision["evidence_refs"]):
            request = {k: v for k, v in request.items() if k != "already_visible_source"}
        if (
            key in self._offered_decisions
            or (recorded_keys and key in recorded_keys)
            or not self.schedule({**request, "decision": decision}, fingerprint=f"{fingerprint}:{key}")
        ):
            return False
        self._offered_decisions.add(key)
        self._decision_receipt = {
            "decision_key": key,
            "decision_reason": decision["reason"],
            "evidence_refs": decision["evidence_refs"],
        }
        return True

    @property
    def decision_receipt(self) -> dict[str, Any]:
        return dict(self._decision_receipt)

    def schedule(self, request: dict[str, Any], *, fingerprint: str) -> bool:
        # Never abandon an in-flight draw: the model may take longer than one
        # turn, and cancelling on every fingerprint change would starve it.
        if self._job is not None and not self._job.done():
            return False
        if fingerprint == self._fingerprint:
            return False
        self._fingerprint = fingerprint
        self._request = request
        from polaris.cells.roles.runtime.internal.engineering_plugin_slot import claim_plugin_slot

        async def _draw() -> list[str]:
            async with claim_plugin_slot():
                return await self.pool.draw_many([request])

        self._job = asyncio.create_task(_draw())
        return True

    def poll(self) -> str | None:
        if (
            self._job is None
            or not self._job.done()
            or self._fingerprint in self._rejections
            or self._consumed == self._fingerprint
        ):
            return None
        try:
            results = self._job.result()
        except asyncio.CancelledError:
            return None
        except Exception as error:  # noqa: BLE001 - 记录真实失败原因，别让审计猜
            # 旧版只把 None 交给校验层，拒绝详情永远是一句 "NoneType"，
            # 无法区分超时 / 空响应 / HTTP 5xx。
            self._last_error = f"{type(error).__name__}: {error}"[:200]
            return ""
        return results[0] if results else ""

    @property
    def last_error(self) -> str:
        return self._last_error or self.pool.last_error

    @property
    def fingerprint(self) -> str:
        return self._fingerprint

    @property
    def prior_raw_responses(self) -> list[str]:
        prior = getattr(self.pool, "prior_raw_responses", None)
        return prior if isinstance(prior, list) else []

    def retire_delivered_probe(self, events: Sequence[Mapping[str, Any]], *, workspace: str, session_id: str) -> bool:
        """Consume only after the submitted check is visible in a completed physical request."""
        if not self._request.get("decision"):
            return False
        from polaris.cells.context.engine.public.snapshot_paths import ContextSnapshotReadError, read_context_snapshot

        offered: dict[int, str] = {}
        for event in events:
            if event.get("role") != "director":
                continue
            p = event["payload"]
            turn = p.get("turn_seq")
            if not isinstance(turn, int):
                continue
            if event["kind"] == "run_card_injected" and p.get("cartographer_fingerprint") == self._fingerprint:
                actions = p.get("advisory_next_actions") or []
                if len(actions) == 1 and actions[0].get("action") and actions[0].get("expected"):
                    offered[turn] = f"制图师建议（待检验）：{actions[0]['action']}；预期观察：{actions[0]['expected']}"
                continue
            if (
                event["kind"] != "llm_request_observed"
                or turn not in offered
                or p.get("phase") != "terminal"
                or p.get("status") != "completed"
            ):
                continue
            ref = p.get("context_snapshot_ref")
            if not isinstance(ref, str) or not re.fullmatch("[0-9a-f]{24}", ref) or ref in self._checked_delivery_refs:
                continue
            if p.get("call_id") != f"{session_id}:director:{turn}":
                continue
            try:
                snapshot = read_context_snapshot(workspace, ref).payload
            except (ContextSnapshotReadError, OSError, ValueError):
                continue
            self._checked_delivery_refs.add(ref)
            if snapshot.get("call_id") != p["call_id"]:
                continue
            messages = (
                snapshot.get("provider_request", {})
                .get("final_physical_request", {})
                .get("body", {})
                .get("messages", [])
            )
            if not messages or messages[-1].get("role") != "user":
                continue
            content = messages[-1].get("content")
            texts = (
                [content]
                if isinstance(content, str)
                else [b.get("text", "") for b in (content or []) if isinstance(b, dict) and b.get("type") == "text"]
            )
            if any(isinstance(text, str) and offered[turn] in text for text in texts):
                self._consumed = self._fingerprint
                return True
        return False

    def validate_current(self, payload: object, facts: Mapping[str, Any]) -> dict[str, Any] | None:
        parsed = parse_cartographer_response(payload) if isinstance(payload, str) else payload
        decision = self._request.get("decision")
        if decision and isinstance(parsed, Mapping) and parsed.get("abstain") is True:
            abstention = _decision_probe(parsed, decision)
            self._consumed = self._fingerprint
            return abstention
        observed = self._request["working_state"]
        for key in ("workspace_revision", "verification", "phase"):
            if observed[key] != facts[key]:
                raise NarrativeRejectedError("stale_working_state", f"{key}: {observed[key]!r} -> {facts[key]!r}")
        allowed_refs = set(self._request["evidence_refs"]) & facts["evidence_refs"]
        source = self._request.get("already_visible_source")
        if (
            isinstance(source, Mapping)
            and facts.get("latest_source_change", {}).get(source["path"], 0) > source["observed_seq"]
        ):
            raise NarrativeRejectedError(
                "superseded_visible_source", "The path in the retained source excerpt has a later write dispatch."
            )
        result: dict[str, Any] | None
        if decision:
            result = _decision_probe(parsed, decision)
            cited = result["evidence_refs"]
        else:
            result = validate_run_card_narrative(parsed, facts, allowed_refs=allowed_refs)
            cited = parsed.get("evidence_refs") if isinstance(parsed, Mapping) else None
        if result and isinstance(cited, list) and any(str(ref) not in allowed_refs for ref in cited):
            raise NarrativeRejectedError("unbound_evidence_ref", "The draw cited evidence outside its observed inputs.")
        if result and isinstance(cited, list):
            draw_seq = max(
                (
                    int(ref[6:])
                    for ref in self._request["evidence_refs"]
                    if ref.startswith("event:") and ref[6:].isdecimal()
                ),
                default=0,
            )
            superseded = facts.get("superseded_evidence", {})
            observation = decision.get("observation", {}) if decision else {}
            target = observation.get("subject")
            if (
                result.get("check_tool") == "read_file"
                and observation.get("tool") in {"edit_file", "write_file", "read_file"}
                and isinstance(target, str)
            ):
                result["check_target"] = target
                if facts.get("read_observed_at", {}).get(target, 0) > draw_seq:
                    raise NarrativeRejectedError(
                        "probe_already_observed", "The proposed source read already has a newer successful observation."
                    )
            for ref in cited:
                if superseded.get(ref, 0) > draw_seq:
                    raise NarrativeRejectedError(
                        "superseded_evidence", f"{ref} has a newer observation at event:{superseded[ref]}"
                    )
        return result

    def note_rejection(self, code: str, detail: str = "") -> bool:
        """Return True only the first time this fingerprint rejection is seen.

        A completed draw is re-validated on every host poll, so the same
        rejection would otherwise flood the event journal.
        """

        key = self._fingerprint
        if key in self._rejections:
            return False
        self._rejections.add(key)
        return True
