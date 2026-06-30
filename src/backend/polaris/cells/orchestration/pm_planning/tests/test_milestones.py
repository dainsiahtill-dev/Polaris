"""Tests for the PM milestones registry.

These cover the enum vocabulary, the frozen record + ``to_dict`` round-trip, the
PURE schedule-aware health classifier (which performs no I/O and never raises),
the lifecycle transition guard, the storage round-trip, the path-traversal
guard, fail-closed loading/coercion, atomicity, determinism, the default-off
wiring gate, and the boundary/§8 invariants (no business literals, no forbidden
imports, no live-path wiring).
"""

from __future__ import annotations

import ast
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal import milestones as ms
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import compute_schedule
from polaris.cells.orchestration.pm_planning.internal.milestones import (
    MilestoneRecordV1,
    MilestoneRegister,
    MilestoneStatus,
    classify_milestone_health,
)

# Expected enum vocabulary, asserted via a hardcoded map (no cross-cell import).
EXPECTED_STATUS = {
    "PLANNED": "planned",
    "IN_PROGRESS": "in_progress",
    "ACHIEVED": "achieved",
    "MISSED": "missed",
    "CANCELLED": "cancelled",
}


@pytest.fixture
def register(tmp_path: Path) -> MilestoneRegister:
    """Return a MilestoneRegister rooted at a per-test temp workspace."""
    return MilestoneRegister(str(tmp_path))


def _record(**overrides: Any) -> MilestoneRecordV1:
    """Build a MilestoneRecordV1 with sane generic defaults, overridable per test."""
    base: dict[str, Any] = {
        "milestone_id": "milestone_seed_0001",
        "name": "label-a",
        "status": MilestoneStatus.PLANNED,
        "target_iteration": None,
    }
    base.update(overrides)
    return MilestoneRecordV1(**base)


# ----------------------------------------------------------------------
# Enum vocabulary
# ----------------------------------------------------------------------


def test_enum_values() -> None:
    actual = {m.name: m.value for m in MilestoneStatus}
    assert actual == EXPECTED_STATUS
    assert len(list(MilestoneStatus)) == 5


def test_str_enum_serializes_bare() -> None:
    assert MilestoneStatus.ACHIEVED == "achieved"
    assert json.dumps(MilestoneStatus.ACHIEVED.value) == '"achieved"'


def test_enums_owned_by_module() -> None:
    assert issubclass(MilestoneStatus, Enum)
    assert MilestoneStatus.__module__ == ms.__name__


# ----------------------------------------------------------------------
# Record + to_dict
# ----------------------------------------------------------------------


def test_record_to_dict_json_safe() -> None:
    record = MilestoneRecordV1(
        milestone_id="milestone_x_1",
        name="label-a",
        description="text-a",
        status=MilestoneStatus.IN_PROGRESS,
        target_iteration=3,
        task_ids=("alpha", "beta"),
        owner="actor-a",
        acceptance="criteria-a",
        created_at="2026-01-01T00:00:00Z",
        achieved_at=None,
        history=({"at": "t", "actor": "a", "action": "registered", "note": ""},),
    )
    payload = record.to_dict()
    assert payload["status"] == "in_progress"
    assert isinstance(payload["status"], str)
    assert payload["task_ids"] == ["alpha", "beta"]
    assert payload["history"] == [{"at": "t", "actor": "a", "action": "registered", "note": ""}]
    # None preserved honestly (not 0 / "").
    assert payload["target_iteration"] == 3
    assert payload["achieved_at"] is None
    # Whole dict is JSON round-trippable.
    assert json.loads(json.dumps(payload)) == payload


def test_record_to_dict_preserves_none_target_and_achieved() -> None:
    payload = _record(target_iteration=None, achieved_at=None).to_dict()
    assert payload["target_iteration"] is None
    assert payload["achieved_at"] is None


def test_record_invariant_milestone_id_required() -> None:
    with pytest.raises(ValueError):
        MilestoneRecordV1(milestone_id="")
    with pytest.raises(ValueError):
        MilestoneRecordV1(milestone_id="   ")
    # Empty name AND empty created_at must NOT raise (partial-recovery).
    rec = MilestoneRecordV1(milestone_id="milestone_x", name="", created_at="")
    assert rec.milestone_id == "milestone_x"


# ----------------------------------------------------------------------
# register / load round-trip
# ----------------------------------------------------------------------


def test_register_returns_planned_seeded(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a", owner="actor-a", actor="actor-a")
    assert rec.status is MilestoneStatus.PLANNED
    assert rec.created_at.endswith("Z")
    assert rec.achieved_at is None
    assert len(rec.history) == 1
    assert rec.history[0]["action"] == "registered"
    assert rec.history[0]["actor"] == "actor-a"
    assert rec.milestone_id.startswith("milestone_")
    assert re.match(r"^[A-Za-z0-9_.-]+$", rec.milestone_id)


def test_register_actor_falls_back_to_owner(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a", owner="owner-a")
    assert rec.history[0]["actor"] == "owner-a"


def test_register_requires_name(register: MilestoneRegister) -> None:
    with pytest.raises(ValueError):
        register.register(name="")


def test_register_then_load_round_trip(register: MilestoneRegister) -> None:
    rec = register.register(
        name="label-a",
        description="text-a",
        target_iteration=5,
        task_ids=("alpha", "beta"),
        owner="actor-a",
        acceptance="criteria-a",
    )
    loaded = register.load(rec.milestone_id)
    assert loaded is not None
    assert loaded.name == "label-a"
    assert loaded.description == "text-a"
    assert loaded.status is MilestoneStatus.PLANNED
    assert loaded.target_iteration == 5
    assert loaded.task_ids == ("alpha", "beta")
    assert isinstance(loaded.task_ids, tuple)
    assert loaded.owner == "actor-a"
    assert loaded.acceptance == "criteria-a"
    assert loaded.achieved_at is None
    assert len(loaded.history) == 1


def test_register_none_target_round_trips(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a", target_iteration=None)
    loaded = register.load(rec.milestone_id)
    assert loaded is not None
    assert loaded.target_iteration is None


def test_milestone_id_guard_clean(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    assert ms._SAFE_ID_FULLMATCH.match(rec.milestone_id)
    assert ".." not in rec.milestone_id
    assert ms._validate_record_id(rec.milestone_id) == rec.milestone_id


def test_load_absent_returns_none(register: MilestoneRegister) -> None:
    assert register.load("milestone_absent_0001") is None


def test_load_traversal_raises(register: MilestoneRegister) -> None:
    with pytest.raises(ValueError):
        register.load("../x")
    with pytest.raises(ValueError):
        register.load("a/b")
    assert sorted(register._dir.glob("*.json")) == []


# ----------------------------------------------------------------------
# Lifecycle transitions
# ----------------------------------------------------------------------


def test_transition_legal_chain(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    created_at = rec.created_at
    step1 = register.update_status(rec.milestone_id, MilestoneStatus.IN_PROGRESS, actor="actor-a")
    assert step1.status is MilestoneStatus.IN_PROGRESS
    assert len(step1.history) == 2
    assert step1.history[0]["action"] == "registered"  # earlier event preserved
    step2 = register.update_status(rec.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    assert step2.status is MilestoneStatus.ACHIEVED
    assert step2.achieved_at is not None
    assert step2.achieved_at.endswith("Z")
    assert len(step2.history) == 3
    assert step2.history[-1]["action"] == "status:achieved"
    assert step2.created_at == created_at  # unchanged


def test_transition_to_achieved_idempotent_achieved_at(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    first = register.update_status(rec.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    stamped = first.achieved_at
    assert stamped is not None
    # Self-transition ACHIEVED -> ACHIEVED is an allowed no-op; achieved_at preserved.
    second = register.update_status(rec.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    assert second.achieved_at == stamped
    assert len(second.history) == 3  # registered + status:achieved + status:achieved


def test_transition_illegal_from_terminal_raises_and_record_untouched(
    register: MilestoneRegister,
) -> None:
    rec = register.register(name="label-a")
    register.update_status(rec.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    before = register.load(rec.milestone_id)
    assert before is not None
    raw_before = (register._dir / f"{rec.milestone_id}.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        register.update_status(rec.milestone_id, MilestoneStatus.IN_PROGRESS, actor="actor-a")
    raw_after = (register._dir / f"{rec.milestone_id}.json").read_text(encoding="utf-8")
    assert raw_after == raw_before  # byte-untouched


def test_transition_from_missed_and_cancelled_terminal(register: MilestoneRegister) -> None:
    for terminal in (MilestoneStatus.MISSED, MilestoneStatus.CANCELLED):
        rec = register.register(name="label-a")
        register.update_status(rec.milestone_id, terminal, actor="actor-a")
        with pytest.raises(ValueError):
            register.update_status(rec.milestone_id, MilestoneStatus.IN_PROGRESS, actor="actor-a")


def test_transition_self_recorded(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    same = register.update_status(rec.milestone_id, MilestoneStatus.PLANNED, actor="actor-a")
    assert same.status is MilestoneStatus.PLANNED
    assert len(same.history) == 2  # registered + recorded self no-op


def test_update_status_absent_raises_filenotfound(register: MilestoneRegister) -> None:
    with pytest.raises(FileNotFoundError):
        register.update_status("milestone_absent_0001", MilestoneStatus.ACHIEVED, actor="actor-a")


# ----------------------------------------------------------------------
# Pure health classifier
# ----------------------------------------------------------------------


def test_health_terminal_mirrors() -> None:
    assert (
        classify_milestone_health(_record(status=MilestoneStatus.ACHIEVED, target_iteration=0), current_iteration=100)
        == "achieved"
    )
    assert (
        classify_milestone_health(_record(status=MilestoneStatus.CANCELLED, target_iteration=0), current_iteration=100)
        == "cancelled"
    )
    assert (
        classify_milestone_health(_record(status=MilestoneStatus.MISSED, target_iteration=99), current_iteration=0)
        == "overdue"
    )


def test_health_no_target_fail_closed() -> None:
    band = classify_milestone_health(
        _record(status=MilestoneStatus.PLANNED, target_iteration=None), current_iteration=10_000
    )
    assert band == "on_track"


def test_health_overdue() -> None:
    band = classify_milestone_health(
        _record(status=MilestoneStatus.IN_PROGRESS, target_iteration=3), current_iteration=4
    )
    assert band == "overdue"


def test_health_at_risk_boundary_table() -> None:
    # delta 0 -> at_risk
    assert classify_milestone_health(_record(target_iteration=5), current_iteration=5) == "at_risk"
    # delta == _AT_RISK_HORIZON (1) -> at_risk
    assert classify_milestone_health(_record(target_iteration=6), current_iteration=5) == "at_risk"
    # delta 2 -> on_track (exact threshold)
    assert classify_milestone_health(_record(target_iteration=7), current_iteration=5) == "on_track"


def test_health_before_target_on_track() -> None:
    band = classify_milestone_health(_record(target_iteration=20), current_iteration=5)
    assert band == "on_track"


def test_health_schedule_promotes_on_track_to_at_risk() -> None:
    tasks = [
        {"id": "alpha", "estimated_effort": 3},
        {"id": "beta", "estimated_effort": 3, "depends_on": ["alpha"]},
    ]
    schedule = compute_schedule(tasks)
    assert "alpha" in schedule.critical_path
    rec = _record(target_iteration=20, task_ids=("alpha",))  # base band: on_track (delta 15)
    assert classify_milestone_health(rec, current_iteration=5, schedule=schedule) == "at_risk"
    # Without a schedule the same record stays on_track.
    assert classify_milestone_health(rec, current_iteration=5) == "on_track"


def test_health_schedule_tighten_only() -> None:
    tasks = [
        {"id": "alpha", "estimated_effort": 3},
        {"id": "beta", "estimated_effort": 3, "depends_on": ["alpha"]},
    ]
    schedule = compute_schedule(tasks)
    # An overdue record stays overdue even with a schedule.
    overdue = _record(status=MilestoneStatus.IN_PROGRESS, target_iteration=1, task_ids=("alpha",))
    assert classify_milestone_health(overdue, current_iteration=9, schedule=schedule) == "overdue"
    # A linked id absent from schedule.weights does NOT promote (no fabrication).
    unknown = _record(target_iteration=20, task_ids=("gamma",))
    assert classify_milestone_health(unknown, current_iteration=5, schedule=schedule) == "on_track"
    # Empty task_ids + schedule -> band unchanged.
    none_linked = _record(target_iteration=20, task_ids=())
    assert classify_milestone_health(none_linked, current_iteration=5, schedule=schedule) == "on_track"


def test_health_pure_no_io(tmp_path: Path) -> None:
    rec = _record(target_iteration=5)
    before = sorted(tmp_path.iterdir())
    first = classify_milestone_health(rec, current_iteration=5)
    second = classify_milestone_health(rec, current_iteration=5)
    after = sorted(tmp_path.iterdir())
    assert first == second
    assert before == after  # zero filesystem touches


def test_health_never_raises_on_garbage() -> None:
    empty_schedule = compute_schedule([])
    band = classify_milestone_health(
        _record(target_iteration=None, task_ids=("alpha",)),
        current_iteration=-7,
        schedule=empty_schedule,
    )
    assert band in {"achieved", "cancelled", "on_track", "at_risk", "overdue"}


# ----------------------------------------------------------------------
# Traversal guard
# ----------------------------------------------------------------------


def test_validate_record_id_rejects_traversal_table(register: MilestoneRegister) -> None:
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
            register.update_status(bad, MilestoneStatus.ACHIEVED, actor="actor-a")
    assert sorted(register._dir.glob("*.json")) == []


def test_validate_record_id_accepts_safe_dotted() -> None:
    assert ms._validate_record_id("milestone_ms_abc-123.def") == "milestone_ms_abc-123.def"


# ----------------------------------------------------------------------
# Fail-closed loading / coercion
# ----------------------------------------------------------------------


def test_corrupt_json_skipped(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    (register._dir / "milestone_bad_0001.json").write_text("{ not json", encoding="utf-8")
    listed = register.list()
    assert [r.milestone_id for r in listed] == [rec.milestone_id]
    assert register.summarize()["total"] == 1
    assert register.load("milestone_bad_0001") is None


def test_non_dict_json_returns_none(register: MilestoneRegister) -> None:
    for idx, payload in enumerate(("[]", '"str"', "42")):
        path = register._dir / f"milestone_nd_{idx}.json"
        path.write_text(payload, encoding="utf-8")
        assert register._load_path(path) is None


def test_coercion_fail_closed_bad_values(register: MilestoneRegister) -> None:
    payload = {
        "milestone_id": "milestone_coerce_1",
        "status": "bogus",
        "target_iteration": "not-an-int",
        "task_ids": [],
        "history": "nope",
    }
    path = register._dir / "milestone_coerce_1.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded = register.load("milestone_coerce_1")
    assert loaded is not None
    assert loaded.status is MilestoneStatus.PLANNED
    assert loaded.target_iteration is None
    assert loaded.task_ids == ()
    assert loaded.history == ()


def test_coerce_task_ids_table() -> None:
    # Mirrors the RAID register's link coercion: None/empty -> (), list/tuple ->
    # stripped non-empty strings, a bare scalar -> a single-element tuple.
    assert ms._coerce_task_ids(None) == ()
    assert ms._coerce_task_ids([]) == ()
    assert ms._coerce_task_ids(["alpha", " ", "beta"]) == ("alpha", "beta")
    assert ms._coerce_task_ids(("alpha",)) == ("alpha",)
    assert ms._coerce_task_ids(42) == ("42",)
    assert ms._coerce_task_ids("") == ()


def test_load_missing_partial_fields(register: MilestoneRegister) -> None:
    path = register._dir / "milestone_partial_1.json"
    path.write_text("{}", encoding="utf-8")
    loaded = register.load("milestone_partial_1")
    assert loaded is not None
    assert loaded.milestone_id == "milestone_partial_1"  # from path.stem
    assert loaded.status is MilestoneStatus.PLANNED
    assert loaded.task_ids == ()
    assert loaded.history == ()
    assert loaded.target_iteration is None
    assert loaded.achieved_at is None


def test_coerce_target_iteration_table() -> None:
    assert ms._coerce_target_iteration(3) == 3
    assert ms._coerce_target_iteration("4") == 4
    assert ms._coerce_target_iteration("-2") == -2
    assert ms._coerce_target_iteration(None) is None
    assert ms._coerce_target_iteration("x") is None
    assert ms._coerce_target_iteration(1.5) is None
    assert ms._coerce_target_iteration(True) is None  # bool is not a valid int target


# ----------------------------------------------------------------------
# list / summarize
# ----------------------------------------------------------------------


def test_list_no_filter_sorted(register: MilestoneRegister) -> None:
    ids = {register.register(name=f"label-{i}").milestone_id for i in range(4)}
    first = register.list()
    second = register.list()
    assert {r.milestone_id for r in first} == ids
    assert [r.milestone_id for r in first] == sorted(r.milestone_id for r in first)
    assert [r.milestone_id for r in first] == [r.milestone_id for r in second]


def test_list_filter_by_status(register: MilestoneRegister) -> None:
    a = register.register(name="label-a")
    register.register(name="label-b")
    register.update_status(a.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    achieved = register.list(status=MilestoneStatus.ACHIEVED)
    assert [r.milestone_id for r in achieved] == [a.milestone_id]


def test_list_filter_by_target_iteration(register: MilestoneRegister) -> None:
    a = register.register(name="label-a", target_iteration=7)
    register.register(name="label-b", target_iteration=9)
    matched = register.list(target_iteration=7)
    assert [r.milestone_id for r in matched] == [a.milestone_id]


def test_list_filter_by_task_id(register: MilestoneRegister) -> None:
    a = register.register(name="label-a", task_ids=("alpha",))
    register.register(name="label-b", task_ids=("beta",))
    matched = register.list(task_id="alpha")
    assert [r.milestone_id for r in matched] == [a.milestone_id]


def test_list_combined_filters_intersect(register: MilestoneRegister) -> None:
    a = register.register(name="label-a", target_iteration=7, task_ids=("alpha",))
    register.register(name="label-b", target_iteration=7, task_ids=("beta",))
    register.register(name="label-c", target_iteration=9, task_ids=("alpha",))
    matched = register.list(target_iteration=7, task_id="alpha")
    assert [r.milestone_id for r in matched] == [a.milestone_id]


def test_summarize_by_status_zero_defaults(register: MilestoneRegister) -> None:
    register.register(name="label-a")
    a = register.register(name="label-b")
    register.update_status(a.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    summary = register.summarize()
    assert set(summary["by_status"]) == {s.value for s in MilestoneStatus}
    assert summary["by_status"]["planned"] == 1
    assert summary["by_status"]["achieved"] == 1
    assert summary["by_status"]["missed"] == 0
    assert summary["total"] == len(register.list())


def test_summarize_overdue_and_next_upcoming(register: MilestoneRegister) -> None:
    past = register.register(name="label-a", target_iteration=2)  # overdue at ci=5
    near = register.register(name="label-b", target_iteration=6)  # upcoming
    register.register(name="label-c", target_iteration=12)  # further out
    summary = register.summarize(current_iteration=5)
    assert summary["overdue"] == 1
    assert summary["next_upcoming"] == {"milestone_id": near.milestone_id, "target_iteration": 6}
    assert past.milestone_id  # referenced
    # Deterministic and fail-closed when current_iteration is None.
    none_summary = register.summarize(current_iteration=None)
    assert none_summary["overdue"] == 0
    assert none_summary["next_upcoming"] is None
    assert register.summarize(current_iteration=5) == summary


def test_summarize_next_upcoming_skips_terminal(register: MilestoneRegister) -> None:
    done = register.register(name="label-a", target_iteration=6)
    register.update_status(done.milestone_id, MilestoneStatus.ACHIEVED, actor="actor-a")
    upcoming = register.register(name="label-b", target_iteration=8)
    summary = register.summarize(current_iteration=5)
    assert summary["next_upcoming"] == {
        "milestone_id": upcoming.milestone_id,
        "target_iteration": 8,
    }


def test_summarize_scoped_by_task_id(register: MilestoneRegister) -> None:
    register.register(name="label-a", task_ids=("alpha",), target_iteration=2)
    register.register(name="label-b", task_ids=("beta",), target_iteration=2)
    summary = register.summarize(current_iteration=5, task_id="alpha")
    assert summary["total"] == 1
    assert summary["overdue"] == 1


def test_summarize_one_corrupt_file_survives(register: MilestoneRegister) -> None:
    register.register(name="label-a", target_iteration=2)
    (register._dir / "milestone_bad_0001.json").write_text("{ broken", encoding="utf-8")
    summary = register.summarize(current_iteration=5)
    assert summary["total"] == 1


# ----------------------------------------------------------------------
# Atomicity / durability / encoding
# ----------------------------------------------------------------------


def test_atomic_no_tmp_left(register: MilestoneRegister) -> None:
    rec = register.register(name="label-a")
    register.update_status(rec.milestone_id, MilestoneStatus.IN_PROGRESS, actor="actor-a")
    assert sorted(register._dir.glob("*.tmp")) == []
    assert sorted(p.name for p in register._dir.glob("*.json")) == [f"{rec.milestone_id}.json"]


def test_atomicity_no_half_written(register: MilestoneRegister, monkeypatch: Any) -> None:
    def boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(register._fs, "write_json_atomic", boom)
    with pytest.raises(OSError):
        register.register(name="label-a")
    assert register.list() == []
    assert sorted(register._dir.glob("*.json")) == []
    monkeypatch.undo()
    rec = register.register(name="label-a")
    assert register.load(rec.milestone_id) is not None


def test_utf8_non_ascii_round_trip(register: MilestoneRegister) -> None:
    placeholder = "记录-Ω-é"
    rec = register.register(
        name=placeholder,
        description=placeholder,
        owner=placeholder,
        acceptance=placeholder,
    )
    loaded = register.load(rec.milestone_id)
    assert loaded is not None
    assert loaded.description == placeholder
    assert loaded.owner == placeholder
    raw = (register._dir / f"{rec.milestone_id}.json").read_text(encoding="utf-8")
    assert placeholder in raw  # ensure_ascii=False keeps it literal


def test_byte_stable_with_patched_clock_and_nonce(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(ms, "_utc_now", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(ms, "_nonce", lambda: "deadbeef")
    reg_a = MilestoneRegister(str(tmp_path / "a"))
    reg_b = MilestoneRegister(str(tmp_path / "b"))
    rec_a = reg_a.register(name="label-a", owner="actor-a")
    rec_b = reg_b.register(name="label-a", owner="actor-a")
    assert rec_a.milestone_id == rec_b.milestone_id
    bytes_a = (reg_a._dir / f"{rec_a.milestone_id}.json").read_bytes()
    bytes_b = (reg_b._dir / f"{rec_b.milestone_id}.json").read_bytes()
    assert bytes_a == bytes_b


def test_ensure_directory_false_no_mkdir(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "ws"
    reg = MilestoneRegister(str(target), ensure_directory=False)
    assert not reg._dir.exists()
    assert reg.list() == []
    assert reg.summarize()["total"] == 0


def test_storage_root_runtime_pm_milestones(register: MilestoneRegister) -> None:
    assert register._dir.is_absolute()
    assert register._dir.as_posix().endswith("runtime/pm/milestones")
    rec = register.register(name="label-a")
    assert (register._dir / f"{rec.milestone_id}.json").exists()


# ----------------------------------------------------------------------
# Default-off wiring gate
# ----------------------------------------------------------------------


def test_gate_predicate_default_off(monkeypatch: Any) -> None:
    monkeypatch.delenv(ms._MILESTONE_GATE_ENV, raising=False)
    assert ms._milestone_gate_enabled() is False
    for truthy in ("1", "true", "TRUE", " yes ", "On"):
        assert ms._milestone_gate_enabled(truthy) is True
    for falsy in ("0", "false", "off", "", "maybe"):
        assert ms._milestone_gate_enabled(falsy) is False
    # Env-driven, explicit value overrides.
    monkeypatch.setenv(ms._MILESTONE_GATE_ENV, "true")
    assert ms._milestone_gate_enabled() is True
    assert ms._milestone_gate_enabled("0") is False


# ----------------------------------------------------------------------
# Boundary + §8 invariants
# ----------------------------------------------------------------------


def _imported_module_names(path: Path) -> set[str]:
    """Return every module name referenced by an import statement in a file.

    A relative ``from . import`` is prefixed with one dot per ``level`` so a
    sibling import (e.g. ``.dependency_validator``) is provably distinguishable
    from an absolute cross-cell import. Module names mentioned only in a
    docstring/comment are NOT counted -- only real import statements are.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add("." * node.level + node.module)
    return names


def test_module_imports_only_allowed() -> None:
    forbidden_ce = "chief" + "_engineer"
    forbidden_delivery = "polaris." + "delivery"
    imported = _imported_module_names(Path(ms.__file__))
    assert not any(forbidden_ce in module for module in imported)
    assert not any(module.startswith(forbidden_delivery) for module in imported)
    # The only non-stdlib imports are the KFS/storage helpers and the sibling Schedule.
    allowed_non_stdlib = {
        "polaris.kernelone.fs",
        "polaris.kernelone.storage",
        ".dependency_validator",
    }
    stdlib = {
        "__future__",
        "json",
        "os",
        "re",
        "uuid",
        "dataclasses",
        "datetime",
        "enum",
        "pathlib",
        "typing",
    }
    assert imported <= (stdlib | allowed_non_stdlib)


def test_test_file_does_not_import_chief_engineer() -> None:
    forbidden_ce = "chief" + "_engineer"
    imported = _imported_module_names(Path(__file__))
    assert not any(forbidden_ce in module for module in imported)


def test_no_business_literals_s8() -> None:
    source = Path(ms.__file__).read_text(encoding="utf-8").lower()
    tokens = set(re.findall(r"[a-z0-9]+", source))
    denylist = (
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
    )
    for token in denylist:
        assert token not in tokens, f"S8 violation: business literal {token!r}"


def test_no_live_path_wiring() -> None:
    internal_dir = Path(ms.__file__).parent
    live_modules = [
        "pm_agent.py",
        "task_quality_gate.py",
        "pipeline_ports.py",
        "status_rollup.py",
        "wsjf.py",
        "shared_quality.py",
        "dependency_validator.py",
    ]
    for name in live_modules:
        path = internal_dir / name
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "milestone" not in source
        assert "Milestone" not in source
        assert "MilestoneRegister" not in source
