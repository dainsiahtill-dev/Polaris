"""Asynchronous EpochCard compression for closed long-term engineering history."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import CandidateRefs


def _activity_events(events: Sequence[Mapping[str, Any]]) -> list[tuple[int, list[Mapping[str, Any]]]]:
    groups: list[tuple[int, list[Mapping[str, Any]]]] = []
    activity = 0
    current: list[Mapping[str, Any]] = []
    for event in events:
        if event["kind"] == "turn_started":
            if current:
                groups.append((activity, current))
            activity += 1
            current = []
        current.append(event)
    if current:
        groups.append((activity, current))
    return groups


def _epoch_facts(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    files: set[str] = set()
    verification: list[str] = []
    failures: list[str] = []
    evidence: set[str] = set()
    for event in events:
        payload = event["payload"]
        if payload.get("tool") in {"write_file", "edit_file"}:
            path = payload.get("arguments", {}).get("file")
            if isinstance(path, str):
                files.add(path)
        if event["kind"] == "verification_completed":
            verification.append(str(payload.get("verification") or "unknown"))
        if payload.get("ok") is False:
            failures.append(str(payload.get("content") or "")[:240])
        for key in ("call_id", "content_ref"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                evidence.add(value)
    return {
        "file_changes": sorted(files),
        "verification_history": verification,
        "unresolved_failures": failures[-8:],
        "evidence_refs": sorted(evidence)[:24],
    }


def facts_for_epoch(
    run: Mapping[str, Any], events: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Select the oldest closed epoch at least fifty activities behind current."""

    groups = _activity_events(events)
    current = groups[-1][0] if groups else 0
    eligible = [
        (activity, group)
        for activity, group in groups
        if activity >= 10 and activity % 10 == 0 and activity <= current - 50
    ]
    if not eligible:
        empty = CandidateRefs(node_ids=set(), slice_ids=set(), evidence_refs=set())
        return {"candidates": []}, empty, ""
    activity, group = eligible[0]
    start = activity - 9
    epoch_id = f"epoch-{start:04d}-{activity:04d}"
    facts = _epoch_facts(group)
    evidence_refs = set(facts["evidence_refs"])
    refs = CandidateRefs(node_ids={epoch_id}, slice_ids={"slice_epoch"}, evidence_refs=evidence_refs)
    request = {
        "candidates": [
            {
                "node_id": epoch_id,
                "snapshot_id": epoch_id,
                "slice_ids": ["slice_epoch"],
                "content": json.dumps(
                    {
                        "goal": run.get("instruction") or "",
                        "activity_range": [start, activity],
                        **facts,
                    },
                    ensure_ascii=False,
                ),
                "coverage": {"observed_line_ranges": [[1, len(group)]]},
            }
        ]
    }
    fingerprint = hashlib.sha256(
        json.dumps({"epoch_id": epoch_id, "facts": facts}, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    return request, refs, fingerprint


class EpochRuntime:
    """Coalesce one asynchronous EpochCard summary job per epoch."""

    def __init__(self, pool: Any) -> None:
        self.pool = pool
        self._job: asyncio.Task[list[dict[str, Any]]] | None = None
        self._fingerprint = ""
        self._request: dict[str, Any] = {}

    def schedule(self, request: dict[str, Any], candidates: CandidateRefs, *, fingerprint: str) -> None:
        if fingerprint == self._fingerprint and self._job is not None and not self._job.done():
            return
        if self._job is not None and not self._job.done():
            self._job.cancel()
        self._fingerprint = fingerprint
        self._request = request
        candidate = request["candidates"][0]
        summary_request = {
            "node_id": candidate["node_id"],
            "snapshot_id": candidate["snapshot_id"],
            "content": candidate["content"],
            "coverage": candidate["coverage"],
            "allowed_evidence_refs": sorted(candidates.evidence_refs),
            "max_summary_tokens": 640,
        }
        self._job = asyncio.create_task(self.pool.summarize_many([summary_request]))

    def poll(self) -> dict[str, Any] | None:
        if self._job is None or not self._job.done():
            return None
        try:
            result = self._job.result()[0]
        except asyncio.CancelledError:
            return None
        candidate = self._request["candidates"][0]
        card = {
            "kind": "epoch_card",
            "id": candidate["node_id"],
            "title": candidate["node_id"],
            "fingerprint": self._fingerprint,
            "validation_result": result.get("validation_result"),
        }
        if result.get("validation_result") == "VALID":
            summary = result["result"]
            card.update(
                {
                    "summary": summary["summary"],
                    "summary_kind": summary["summary_kind"],
                    "coverage": summary["coverage"],
                    "uncertain": summary["uncertain"],
                    "evidence_refs": summary["evidence_refs"],
                }
            )
        else:
            card["error"] = result.get("error")
        return card
