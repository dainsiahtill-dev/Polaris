"""Asynchronous semantic advisor lifecycle inside the engineering request loop."""

from __future__ import annotations

import asyncio
import os
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import RemoteAdvisorConfig
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool


class SemanticRuntime:
    """Coalesce background advice jobs; never block the next provider request."""

    def __init__(self, pool: RemoteAdvisorPool, mode: str) -> None:
        self.pool = pool
        self.mode = mode
        self._fingerprint = ""
        self._job: asyncio.Task[list[dict[str, Any]]] | None = None
        self.result: dict[str, Any] | None = None
        self._pins: dict[str, str] = {}
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
        if fingerprint == self._fingerprint and self._job is not None and not self._job.done():
            return
        if self._job is not None and not self._job.done():
            self._job.cancel()
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
        summaries = await self.pool.summarize_many(summary_requests) if summary_requests else []
        # The assessment is what makes folding model-driven: its suggested_view
        # becomes a host pin. Returning an empty assessment list here reduced the
        # whole assist mode to rule-based folding with an unused model call.
        advice = await self.pool.advise_many([request], candidates) if candidates.node_ids else []
        if not advice:
            return [
                {
                    "endpoint_index": 0,
                    "validation_result": "VALID",
                    "result": {"assessments": [], "need_evidence": []},
                    "summaries": summaries,
                }
            ]
        return [{**endpoint, "summaries": summaries} for endpoint in advice]

    def poll(self) -> dict[str, Any] | None:
        job = self._job
        if job is None or not job.done():
            return None
        assert job is not None
        try:
            self.result = job.result()[0]
            self.result["fingerprint"] = self._fingerprint
            self._record_pins()
        except asyncio.CancelledError:
            return None
        return self.result

    def _record_pins(self) -> None:
        if self.mode != "assist" or not self.result or self.result.get("validation_result") != "VALID":
            return
        for assessment in self.result["result"].get("assessments", []):
            view = assessment.get("suggested_view")
            node = assessment.get("node_id")
            if not isinstance(node, str):
                continue
            if view in {"FULL", "SLICE"}:
                self._pins[node] = "semantic_relevance"
            elif view == "CARD":
                self._pins[node] = "semantic_card"
            elif view == "REFERENCE":
                self._pins[node] = "semantic_reference"
        for summary in self.result.get("summaries", []):
            if summary.get("validation_result") != "VALID":
                continue
            result = summary.get("result") or {}
            node = result.get("node_id")
            if isinstance(node, str):
                self._summaries[node] = result

    def pin_reasons(self) -> dict[str, str]:
        return dict(self._pins)

    def fold_summaries(self) -> dict[str, dict[str, Any]]:
        return dict(self._summaries)


def facts_for_advice(run: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Build a bounded advisor input from current FileCards and the latest failure."""

    import hashlib
    import json

    from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation

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
    latest_paths: dict[str, int] = {}
    for event in events:
        if event["payload"].get("tool") == "read_file":
            card = card_from_observation(str(event["payload"].get("content") or ""))
            if card is not None and isinstance(card.get("path"), str):
                latest_paths[card["path"]] = int(event["seq"])
    targets = {
        str(event["payload"].get("arguments", {}).get("file"))
        for event in events[-12:]
        if event["payload"].get("tool") in {"write_file", "edit_file"}
        and event["payload"].get("arguments", {}).get("file")
    }
    instruction = str(run.get("instruction") or "")
    for event in events:
        payload = event["payload"]
        age = current_activity - activity_at_seq.get(int(event["seq"]), current_activity)
        if age < 10:
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
            if isinstance(reference, str) and reference and len(preview.encode("utf-8")) >= 512:
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
        if len(preview.encode("utf-8")) < 512 or path in targets or path in instruction:
            continue
        reference = card.get("content_ref")
        if not isinstance(reference, str) or not reference:
            continue
        node_ids.add(path)
        symbols = card.get("symbols") or []
        slices = [f"slice_{symbol}" for symbol in symbols] or ["slice_file"]
        slice_ids.update(slices)
        candidates.append(
            {
                "node_id": path,
                "snapshot_id": reference,
                "slice_ids": slices,
                "content": preview or card.get("summary") or "",
                "activity_age": age,
                "duplicate": latest_paths.get(path) != int(event["seq"]),
                "kind": "file_card",
                "coverage": card.get("coverage") or {"observed_line_ranges": [[1, max(1, card.get("line_count", 1))]]},
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
    activity_text = last_error or str(run.get("verification") or "not_run")
    request = {"task": run.get("instruction") or "", "activity": activity_text, "candidates": candidates[-4:]}
    refs = CandidateRefs(node_ids=node_ids, slice_ids=slice_ids, evidence_refs=evidence_refs)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "revision": run.get("workspace_revision"),
                "activity": activity_text,
                "candidates": [item["snapshot_id"] for item in candidates[-4:]],
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
