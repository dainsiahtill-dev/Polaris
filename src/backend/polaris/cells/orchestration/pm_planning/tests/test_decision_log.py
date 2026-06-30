"""Tests for the PM decision register (ADR-lite governance store).

These cover the enum vocabulary, the frozen record + ``to_dict`` round-trip, the
ADR-lite lifecycle transition guard (legal chains, illegal transitions leaving
disk untouched, terminal states, the self-transition no-op), the storage
round-trip, the path-traversal guard, fail-closed loading/coercion, atomicity,
determinism, the deliberately-omitted gate, and the boundary/§8 invariants (no
business literals, no forbidden imports, no live-path wiring).

The enum/transition vocabulary is asserted via hardcoded maps (NOT by importing
``chief_engineer`` or any sibling) so the boundary stays clean.
"""

from __future__ import annotations

import ast
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal import decision_log as dl
from polaris.cells.orchestration.pm_planning.internal.decision_log import (
    DecisionRecordV1,
    DecisionRegister,
    DecisionStatus,
)

# Expected enum vocabulary, asserted via a hardcoded map (no cross-cell import).
EXPECTED_STATUS = {
    "PROPOSED": "proposed",
    "ACCEPTED": "accepted",
    "REJECTED": "rejected",
    "SUPERSEDED": "superseded",
    "DEFERRED": "deferred",
}

_MODULE_PATH = Path(dl.__file__)
_TEST_PATH = Path(__file__)


@pytest.fixture
def register(tmp_path: Path) -> DecisionRegister:
    """Return a DecisionRegister rooted at a per-test temp workspace."""
    return DecisionRegister(str(tmp_path))


# ----------------------------------------------------------------------
# Enum vocabulary
# ----------------------------------------------------------------------


def test_decision_status_enum_values() -> None:
    """The enum members map to the exact ADR-lite vocabulary."""
    assert {m.name: m.value for m in DecisionStatus} == EXPECTED_STATUS


def test_str_enum_serializes_as_bare_string() -> None:
    """DecisionStatus is a str-enum: it compares to and json-dumps as its value."""
    assert DecisionStatus.ACCEPTED == "accepted"
    assert json.dumps(DecisionStatus.ACCEPTED.value) == '"accepted"'


# ----------------------------------------------------------------------
# Record + to_dict
# ----------------------------------------------------------------------


def test_record_to_dict_round_trip() -> None:
    """to_dict has a fixed key order, bare-string status, list collections."""
    record = DecisionRecordV1(
        decision_id="decision_seed_0001",
        title="label-a",
        context="ctx",
        options=("opt-a", "opt-b"),
        decision="opt-a",
        rationale="why-a",
        owner="owner-a",
        status=DecisionStatus.ACCEPTED,
        task_ids=("t1", "t2"),
        risk_ids=("r1",),
        decided_at="2026-01-01T00:00:00Z",
        history=({"at": "x", "actor": "a", "action": "registered", "note": ""},),
    )
    payload = record.to_dict()
    assert list(payload.keys()) == [
        "decision_id",
        "title",
        "context",
        "options",
        "decision",
        "rationale",
        "owner",
        "status",
        "task_ids",
        "risk_ids",
        "decided_at",
        "history",
    ]
    assert payload["status"] == "accepted"
    assert isinstance(payload["options"], list)
    assert isinstance(payload["task_ids"], list)
    assert isinstance(payload["risk_ids"], list)
    assert isinstance(payload["history"], list)
    assert json.loads(json.dumps(payload)) == payload


def test_record_invariant_decision_id_required() -> None:
    """decision_id must be non-empty; other fields tolerate empties (partial recovery)."""
    with pytest.raises(ValueError):
        DecisionRecordV1(decision_id="")
    with pytest.raises(ValueError):
        DecisionRecordV1(decision_id="   ")
    recovered = DecisionRecordV1(decision_id="decision_partial", title="", decided_at="")
    assert recovered.title == ""
    assert recovered.decided_at == ""


# ----------------------------------------------------------------------
# register
# ----------------------------------------------------------------------


def test_register_returns_proposed_seeded(register: DecisionRegister) -> None:
    """A fresh decision is PROPOSED, decided_at-stamped, and history-seeded."""
    record = register.register(title="label-a", owner="owner-a")
    assert record.status is DecisionStatus.PROPOSED
    assert record.decided_at.endswith("Z")
    assert len(record.history) == 1
    assert record.history[0]["action"] == "registered"
    # actor falls back to owner when actor is empty.
    assert record.history[0]["actor"] == "owner-a"


def test_decision_id_format_is_guard_clean(register: DecisionRegister) -> None:
    """The generated id is prefixed, traversal-clean, and passes the id guard."""
    record = register.register(title="label with spaces / weird.chars")
    decision_id = record.decision_id
    assert decision_id.startswith("decision_")
    assert dl._SAFE_ID_FULLMATCH.match(decision_id)
    assert ".." not in decision_id
    assert dl._validate_record_id(decision_id) == decision_id


def test_register_then_load_round_trip(register: DecisionRegister) -> None:
    """All fields survive a register -> load round-trip via the filesystem."""
    record = register.register(
        title="label-a",
        context="ctx",
        options=("opt-a", "opt-b"),
        decision="opt-a",
        rationale="why-a",
        owner="owner-a",
        task_ids=("t1", "t2"),
        risk_ids=("r1",),
    )
    loaded = register.load(record.decision_id)
    assert loaded is not None
    assert loaded.context == "ctx"
    assert loaded.options == ("opt-a", "opt-b")
    assert loaded.decision == "opt-a"
    assert loaded.rationale == "why-a"
    assert loaded.owner == "owner-a"
    assert loaded.task_ids == ("t1", "t2")
    assert loaded.risk_ids == ("r1",)
    assert len(loaded.history) == 1


def test_register_empty_title_raises(register: DecisionRegister) -> None:
    """register requires a non-empty title; nothing is written on failure."""
    with pytest.raises(ValueError):
        register.register(title="")
    with pytest.raises(ValueError):
        register.register(title="   ")
    assert list(register._dir.glob("*.json")) == []


# ----------------------------------------------------------------------
# Path-traversal guard
# ----------------------------------------------------------------------


def test_validate_record_id_rejects_traversal_table(register: DecisionRegister) -> None:
    """Every traversal/separator/empty id is rejected at load and update_status."""
    bad_ids = [
        "../escape",
        "a/b",
        "a\\b",
        "/etc/passwd",
        "..",
        "",
        "   ",
        "a..b",
        "foo/../bar",
    ]
    for bad in bad_ids:
        with pytest.raises(ValueError):
            register.load(bad)
        with pytest.raises(ValueError):
            register.update_status(bad, DecisionStatus.ACCEPTED, actor="a")
    assert list(register._dir.glob("*.json")) == []


def test_validate_record_id_accepts_safe_dotted() -> None:
    """A safe dotted id (legal '.' char) passes the guard unchanged."""
    assert dl._validate_record_id("decision_x_abc-123.def") == "decision_x_abc-123.def"


def test_load_absent_returns_none(register: DecisionRegister) -> None:
    """Loading a never-registered but safe id returns None (no raise)."""
    assert register.load("decision_never_registered") is None


# ----------------------------------------------------------------------
# Lifecycle transitions
# ----------------------------------------------------------------------


def test_transition_legal_chain(register: DecisionRegister) -> None:
    """PROPOSED -> ACCEPTED -> SUPERSEDED is legal; history grows; decided_at fixed."""
    record = register.register(title="label-a")
    decided_at = record.decided_at
    a = register.update_status(record.decision_id, DecisionStatus.ACCEPTED, actor="a")
    assert a.status is DecisionStatus.ACCEPTED
    assert len(a.history) == 2
    assert a.history[-1]["action"] == "status:accepted"
    s = register.update_status(record.decision_id, DecisionStatus.SUPERSEDED, actor="a")
    assert s.status is DecisionStatus.SUPERSEDED
    assert len(s.history) == 3
    assert s.history[-1]["action"] == "status:superseded"
    assert s.decided_at == decided_at


def test_transition_proposed_to_rejected_and_deferred(register: DecisionRegister) -> None:
    """PROPOSED -> REJECTED, PROPOSED -> DEFERRED, and DEFERRED -> PROPOSED are legal."""
    r1 = register.register(title="label-a")
    rejected = register.update_status(r1.decision_id, DecisionStatus.REJECTED, actor="a")
    assert rejected.status is DecisionStatus.REJECTED

    r2 = register.register(title="label-b")
    deferred = register.update_status(r2.decision_id, DecisionStatus.DEFERRED, actor="a")
    assert deferred.status is DecisionStatus.DEFERRED
    reopened = register.update_status(r2.decision_id, DecisionStatus.PROPOSED, actor="a")
    assert reopened.status is DecisionStatus.PROPOSED


def test_illegal_transition_from_accepted_raises_and_record_untouched(
    register: DecisionRegister,
) -> None:
    """ACCEPTED -> PROPOSED raises and leaves on-disk bytes byte-identical."""
    record = register.register(title="label-a")
    register.update_status(record.decision_id, DecisionStatus.ACCEPTED, actor="a")
    path = register._dir / f"{record.decision_id}.json"
    before = path.read_bytes()
    with pytest.raises(ValueError):
        register.update_status(record.decision_id, DecisionStatus.PROPOSED, actor="a")
    assert path.read_bytes() == before
    again = register.load(record.decision_id)
    assert again is not None and again.status is DecisionStatus.ACCEPTED


def test_illegal_transition_from_terminal_rejected_raises(register: DecisionRegister) -> None:
    """REJECTED is terminal: any onward transition raises and leaves it untouched."""
    record = register.register(title="label-a")
    register.update_status(record.decision_id, DecisionStatus.REJECTED, actor="a")
    path = register._dir / f"{record.decision_id}.json"
    before = path.read_bytes()
    for target in (DecisionStatus.ACCEPTED, DecisionStatus.PROPOSED):
        with pytest.raises(ValueError):
            register.update_status(record.decision_id, target, actor="a")
    assert path.read_bytes() == before


def test_illegal_transition_from_terminal_superseded_raises(register: DecisionRegister) -> None:
    """SUPERSEDED is terminal: any onward transition raises and leaves it untouched."""
    record = register.register(title="label-a")
    register.update_status(record.decision_id, DecisionStatus.ACCEPTED, actor="a")
    register.update_status(record.decision_id, DecisionStatus.SUPERSEDED, actor="a")
    path = register._dir / f"{record.decision_id}.json"
    before = path.read_bytes()
    for target in (DecisionStatus.ACCEPTED, DecisionStatus.PROPOSED, DecisionStatus.REJECTED):
        with pytest.raises(ValueError):
            register.update_status(record.decision_id, target, actor="a")
    assert path.read_bytes() == before


def test_self_transition_is_idempotent_noop(register: DecisionRegister) -> None:
    """X -> X does not raise, records one no-op event, and keeps the status."""
    record = register.register(title="label-a")
    same = register.update_status(record.decision_id, DecisionStatus.PROPOSED, actor="a")
    assert same.status is DecisionStatus.PROPOSED
    assert len(same.history) == 2
    assert same.history[-1]["action"] == "status:proposed"


def test_update_status_missing_entry_raises_filenotfound(register: DecisionRegister) -> None:
    """update_status on a valid-but-absent id raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        register.update_status("decision_absent_but_safe", DecisionStatus.ACCEPTED, actor="a")


def test_update_status_appends_history_preserves_prior(register: DecisionRegister) -> None:
    """A transition grows history by one, preserves the seed event, carries the note."""
    record = register.register(title="label-a")
    seed = dict(record.history[0])
    updated = register.update_status(record.decision_id, DecisionStatus.ACCEPTED, actor="b", note="rationale-note")
    assert len(updated.history) == 2
    assert dict(updated.history[0]) == seed
    assert updated.history[-1]["note"] == "rationale-note"
    assert updated.history[-1]["actor"] == "b"


# ----------------------------------------------------------------------
# list / summarize
# ----------------------------------------------------------------------


def test_list_no_filter_returns_all_sorted(register: DecisionRegister) -> None:
    """list() returns all records filename-sorted, deterministically."""
    ids = [register.register(title=f"label-{i}").decision_id for i in range(3)]
    listed = [r.decision_id for r in register.list()]
    assert listed == sorted(ids)
    assert [r.decision_id for r in register.list()] == listed


def test_list_filter_by_status(register: DecisionRegister) -> None:
    """list(status=...) returns only the records in that status."""
    keep = register.register(title="label-keep")
    register.register(title="label-other")
    register.update_status(keep.decision_id, DecisionStatus.ACCEPTED, actor="a")
    accepted = register.list(status=DecisionStatus.ACCEPTED)
    assert [r.decision_id for r in accepted] == [keep.decision_id]


def test_list_filter_by_task_id(register: DecisionRegister) -> None:
    """list(task_id=...) returns only records whose task_ids contain it."""
    keep = register.register(title="label-keep", task_ids=("alpha", "beta"))
    register.register(title="label-other", task_ids=("gamma",))
    result = register.list(task_id="alpha")
    assert [r.decision_id for r in result] == [keep.decision_id]


def test_list_filter_by_risk_id(register: DecisionRegister) -> None:
    """list(risk_id=...) returns only records whose risk_ids contain it."""
    keep = register.register(title="label-keep", risk_ids=("raid_risk_x",))
    register.register(title="label-other", risk_ids=("raid_risk_y",))
    result = register.list(risk_id="raid_risk_x")
    assert [r.decision_id for r in result] == [keep.decision_id]


def test_list_combined_filters_intersect(register: DecisionRegister) -> None:
    """status + task_id + risk_id AND-filter returns exactly the all-matching record."""
    keep = register.register(title="label-keep", task_ids=("alpha",), risk_ids=("raid_x",))
    register.register(title="label-task-only", task_ids=("alpha",), risk_ids=("raid_y",))
    register.register(title="label-risk-only", task_ids=("beta",), risk_ids=("raid_x",))
    register.update_status(keep.decision_id, DecisionStatus.ACCEPTED, actor="a")
    result = register.list(status=DecisionStatus.ACCEPTED, task_id="alpha", risk_id="raid_x")
    assert [r.decision_id for r in result] == [keep.decision_id]


def test_summarize_counts_by_status_zero_default(register: DecisionRegister) -> None:
    """summarize has every status key with a zero default and correct counts."""
    register.register(title="label-a")
    keep = register.register(title="label-b")
    register.update_status(keep.decision_id, DecisionStatus.ACCEPTED, actor="a")
    summary = register.summarize()
    assert set(summary["by_status"].keys()) == set(EXPECTED_STATUS.values())
    assert summary["by_status"]["proposed"] == 1
    assert summary["by_status"]["accepted"] == 1
    assert summary["by_status"]["rejected"] == 0
    assert summary["total"] == len(register.list())


def test_summarize_accepted_proposed_tally(register: DecisionRegister) -> None:
    """The top-level accepted/proposed tally mirrors by_status."""
    register.register(title="label-a")
    keep = register.register(title="label-b")
    register.update_status(keep.decision_id, DecisionStatus.ACCEPTED, actor="a")
    summary = register.summarize()
    assert summary["accepted"] == summary["by_status"]["accepted"]
    assert summary["proposed"] == summary["by_status"]["proposed"]


def test_summarize_scoped_by_task_and_risk_id(register: DecisionRegister) -> None:
    """summarize(task_id=...) and (risk_id=...) scope total/by_status to the subset."""
    register.register(title="label-a", task_ids=("alpha",), risk_ids=("raid_x",))
    register.register(title="label-b", task_ids=("beta",), risk_ids=("raid_y",))
    by_task = register.summarize(task_id="alpha")
    assert by_task["total"] == 1
    assert by_task["by_status"]["proposed"] == 1
    by_risk = register.summarize(risk_id="raid_y")
    assert by_risk["total"] == 1
    assert by_risk["by_status"]["proposed"] == 1


# ----------------------------------------------------------------------
# Fail-closed loading / coercion
# ----------------------------------------------------------------------


def test_corrupt_json_skipped_on_list(register: DecisionRegister) -> None:
    """A corrupt *.json file is ignored by list()/summarize(); valid records survive."""
    valid = register.register(title="label-a")
    (register._dir / "decision_corrupt.json").write_text("{ not json", encoding="utf-8")
    listed = [r.decision_id for r in register.list()]
    assert listed == [valid.decision_id]
    assert register.summarize()["total"] == 1


def test_corrupt_json_load_returns_none(register: DecisionRegister) -> None:
    """load() of a corrupt-file id returns None instead of raising."""
    (register._dir / "decision_corrupt.json").write_text("{ not json", encoding="utf-8")
    assert register.load("decision_corrupt") is None


def test_non_dict_json_returns_none(register: DecisionRegister) -> None:
    """Files whose top-level JSON is not an object load to None."""
    for i, body in enumerate(("[]", '"str"', "42")):
        name = f"decision_nondict_{i}"
        (register._dir / f"{name}.json").write_text(body, encoding="utf-8")
        assert register.load(name) is None


def test_coercion_fail_closed_on_unknown_status(register: DecisionRegister) -> None:
    """An unknown status coerces to PROPOSED; the record still loads."""
    record = register.register(title="label-a")
    path = register._dir / f"{record.decision_id}.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["status"] = "bogus"
    path.write_text(json.dumps(data), encoding="utf-8")
    loaded = register.load(record.decision_id)
    assert loaded is not None
    assert loaded.status is DecisionStatus.PROPOSED


def test_load_missing_partial_fields(register: DecisionRegister) -> None:
    """An empty-object file loads with id from path.stem and all-default fields."""
    (register._dir / "decision_partial.json").write_text("{}", encoding="utf-8")
    loaded = register.load("decision_partial")
    assert loaded is not None
    assert loaded.decision_id == "decision_partial"
    assert loaded.status is DecisionStatus.PROPOSED
    assert loaded.options == ()
    assert loaded.task_ids == ()
    assert loaded.risk_ids == ()
    assert loaded.history == ()
    assert loaded.title == ""
    assert loaded.context == ""
    assert loaded.decision == ""
    assert loaded.rationale == ""
    assert loaded.owner == ""
    assert loaded.decided_at == ""


def test_coerce_str_tuple_table() -> None:
    """_coerce_str_tuple normalizes scalars, lists, Nones and empties fail-closed."""
    assert dl._coerce_str_tuple(None) == ()
    assert dl._coerce_str_tuple(["a", "", "b"]) == ("a", "b")
    assert dl._coerce_str_tuple("x") == ("x",)
    assert dl._coerce_str_tuple("") == ()
    assert dl._coerce_str_tuple((" a ",)) == ("a",)


# ----------------------------------------------------------------------
# Atomicity / UTF-8 / determinism
# ----------------------------------------------------------------------


def test_atomic_write_no_tmp_left_behind(register: DecisionRegister) -> None:
    """After register + update no *.tmp remains; exactly one {id}.json exists."""
    record = register.register(title="label-a")
    register.update_status(record.decision_id, DecisionStatus.ACCEPTED, actor="a")
    assert list(register._dir.glob("*.tmp")) == []
    assert [p.name for p in register._dir.glob("*.json")] == [f"{record.decision_id}.json"]


def test_atomicity_no_half_written_record_visible(register: DecisionRegister, monkeypatch: pytest.MonkeyPatch) -> None:
    """A mid-write failure leaves no *.json; a later register lands atomically."""

    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(register._fs, "write_json_atomic", _boom)
    with pytest.raises(OSError):
        register.register(title="label-a")
    assert register.list() == []
    assert list(register._dir.glob("*.json")) == []
    monkeypatch.undo()
    record = register.register(title="label-b")
    assert register.load(record.decision_id) is not None


def test_utf8_non_ascii_round_trip(register: DecisionRegister) -> None:
    """Non-ASCII text round-trips and is stored literally (ensure_ascii=False)."""
    text = "决策-α-Ω"
    record = register.register(title=text, context=text, rationale=text, owner=text)
    loaded = register.load(record.decision_id)
    assert loaded is not None
    assert loaded.title == text
    assert loaded.context == text
    assert loaded.rationale == text
    assert loaded.owner == text
    raw = (register._dir / f"{record.decision_id}.json").read_text(encoding="utf-8")
    assert text in raw


def test_round_trip_byte_stable_with_patched_clock_and_nonce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With a frozen clock+nonce, identical register args yield identical id + bytes."""
    monkeypatch.setattr(dl, "_utc_now", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(dl, "_nonce", lambda: "deadbeef")

    reg_a = DecisionRegister(str(tmp_path / "a"))
    reg_b = DecisionRegister(str(tmp_path / "b"))
    rec_a = reg_a.register(title="label-a", context="ctx", options=("o",))
    rec_b = reg_b.register(title="label-a", context="ctx", options=("o",))
    assert rec_a.decision_id == rec_b.decision_id
    bytes_a = (reg_a._dir / f"{rec_a.decision_id}.json").read_bytes()
    bytes_b = (reg_b._dir / f"{rec_b.decision_id}.json").read_bytes()
    assert bytes_a == bytes_b


def test_list_and_summarize_ordering_stable(register: DecisionRegister) -> None:
    """Two consecutive list() (and summarize()) calls return equal results."""
    for i in range(3):
        register.register(title=f"label-{i}")
    assert [r.decision_id for r in register.list()] == [r.decision_id for r in register.list()]
    assert register.summarize() == register.summarize()


# ----------------------------------------------------------------------
# Directory / storage-root semantics
# ----------------------------------------------------------------------


def test_ensure_directory_false_does_not_create_dir(tmp_path: Path) -> None:
    """ensure_directory=False does not create the dir; reads stay neutral."""
    reg = DecisionRegister(str(tmp_path), ensure_directory=False)
    assert not reg._dir.exists()
    assert reg.list() == []
    assert reg.summarize()["total"] == 0
    assert not reg._dir.exists()


def test_storage_root_is_runtime_pm_decisions(register: DecisionRegister) -> None:
    """The store roots under an absolute runtime/pm/decisions path."""
    import os

    assert register._dir.is_absolute()
    assert str(register._dir).endswith(os.path.join("runtime", "pm", "decisions"))
    record = register.register(title="label-a")
    assert (register._dir / f"{record.decision_id}.json").exists()


# ----------------------------------------------------------------------
# Boundary / §8 invariants
# ----------------------------------------------------------------------


def _imported_modules(source: str) -> set[str]:
    """Return the set of top-level imported module names in a source string."""
    names: set[str] = set()
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


def test_module_does_not_import_chief_engineer_or_delivery() -> None:
    """The module imports no chief_engineer/delivery and only the storage SSoT."""
    source = _MODULE_PATH.read_text(encoding="utf-8")
    modules = _imported_modules(source)
    for name in modules:
        assert "chief_engineer" not in name
        assert not name.startswith("polaris.delivery")
    assert DecisionStatus.__module__.endswith("decision_log")
    non_stdlib = {m for m in modules if m.startswith("polaris")}
    assert non_stdlib <= {"polaris.kernelone.fs", "polaris.kernelone.storage"}


def test_test_file_does_not_import_chief_engineer() -> None:
    """The test file imports no chief_engineer (parity via hardcoded maps)."""
    source = _TEST_PATH.read_text(encoding="utf-8")
    for name in _imported_modules(source):
        assert "chief_engineer" not in name


def test_no_business_literals_s8() -> None:
    """The module is 100% generic: no business/domain literals present.

    Matching is word-boundary aware (``\\b``) so the denylist catches a genuine
    standalone business token but does not false-positive on the same letters
    embedded inside ordinary English words (e.g. ``card`` inside ``discarding``,
    ``react`` inside ``reaction``).
    """
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()
    denylist = {
        "game",
        "card3d",
        "card",
        "express",
        "django",
        "react",
        "tenant",
        "dag",
        "tetris",
        "tank",
    }
    for token in denylist:
        pattern = re.compile(rf"\b{re.escape(token)}\b")
        assert pattern.search(source) is None, f"business literal leaked: {token!r}"


def test_no_live_path_wiring() -> None:
    """No live planning module references the new store (wired to nothing)."""
    internal = _MODULE_PATH.parent
    live = [
        "pm_agent.py",
        "task_quality_gate.py",
        "pipeline_ports.py",
        "status_rollup.py",
        "wsjf.py",
        "shared_quality.py",
        "dependency_validator.py",
        "project_report.py",
    ]
    for name in live:
        source = (internal / name).read_text(encoding="utf-8")
        assert "decision_log" not in source
        assert "DecisionRegister" not in source


def test_no_decision_gate_predicate_shipped() -> None:
    """No env-gate predicate or gate constant ships (the YAGNI gate decision)."""
    assert not hasattr(dl, "_decision_gate_enabled")
    source = _MODULE_PATH.read_text(encoding="utf-8")
    assert "KERNELONE_PM_DECISION_GATE" not in source


# Defensive: keep the Enum import used (mirrors sibling test hygiene).
assert issubclass(DecisionStatus, Enum)
