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
        self._job = asyncio.create_task(self.pool.advise_many([request], candidates))

    def poll(self) -> dict[str, Any] | None:
        job = self._job
        if job is None or not job.done():
            return None
        assert job is not None
        try:
            self.result = job.result()[0]
        except asyncio.CancelledError:
            return None
        return self.result

    def pin_reasons(self) -> dict[str, str]:
        if self.mode != "assist" or not self.result or self.result.get("validation_result") != "VALID":
            return {}
        pins: dict[str, str] = {}
        for assessment in self.result["result"].get("assessments", []):
            view = assessment.get("suggested_view")
            node = assessment.get("node_id")
            if not isinstance(node, str):
                continue
            if view in {"FULL", "SLICE"}:
                pins[node] = "semantic_relevance"
            elif view == "CARD":
                pins[node] = "semantic_card"
            elif view == "REFERENCE":
                pins[node] = "semantic_reference"
        return pins


def facts_for_advice(run: dict[str, Any], events: list[dict[str, Any]]) -> tuple[dict[str, Any], CandidateRefs, str]:
    """Build a bounded advisor input from current FileCards and the latest failure."""

    import hashlib
    import json

    from polaris.cells.roles.runtime.internal.engineering_file_cards import card_from_observation

    candidates: list[dict[str, Any]] = []
    node_ids: set[str] = set()
    slice_ids: set[str] = set()
    for event in events[-12:]:
        payload = event["payload"]
        if payload.get("tool") != "read_file" or payload.get("ok") is not True:
            continue
        card = card_from_observation(str(payload.get("content") or ""))
        if card is None:
            continue
        path = str(card["path"])
        node_ids.add(path)
        symbols = card.get("symbols") or []
        slices = [f"slice_{symbol}" for symbol in symbols] or ["slice_file"]
        slice_ids.update(slices)
        candidates.append(
            {
                "node_id": path,
                "snapshot_id": card["content_ref"],
                "slice_ids": slices,
                "content": card.get("content_preview") or card.get("summary") or "",
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
    for event in events[-12:]:
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
    activity = last_error or str(run.get("verification") or "not_run")
    request = {"task": run.get("instruction") or "", "activity": activity, "candidates": candidates[-4:]}
    refs = CandidateRefs(node_ids=node_ids, slice_ids=slice_ids, evidence_refs=evidence_refs)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "revision": run.get("workspace_revision"),
                "activity": activity,
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
