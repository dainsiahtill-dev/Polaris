"""Asynchronous distiller lifecycle inside the engineering request loop."""

from __future__ import annotations

import asyncio
import os
import re
from collections import Counter
from collections.abc import Collection, Mapping
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
    CandidateRefs,
    summary_input_key,
    write_snapshot_id,
)
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import RemoteAdvisorConfig
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool


def _candidate_key(candidate: dict[str, Any]) -> str:
    return summary_input_key(
        candidate["node_id"],
        candidate["snapshot_id"],
        candidate["content"],
        candidate["coverage"]["observed_line_ranges"],
    )


class SemanticRuntime:
    """Coalesce background summary jobs; never block the next provider request.

    The model's only job is to compress: it produces the summaries the host uses
    when it folds a card. Deciding *what* to fold stays with the host's activity,
    byte and duplicate rules plus its deterministic focus pins.
    """

    def __init__(self, pool: RemoteAdvisorPool, mode: str) -> None:
        self.pool = pool
        self.mode = mode
        self._fingerprint = ""
        self._job: asyncio.Task[list[dict[str, Any]]] | None = None
        self.result: dict[str, Any] | None = None
        self._summaries: dict[str, dict[str, Any]] = {}
        self._evaluated: set[str] = set()

    @property
    def ready(self) -> bool:
        return self._job is None and self.mode != "disabled" and (self.mode != "shadow" or self.result is None)

    def schedule(
        self,
        request: dict[str, Any],
        candidates: CandidateRefs,
        *,
        fingerprint: str,
    ) -> None:
        if not self.ready:
            # Never cancel an in-flight job. The loop re-schedules on every turn,
            # so cancelling on a changed fingerprint guarantees a slow remote
            # summary never completes and the fold always falls back to the
            # deterministic envelope. A superseded request waits instead: the
            # next turn schedules it again once the job lands.
            return
        pending = [
            item
            for item in request.get("candidates", [])
            if item["node_id"] in candidates.node_ids and _candidate_key(item) not in self._evaluated
        ]
        if not pending:
            return
        self._fingerprint = fingerprint
        self._job = asyncio.create_task(self._evaluate(pending))

    async def _evaluate(self, pending: list[dict[str, Any]]) -> list[dict[str, Any]]:
        summary_requests = [
            {
                "node_id": candidate["node_id"],
                "node_kind": candidate.get("kind", "source"),
                "snapshot_id": candidate.get("snapshot_id") or candidate["node_id"],
                "content": candidate.get("content") or "",
                "coverage": candidate.get("coverage") or {"observed_line_ranges": [[1, 1]]},
                "allowed_evidence_refs": [],  # The node snapshot is the sole supplied evidence.
                "max_summary_tokens": 192,
            }
            for candidate in pending
        ]
        from polaris.cells.roles.runtime.internal.engineering_plugin_slot import request_node_summaries

        summaries = await request_node_summaries(summary_requests, self.pool.summarize_many)
        valid = 0
        for item, receipt in zip(pending, summaries, strict=True):
            key = _candidate_key(item)
            self._evaluated.add(key)
            if receipt.get("validation_result") != "VALID":
                continue
            result = receipt["result"]
            coverage = result.get("coverage")
            if (
                result.get("node_id") != item["node_id"]
                or result.get("snapshot_id") != item["snapshot_id"]
                or not isinstance(coverage, dict)
                or coverage.get("observed_line_ranges") != item["coverage"]["observed_line_ranges"]
            ):
                receipt.update(validation_result="REJECTED", error="summary source binding mismatch")
                continue
            self._summaries[key] = result
            valid += 1
        status = "VALID" if valid == len(pending) else "PARTIAL" if valid else "REJECTED"
        return [{"validation_result": status, "summaries": summaries}]

    def evaluated_inputs(self) -> Collection[str]:
        """Already attempted in this role invocation, including rejected advice."""
        return self._evaluated

    def poll(self) -> dict[str, Any] | None:
        job = self._job
        if job is None or not job.done():
            return None
        self._job = None
        try:
            self.result = job.result()[0]
            self.result["fingerprint"] = self._fingerprint
        except asyncio.CancelledError:
            return None
        return self.result

    def fold_summaries(self) -> dict[str, dict[str, Any]]:
        return dict(self._summaries) if self.mode == "assist" else {}


# Bounded so one plane cannot crowd out the others; four left the command and
# file cards competing for a single slot on busy turns.
_MAX_DISTILLER_CANDIDATES = 6


def facts_for_advice(
    run: dict[str, Any],
    events: list[dict[str, Any]],
    *,
    evaluated: Collection[str] = (),
    targets: Mapping[tuple[str, str], bool] | None = None,
    role: str | None = None,
) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Select not-yet-summarized observations eligible for a future fold."""

    import hashlib
    import json

    from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
        file_node_id,
        file_summary_source,
        write_node_id,
    )
    from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
        distill_min_activities,
        distill_min_bytes,
        distill_result_min_bytes,
    )

    if targets is not None and not targets:
        return {"candidates": []}, CandidateRefs(node_ids=set(), slice_ids=set(), evidence_refs=set()), ""
    if role is not None:
        events = [event for event in events if event.get("role") == role]
    observed_ids = Counter(
        event["payload"]["call_id"]
        for event in events
        if event.get("kind") == "tool_observed" and isinstance(event["payload"].get("call_id"), str)
    )
    candidates: list[dict[str, Any]] = []
    activity = 0
    activity_at_seq: dict[int, int] = {}
    for event in events:
        if event.get("kind") == "turn_started":
            activity += 1
        activity_at_seq[int(event["seq"])] = activity
    current_activity = activity
    recent_targets = {
        str(event["payload"].get("arguments", {}).get("file"))
        for event in events[-12:]
        if event["payload"].get("tool") in {"write_file", "edit_file"}
        and event["payload"].get("arguments", {}).get("file")
    }
    visible_ids = {cid for cid, _node in targets} if targets is not None else None

    def wanted(call_id: str, node_id: str) -> bool:
        return targets is None or (observed_ids[call_id] == 1 and (call_id, node_id) in targets)

    instruction = str(run.get("instruction") or "")
    min_age = distill_min_activities()
    min_bytes = distill_min_bytes()
    result_min_bytes = distill_result_min_bytes()
    for event in events:
        payload = event["payload"]
        age = current_activity - activity_at_seq.get(int(event["seq"]), current_activity)
        call_id = payload.get("call_id", "")
        if event.get("kind") != "tool_observed" or (visible_ids is not None and call_id not in visible_ids):
            continue
        if targets is None and age < min_age:
            continue
        if payload.get("tool") == "execute_command" and not payload.get("interrupted") and not payload.get("timed_out"):
            try:
                observation = json.loads(str(payload.get("content") or "{}"))
            except json.JSONDecodeError:
                observation = None
            card = observation.get("command_card") if isinstance(observation, dict) else None
            reference = card.get("content_ref") if isinstance(card, dict) else None
            preview = (
                str(card.get("stdout_preview") or "") + "\n" + str(card.get("stderr_preview") or "")
                if isinstance(card, dict)
                else ""
            )
            if (
                isinstance(reference, str)
                and reference
                and wanted(call_id, reference)
                and (targets is not None or len(preview.encode("utf-8")) >= result_min_bytes)
            ):
                candidates.append(
                    {
                        "call_id": call_id,
                        "node_id": reference,
                        "snapshot_id": reference,
                        "slice_ids": ["slice_output"],
                        "content": preview or (str(card.get("summary") or "") if isinstance(card, dict) else ""),
                        "activity_age": age,
                        "kind": "command_card",
                        "coverage": {"observed_line_ranges": [[1, max(1, len(preview.splitlines()))]]},
                    }
                )
            continue
        if payload.get("tool") != "read_file" or payload.get("ok") is not True:
            continue
        card = card_from_observation(str(payload.get("content") or ""))
        if card is None:
            continue
        path = str(card["path"])
        preview = str(card.get("content_preview") or "")
        if not wanted(call_id, file_node_id(path)) or (
            targets is None
            and (len(preview.encode("utf-8")) < result_min_bytes or path in recent_targets or path in instruction)
        ):
            continue
        reference = card.get("content_ref")
        if not isinstance(reference, str) or not reference:
            continue
        source = file_summary_source(card, preview)
        if source is None:
            continue
        visible, ranges = source
        symbols = card.get("symbols") or []
        slices = [f"slice_{symbol}" for symbol in symbols] or ["slice_file"]
        candidates.append(
            {
                # 必须是 plane-qualified key：折叠侧
                # engineering_tool_payload_projection 用 file_node_id(path) 查摘要，
                # 这里写裸 path 永远查不中 —— 实测 62 次 file_card 折叠 0 次拿到
                # model_inference 摘要，全部退化成 512B 预览，模型只能重读。
                "call_id": call_id,
                "node_id": file_node_id(path),
                "snapshot_id": reference,
                "slice_ids": slices,
                "content": visible,
                "activity_age": age,
                "kind": "file_card",
                "coverage": {"observed_line_ranges": ranges},
            }
        )
    # The model's own write_file bodies are the other large payload plane. Give
    # the distiller the same chance to explain them, so folding a write call can
    # use a real summary instead of only the deterministic structural note.
    started: dict[tuple[object, str, str], dict[str, Any]] = {}
    succeeded: dict[tuple[object, str, str], int] = {}
    latest_change: dict[str, tuple[int, str | None]] = {}
    for event in events:
        payload = event["payload"]
        tool = payload.get("tool")
        call_id = payload.get("call_id")
        if tool not in {"write_file", "edit_file"} or not isinstance(call_id, str):
            continue
        dispatch = (payload.get("turn_seq"), call_id, tool)
        args = payload.get("arguments")
        if event["kind"] == "tool_started" and isinstance(args, dict):
            started[dispatch] = args
        elif (
            event["kind"] == "tool_observed"
            and payload.get("ok") is True
            and not payload.get("interrupted")
            and not payload.get("timed_out")
            and dispatch in started
        ):
            seq = int(event["seq"])
            succeeded[dispatch] = seq
            args = started[dispatch]
            changed_path = args.get("file")
            if isinstance(changed_path, str):
                if tool == "write_file" and isinstance(args.get("content"), str):
                    latest_change[changed_path] = (seq, args["content"])
                elif (
                    tool == "edit_file"
                    and isinstance(args.get("search"), str)
                    and isinstance(args.get("replace"), str)
                    and args["search"] != args["replace"]
                ):
                    latest_change[changed_path] = (seq, None)

    last_reasoning = next(
        (
            str(message["content"])
            for event in reversed(events)
            if event.get("kind") == "message"
            and isinstance(message := event["payload"].get("message"), dict)
            and message.get("role") == "assistant"
            and isinstance(message.get("content"), str)
            and message["content"].strip()
        ),
        "",
    )
    historical_refs = set(re.findall(r"[\w:-]+", instruction + "\n" + last_reasoning))

    for event in events:
        payload = event["payload"]
        call_id = payload.get("call_id")
        if (
            event.get("kind") != "tool_started"
            or payload.get("tool") != "write_file"
            or not isinstance(call_id, str)
            or (payload.get("turn_seq"), call_id, "write_file") not in succeeded
            or (visible_ids is not None and call_id not in visible_ids)
        ):
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            continue
        written_path = arguments.get("file")
        body = arguments.get("content")
        if not isinstance(written_path, str) or not written_path or not isinstance(body, str):
            continue
        if not wanted(call_id, write_node_id(written_path)) or (
            targets is None and len(body.encode("utf-8")) < min_bytes
        ):
            continue
        age = current_activity - activity_at_seq.get(int(event["seq"]), current_activity)
        if targets is None and (age < min_age or written_path in recent_targets or written_path in instruction):
            continue
        change = latest_change.get(written_path)
        if (
            change is not None
            and change[0] > succeeded[payload.get("turn_seq"), call_id, "write_file"]
            and change[1] != body
            and call_id not in historical_refs
            and write_snapshot_id(body) not in historical_refs
        ):
            continue
        path = written_path
        candidates.append(
            {
                "call_id": call_id,
                "node_id": write_node_id(path),
                "snapshot_id": write_snapshot_id(body),
                "slice_ids": ["slice_file"],
                "content": body,
                "activity_age": age,
                "kind": "write_call",
                "coverage": {"observed_line_ranges": [[1, max(1, len(body.splitlines()))]]},
            }
        )
    # Serve actual fallback folds first. A file or command observation is more
    # costly to reconstruct than a successful write already present on disk.
    ranked = sorted(
        (item for item in candidates if _candidate_key(item) not in evaluated),
        key=lambda item: (
            not (targets or {}).get((item["call_id"], item["node_id"]), False),
            item["kind"] == "write_call",
            -item["activity_age"],
            -len(item["content"].encode("utf-8")),
        ),
    )
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked:
        if item["node_id"] not in seen:
            selected.append(item)
            seen.add(item["node_id"])
            if len(selected) == _MAX_DISTILLER_CANDIDATES:
                break
    refs = CandidateRefs(
        node_ids={item["node_id"] for item in selected},
        slice_ids={slice_id for item in selected for slice_id in item["slice_ids"]},
        evidence_refs={item["snapshot_id"] for item in selected},
    )
    request = {"candidates": selected}
    fingerprint = hashlib.sha256(
        json.dumps([_candidate_key(item) for item in selected], ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return request, refs, fingerprint


def semantic_runtime_from_environment() -> SemanticRuntime | None:
    raw = os.environ.get("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG", "").strip()
    if not raw:
        return None
    try:
        import json

        configs = json.loads(raw)
        if not isinstance(configs, list):
            raise ValueError("advisor config must be a list")
        parsed = [RemoteAdvisorConfig(base_url=str(item["base_url"]), model=str(item["model"])) for item in configs]
        mode = os.environ.get("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_MODE", "shadow").strip() or "shadow"
        if mode not in {"disabled", "shadow", "assist"}:
            raise ValueError("advisor mode must be disabled, shadow or assist")
        return SemanticRuntime(RemoteAdvisorPool(parsed), mode)
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise ValueError(f"invalid KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG: {error}") from error
