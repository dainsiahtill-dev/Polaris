"""Tests for the PM RAID register store.

These tests cover the enum vocabulary (with CE-parity asserted against
hardcoded literal maps, importing NOTHING from ``chief_engineer``), the pure
risk-score/band functions, the fail-closed env gate, the storage round-trip,
the path-traversal guard, lifecycle transitions, summarization, fail-closed
loading, atomicity, determinism, and the boundary/S8 invariants.
"""

from __future__ import annotations

import ast
import json
import re
from enum import Enum
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal import raid_register as rr
from polaris.cells.orchestration.pm_planning.internal.raid_register import (
    RaidCategory,
    RaidEventV1,
    RaidProbability,
    RaidRegister,
    RaidSeverity,
    RaidStatus,
    build_raid_event,
    compute_risk_band,
    compute_risk_score,
)

# CE-byte-identical vocabulary, asserted WITHOUT importing chief_engineer.
EXPECTED_SEVERITY = {
    "LOW": "low",
    "MEDIUM": "medium",
    "HIGH": "high",
    "CRITICAL": "critical",
    "BLOCKER": "blocker",
}
EXPECTED_STATUS = {
    "OPEN": "open",
    "MITIGATING": "mitigating",
    "ACCEPTED": "accepted",
    "RESOLVED": "resolved",
    "REVERTED": "reverted",
}


@pytest.fixture
def register(tmp_path: Path) -> RaidRegister:
    """Return a RaidRegister rooted at a per-test temp workspace."""
    return RaidRegister(str(tmp_path))


def _register_one(
    register: RaidRegister,
    *,
    task_id: str = "t1",
    category: RaidCategory = RaidCategory.RISK,
    title: str = "generic entry",
    severity: RaidSeverity = RaidSeverity.MEDIUM,
    probability: RaidProbability = RaidProbability.POSSIBLE,
    owner: str = "owner-role",
    detail: str = "",
    links: tuple[str, ...] = (),
    supersedes: str | None = None,
) -> Any:
    return register.register(
        task_id=task_id,
        category=category,
        title=title,
        severity=severity,
        probability=probability,
        owner=owner,
        detail=detail,
        links=links,
        supersedes=supersedes,
    )


# ----------------------------------------------------------------------
# Env gate
# ----------------------------------------------------------------------


def test_risk_gate_enabled_true_tokens() -> None:
    for token in ("1", "true", "yes", "on", " True ", "ON", "  yes\t"):
        assert rr._risk_gate_enabled(token) is True


def test_risk_gate_enabled_default_off() -> None:
    # explicit non-truthy values are OFF (None means "read the env" -- see below)
    for token in ("", "0", "false", "no", "off", "maybe", "2", "  "):
        assert rr._risk_gate_enabled(token) is False


def test_risk_gate_reads_env_when_no_value(monkeypatch: pytest.MonkeyPatch) -> None:
    # unset env -> OFF (the default that pins the live floor)
    monkeypatch.delenv(rr._RISK_GATE_ENV, raising=False)
    assert rr._risk_gate_enabled() is False
    # env truthy -> ON; env falsey -> OFF
    monkeypatch.setenv(rr._RISK_GATE_ENV, "1")
    assert rr._risk_gate_enabled() is True
    monkeypatch.setenv(rr._RISK_GATE_ENV, "0")
    assert rr._risk_gate_enabled() is False
    # an explicit value overrides the environment
    monkeypatch.setenv(rr._RISK_GATE_ENV, "1")
    assert rr._risk_gate_enabled("off") is False


# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------


def test_raid_category_enum_values() -> None:
    assert {m.name: m.value for m in RaidCategory} == {
        "RISK": "risk",
        "ASSUMPTION": "assumption",
        "ISSUE": "issue",
        "DEPENDENCY": "dependency",
    }


def test_severity_parity_with_ce_vocabulary() -> None:
    assert {m.name: m.value for m in RaidSeverity} == EXPECTED_SEVERITY


def test_status_parity_with_ce_vocabulary() -> None:
    assert {m.name: m.value for m in RaidStatus} == EXPECTED_STATUS


def test_probability_enum_values() -> None:
    assert {m.name: m.value for m in RaidProbability} == {
        "RARE": "rare",
        "UNLIKELY": "unlikely",
        "POSSIBLE": "possible",
        "LIKELY": "likely",
        "ALMOST_CERTAIN": "almost_certain",
    }


def test_str_enum_serializes_as_bare_string() -> None:
    assert RaidSeverity.CRITICAL == "critical"
    assert RaidStatus.OPEN == "open"
    assert (
        json.dumps({"s": RaidSeverity.CRITICAL.value, "st": RaidStatus.OPEN.value}) == '{"s": "critical", "st": "open"}'
    )


# ----------------------------------------------------------------------
# Risk score / band
# ----------------------------------------------------------------------


def test_risk_score_is_probability_times_severity(register: RaidRegister) -> None:
    cases = [
        (RaidProbability.RARE, RaidSeverity.LOW, 1),
        (RaidProbability.LIKELY, RaidSeverity.HIGH, 12),
        (RaidProbability.ALMOST_CERTAIN, RaidSeverity.BLOCKER, 25),
        (RaidProbability.POSSIBLE, RaidSeverity.MEDIUM, 6),
    ]
    for probability, severity, expected in cases:
        assert compute_risk_score(probability, severity) == expected
        record = _register_one(register, probability=probability, severity=severity)
        assert record.to_dict()["risk_score"] == expected


def test_risk_score_unknown_input_fail_closed() -> None:
    assert compute_risk_score(None, None) == 6
    assert compute_risk_score("bogus", RaidSeverity.HIGH) == 3 * 3
    assert compute_risk_score(RaidProbability.LIKELY, "bogus") == 4 * 2
    assert compute_risk_score(123, object()) == 6


def test_risk_band_thresholds() -> None:
    assert compute_risk_band(4) == "low"
    assert compute_risk_band(5) == "medium"
    assert compute_risk_band(12) == "medium"
    assert compute_risk_band(13) == "high"
    assert compute_risk_band(1) == "low"
    assert compute_risk_band(25) == "high"


# ----------------------------------------------------------------------
# Register / round-trip
# ----------------------------------------------------------------------


def test_register_returns_open_record_with_seeded_history(
    register: RaidRegister,
) -> None:
    record = _register_one(register, owner="role-x")
    assert record.status is RaidStatus.OPEN
    assert record.detected_at.endswith("Z")
    assert len(record.history) == 1
    assert record.history[0]["action"] == "registered"
    assert record.history[0]["actor"] == "role-x"


def test_entry_id_format_is_guard_clean(register: RaidRegister) -> None:
    record = _register_one(register, task_id="task1", category=RaidCategory.ISSUE)
    assert rr._SAFE_ID_FULLMATCH.match(record.entry_id)
    assert ".." not in record.entry_id
    assert record.entry_id.startswith("raid_issue_")
    # round-trips cleanly through the guard
    assert rr._validate_record_id(record.entry_id) == record.entry_id


def test_register_then_load_round_trip(register: RaidRegister) -> None:
    record = _register_one(
        register,
        task_id="task-42",
        category=RaidCategory.DEPENDENCY,
        title="external service availability",
        severity=RaidSeverity.HIGH,
        probability=RaidProbability.LIKELY,
        owner="role-y",
        detail="contingency plan text",
        links=("path/to/ref", "ADR-0001"),
        supersedes="raid_risk_old_abc123",
    )
    loaded = register.load(record.entry_id)
    assert loaded is not None
    assert loaded.entry_id == record.entry_id
    assert loaded.category is RaidCategory.DEPENDENCY
    assert loaded.task_id == "task-42"
    assert loaded.title == "external service availability"
    assert loaded.severity is RaidSeverity.HIGH
    assert loaded.probability is RaidProbability.LIKELY
    assert loaded.owner == "role-y"
    assert loaded.detail == "contingency plan text"
    assert loaded.status is RaidStatus.OPEN
    assert loaded.links == ("path/to/ref", "ADR-0001")
    assert loaded.supersedes == "raid_risk_old_abc123"
    assert loaded.history == record.history


# ----------------------------------------------------------------------
# Traversal guard
# ----------------------------------------------------------------------


def test_validate_record_id_rejects_traversal(register: RaidRegister) -> None:
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
            register.update_status(bad, RaidStatus.RESOLVED, actor="role")
    # no stray files created/read outside the register dir
    assert sorted(p.name for p in register._dir.glob("*.json")) == []


def test_validate_record_id_accepts_safe() -> None:
    safe = "raid_risk_task1_abc-123.def"
    assert rr._validate_record_id(safe) == safe


# ----------------------------------------------------------------------
# Lifecycle
# ----------------------------------------------------------------------


def test_update_status_appends_history_and_changes_status(
    register: RaidRegister,
) -> None:
    record = _register_one(register)
    detected_at = record.detected_at
    updated = register.update_status(record.entry_id, RaidStatus.MITIGATING, actor="role-z", note="started")
    assert updated.status is RaidStatus.MITIGATING
    assert len(updated.history) == 2
    assert updated.history[0]["action"] == "registered"
    assert updated.history[1]["action"] == "status:mitigating"
    assert updated.history[1]["note"] == "started"
    assert updated.detected_at == detected_at
    loaded = register.load(record.entry_id)
    assert loaded is not None
    assert loaded.status is RaidStatus.MITIGATING


def test_update_status_full_lifecycle_shared_ladder(register: RaidRegister) -> None:
    record = _register_one(register)
    ladder = [
        RaidStatus.MITIGATING,
        RaidStatus.ACCEPTED,
        RaidStatus.RESOLVED,
        RaidStatus.REVERTED,
    ]
    for index, status in enumerate(ladder, start=1):
        register.update_status(record.entry_id, status, actor="role")
        loaded = register.load(record.entry_id)
        assert loaded is not None
        assert loaded.status is status
        # history never shrinks: 1 seed + index transitions
        assert len(loaded.history) == 1 + index


def test_status_ladder_shared_across_all_categories(register: RaidRegister) -> None:
    for category in RaidCategory:
        record = _register_one(register, category=category)
        for status in (
            RaidStatus.MITIGATING,
            RaidStatus.ACCEPTED,
            RaidStatus.RESOLVED,
            RaidStatus.REVERTED,
        ):
            updated = register.update_status(record.entry_id, status, actor="role")
            assert updated.status is status


def test_update_status_missing_entry_raises_filenotfound(
    register: RaidRegister,
) -> None:
    with pytest.raises(FileNotFoundError):
        register.update_status("raid_risk_x_doesnotexist", RaidStatus.RESOLVED, actor="role")


def test_project_level_entry_allows_empty_task_id(register: RaidRegister) -> None:
    record = _register_one(register, task_id="")
    assert record.task_id == ""
    entries = register.list()
    assert record.entry_id in {e.entry_id for e in entries}


# ----------------------------------------------------------------------
# List / filters
# ----------------------------------------------------------------------


def test_list_no_filter_returns_all_sorted(register: RaidRegister) -> None:
    created = [_register_one(register, title=f"entry {i}") for i in range(5)]
    listed = register.list()
    assert len(listed) == 5
    ids = [e.entry_id for e in listed]
    assert ids == sorted(ids)
    assert {e.entry_id for e in created} == set(ids)


def test_list_filter_by_category(register: RaidRegister) -> None:
    _register_one(register, category=RaidCategory.RISK)
    _register_one(register, category=RaidCategory.ISSUE)
    issues = register.list(category=RaidCategory.ISSUE)
    assert len(issues) == 1
    assert issues[0].category is RaidCategory.ISSUE


def test_list_filter_by_severity(register: RaidRegister) -> None:
    _register_one(register, severity=RaidSeverity.LOW)
    _register_one(register, severity=RaidSeverity.BLOCKER)
    blockers = register.list(severity=RaidSeverity.BLOCKER)
    assert len(blockers) == 1
    assert blockers[0].severity is RaidSeverity.BLOCKER


def test_list_filter_by_status(register: RaidRegister) -> None:
    first = _register_one(register)
    _register_one(register)
    register.update_status(first.entry_id, RaidStatus.RESOLVED, actor="role")
    resolved = register.list(status=RaidStatus.RESOLVED)
    assert [e.entry_id for e in resolved] == [first.entry_id]


def test_list_filter_by_task_id(register: RaidRegister) -> None:
    _register_one(register, task_id="alpha")
    _register_one(register, task_id="beta")
    alpha = register.list(task_id="alpha")
    assert len(alpha) == 1
    assert alpha[0].task_id == "alpha"


def test_list_combined_filters_intersect(register: RaidRegister) -> None:
    target = _register_one(
        register,
        category=RaidCategory.RISK,
        severity=RaidSeverity.HIGH,
    )
    _register_one(register, category=RaidCategory.RISK, severity=RaidSeverity.LOW)
    _register_one(register, category=RaidCategory.ISSUE, severity=RaidSeverity.HIGH)
    matches = register.list(
        category=RaidCategory.RISK,
        severity=RaidSeverity.HIGH,
        status=RaidStatus.OPEN,
    )
    assert [e.entry_id for e in matches] == [target.entry_id]


# ----------------------------------------------------------------------
# Summarize
# ----------------------------------------------------------------------


def test_summarize_counts_by_category_severity_status(
    register: RaidRegister,
) -> None:
    _register_one(register, category=RaidCategory.RISK, severity=RaidSeverity.LOW)
    _register_one(register, category=RaidCategory.ISSUE, severity=RaidSeverity.HIGH)
    summary = register.summarize()
    assert summary["total"] == 2
    # every enum-member key present with zero defaults
    assert set(summary["by_category"]) == {c.value for c in RaidCategory}
    assert set(summary["by_severity"]) == {s.value for s in RaidSeverity}
    assert set(summary["by_status"]) == {s.value for s in RaidStatus}
    assert summary["by_category"]["risk"] == 1
    assert summary["by_category"]["issue"] == 1
    assert summary["by_category"]["dependency"] == 0
    assert summary["by_severity"]["low"] == 1
    assert summary["by_severity"]["high"] == 1
    assert summary["by_status"]["open"] == 2


def test_summarize_open_critical_or_blocker_tally(register: RaidRegister) -> None:
    open_crit = _register_one(register, severity=RaidSeverity.CRITICAL)
    _register_one(register, severity=RaidSeverity.BLOCKER)  # open blocker counts
    open_medium = _register_one(register, severity=RaidSeverity.MEDIUM)
    resolved_crit = _register_one(register, severity=RaidSeverity.CRITICAL)
    register.update_status(resolved_crit.entry_id, RaidStatus.RESOLVED, actor="role")
    mitigating_blocker = _register_one(register, severity=RaidSeverity.BLOCKER)
    register.update_status(mitigating_blocker.entry_id, RaidStatus.MITIGATING, actor="role")
    summary = register.summarize()
    # only open CRITICAL + open BLOCKER counted (2)
    assert summary["open_critical_or_blocker"] == 2
    assert open_crit.severity is RaidSeverity.CRITICAL
    assert open_medium.severity is RaidSeverity.MEDIUM


def test_summarize_scoped_by_task_or_category(register: RaidRegister) -> None:
    _register_one(register, task_id="alpha", category=RaidCategory.RISK)
    _register_one(register, task_id="alpha", category=RaidCategory.ISSUE)
    _register_one(register, task_id="beta", category=RaidCategory.RISK)
    by_task = register.summarize(task_id="alpha")
    assert by_task["total"] == 2
    by_category = register.summarize(category=RaidCategory.RISK)
    assert by_category["total"] == 2
    assert by_category["by_category"]["risk"] == 2


# ----------------------------------------------------------------------
# Fail-closed load
# ----------------------------------------------------------------------


def test_corrupt_json_skipped_on_list(register: RaidRegister) -> None:
    valid = _register_one(register)
    (register._dir / "raid_risk_corrupt_x.json").write_text("{ not json", encoding="utf-8")
    listed = register.list()
    assert [e.entry_id for e in listed] == [valid.entry_id]
    # summarize ignores the corrupt file too
    assert register.summarize()["total"] == 1


def test_corrupt_json_load_returns_none(register: RaidRegister) -> None:
    (register._dir / "raid_risk_corrupt_y.json").write_text("{ not json", encoding="utf-8")
    assert register.load("raid_risk_corrupt_y") is None


def test_non_dict_json_returns_none(register: RaidRegister) -> None:
    for index, payload in enumerate(("[]", '"str"', "42")):
        name = f"raid_risk_nondict_{index}"
        (register._dir / f"{name}.json").write_text(payload, encoding="utf-8")
        assert register.load(name) is None


def test_coercion_fail_closed_on_bad_enum(register: RaidRegister) -> None:
    name = "raid_risk_bogus_enum"
    (register._dir / f"{name}.json").write_text(
        json.dumps(
            {
                "entry_id": name,
                "title": "x",
                "detected_at": "2026-01-01T00:00:00Z",
                "category": "bogus",
                "severity": "bogus",
                "probability": "bogus",
                "status": "bogus",
            }
        ),
        encoding="utf-8",
    )
    loaded = register.load(name)
    assert loaded is not None
    assert loaded.category is RaidCategory.RISK
    assert loaded.severity is RaidSeverity.MEDIUM
    assert loaded.probability is RaidProbability.POSSIBLE
    assert loaded.status is RaidStatus.OPEN


def test_load_missing_partial_fields(register: RaidRegister) -> None:
    name = "raid_risk_empty_obj"
    (register._dir / f"{name}.json").write_text("{}", encoding="utf-8")
    loaded = register.load(name)
    assert loaded is not None
    assert loaded.entry_id == name
    assert loaded.task_id == ""
    assert loaded.title == ""
    assert loaded.category is RaidCategory.RISK
    assert loaded.severity is RaidSeverity.MEDIUM
    assert loaded.probability is RaidProbability.POSSIBLE
    assert loaded.status is RaidStatus.OPEN
    assert loaded.links == ()
    assert loaded.history == ()
    assert loaded.supersedes is None


# ----------------------------------------------------------------------
# Atomicity
# ----------------------------------------------------------------------


def test_atomic_write_no_tmp_left_behind(register: RaidRegister) -> None:
    record = _register_one(register)
    register.update_status(record.entry_id, RaidStatus.RESOLVED, actor="role")
    tmp_files = list(register._dir.glob("*.tmp"))
    assert tmp_files == []
    json_files = sorted(p.name for p in register._dir.glob("*.json"))
    assert json_files == [f"{record.entry_id}.json"]


def test_atomicity_no_half_written_record_visible(register: RaidRegister, monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(register._fs, "write_json_atomic", _boom)
    with pytest.raises(OSError):
        _register_one(register)
    # the final target was never created; list surfaces nothing
    assert register.list() == []
    assert sorted(p.name for p in register._dir.glob("*.json")) == []
    # a subsequent successful save makes a record appear atomically
    monkeypatch.undo()
    record = _register_one(register)
    assert [e.entry_id for e in register.list()] == [record.entry_id]


# ----------------------------------------------------------------------
# Serialization / utf-8 / determinism
# ----------------------------------------------------------------------


def test_to_dict_serializes_enums_and_derived_fields(register: RaidRegister) -> None:
    record = _register_one(
        register,
        category=RaidCategory.ASSUMPTION,
        severity=RaidSeverity.HIGH,
        probability=RaidProbability.LIKELY,
        links=("a", "b"),
        supersedes="raid_risk_prev_z",
    )
    payload = record.to_dict()
    assert payload["category"] == "assumption"
    assert payload["severity"] == "high"
    assert payload["probability"] == "likely"
    assert payload["status"] == "open"
    assert payload["links"] == ["a", "b"]
    assert isinstance(payload["history"], list)
    assert payload["supersedes"] == "raid_risk_prev_z"
    assert payload["risk_score"] == 12
    assert payload["risk_band"] == "medium"


def test_utf8_non_ascii_round_trip(register: RaidRegister) -> None:
    text = "记录-Ω-é"
    record = _register_one(register, title=text, detail=text, owner=text)
    loaded = register.load(record.entry_id)
    assert loaded is not None
    assert loaded.title == text
    assert loaded.detail == text
    assert loaded.owner == text
    raw = (register._dir / f"{record.entry_id}.json").read_text(encoding="utf-8")
    assert text in raw  # ensure_ascii=False keeps non-ASCII literal


def test_round_trip_byte_stable_with_patched_clock_and_nonce(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rr, "_utc_now", lambda: "2026-01-01T00:00:00Z")
    monkeypatch.setattr(rr, "_nonce", lambda: "deadbeef")
    first = RaidRegister(str(tmp_path / "a"))
    second = RaidRegister(str(tmp_path / "b"))
    rec_a = _register_one(first, task_id="task", category=RaidCategory.RISK)
    rec_b = _register_one(second, task_id="task", category=RaidCategory.RISK)
    assert rec_a.entry_id == rec_b.entry_id
    bytes_a = (first._dir / f"{rec_a.entry_id}.json").read_bytes()
    bytes_b = (second._dir / f"{rec_b.entry_id}.json").read_bytes()
    assert bytes_a == bytes_b


def test_list_and_summarize_ordering_stable(register: RaidRegister) -> None:
    for i in range(4):
        _register_one(register, title=f"e{i}")
    first_list = [e.entry_id for e in register.list()]
    second_list = [e.entry_id for e in register.list()]
    assert first_list == second_list
    assert register.summarize() == register.summarize()


def test_ensure_directory_false_does_not_create_dir(tmp_path: Path) -> None:
    register = RaidRegister(str(tmp_path), ensure_directory=False)
    assert not register._dir.exists()
    assert register.list() == []
    summary = register.summarize()
    assert summary["total"] == 0
    assert summary["open_critical_or_blocker"] == 0


def test_storage_root_is_runtime_pm_raid(tmp_path: Path) -> None:
    register = RaidRegister(str(tmp_path))
    root = register._dir
    assert root.is_absolute()
    assert root.as_posix().endswith("runtime/pm/raid")
    assert "runtime/risks" not in root.as_posix()
    record = _register_one(register)
    assert (root / f"{record.entry_id}.json").exists()


# ----------------------------------------------------------------------
# Event helper
# ----------------------------------------------------------------------


def test_build_raid_event_is_timestamped(register: RaidRegister) -> None:
    event = build_raid_event(
        entry_id="raid_risk_x_1",
        workspace="ws",
        action="registered",
        actor="role",
        note="n",
    )
    assert isinstance(event, RaidEventV1)
    assert event.at.endswith("Z")
    payload = event.to_dict()
    assert payload["entry_id"] == "raid_risk_x_1"
    assert payload["action"] == "registered"
    assert payload["note"] == "n"


# ----------------------------------------------------------------------
# Boundary + S8 invariants
# ----------------------------------------------------------------------


def _imported_module_names(path: Path) -> set[str]:
    """Return every module name referenced by an import statement in a file.

    Uses the AST so that a mention of a module name inside a docstring or
    comment is NOT counted -- only real ``import``/``from`` statements are.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_module_does_not_import_chief_engineer_or_delivery() -> None:
    # CE vocabulary token reconstructed dynamically so this assertion's own
    # source does not contain the forbidden import string verbatim.
    forbidden_ce = "chief" + "_engineer"
    forbidden_delivery = "polaris." + "delivery"
    imported = _imported_module_names(Path(rr.__file__))
    assert not any(forbidden_ce in module for module in imported)
    assert not any(module.startswith(forbidden_delivery) for module in imported)
    for enum_cls in (RaidCategory, RaidSeverity, RaidProbability, RaidStatus):
        assert issubclass(enum_cls, Enum)
        assert enum_cls.__module__ == rr.__name__


def test_test_file_does_not_import_chief_engineer() -> None:
    # this test module asserts CE parity via hardcoded maps, never an import
    forbidden_ce = "chief" + "_engineer"
    imported = _imported_module_names(Path(__file__))
    assert not any(forbidden_ce in module for module in imported)


def test_no_business_literals_s8() -> None:
    # Scan identifier-like tokens so substrings of ordinary English words
    # (e.g. "card" in no word here, "dag" in no word here) cannot false-trip;
    # we match the denylist token as a whole word against the module source.
    source = Path(rr.__file__).read_text(encoding="utf-8").lower()
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
    internal_dir = Path(rr.__file__).parent
    live_modules = [
        "pm_agent.py",
        "task_quality_gate.py",
        "pipeline_ports.py",
        "dependency_validator.py",
        "shared_quality.py",
    ]
    for name in live_modules:
        path = internal_dir / name
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "raid_register" not in source
        assert "RaidRegister" not in source
