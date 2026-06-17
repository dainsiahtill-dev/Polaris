"""Tests for the pure PM status-rollup core (``orchestration.pm_planning``).

The whole module is exercised with only plain dicts plus a real
:class:`Schedule` built from :func:`compute_schedule`; NO ``TaskBoard``,
``RaidRegister`` or CLI is ever constructed (purity guard). Boundary tests pin
the import whitelist, the clock-free / env-free invariant, the §8 "no business
literals" rule, and the unwired/floor-safe state.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import re
from pathlib import Path

import pytest
from polaris.cells.orchestration.pm_planning.internal import status_rollup as sr
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    Schedule,
    compute_schedule,
)
from polaris.cells.orchestration.pm_planning.internal.status_rollup import (
    PmStatusRollup,
    compute_status_rollup,
    render_status_markdown,
)

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _empty_raid() -> dict[str, object]:
    """A RAID summary shaped like ``RaidRegister.summarize()`` with zero items."""
    return {
        "total": 0,
        "by_category": {},
        "by_severity": {},
        "by_status": {},
        "open_critical_or_blocker": 0,
    }


def _empty_schedule() -> Schedule:
    return compute_schedule([])


def _linear_schedule() -> Schedule:
    """Three unit-weight tasks in a chain a -> b -> c (sum(weights) == 3.0)."""
    return compute_schedule(
        [
            {"id": "a"},
            {"id": "b", "depends_on": ["a"]},
            {"id": "c", "depends_on": ["b"]},
        ]
    )


# ----------------------------------------------------------------------
# percent_complete / partition
# ----------------------------------------------------------------------


def test_percent_complete_basic_mixed() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 2, "in_progress": 1, "ready": 2},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.total_tasks == 5
    assert rollup.completed_tasks == 2
    assert rollup.terminal_tasks == 2
    assert rollup.active_tasks == 3
    assert rollup.percent_complete == 0.4


def test_percent_complete_total_zero_is_zero_not_div_error() -> None:
    for counts in ({}, {"completed": 0}):
        rollup = compute_status_rollup(
            status_counts=counts,
            raid_summary=_empty_raid(),
            schedule=_empty_schedule(),
        )
        assert rollup.total_tasks == 0
        assert rollup.completed_tasks == 0
        assert rollup.percent_complete == 0.0
        assert rollup.active_tasks == 0
        assert rollup.terminal_tasks == 0
        assert rollup.rag == "green"


def test_percent_complete_all_completed_is_one() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 4},
        raid_summary=_empty_raid(),
        schedule=compute_schedule([{"id": f"t{i}"} for i in range(4)]),
        velocity=2.0,
    )
    assert rollup.percent_complete == 1.0
    assert rollup.rag == "green"
    assert rollup.remaining_effort == 0.0
    assert rollup.eta_iterations == 0.0


def test_terminal_vs_active_partition() -> None:
    rollup = compute_status_rollup(
        status_counts={
            "completed": 1,
            "failed": 1,
            "cancelled": 1,
            "timeout": 1,
            "in_progress": 2,
            "blocked": 1,
        },
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.total_tasks == 7
    assert rollup.terminal_tasks == 4
    assert rollup.completed_tasks == 1
    assert rollup.active_tasks == 3
    assert rollup.percent_complete == round(1 / 7, 4)


def test_completed_excludes_other_terminal() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "failed": 1, "cancelled": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.completed_tasks == 1
    assert rollup.terminal_tasks == 3
    assert rollup.percent_complete == round(1 / 3, 4)


def test_unknown_status_key_counts_toward_total_only() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "frobnicate": 2},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.total_tasks == 3
    assert rollup.completed_tasks == 1
    assert rollup.terminal_tasks == 1
    assert rollup.active_tasks == 2


def test_negative_and_nonint_counts_clamped() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": -3, "ready": "x", "blocked": None},  # type: ignore[dict-item]
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.total_tasks == 0
    assert rollup.percent_complete == 0.0


def test_bool_counts_do_not_inflate() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": True, "ready": False},  # type: ignore[dict-item]
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.total_tasks == 0
    assert rollup.completed_tasks == 0


# ----------------------------------------------------------------------
# RAG band
# ----------------------------------------------------------------------


def test_rag_red_when_open_critical_or_blocker_positive() -> None:
    raid = _empty_raid()
    raid["open_critical_or_blocker"] = 1
    rollup = compute_status_rollup(
        status_counts={"completed": 9, "ready": 1},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.percent_complete == 0.9
    assert rollup.rag == "red"


def test_rag_red_overrides_completion_overlay() -> None:
    raid = _empty_raid()
    raid["open_critical_or_blocker"] = 1
    rollup = compute_status_rollup(
        status_counts={"completed": 4},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.percent_complete == 1.0
    assert rollup.rag == "red"


def test_rag_red_threshold_boundary() -> None:
    base = {"status_counts": {"ready": 2}, "schedule": _empty_schedule()}
    raid0 = _empty_raid()
    raid0["open_critical_or_blocker"] = 0
    assert compute_status_rollup(raid_summary=raid0, **base).rag != "red"  # type: ignore[arg-type]
    raid1 = _empty_raid()
    raid1["open_critical_or_blocker"] = 1
    assert compute_status_rollup(raid_summary=raid1, **base).rag == "red"  # type: ignore[arg-type]


def test_rag_green_completion_overlay() -> None:
    raid = _empty_raid()
    raid["by_status"] = {"open": 2}
    raid["by_severity"] = {"high": 0}
    rollup = compute_status_rollup(
        status_counts={"completed": 3},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.percent_complete == 1.0
    assert rollup.open_risks == 2
    assert rollup.rag == "green"


def test_rag_amber_high_severity_only() -> None:
    raid = _empty_raid()
    raid["by_severity"] = {"high": 1}
    rollup = compute_status_rollup(
        status_counts={"completed": 6, "ready": 4},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.percent_complete == 0.6
    assert rollup.rag == "amber"


def test_rag_amber_high_threshold_boundary() -> None:
    base_counts = {"completed": 6, "ready": 4}  # 0.6, above the floor
    raid0 = _empty_raid()
    raid0["by_severity"] = {"high": 0}
    assert (
        compute_status_rollup(
            status_counts=base_counts,
            raid_summary=raid0,
            schedule=_empty_schedule(),
        ).rag
        == "green"
    )
    raid1 = _empty_raid()
    raid1["by_severity"] = {"high": 1}
    assert (
        compute_status_rollup(
            status_counts=base_counts,
            raid_summary=raid1,
            schedule=_empty_schedule(),
        ).rag
        == "amber"
    )


def test_rag_amber_materially_behind() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "in_progress": 9},  # 0.1 < 0.5
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),  # makespan > 0
    )
    assert rollup.makespan > 0.0
    assert rollup.active_tasks > 0
    assert rollup.percent_complete < 0.5
    assert rollup.rag == "amber"


def test_rag_green_when_clean() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 6, "in_progress": 4},  # 0.6 >= floor
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
    )
    assert rollup.rag == "green"


def test_rag_behind_needs_makespan() -> None:
    # percent < 0.5 and active work, but empty schedule (makespan == 0.0).
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "in_progress": 9},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert rollup.makespan == 0.0
    assert rollup.percent_complete < 0.5
    assert rollup.rag == "green"


# ----------------------------------------------------------------------
# ETA / remaining effort
# ----------------------------------------------------------------------


def test_eta_with_positive_velocity() -> None:
    schedule = _linear_schedule()  # sum(weights) == 3.0
    rollup = compute_status_rollup(
        status_counts={"in_progress": 3},  # nothing completed -> fraction 0
        raid_summary=_empty_raid(),
        schedule=schedule,
        velocity=2.0,
    )
    assert rollup.remaining_effort == 3.0
    assert rollup.eta_iterations == round(3.0 / 2.0, 4)


def test_remaining_effort_aggregate_scaling() -> None:
    schedule = _linear_schedule()  # W == 3.0
    half = compute_status_rollup(
        status_counts={"completed": 1, "in_progress": 1},  # fraction 0.5
        raid_summary=_empty_raid(),
        schedule=schedule,
    )
    assert half.remaining_effort == round(3.0 * 0.5, 4)
    full = compute_status_rollup(
        status_counts={"completed": 2},
        raid_summary=_empty_raid(),
        schedule=schedule,
    )
    assert full.remaining_effort == 0.0
    none_done = compute_status_rollup(
        status_counts={"in_progress": 2},
        raid_summary=_empty_raid(),
        schedule=schedule,
    )
    assert none_done.remaining_effort == round(3.0, 4)


def test_remaining_effort_exact_with_completed_ids() -> None:
    schedule = _linear_schedule()  # weights a/b/c each 1.0
    rollup = compute_status_rollup(
        status_counts={"completed": 99, "in_progress": 1},  # aggregate would differ
        raid_summary=_empty_raid(),
        schedule=schedule,
        completed_ids=["a", "absent_id"],  # absent id tolerated (contributes 0)
    )
    assert rollup.remaining_effort == round(2.0, 4)


def test_eta_velocity_none_is_none() -> None:
    rollup = compute_status_rollup(
        status_counts={"in_progress": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=None,
    )
    assert rollup.eta_iterations is None


def test_eta_velocity_zero_is_none() -> None:
    rollup = compute_status_rollup(
        status_counts={"in_progress": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=0,
    )
    assert rollup.eta_iterations is None


def test_eta_velocity_negative_is_none() -> None:
    rollup = compute_status_rollup(
        status_counts={"in_progress": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=-1.0,
    )
    assert rollup.eta_iterations is None


def test_eta_velocity_nan_inf_is_none() -> None:
    for bad in (float("nan"), float("inf"), float("-inf")):
        rollup = compute_status_rollup(
            status_counts={"in_progress": 1},
            raid_summary=_empty_raid(),
            schedule=_linear_schedule(),
            velocity=bad,
        )
        assert rollup.eta_iterations is None


def test_eta_zero_remaining_effort() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 3},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=3.0,
    )
    assert rollup.remaining_effort == 0.0
    assert rollup.eta_iterations == 0.0


# ----------------------------------------------------------------------
# Schedule passthrough (§7 reuse)
# ----------------------------------------------------------------------


def test_makespan_and_critical_path_surfaced_from_schedule() -> None:
    schedule = compute_schedule(
        [
            {"id": "a", "estimated_effort": 2},
            {"id": "b", "depends_on": ["a"], "estimated_effort": 3},
            {"id": "c"},
        ]
    )
    rollup = compute_status_rollup(
        status_counts={"in_progress": 3},
        raid_summary=_empty_raid(),
        schedule=schedule,
    )
    assert rollup.makespan == schedule.makespan
    assert rollup.critical_path == schedule.critical_path


def test_empty_schedule_smoke() -> None:
    schedule = _empty_schedule()
    assert schedule.makespan == 0.0
    assert schedule.critical_path == ()
    rollup = compute_status_rollup(
        status_counts={},
        raid_summary=_empty_raid(),
        schedule=schedule,
    )
    assert rollup.percent_complete == 0.0
    assert rollup.remaining_effort == 0.0
    assert rollup.eta_iterations is None
    assert rollup.rag == "green"


# ----------------------------------------------------------------------
# RAID summary passthrough
# ----------------------------------------------------------------------


def test_open_risks_from_by_status() -> None:
    raid = _empty_raid()
    raid["by_status"] = {"open": 4, "resolved": 2}
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.open_risks == 4

    raid_missing = _empty_raid()
    raid_missing.pop("by_status")
    rollup_missing = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=raid_missing,
        schedule=_empty_schedule(),
    )
    assert rollup_missing.open_risks == 0


def test_open_critical_or_blocker_passthrough() -> None:
    raid = _empty_raid()
    raid["open_critical_or_blocker"] = 3
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.open_critical_or_blocker == 3

    raid_absent = _empty_raid()
    raid_absent.pop("open_critical_or_blocker")
    assert (
        compute_status_rollup(
            status_counts={"ready": 1},
            raid_summary=raid_absent,
            schedule=_empty_schedule(),
        ).open_critical_or_blocker
        == 0
    )


def test_raid_summary_missing_keys_defaults_zero() -> None:
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary={},
        schedule=_empty_schedule(),
    )
    assert rollup.open_risks == 0
    assert rollup.open_critical_or_blocker == 0
    assert rollup.rag == "green"


# ----------------------------------------------------------------------
# Requirements coverage
# ----------------------------------------------------------------------


def test_requirements_coverage_present_ratio() -> None:
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
        requirements_total=10,
        requirements_covered=7,
    )
    assert rollup.requirements_total == 10
    assert rollup.requirements_covered == 7
    assert rollup.requirements_coverage == 0.7


def test_requirements_coverage_from_ratio_input() -> None:
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
        requirements_total=8,
        requirements_covered=None,
        requirements_coverage=0.5,
    )
    assert rollup.requirements_covered == 4
    assert rollup.requirements_coverage == 0.5


def test_requirements_coverage_absent_is_none() -> None:
    for total in (None, 0):
        rollup = compute_status_rollup(
            status_counts={"ready": 1},
            raid_summary=_empty_raid(),
            schedule=_empty_schedule(),
            requirements_total=total,
        )
        assert rollup.requirements_coverage is None
        assert rollup.requirements_covered == 0
        assert rollup.requirements_total == 0


def test_requirements_covered_without_total_is_none() -> None:
    for total in (None, 0):
        rollup = compute_status_rollup(
            status_counts={"ready": 1},
            raid_summary=_empty_raid(),
            schedule=_empty_schedule(),
            requirements_total=total,
            requirements_covered=5,
        )
        assert rollup.requirements_coverage is None
        assert rollup.requirements_covered == 0


# ----------------------------------------------------------------------
# generated_at / to_dict / frozen
# ----------------------------------------------------------------------


def test_generated_at_default_empty_keeps_core_clockfree() -> None:
    default = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert default.generated_at == ""
    stamped = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
        generated_at="2026-06-17T00:00:00Z",
    )
    assert stamped.generated_at == "2026-06-17T00:00:00Z"


def test_to_dict_roundtrip() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "ready": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
    )
    payload = rollup.to_dict()
    expected_fields = {f.name for f in dataclasses.fields(PmStatusRollup)}
    assert set(payload) == expected_fields
    assert isinstance(payload["critical_path"], list)
    assert payload["eta_iterations"] is None
    assert payload["requirements_coverage"] is None
    # JSON-serializable without raising.
    assert json.loads(json.dumps(payload)) == payload


def test_dataclass_is_frozen() -> None:
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        rollup.rag = "red"  # type: ignore[misc]


# ----------------------------------------------------------------------
# Renderer
# ----------------------------------------------------------------------


def test_render_contains_percent_line() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "ready": 4},  # 0.2
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert "20.0%" in render_status_markdown(rollup)


def test_render_contains_rag_token() -> None:
    raid = _empty_raid()
    raid["open_critical_or_blocker"] = 1
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    assert rollup.rag == "red"
    assert "RED" in render_status_markdown(rollup)


def test_render_eta_unknown_and_value() -> None:
    unknown = compute_status_rollup(
        status_counts={"in_progress": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=None,
    )
    assert "ETA: unknown" in render_status_markdown(unknown)

    known = compute_status_rollup(
        status_counts={"in_progress": 3},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=2.0,
    )
    text = render_status_markdown(known)
    assert "ETA: unknown" not in text
    assert f"{known.eta_iterations:g}" in text


def test_render_critical_path_and_na() -> None:
    present = compute_status_rollup(
        status_counts={"in_progress": 3},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
    )
    rendered = render_status_markdown(present)
    assert " -> ".join(present.critical_path) in rendered

    absent = compute_status_rollup(
        status_counts={},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert "Critical path: n/a" in render_status_markdown(absent)


def test_render_open_raid_line() -> None:
    raid = _empty_raid()
    raid["by_status"] = {"open": 5}
    raid["open_critical_or_blocker"] = 2
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=raid,
        schedule=_empty_schedule(),
    )
    rendered = render_status_markdown(rollup)
    assert "5 open" in rendered
    assert "2 critical/blocker" in rendered


def test_render_requirements_na_when_none() -> None:
    none_rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    assert "n/a" in render_status_markdown(none_rollup)

    covered = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
        requirements_total=4,
        requirements_covered=2,
    )
    assert "50.0%" in render_status_markdown(covered)


def test_render_is_byte_stable_deterministic() -> None:
    rollup = compute_status_rollup(
        status_counts={"completed": 1, "ready": 1},
        raid_summary=_empty_raid(),
        schedule=_linear_schedule(),
        velocity=1.5,
        requirements_total=3,
        requirements_covered=1,
        generated_at="2026-06-17T00:00:00Z",
    )
    assert render_status_markdown(rollup) == render_status_markdown(rollup)


def test_render_no_clock_token() -> None:
    rollup = compute_status_rollup(
        status_counts={"ready": 1},
        raid_summary=_empty_raid(),
        schedule=_empty_schedule(),
    )
    rendered = render_status_markdown(rollup)
    # With no caller stamp, nothing in the output should look like a date.
    assert not re.search(r"\d{4}-\d{2}-\d{2}", rendered)


def test_compute_is_deterministic() -> None:
    kwargs = {
        "status_counts": {"completed": 2, "in_progress": 1},
        "raid_summary": _empty_raid(),
        "schedule": _linear_schedule(),
        "velocity": 2.0,
        "requirements_total": 5,
        "requirements_covered": 3,
    }
    first = compute_status_rollup(**kwargs)  # type: ignore[arg-type]
    second = compute_status_rollup(**kwargs)  # type: ignore[arg-type]
    assert first == second


# ----------------------------------------------------------------------
# Boundary + §8 invariants
# ----------------------------------------------------------------------


def _imported_module_names(path: Path) -> set[str]:
    """Return every module referenced by a real import statement (AST-based)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_module_does_not_import_delivery_or_other_cells() -> None:
    # Forbidden strings reconstructed dynamically so this test source stays clean.
    forbidden_delivery = "polaris." + "delivery"
    same_cell_internal = "orchestration." + "pm_planning.internal"
    imported = _imported_module_names(Path(sr.__file__))
    assert not any(module.startswith(forbidden_delivery) for module in imported)
    for module in imported:
        if module.startswith("polaris."):
            assert same_cell_internal in module, f"unexpected polaris import: {module}"


def test_module_no_os_environ_or_clock() -> None:
    source = Path(sr.__file__).read_text(encoding="utf-8")
    for token in ("os." + "environ", "getenv", "datetime", "time.time"):
        assert token not in source, f"forbidden effect token present: {token!r}"


def test_no_business_literals_s8() -> None:
    source = Path(sr.__file__).read_text(encoding="utf-8").lower()
    tokens = set(re.findall(r"[a-z0-9]+", source))
    denylist = (
        "game",
        "card3d",
        "card",
        "express",
        "django",
        "react",
        "tenant",
        "tetris",
        "tank",
    )
    for token in denylist:
        assert token not in tokens, f"S8 violation: business literal {token!r}"


def test_no_live_path_references_status_rollup() -> None:
    internal_dir = Path(sr.__file__).parent
    cell_dir = internal_dir.parent
    live_paths = [
        internal_dir / "pm_agent.py",
        internal_dir / "pipeline_ports.py",
        internal_dir / "task_quality_gate.py",
        internal_dir / "shared_quality.py",
        cell_dir / "public" / "contracts.py",
        cell_dir / "service.py",
    ]
    for path in live_paths:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "status_rollup" not in source
        assert "PmStatusRollup" not in source


def test_purity_no_taskboard_or_register_constructed() -> None:
    # The whole rollup is exercised with only plain dicts + a Schedule; assert
    # the module imports neither TaskBoard nor RaidRegister.
    imported = _imported_module_names(Path(sr.__file__))
    assert not any("task_board" in module or "task_runtime" in module for module in imported)
    assert not any("raid_register" in module for module in imported)
    # math is the only effect-free third-party-of-stdlib helper expected.
    assert math.isfinite(1.0)
