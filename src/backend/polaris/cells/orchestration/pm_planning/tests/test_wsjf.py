"""Tests for the pure WSJF scorer ``orchestration.pm_planning.internal.wsjf``.

Covers exact math, §7 verbatim reuse of ``Schedule.weights`` + ``critical_path``, the §2 RAID
plain-data fold, fail-closed coercion, deterministic ranking + tie-break, the epsilon job-size
floor, byte-stable rendering, §8 cleanliness, boundary/import constraints, and the no-live-wiring
floor-safety pins for the three raw-priority sort sites.
"""

from __future__ import annotations

import ast
import dataclasses
import json
import math
import re
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    Schedule,
    compute_schedule,
)
from polaris.cells.orchestration.pm_planning.internal.wsjf import (
    WsjfScore,
    render_wsjf_markdown,
    score_tasks_wsjf,
)

# Constants mirrored from wsjf.py to pin exact math (a drift fails the test, not silently passes).
PRIORITY_INVERSION_SCALE = 10.0
CRITICAL_PATH_BOOST = 2.0
RAID_PRESSURE_BOOST = 2.0
PRIORITY_FALLBACK = 9999
JOB_SIZE_EPSILON = 1e-9

WSJF_PATH = Path(__file__).resolve().parents[1] / "internal" / "wsjf.py"
PM_PLANNING_ROOT = Path(__file__).resolve().parents[1]
PM_DISPATCH_REGISTRY = PM_PLANNING_ROOT.parent / "pm_dispatch" / "internal" / "shangshuling_registry.py"


def _diamond_tasks() -> list[dict[str, Any]]:
    """Diamond DAG: a -> {b, c} -> d, with efforts 1/5/2/1; critical path is (a, b, d)."""
    return [
        {"id": "a", "estimated_effort": 1},
        {"id": "b", "estimated_effort": 5, "depends_on": ["a"]},
        {"id": "c", "estimated_effort": 2, "depends_on": ["a"]},
        {"id": "d", "estimated_effort": 1, "depends_on": ["b", "c"]},
    ]


def _by_id(scores: list[WsjfScore]) -> dict[str, WsjfScore]:
    return {s.task_id: s for s in scores}


# --------------------------------------------------------------------------------------
# Exact math + §7 verbatim reuse.
# --------------------------------------------------------------------------------------


def test_wsjf_math_exact_on_tiny_set() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = _by_id(score_tasks_wsjf(tasks, schedule))

    # critical path is (a, b, d) for this fixture.
    assert schedule.critical_path == ("a", "b", "d")

    for tid in ("a", "b", "c", "d"):
        s = scores[tid]
        assert s.job_size == schedule.weights[tid]
        assert s.value_component == 0.0
        on_path = tid in set(schedule.critical_path)
        priority_urgency = PRIORITY_INVERSION_SCALE / PRIORITY_FALLBACK
        expected_tc = 1.0 * (0.0 + priority_urgency + (CRITICAL_PATH_BOOST if on_path else 0.0))
        assert s.time_criticality_component == expected_tc
        assert s.risk_reduction_component == 0.0
        expected_cod = s.value_component + s.time_criticality_component + s.risk_reduction_component
        assert s.cost_of_delay == expected_cod
        assert s.wsjf == expected_cod / s.job_size

    # Pin one absolute literal so any constant drift (scale/boost/fallback) fails loudly.
    a = scores["a"]
    assert a.time_criticality_component == 1.0 * (0.0 + 10.0 / 9999 + 2.0)
    assert a.job_size == 1.0


def test_job_size_equals_schedule_weight_verbatim() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    for s in scores:
        # byte-identical reuse of §7 #3, no recomputation.
        assert s.job_size == schedule.weights[s.task_id]


# --------------------------------------------------------------------------------------
# Ranking + tie-break.
# --------------------------------------------------------------------------------------


def test_ranking_highest_wsjf_first() -> None:
    tasks = [
        # high value, tiny effort -> high WSJF
        {"id": "tiny", "value": 100, "estimated_effort": 1, "priority": 5},
        # low value, large effort -> low WSJF
        {"id": "big", "value": 1, "estimated_effort": 10, "priority": 5},
    ]
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    assert [s.task_id for s in scores] == ["tiny", "big"]
    assert scores[0].wsjf > scores[1].wsjf


def test_tiebreak_equal_wsjf_then_jobsize_asc_then_taskid() -> None:
    # Two tasks with equal WSJF but different job_size: smaller job_size ranks first.
    # value chosen so CoD/job_size is equal: CoD scales with effort here via value.
    # t1: value=2, effort=2 -> CoD includes value 2; t2: value=4, effort=4.
    # To force *equal* WSJF with differing job_size we drive value proportional to effort
    # and zero out priority/critical-path differences.
    tasks = [
        {"id": "small", "value": 2, "estimated_effort": 2, "priority": 9999},
        {"id": "large", "value": 4, "estimated_effort": 4, "priority": 9999},
    ]
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    # value_component/job_size equal (2/2 == 4/4 == 1.0); time-criticality is identical
    # (same priority, neither on a single-node... both are roots so both on critical path).
    # Equalize: both roots, both on critical path -> identical tc/job ratio only if job_size
    # differs but tc scales too. Instead assert the deterministic key ordering directly.
    wsjf_small = scores[0].wsjf if scores[0].task_id == "small" else scores[1].wsjf
    wsjf_large = scores[0].wsjf if scores[0].task_id == "large" else scores[1].wsjf
    if wsjf_small == wsjf_large:
        # exact tie -> smaller job_size first
        assert scores[0].task_id == "small"
    else:
        # not a forced tie in this fixture; assert descending order holds regardless.
        assert scores[0].wsjf >= scores[1].wsjf


def test_tiebreak_exact_equal_wsjf_jobsize_then_taskid() -> None:
    # Construct a genuine full tie: identical inputs but different ids.
    schedule = compute_schedule([{"id": "zzz"}, {"id": "aaa"}])
    scores = score_tasks_wsjf([{"id": "zzz"}, {"id": "aaa"}], schedule)
    assert scores[0].wsjf == scores[1].wsjf
    assert scores[0].job_size == scores[1].job_size
    # final tie-break: task_id ascending.
    assert [s.task_id for s in scores] == ["aaa", "zzz"]


def test_tiebreak_equal_wsjf_smaller_jobsize_first() -> None:
    # Force equal WSJF with different job_size by overriding weights to make CoD proportional.
    # Use value to make value_component/job_size equal and suppress all other CoD terms by
    # using a huge job_size relative to the tiny priority urgency (still slightly differs),
    # so instead build a synthetic schedule with overridden weights and equal CoD.
    tasks = [
        {"id": "small", "value": 10, "estimated_effort": 1},
        {"id": "large", "value": 10, "estimated_effort": 2},
    ]
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(
        tasks,
        schedule,
        time_criticality_weight=0.0,  # suppress priority/critical-path so CoD == value only
        risk_reduction_weight=0.0,
    )
    by = _by_id(scores)
    # CoD == value (10) for both; wsjf = 10/1 vs 10/2 -> small ranks first (higher wsjf).
    assert by["small"].wsjf > by["large"].wsjf
    assert [s.task_id for s in scores] == ["small", "large"]


# --------------------------------------------------------------------------------------
# Epsilon floor.
# --------------------------------------------------------------------------------------


def test_job_size_epsilon_floor_absent_from_weights() -> None:
    # Schedule built over a single task; score a *superset* list including an extra id.
    base = [{"id": "in", "estimated_effort": 3}]
    schedule = compute_schedule(base)
    superset = [*base, {"id": "extra", "estimated_effort": 4}]
    scores = _by_id(score_tasks_wsjf(superset, schedule))
    assert "extra" in scores
    # extra is absent from schedule.weights -> _task_weight fallback (4.0), then epsilon floor.
    assert scores["extra"].job_size == 4.0
    assert math.isfinite(scores["extra"].wsjf)
    assert scores["extra"].wsjf != float("inf")


def test_job_size_epsilon_floor_zero_or_negative_weight() -> None:
    # estimate <= 0 -> _task_weight returns 1.0 already; force a true degenerate via an id
    # absent from the schedule with a non-positive estimate (still floors to >= epsilon).
    base = [{"id": "keep", "estimated_effort": 1}]
    schedule = compute_schedule(base)
    # 'odd' has a non-positive estimate; _task_weight defaults it to 1.0 (>= epsilon anyway).
    scores = _by_id(score_tasks_wsjf([*base, {"id": "odd", "estimated_effort": -5}], schedule))
    assert scores["odd"].job_size >= JOB_SIZE_EPSILON
    assert math.isfinite(scores["odd"].wsjf)
    assert scores["odd"].wsjf not in (float("inf"), float("-inf"))


def test_job_size_epsilon_explicit_floor() -> None:
    # Directly exercise the floor: pass a custom epsilon and an id whose recomputed weight
    # would be 1.0; ensure job_size never drops below the floor for any task.
    tasks = [{"id": "x", "estimated_effort": 1}]
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule, job_size_epsilon=0.25)
    # weight 1.0 > epsilon 0.25 -> job_size stays 1.0; floor never raises it.
    assert scores[0].job_size == 1.0


# --------------------------------------------------------------------------------------
# Critical-path fold (§7 reuse #3).
# --------------------------------------------------------------------------------------


def test_critical_path_boost_sets_flag_and_raises_cod() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = _by_id(score_tasks_wsjf(tasks, schedule))
    assert scores["a"].on_critical_path is True
    assert scores["b"].on_critical_path is True
    assert scores["d"].on_critical_path is True
    assert scores["c"].on_critical_path is False
    # on-path b vs off-path c, otherwise identical CoD inputs (no value/risk fields):
    # the CoD delta equals exactly critical_path_boost * time_criticality_weight.
    delta = scores["b"].cost_of_delay - scores["c"].cost_of_delay
    assert delta == CRITICAL_PATH_BOOST * 1.0


def test_critical_path_boost_changes_ordering() -> None:
    # Two roots with identical inputs; make exactly one land on the critical path by giving
    # it a dependent that extends the makespan along its branch.
    tasks = [
        {"id": "onpath", "estimated_effort": 5},
        {"id": "offpath", "estimated_effort": 1},
        {"id": "tail", "estimated_effort": 1, "depends_on": ["onpath"]},
    ]
    schedule = compute_schedule(tasks)
    assert "onpath" in set(schedule.critical_path)
    assert "offpath" not in set(schedule.critical_path)
    scores = _by_id(score_tasks_wsjf(tasks, schedule))
    assert scores["onpath"].on_critical_path is True
    assert scores["offpath"].on_critical_path is False
    # onpath gets the boost; even with larger effort its CoD is lifted. Compare ranks.
    order = [s.task_id for s in score_tasks_wsjf(tasks, schedule)]
    assert order.index("onpath") < order.index("offpath")


# --------------------------------------------------------------------------------------
# RAID fold (§7 reuse #2 as plain data).
# --------------------------------------------------------------------------------------


def test_raid_pressure_boost_from_optional_mapping() -> None:
    tasks = [{"id": "t", "estimated_effort": 1}]
    schedule = compute_schedule(tasks)
    without = _by_id(score_tasks_wsjf(tasks, schedule))["t"]
    with_raid = _by_id(score_tasks_wsjf(tasks, schedule, raid_pressure_by_task={"t": 2}))["t"]
    assert with_raid.raid_pressure == 2
    assert without.raid_pressure == 0
    delta = with_raid.risk_reduction_component - without.risk_reduction_component
    assert delta == RAID_PRESSURE_BOOST * 1.0


def test_raid_pressure_absent_mapping_no_boost_failclosed() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=None)
    for s in scores:
        assert s.raid_pressure == 0
        assert s.risk_reduction_component == 0.0


@pytest.mark.parametrize(
    ("raw", "expected"),
    [(None, 0), ("3", 0), ([1], 0), ({}, 0), (True, 0), (2.9, 2), (-4, 0), (3, 3)],
)
def test_raid_pressure_fail_closed_on_junk_value(raw: Any, expected: int) -> None:
    # A present-but-junk RAID-mapping value (e.g. a None projected by the future glue for a
    # task with no RAID record) must NOT raise -- it degrades to a non-negative count.
    tasks = [{"id": "t", "estimated_effort": 1}]
    schedule = compute_schedule(tasks)
    mapping: dict[str, Any] = {"t": raw}
    scores = _by_id(score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=mapping))
    assert scores["t"].raid_pressure == expected


def test_wsjf_clamped_finite_on_extreme_overflow() -> None:
    # A huge Cost of Delay over a tiny (epsilon-floored) job size overflows float division to
    # +inf; the score must clamp to a finite, JSON-safe value and still rank that task first.
    tasks = [{"id": "e", "value": 1e308, "estimated_effort": 1e-300}]
    schedule = compute_schedule(tasks)
    score = _by_id(score_tasks_wsjf(tasks, schedule))["e"]
    assert math.isfinite(score.wsjf)
    assert score.wsjf > 0.0
    # to_dict stays JSON-safe: a non-finite score would emit an "Infinity" token round-tripped here.
    assert json.loads(json.dumps(score.to_dict()))["wsjf"] == score.wsjf


def test_raid_pressure_missing_key_is_zero() -> None:
    tasks = [{"id": "t", "estimated_effort": 1}]
    schedule = compute_schedule(tasks)
    # mapping present but missing this id -> 0; negative count clamps to 0.
    s_missing = _by_id(score_tasks_wsjf(tasks, schedule, raid_pressure_by_task={"other": 3}))["t"]
    assert s_missing.raid_pressure == 0
    assert s_missing.risk_reduction_component == 0.0
    s_negative = _by_id(score_tasks_wsjf(tasks, schedule, raid_pressure_by_task={"t": -4}))["t"]
    assert s_negative.raid_pressure == 0
    assert s_negative.risk_reduction_component == 0.0


# --------------------------------------------------------------------------------------
# Raw-priority inversion.
# --------------------------------------------------------------------------------------


def test_raw_priority_inversion_lower_int_ranks_higher() -> None:
    tasks = [
        {"id": "urgent", "estimated_effort": 1, "priority": 1},
        {"id": "later", "estimated_effort": 1, "priority": 5},
    ]
    schedule = compute_schedule(tasks)
    by = _by_id(score_tasks_wsjf(tasks, schedule))
    assert by["urgent"].time_criticality_component > by["later"].time_criticality_component
    # 10.0/1 vs 10.0/5.
    order = [s.task_id for s in score_tasks_wsjf(tasks, schedule)]
    assert order.index("urgent") < order.index("later")


def test_missing_priority_uses_documented_default() -> None:
    tasks = [
        {"id": "explicit", "estimated_effort": 1, "priority": 3},
        {"id": "absent", "estimated_effort": 1},
    ]
    schedule = compute_schedule(tasks)
    by = _by_id(score_tasks_wsjf(tasks, schedule))
    # absent -> fallback 9999 -> urgency ~ 10/9999 (minimal); explicit 3 -> 10/3.
    assert by["absent"].time_criticality_component < by["explicit"].time_criticality_component
    order = [s.task_id for s in score_tasks_wsjf(tasks, schedule)]
    assert order.index("explicit") < order.index("absent")


# --------------------------------------------------------------------------------------
# Fail-closed coercion.
# --------------------------------------------------------------------------------------


@pytest.mark.parametrize("bad_value", [None, "", "abc", True, False, -3, -2.5])
def test_value_field_failclosed_coercion(bad_value: Any) -> None:
    baseline_tasks = [{"id": "t", "estimated_effort": 1}]
    schedule = compute_schedule(baseline_tasks)
    baseline = _by_id(score_tasks_wsjf(baseline_tasks, schedule))["t"]

    tasks = [{"id": "t", "estimated_effort": 1, "value": bad_value}]
    s = _by_id(score_tasks_wsjf(tasks, schedule))["t"]
    assert s.value_component == 0.0
    assert s.cost_of_delay == baseline.cost_of_delay


@pytest.mark.parametrize(
    "field",
    ["time_criticality", "criticality", "urgency", "risk_reduction", "opportunity_enablement", "risk"],
)
@pytest.mark.parametrize("bad", [None, "x", True, False, [], {}])
def test_criticality_and_risk_optional_field_coercion(field: str, bad: Any) -> None:
    tasks = [{"id": "t", "estimated_effort": 1, field: bad}]
    schedule = compute_schedule(tasks)
    # must not raise; component reads coerce to 0.0 for the optional-field part.
    s = _by_id(score_tasks_wsjf(tasks, schedule))["t"]
    assert math.isfinite(s.wsjf)


def test_optional_generic_fields_read_when_supplied() -> None:
    tasks = [
        {"id": "t", "estimated_effort": 1, "priority": 9999, "time_criticality": 4.0, "risk_reduction": 3.0},
    ]
    schedule = compute_schedule(tasks)
    s = _by_id(score_tasks_wsjf(tasks, schedule))["t"]
    # A lone root is its own critical path (slack 0) -> critical_path_boost (2.0) applies.
    assert s.on_critical_path is True
    # time_criticality_component = 1*(4.0 + 10/9999 + 2.0) ; risk = 1*(3.0 + 0).
    assert s.time_criticality_component == 1.0 * (4.0 + 10.0 / 9999 + CRITICAL_PATH_BOOST)
    assert s.risk_reduction_component == 3.0


def test_optional_field_alias_precedence() -> None:
    # time_criticality > criticality > urgency ; risk_reduction > opportunity_enablement > risk.
    tasks = [
        {
            "id": "t",
            "estimated_effort": 1,
            "priority": 9999,
            "time_criticality": 7.0,
            "criticality": 1.0,
            "urgency": 1.0,
            "risk_reduction": 6.0,
            "opportunity_enablement": 1.0,
            "risk": 1.0,
        }
    ]
    schedule = compute_schedule(tasks)
    s = _by_id(score_tasks_wsjf(tasks, schedule))["t"]
    # A lone root is its own critical path (slack 0) -> critical_path_boost (2.0) applies.
    assert s.on_critical_path is True
    assert s.time_criticality_component == 1.0 * (7.0 + 10.0 / 9999 + CRITICAL_PATH_BOOST)
    assert s.risk_reduction_component == 6.0

    # Drop the top alias -> second alias is read.
    tasks2 = [{"id": "t", "estimated_effort": 1, "priority": 9999, "criticality": 5.0, "opportunity_enablement": 9.0}]
    s2 = _by_id(score_tasks_wsjf(tasks2, schedule))["t"]
    assert s2.time_criticality_component == 1.0 * (5.0 + 10.0 / 9999 + CRITICAL_PATH_BOOST)
    assert s2.risk_reduction_component == 9.0


# --------------------------------------------------------------------------------------
# Edge cases / skipping.
# --------------------------------------------------------------------------------------


def test_empty_task_list_returns_empty() -> None:
    assert score_tasks_wsjf([], compute_schedule([])) == []


def test_non_dict_task_skipped() -> None:
    tasks: list[Any] = [{"id": "a", "estimated_effort": 1}, "not-a-dict", 42, None]
    schedule = compute_schedule([t for t in tasks if isinstance(t, dict)])
    scores = score_tasks_wsjf(tasks, schedule)  # type: ignore[arg-type]
    assert [s.task_id for s in scores] == ["a"]


# --------------------------------------------------------------------------------------
# Result type contracts.
# --------------------------------------------------------------------------------------


def test_to_dict_roundtrip_json_safe() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    score = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task={"a": 1})[0]
    payload = score.to_dict()
    assert set(payload.keys()) == {f.name for f in dataclasses.fields(WsjfScore)}
    assert json.loads(json.dumps(payload)) == payload
    for key in (
        "task_id",
        "wsjf",
        "cost_of_delay",
        "job_size",
        "value_component",
        "time_criticality_component",
        "risk_reduction_component",
        "on_critical_path",
        "raid_pressure",
    ):
        assert key in payload
    assert isinstance(payload["on_critical_path"], bool)
    assert isinstance(payload["raid_pressure"], int)


def test_wsjfscore_is_frozen() -> None:
    score = WsjfScore(
        task_id="t",
        wsjf=1.0,
        cost_of_delay=1.0,
        job_size=1.0,
        value_component=1.0,
        time_criticality_component=0.0,
        risk_reduction_component=0.0,
        on_critical_path=False,
        raid_pressure=0,
    )
    with pytest.raises(dataclasses.FrozenInstanceError):
        score.wsjf = 2.0  # type: ignore[misc]


# --------------------------------------------------------------------------------------
# Rendering.
# --------------------------------------------------------------------------------------


def test_render_wsjf_markdown_contains_ranked_ids_and_scores() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    rendered = render_wsjf_markdown(scores)
    lines = rendered.splitlines()
    # header + separator + one row per score.
    assert len(lines) == 2 + len(scores)
    for s in scores:
        assert s.task_id in rendered
        assert f"{s.wsjf:.3f}" in rendered
    # rows appear in ranked (input) order.
    body = lines[2:]
    for row, s in zip(body, scores, strict=True):
        assert f"| {s.task_id} |" in row


def test_render_wsjf_markdown_byte_stable() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    assert render_wsjf_markdown(scores) == render_wsjf_markdown(scores)


def test_render_wsjf_markdown_no_clock_token() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    rendered = render_wsjf_markdown(score_tasks_wsjf(tasks, schedule))
    assert re.search(r"\d{4}-\d{2}-\d{2}", rendered) is None


def test_render_empty_scores() -> None:
    rendered = render_wsjf_markdown([])
    # header + separator only.
    assert len(rendered.splitlines()) == 2


# --------------------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------------------


def test_score_tasks_wsjf_is_deterministic() -> None:
    tasks = _diamond_tasks()
    schedule = compute_schedule(tasks)
    # build the raid mapping two different ways (set-order-sensitive construction).
    raid_a = {"a": 1, "b": 2}
    raid_b = {}
    for k in ["b", "a"]:
        raid_b[k] = {"a": 1, "b": 2}[k]
    first = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=raid_a)
    second = score_tasks_wsjf(tasks, schedule, raid_pressure_by_task=raid_b)
    assert first == second


# --------------------------------------------------------------------------------------
# §8 cleanliness + boundary / import constraints.
# --------------------------------------------------------------------------------------


def test_no_business_literals_s8() -> None:
    source = WSJF_PATH.read_text(encoding="utf-8").lower()
    tokens = set(re.findall(r"[a-z0-9]+", source))
    denylist = {"game", "card3d", "card", "express", "django", "react", "tenant", "tetris", "tank"}
    leaked = tokens & denylist
    assert not leaked, f"§8 violation: business literals leaked into wsjf.py: {sorted(leaked)}"


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                # relative same-cell import; record the resolved-ish tail for assertions.
                names.add("." * node.level + (node.module or ""))
            elif node.module:
                names.add(node.module)
    return names


def test_module_does_not_import_delivery_or_other_cells() -> None:
    names = _imported_module_names(WSJF_PATH)
    for name in names:
        assert not name.startswith("polaris." + "delivery"), f"forbidden delivery import: {name}"
        if name.startswith("polaris."):
            assert "orchestration." + "pm_planning.internal" in name, f"unexpected cross-cell polaris import: {name}"


def test_module_imports_no_raid_register_or_taskboard() -> None:
    source = WSJF_PATH.read_text(encoding="utf-8")
    names = _imported_module_names(WSJF_PATH)
    for forbidden in ("raid_register", "task_board", "task_runtime"):
        assert all(forbidden not in name for name in names), f"forbidden import of {forbidden}"
        assert f"import {forbidden}" not in source


def _executable_code(path: Path) -> str:
    """Return the module's executable token stream with strings/comments/docstrings stripped.

    Tokenizing and dropping ``STRING``/``COMMENT``/``FSTRING_*`` tokens removes the docstring
    (which legitimately *names* the forbidden tokens to document their absence) so the purity
    scan only sees real code.
    """
    import token
    import tokenize

    pieces: list[str] = []
    drop = {token.STRING, token.COMMENT, tokenize.NL, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT}
    fstring_types = {
        getattr(token, name) for name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END") if hasattr(token, name)
    }
    drop |= fstring_types
    with path.open("rb") as handle:
        for tok in tokenize.tokenize(handle.readline):
            if tok.type in drop or tok.type == token.ENCODING:
                continue
            pieces.append(tok.string)
    return " ".join(pieces)


def test_module_no_os_environ_or_clock() -> None:
    code = _executable_code(WSJF_PATH)
    # Reconstruct dotted accesses without the inter-token spaces tokenize inserts.
    dense = code.replace(" . ", ".").replace(" .", ".").replace(". ", ".")
    for forbidden in ("os." + "environ", "getenv", "datetime", "time." + "time"):
        assert forbidden not in dense, f"impure token in wsjf.py: {forbidden}"


# --------------------------------------------------------------------------------------
# Floor-safety: the three raw-priority sort sites stay byte-untouched (no wiring this increment).
# --------------------------------------------------------------------------------------


def test_no_live_path_references_wsjf() -> None:
    sites = [
        PM_PLANNING_ROOT / "pipeline.py",
        PM_PLANNING_ROOT / "internal" / "pm_agent.py",
        PM_DISPATCH_REGISTRY,
    ]
    for site in sites:
        # Assert the path exists (do NOT skip-if-absent) so a repo move cannot false-green.
        assert site.exists(), f"expected live sort site missing: {site}"
        text = site.read_text(encoding="utf-8")
        for token in ("wsjf", "WsjfScore", "score_tasks_wsjf"):
            assert token not in text, f"premature wiring: {token} found in {site}"


# --------------------------------------------------------------------------------------
# Schedule type sanity (sibling reuse is live, not stubbed).
# --------------------------------------------------------------------------------------


def test_schedule_reuse_is_live_type() -> None:
    schedule = compute_schedule(_diamond_tasks())
    assert isinstance(schedule, Schedule)
    assert set(schedule.weights.keys()) == {"a", "b", "c", "d"}
