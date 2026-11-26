"""Addressable file facts retain their observed paths, versions and verification scope."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.internal.engineering_acceptance import ClauseFinding, require_observed_clause_evidence
from polaris.cells.roles.runtime.internal.engineering_file_cards import build_file_card, resolve_observed_file
from polaris.cells.roles.runtime.internal.engineering_memory_cards import FILE_FACTS_HEADER, build_known_facts_cards
from polaris.cells.roles.runtime.public.engineering_context_manifest import build_projection_manifest


def _read(seq: int, path: str, source: str, revision: int, **extra: Any) -> dict[str, Any]:
    ref = "filecard:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
    card = build_file_card(
        path, source, preview=source, truncated=False, start_line=1, end_line=2, revision=revision, snapshot_ref=ref
    )
    card.update(extra)
    return {
        "seq": seq,
        "kind": "tool_observed",
        "role": "director",
        "payload": {"tool": "read_file", "ok": True, "content": json.dumps({"file_card": card})},
    }


def test_latest_path_observation_survives_historical_recovery_and_binds_exact_check(tmp_path: Path) -> None:
    source = "def latest():\n    return 'private body'\n"
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()
    ref = "filecard:" + digest
    run = {"workspace": str(tmp_path), "workspace_revision": 3, "verification": "passed"}
    check = {
        "seq": 5,
        "kind": "verification_completed",
        "role": "director",
        "payload": {
            "workspace_revision": 3,
            "verification": "passed",
            "checks": [{"ok": True, "exit_code": 0}],
            "input_binding": {"workspace_revision": 3, "source_manifest": {str(tmp_path / "a.py"): {"sha256": digest}}},
        },
    }
    older = _read(1, "a.py", "def earlier(): pass", 1)
    recovered = _read(4, "a.py", "def earlier(): pass", 1, recovered_from="old", stale=True)
    reads = [older, _read(2, "a.py", source, 3), _read(3, "b.py", source, 3), recovered, check]
    (index,) = build_known_facts_cards(run, reads)
    facts = {row["path"]: row for row in index["entries"]}
    assert set(facts) == {"a.py", "b.py"}
    assert facts["a.py"]["content_ref"] == facts["b.py"]["content_ref"] == ref
    assert facts["a.py"]["symbols"] == ["latest"]
    assert facts["a.py"]["lines"] == 2
    assert facts["a.py"]["at_lines"] == [1]
    assert facts["a.py"]["candidate_check"] == "passed@5"
    assert facts["b.py"]["candidate_check"] == "unknown"
    assert "private body" not in json.dumps(index)
    changed = build_known_facts_cards({**run, "workspace_revision": 4}, reads)[0]
    assert all(row["candidate_check"] == "unknown" for row in changed["entries"])
    (restricted,) = build_known_facts_cards(run, reads, can_read=lambda path: path == "b.py")
    assert restricted["paths"] == ["b.py"]
    with pytest.raises(ValueError, match="multiple source paths"):
        resolve_observed_file(reads, ref, None)
    assert resolve_observed_file(reads, ref, "a.py")["path"] == "a.py"


def test_unrelated_workspace_snapshots_and_unrecoverable_cards_stay_out(tmp_path: Path) -> None:
    root = tmp_path / ".polaris/runtime/filecards"
    root.mkdir(parents=True)
    (root / ("a" * 64 + ".json")).write_text(json.dumps({"path": "other-run.py", "sha256": "a" * 64}), encoding="utf-8")
    bad = _read(1, "missing.py", "data", 3, recoverable=False, snapshot_ref=None)
    assert build_known_facts_cards({"workspace": str(tmp_path)}, [bad]) == []


@pytest.mark.parametrize("with_verifier", [False, True])
def test_no_source_manifest_still_exposes_issued_ref_from_this_run_only(tmp_path: Path, with_verifier: bool) -> None:
    read = _read(1, "inside.py", "def saved(): pass", 3)
    denied = _read(2, "denied.py", "def other(): pass", 3)
    events = [read, denied]
    if with_verifier:
        events.append(
            {
                "seq": 3,
                "kind": "verification_completed",
                "role": "director",
                "payload": {
                    "workspace_revision": 3,
                    "verification": "passed",
                    "checks": [{"ok": True, "exit_code": 0}],
                    "input_binding": {},
                },
            }
        )
    (card,) = build_known_facts_cards(
        {"workspace": str(tmp_path), "workspace_revision": 3, "verification": "passed"},
        events,
        can_read=lambda name: name == "inside.py",
    )
    assert card["paths"] == ["inside.py"]
    (row,) = card["entries"]
    assert row["candidate_check"] == "unknown"
    assert row["content_ref"] == "filecard:" + hashlib.sha256(b"def saved(): pass").hexdigest()


def test_historical_snapshot_cannot_pass_a_current_qa_clause() -> None:
    read = _read(3, "a.py", "def old(): pass", 1, stale=True, recovered_from="saved")
    read["role"] = "qa"
    read["payload"]["call_id"] = "call_read"
    with pytest.raises(ValueError, match="current check"):
        require_observed_clause_evidence(
            [ClauseFinding(clause="behaviour", status="pass", evidence="call_read")],
            events=[read],
            checks=[],
            frame_seq=1,
            report_turn_seq=8,
        )


def test_only_matching_digests_in_physical_user_text_count_as_visible(tmp_path: Path) -> None:
    run = {"workspace": str(tmp_path), "session_id": "r", "workspace_revision": 1}
    events = [_read(1, "a.py", "def first(): pass", 1), _read(2, "b.py", "def second(): pass", 1)]
    entries = build_known_facts_cards(run, events)[0]["entries"]
    text = FILE_FACTS_HEADER + json.dumps(entries[:1])
    messages = [{"role": "user", "content": [{"type": "text", "text": text}]}]
    for body, count in [
        ({"messages": messages}, 1),
        ({"messages": [], "tools": [{"description": text}]}, 0),
        ({}, None),
    ]:
        manifest = build_projection_manifest(
            run,
            events,
            {
                "messages": messages,
                "provider_request": {"final_physical_request": {"body": body}},
            },
        )
        index = next(c for c in manifest["memory_cards"] if c["kind"] == "known_facts")
        assert index["wire_entry_count"] == count
