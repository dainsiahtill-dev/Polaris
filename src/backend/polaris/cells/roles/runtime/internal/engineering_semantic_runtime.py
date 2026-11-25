"""Asynchronous distiller lifecycle inside the engineering request loop."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import RemoteAdvisorConfig
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool


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

    def schedule(
        self,
        request: dict[str, Any],
        candidates: CandidateRefs,
        *,
        fingerprint: str,
    ) -> None:
        if self.mode in {"disabled", "shadow"} and self.result is not None:
            return
        if self._job is not None and not self._job.done():
            # Never cancel an in-flight job. The loop re-schedules on every turn,
            # so cancelling on a changed fingerprint guarantees a slow remote
            # summary never completes and the fold always falls back to the
            # deterministic envelope. A superseded request waits instead: the
            # next turn schedules it again once the job lands.
            return
        self._fingerprint = fingerprint
        self._job = asyncio.create_task(self._evaluate(request, candidates))

    async def _evaluate(self, request: dict[str, Any], candidates: CandidateRefs) -> list[dict[str, Any]]:
        summary_requests = [
            {
                "node_id": candidate["node_id"],
                "snapshot_id": candidate.get("snapshot_id") or candidate["node_id"],
                "content": candidate.get("content") or "",
                "coverage": candidate.get("coverage") or {"observed_line_ranges": [[1, 1]]},
                "allowed_evidence_refs": sorted(candidates.evidence_refs),
                "max_summary_tokens": 192,
            }
            for candidate in request.get("candidates", [])
        ]
        if summary_requests:
            from polaris.cells.roles.runtime.internal.engineering_plugin_slot import claim_plugin_slot

            async with claim_plugin_slot():
                summaries = await self.pool.summarize_many(summary_requests)
        else:
            summaries = []
        return [
            {
                "endpoint_index": 0,
                "validation_result": "VALID",
                "summaries": summaries,
            }
        ]

    def poll(self) -> dict[str, Any] | None:
        job = self._job
        if job is None or not job.done():
            return None
        assert job is not None
        try:
            self.result = job.result()[0]
            self.result["fingerprint"] = self._fingerprint
        except asyncio.CancelledError:
            return None
        for summary in self.result.get("summaries", []):
            if summary.get("validation_result") != "VALID":
                continue
            result = summary.get("result") or {}
            node = result.get("node_id")
            if isinstance(node, str):
                self._summaries[node] = result
        return self.result

    def fold_summaries(self) -> dict[str, dict[str, Any]]:
        return dict(self._summaries)


# Bounded so one plane cannot crowd out the others; four left the command and
# file cards competing for a single slot on busy turns.
_MAX_DISTILLER_CANDIDATES = 6


def facts_for_advice(run: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Build a bounded distiller input from current FileCards and the latest failure."""

    import hashlib
    import json

    from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation
    from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
        file_node_id,
        write_node_id,
    )
    from polaris.cells.roles.runtime.internal.engineering_tool_payload_projection import (
        distill_min_activities,
        distill_min_bytes,
        distill_result_min_bytes,
    )

    candidates: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    slice_ids: set[str] = set()
    activity = 0
    activity_at_seq: dict[int, int] = {}
    for event in events:
        if event.get("kind") == "turn_started":
            activity += 1
        activity_at_seq[int(event["seq"])] = activity
    current_activity = activity
    targets = {
        str(event["payload"].get("arguments", {}).get("file"))
        for event in events[-12:]
        if event["payload"].get("tool") in {"write_file", "edit_file"}
        and event["payload"].get("arguments", {}).get("file")
    }
    instruction = str(run.get("instruction") or "")
    min_age = distill_min_activities()
    min_bytes = distill_min_bytes()
    result_min_bytes = distill_result_min_bytes()
    for event in events:
        payload = event["payload"]
        age = current_activity - activity_at_seq.get(int(event["seq"]), current_activity)
        if age < min_age:
            continue
        if payload.get("tool") == "execute_command" and payload.get("ok") is True:
            try:
                observation = json.loads(str(payload.get("content") or "{}"))
            except json.JSONDecodeError:
                observation = None
            card = observation.get("command_card") if isinstance(observation, dict) else None
            reference = card.get("content_ref") if isinstance(card, dict) else None
            preview = (
                str(card.get("stdout_preview") or "") + str(card.get("stderr_preview") or "")
                if isinstance(card, dict)
                else ""
            )
            if isinstance(reference, str) and reference and len(preview.encode("utf-8")) >= result_min_bytes:
                node_ids.add(reference)
                slice_ids.add("slice_output")
                candidates.append(
                    {
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
        if len(preview.encode("utf-8")) < result_min_bytes or path in targets or path in instruction:
            continue
        reference = card.get("content_ref")
        if not isinstance(reference, str) or not reference:
            continue
        node_ids.add(file_node_id(path))
        symbols = card.get("symbols") or []
        slices = [f"slice_{symbol}" for symbol in symbols] or ["slice_file"]
        slice_ids.update(slices)
        candidates.append(
            {
                # 必须是 plane-qualified key：折叠侧
                # engineering_tool_payload_projection 用 file_node_id(path) 查摘要，
                # 这里写裸 path 永远查不中 —— 实测 62 次 file_card 折叠 0 次拿到
                # model_inference 摘要，全部退化成 512B 预览，模型只能重读。
                "node_id": file_node_id(path),
                "snapshot_id": reference,
                "slice_ids": slices,
                "content": preview or card.get("summary") or "",
                "activity_age": age,
                "kind": "file_card",
                "coverage": card.get("coverage") or {"observed_line_ranges": [[1, max(1, card.get("line_count", 1))]]},
            }
        )
    # The model's own write_file bodies are the other large payload plane. Give
    # the distiller the same chance to explain them, so folding a write call can
    # use a real summary instead of only the deterministic structural note.
    call_ok: dict[str, bool] = {}
    for event in events:
        payload = event["payload"]
        if payload.get("tool") in {"write_file", "edit_file"} and payload.get("call_id"):
            call_ok[str(payload["call_id"])] = payload.get("ok") is True
    for event in events:
        payload = event["payload"]
        if payload.get("tool") != "write_file" or call_ok.get(str(payload.get("call_id"))) is not True:
            continue
        arguments = payload.get("arguments")
        if not isinstance(arguments, dict):
            continue
        written_path = arguments.get("file")
        body = arguments.get("content")
        if not isinstance(written_path, str) or not written_path or not isinstance(body, str):
            continue
        if len(body.encode("utf-8")) < min_bytes:
            continue
        age = current_activity - activity_at_seq.get(int(event["seq"]), current_activity)
        if age < min_age or written_path in targets or written_path in instruction:
            continue
        path = written_path
        node_ids.add(write_node_id(path))
        slice_ids.add("slice_file")
        candidates.append(
            {
                "node_id": write_node_id(path),
                "snapshot_id": "writecall:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
                "slice_ids": ["slice_file"],
                "content": body,
                "activity_age": age,
                "kind": "write_call",
                "coverage": {"observed_line_ranges": [[1, max(1, len(body.splitlines()))]]},
            }
        )
    failure_refs = ["goal"]
    last_error = ""
    for event in reversed(events):
        payload = event["payload"]
        if payload.get("ok") is False and payload.get("call_id"):
            failure_refs.append(str(payload["call_id"]))
            last_error = str(payload.get("content") or "")[:1200]
            if len(failure_refs) >= 4:
                break
    evidence_refs = set(failure_refs)
    for event in events:
        payload = event["payload"]
        if payload.get("tool") != "execute_command":
            continue
        try:
            observation = json.loads(str(payload.get("content") or "{}"))
        except json.JSONDecodeError:
            continue
        card = observation.get("command_card") if isinstance(observation, dict) else None
        reference = card.get("content_ref") if isinstance(card, dict) else None
        if isinstance(reference, str) and reference:
            evidence_refs.add(reference)
    # Spend the bounded slots where the fold will actually happen. The fold tier
    # takes the oldest, largest payloads first, so a newest-first selection
    # spends every slot on payloads the fold has not reached yet.
    selected = sorted(
        candidates,
        key=lambda item: (
            -int(item.get("activity_age") or 0),
            -len(str(item.get("content") or "").encode("utf-8")),
        ),
    )[:_MAX_DISTILLER_CANDIDATES]
    activity_text = last_error or str(run.get("verification") or "not_run")
    request = {
        "task": run.get("instruction") or "",
        "activity": activity_text,
        "candidates": selected,
    }
    refs = CandidateRefs(node_ids=node_ids, slice_ids=slice_ids, evidence_refs=evidence_refs)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "revision": run.get("workspace_revision"),
                "activity": activity_text,
                "candidates": [item["snapshot_id"] for item in selected],
                "evidence_refs": sorted(evidence_refs),
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
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
