"""Saved rolling event windows are the only history used for offline routing evidence."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from scripts.factory_bench._bench_lib.routing_evidence import routing_witness


def _write(path: Path, data: object) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def _saved(tmp_path: Path) -> tuple[dict[str, Any], Path]:
    snaps, contexts = tmp_path / "snapshots", tmp_path / "contexts"
    snaps.mkdir()
    contexts.mkdir()
    events: list[dict[str, Any]] = [{"seq": i, "role": "director", "kind": "turn_finished", "payload": {}} for i in range(1, 432)]
    for start in (2, 425):
        call_id = f"run:director:{start}"
        ref = f"{start:024x}"
        for offset, (kind, payload) in enumerate(
            [
                ("request_prepared", {"turn_seq": start}),
                ("provider_response", {"turn_seq": start, "ok": True, "provider_id": "local", "model": "model"}),
                (
                    "llm_request_observed",
                    {
                        "call_id": call_id,
                        "phase": "terminal",
                        "status": "completed",
                        "provider_id": "local",
                        "model": "model",
                        "context_snapshot_ref": ref,
                    },
                ),
            ]
        ):
            events[start - 1 + offset].update(kind=kind, payload=payload)
        _write(
            contexts / (ref + ".json"),
            {
                "call_id": call_id,
                "role": "director",
                "context_hash": ref,
                "physical_capture_status": "available",
                "evidence_level": "physical_capture",
                "provider_id": "local",
                "final_physical_request": {"body": {"model": "model"}},
            },
        )
    for first, last in [(1, 150), (101, 300), (151, 350), (232, 431)]:
        _write(
            snaps / f"{last}-1.json",
            {
                "run_id": "run",
                "session_id": "run",
                "role": "director",
                "workflow": "direct",
                "workspace": "/candidate",
                "status": "completed" if last == 431 else "running",
                "last_seq": last,
                "events": events[first - 1 : last],
            },
        )
    return {"platform": {"run_id": "run", "status": "completed", "last_seq": 431}}, snaps


def test_minimum_witness_reconstructs_and_pins_complete_rolling_history(tmp_path: Path) -> None:
    obs, _ = _saved(tmp_path)
    witness = routing_witness(tmp_path, obs, None)
    assert witness["status"] == "observed", witness
    assert [r["call_id"] for r in witness["requests"]] == ["run:director:2", "run:director:425"]
    sources = witness["snapshot_sources"]
    assert len(sources) == 3
    for source in sources:
        assert source["sha256"] == hashlib.sha256((tmp_path / source["path"]).read_bytes()).hexdigest()
    selected = tmp_path / sources[0]["path"]
    selected.write_text(selected.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert routing_witness(tmp_path, obs, None) != witness


@pytest.mark.parametrize("bad", ["gap", "overlap", "identity", "wrong_end", "not_terminal"])
def test_incomplete_or_conflicting_windows_remain_unknown(tmp_path: Path, bad: str) -> None:
    obs, snaps = _saved(tmp_path)
    if bad == "gap":
        for end in (300, 350):
            (snaps / f"{end}-1.json").unlink()
    elif bad == "not_terminal":
        obs["platform"]["status"] = "running"
    else:
        path = snaps / ("431-1.json" if bad == "identity" else "300-1.json")
        data = json.loads(path.read_text(encoding="utf-8"))
        if bad == "overlap":
            data["events"][-1]["payload"] = {"inconsistent": True}
        elif bad == "identity":
            data["session_id"] = "different"
        else:
            data["last_seq"] += 1
        _write(path, data)
    assert routing_witness(tmp_path, obs, None)["status"] == "UNKNOWN"
