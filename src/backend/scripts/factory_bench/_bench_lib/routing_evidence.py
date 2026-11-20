"""Bind a Director routing decision to the saved terminal snapshot and physical wire body."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _verified(value: bool, message: str) -> None:
    if not value:
        raise ValueError(message)


def _bytes(path: Path, root: Path) -> tuple[bytes, str]:
    _verified(
        path.resolve(strict=True).is_relative_to(root.resolve(strict=True)) and not path.is_symlink(),
        "invalid evidence path",
    )
    content = path.read_bytes()
    return content, hashlib.sha256(content).hexdigest()


def _read(path: Path, root: Path) -> tuple[dict[str, Any], str]:
    content, digest = _bytes(path, root)
    return json.loads(content.decode("utf-8")), digest


def _event_history(
    directory: Path, terminal_path: Path, terminal: dict[str, Any], terminal_sha: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Compare overlapping immutable windows, then pin a minimum cover ending in the selected terminal file."""
    end = terminal["last_seq"]
    run_id = terminal["run_id"]
    events: dict[int, dict[str, Any]] = {}
    canonical: dict[int, str] = {}
    windows: list[tuple[int, int, str, str]] = []
    seen: dict[str, int] = {}

    def include(path: Path, named_seq: int, snap: dict[str, Any], sha: str) -> None:
        _verified(snap.get("run_id") == snap.get("session_id") == run_id, "event window run identity mismatch")
        _verified(
            all(snap.get(key) == terminal.get(key) for key in ("role", "workflow", "workspace", "engine_version")),
            "event window candidate identity mismatch",
        )
        rows = snap["events"]
        _verified(
            isinstance(rows, list) and bool(rows) and type(snap["last_seq"]) is int and snap["last_seq"] == named_seq,
            "invalid snapshot window endpoint",
        )
        first = named_seq - len(rows) + 1
        _verified(first >= 1, "invalid event window start")
        for seq, event in enumerate(rows, start=first):
            _verified(type(event["seq"]) is int and event["seq"] == seq, "noncontiguous event window")
            encoded = json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
            _verified(seq not in canonical or canonical[seq] == encoded, f"conflicting snapshot event {seq}")
            canonical[seq] = encoded
            events[seq] = event
        windows.append((first, named_seq, path.relative_to(directory).as_posix(), sha))
        seen[sha] = named_seq

    include(terminal_path, end, terminal, terminal_sha)
    # A case may contain many persisted observations of the same frame. Compare
    # all distinct same-run contents, hashing identical copies without decoding them.
    for path in sorted(terminal_path.parent.glob("*-*.json")):
        name = re.fullmatch(r"([0-9]+)-[^/]+\.json", path.name)
        if path == terminal_path or name is None:
            continue
        named_seq = int(name.group(1))
        if not 0 < named_seq <= end:
            continue
        content, sha = _bytes(path, directory)
        if sha in seen:
            _verified(named_seq == seen[sha], "duplicate snapshot under conflicting endpoint")
            continue
        snap = json.loads(content.decode("utf-8"))
        if run_id not in (snap.get("run_id"), snap.get("session_id")):
            continue
        include(path, named_seq, snap, sha)

    selected = [windows[0]]
    needed = selected[0][0] - 1
    while needed > 0:
        covering = (w for w in windows if w[0] <= needed <= w[1])
        # The earliest starting interval covering this endpoint covers at least
        # as much of the missing prefix as any other one-snapshot choice.
        window = min(covering, default=None)
        if window is None:
            raise ValueError(f"incomplete event history at sequence {needed}")
        selected.append(window)
        needed = window[0] - 1
    sources = [{"first_seq": a, "last_seq": b, "path": path, "sha256": sha} for a, b, path, sha in reversed(selected)]
    return [events[seq] for seq in range(1, end + 1)], sources


def routing_witness(directory: Path, observation: dict[str, Any], record_sha: str | None) -> dict[str, Any]:
    try:
        platform = observation["platform"]
        seq, run_id = platform["last_seq"], platform["run_id"]
        _verified(
            type(seq) is int and seq > 0 and isinstance(run_id, str) and bool(run_id), "missing terminal run identity"
        )
        paths = sorted((directory / "snapshots").glob(f"{seq}-*.json"))
        _verified(bool(paths), "terminal snapshot unavailable")
        snap, snapshot_sha = _read(paths[-1], directory)
        _verified(
            snap.get("run_id") == snap.get("session_id") == run_id
            and snap.get("role") == "director"
            and snap.get("workflow") == "direct"
            and snap.get("status") == platform["status"]
            and platform["status"] in {"completed", "failed", "cancelled", "interrupted"}
            and snap["last_seq"] == seq,
            "terminal snapshot identity mismatch",
        )
        events, sources = _event_history(directory, paths[-1], snap, snapshot_sha)
        prepared, successful = {}, {}
        for event in events:
            if event.get("role") != "director":
                continue
            payload = event["payload"]
            turn = payload.get("turn_seq")
            if event["kind"] == "request_prepared":
                prepared[f"{run_id}:director:{turn}"] = payload.get("binding_selection")
            elif event["kind"] == "provider_response" and payload.get("ok") is True:
                successful[f"{run_id}:director:{turn}"] = (payload.get("provider_id"), payload.get("model"))
        _verified(bool(successful), "no successful Director responses")
        evidence = []
        for event in events:
            if event.get("role") != "director" or event["kind"] != "llm_request_observed":
                continue
            payload = event["payload"]
            call_id = payload.get("call_id")
            if payload.get("phase") != "terminal" or payload.get("status") != "completed" or call_id not in successful:
                continue
            pair = (payload.get("provider_id"), payload.get("model"))
            _verified(
                pair == successful[call_id] and all(isinstance(v, str) and v for v in pair), "response binding mismatch"
            )
            ref = payload.get("context_snapshot_ref")
            _verified(isinstance(ref, str) and bool(re.fullmatch("[0-9a-f]{24}", ref)), "invalid physical context ref")
            context, context_sha = _read(directory / "contexts" / (ref + ".json"), directory)
            _verified(
                context.get("call_id") == call_id
                and context.get("role") == "director"
                and context.get("context_hash") == ref
                and context.get("physical_capture_status") == "available"
                and context.get("evidence_level") == "physical_capture"
                and context.get("provider_id") == pair[0]
                and context["final_physical_request"]["body"].get("model") == pair[1],
                "physical context does not prove the selected provider and model",
            )
            _verified(call_id in prepared, "missing Director request preparation")
            decision = prepared[call_id]
            activated = False
            if record_sha is not None:
                _verified(
                    isinstance(decision, dict)
                    and decision.get("qualification_record_sha256") == record_sha
                    and type(decision.get("activated")) is bool,
                    "missing decision for pinned routing record",
                )
                activated = decision["activated"]
                if activated:
                    _verified(
                        list(pair) in decision.get("qualified_order", []), "physical request outside eligible bindings"
                    )
            else:
                _verified(decision is None, "unexpected routing policy on control arm")
            evidence.append(
                {
                    "call_id": call_id,
                    "provider": pair[0],
                    "model": pair[1],
                    "activated": activated,
                    "context_ref": ref,
                    "context_sha256": context_sha,
                    "event_seq": event["seq"],
                }
            )
        _verified(
            {e["call_id"] for e in evidence} == set(successful),
            "physical context unavailable for a completed Director response",
        )
        witness = {"status": "observed", "snapshot_sha256": snapshot_sha, "requests": evidence}
        if len(sources) > 1:
            witness["snapshot_sources"] = sources
        return witness
    except (OSError, ValueError, KeyError, TypeError, AttributeError, IndexError) as exc:
        return {"status": "UNKNOWN", "reason": str(exc)}
