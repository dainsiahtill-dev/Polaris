"""Tests for the composed PM project status report (``orchestration.pm_planning``).

The PURE composer + renderer are exercised with only plain dicts plus a real
:class:`Schedule` and pre-ranked :class:`WsjfScore` list (purity guard). The
GATHER half (:func:`build_pm_project_report`) is exercised against real sibling
``RaidRegister`` / ``MilestoneRegister`` stores seeded in a ``tmp_path``
workspace. Boundary tests pin the relaxed import whitelist (siblings +
``polaris.kernelone.storage`` only, no ``polaris.delivery``), the clock-free /
env-free invariant of the PURE functions, the §8 "no business literals" rule,
and the unwired/floor-safe state.
"""

from __future__ import annotations

import ast
import inspect
import json
import re
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal import project_report as pr
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    Schedule,
    compute_schedule,
)
from polaris.cells.orchestration.pm_planning.internal.milestones import (
    MilestoneRecordV1,
    MilestoneRegister,
    MilestoneStatus,
)
from polaris.cells.orchestration.pm_planning.internal.project_report import (
    ProjectReport,
    build_pm_project_report,
    compose_project_report,
    render_project_markdown,
)
from polaris.cells.orchestration.pm_planning.internal.raid_register import (
    RaidCategory,
    RaidProbability,
    RaidRegister,
    RaidSeverity,
)
from polaris.cells.orchestration.pm_planning.internal.wsjf import WsjfScore, score_tasks_wsjf
from polaris.kernelone.storage import resolve_logical_path

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _linear_schedule() -> Schedule:
    """Three unit-weight tasks in a chain a -> b -> c (sum(weights) == 3.0)."""
    return compute_schedule(_linear_tasks())


def _linear_tasks() -> list[dict[str, Any]]:
    return [
        {"id": "a", "priority": 1, "status": "completed"},
        {"id": "b", "priority": 2, "status": "in_progress", "dependencies": ["a"]},
        {"id": "c", "priority": 3, "status": "ready", "dependencies": ["b"]},
    ]


def _raid_with_open_critical() -> dict[str, Any]:
    """A RAID summary shaped like ``RaidRegister.summarize()`` with one open critical."""
    return {
        "total": 1,
        "by_category": {"risk": 1},
        "by_severity": {"low": 0, "medium": 0, "high": 0, "critical": 1, "blocker": 0},
        "by_status": {"open": 1, "mitigating": 0, "accepted": 0, "resolved": 0, "reverted": 0},
        "open_critical_or_blocker": 1,
    }


def _milestone_records() -> list[MilestoneRecordV1]:
    """Two records: one ACHIEVED, one PLANNED with a target iteration."""
    return [
        MilestoneRecordV1(
            milestone_id="milestone_a",
            name="Alpha",
            status=MilestoneStatus.ACHIEVED,
            target_iteration=1,
        ),
        MilestoneRecordV1(
            milestone_id="milestone_b",
            name="Beta",
            status=MilestoneStatus.PLANNED,
            target_iteration=5,
        ),
    ]


def _milestone_summary() -> dict[str, Any]:
    return {
        "total": 2,
        "by_status": {"planned": 1, "in_progress": 0, "achieved": 1, "missed": 0, "cancelled": 0},
        "overdue": 0,
        "next_upcoming": {"milestone_id": "milestone_b", "target_iteration": 5},
        "current_iteration": 0,
    }


def _full_report(generated_at: str = "2026-06-17T00:00:00Z", *, current_iteration: int = 0) -> ProjectReport:
    tasks = _linear_tasks()
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    return compose_project_report(
        status_counts={"completed": 1, "in_progress": 1, "ready": 1},
        raid_summary=_raid_with_open_critical(),
        schedule=schedule,
        wsjf_scores=scores,
        milestone_records=_milestone_records(),
        milestone_summary=_milestone_summary(),
        current_iteration=current_iteration,
        velocity=2.0,
        generated_at=generated_at,
    )


def _seed_contract(workspace: str, tasks: list[dict[str, Any]]) -> None:
    path = Path(resolve_logical_path(workspace, "runtime/contracts/pm_tasks.contract.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema_version": "2", "tasks": tasks}), encoding="utf-8")


def _seed_raw_contract(workspace: str, raw: str) -> None:
    path = Path(resolve_logical_path(workspace, "runtime/contracts/pm_tasks.contract.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw, encoding="utf-8")


# ----------------------------------------------------------------------
# 1-6: pure composer
# ----------------------------------------------------------------------


def test_compose_is_pure_deterministic() -> None:
    first = _full_report()
    second = _full_report()
    assert first == second
    assert first.to_dict() == second.to_dict()
    # Composed view carries every section.
    assert first.rollup
    assert first.top_wsjf
    assert first.raid == _raid_with_open_critical()
    bands = {entry["milestone_id"]: entry["band"] for entry in first.milestone_health}
    assert bands["milestone_a"] == "achieved"
    assert isinstance(first.critical_path, tuple)
    assert first.makespan == 3.0
    assert first.generated_at == "2026-06-17T00:00:00Z"


def test_compose_empty_degenerate_never_raises() -> None:
    report = compose_project_report(
        status_counts={},
        raid_summary={},
        schedule=compute_schedule([]),
        wsjf_scores=[],
        milestone_records=[],
        milestone_summary={},
        velocity=None,
    )
    assert report.health in {"GREEN", "AMBER", "RED"}
    assert report.percent_complete == 0.0
    assert report.eta_iterations is None
    assert report.top_wsjf == ()
    assert report.milestone_health == ()
    assert report.makespan == 0.0


@pytest.mark.parametrize(
    "kwargs",
    [
        # (a) tasks+schedule present, no RAID/no milestones.
        {
            "status_counts": {"completed": 1, "ready": 1},
            "raid_summary": {},
            "with_schedule": True,
            "with_scores": True,
            "milestone_records": [],
            "milestone_summary": {},
        },
        # (b) RAID present, no tasks/schedule.
        {
            "status_counts": {},
            "raid_summary": _raid_with_open_critical(),
            "with_schedule": False,
            "with_scores": False,
            "milestone_records": [],
            "milestone_summary": {},
        },
        # (c) milestones present, no tasks.
        {
            "status_counts": {},
            "raid_summary": {},
            "with_schedule": False,
            "with_scores": False,
            "milestone_records": _milestone_records(),
            "milestone_summary": _milestone_summary(),
        },
    ],
)
def test_compose_partial_matrix(kwargs: dict[str, Any]) -> None:
    schedule = compute_schedule(_linear_tasks()) if kwargs["with_schedule"] else compute_schedule([])
    scores = score_tasks_wsjf(_linear_tasks(), schedule) if kwargs["with_scores"] else []
    report = compose_project_report(
        status_counts=kwargs["status_counts"],
        raid_summary=kwargs["raid_summary"],
        schedule=schedule,
        wsjf_scores=scores,
        milestone_records=kwargs["milestone_records"],
        milestone_summary=kwargs["milestone_summary"],
    )
    assert isinstance(report, ProjectReport)
    # all-None requirements -> coverage stays None in the rollup.
    assert report.rollup["requirements_coverage"] is None


def test_topn_slice_correctness() -> None:
    tasks = [{"id": f"t{i}", "priority": i} for i in range(15)]
    schedule = compute_schedule(tasks)
    scores = score_tasks_wsjf(tasks, schedule)
    big = compose_project_report(
        status_counts={},
        raid_summary={},
        schedule=schedule,
        wsjf_scores=scores,
        milestone_records=[],
        milestone_summary={},
    )
    assert len(big.top_wsjf) == 10
    assert big.wsjf_total_scored == 15
    # Core ranked order is preserved (slice of the pre-ranked input).
    assert [e["task_id"] for e in big.top_wsjf] == [s.task_id for s in scores[:10]]

    small_scores = scores[:4]
    small = compose_project_report(
        status_counts={},
        raid_summary={},
        schedule=schedule,
        wsjf_scores=small_scores,
        milestone_records=[],
        milestone_summary={},
    )
    assert len(small.top_wsjf) == 4

    capped = compose_project_report(
        status_counts={},
        raid_summary={},
        schedule=schedule,
        wsjf_scores=scores,
        milestone_records=[],
        milestone_summary={},
        top_n=3,
    )
    assert len(capped.top_wsjf) == 3


def test_milestone_health_bands_folded() -> None:
    overdue_record = MilestoneRecordV1(
        milestone_id="milestone_late",
        name="Late",
        status=MilestoneStatus.PLANNED,
        target_iteration=1,
    )
    # An on-track record whose linked task sits on the critical path.
    tasks = _linear_tasks()
    schedule = compute_schedule(tasks)
    on_path_record = MilestoneRecordV1(
        milestone_id="milestone_path",
        name="OnPath",
        status=MilestoneStatus.PLANNED,
        target_iteration=10,
        task_ids=("a",),
    )
    report = compose_project_report(
        status_counts={},
        raid_summary={},
        schedule=schedule,
        wsjf_scores=[],
        milestone_records=[overdue_record, on_path_record],
        milestone_summary={},
        current_iteration=5,
    )
    bands = {e["milestone_id"]: e["band"] for e in report.milestone_health}
    assert bands["milestone_late"] == "overdue"
    # Schedule threaded in => on_track tightened to at_risk via critical path.
    assert bands["milestone_path"] == "at_risk"


def test_to_dict_json_safe_and_lossless() -> None:
    report = _full_report()
    encoded = json.dumps(report.to_dict())
    decoded = json.loads(encoded)
    assert isinstance(decoded["critical_path"], list)
    assert isinstance(decoded["top_wsjf"], list)
    assert isinstance(decoded["milestone_health"], list)
    assert decoded["eta_iterations"] is not None  # velocity>0 here
    assert decoded["rollup"]["requirements_coverage"] is None

    # eta None preserved honestly when velocity is None.
    none_eta = compose_project_report(
        status_counts={"in_progress": 1},
        raid_summary={},
        schedule=compute_schedule(_linear_tasks()),
        wsjf_scores=[],
        milestone_records=[],
        milestone_summary={},
        velocity=None,
    )
    assert none_eta.to_dict()["eta_iterations"] is None


# ----------------------------------------------------------------------
# 7-10: renderer
# ----------------------------------------------------------------------


def test_render_byte_stable() -> None:
    report = _full_report()
    first = render_project_markdown(report)
    assert first == render_project_markdown(report)
    for header in ("## Health & Progress", "## Top Priorities (WSJF)", "## Open RAID", "## Milestones"):
        assert header in first


def test_render_canonical_order_hashseed_independent() -> None:
    canonical_raid = _raid_with_open_critical()
    shuffled_raid = {
        "total": 1,
        "by_category": {"risk": 1},
        "by_severity": {"blocker": 0, "critical": 1, "medium": 0, "low": 0, "high": 0},
        "by_status": {"reverted": 0, "open": 1, "resolved": 0, "accepted": 0, "mitigating": 0},
        "open_critical_or_blocker": 1,
    }
    base = compose_project_report(
        status_counts={},
        raid_summary=canonical_raid,
        schedule=compute_schedule([]),
        wsjf_scores=[],
        milestone_records=[],
        milestone_summary={},
    )
    shuffled = compose_project_report(
        status_counts={},
        raid_summary=shuffled_raid,
        schedule=compute_schedule([]),
        wsjf_scores=[],
        milestone_records=[],
        milestone_summary={},
    )
    assert render_project_markdown(base) == render_project_markdown(shuffled)


def test_render_eta_sentinels() -> None:
    unknown = ProjectReport(eta_iterations=None, critical_path=(), top_wsjf=())
    rendered = render_project_markdown(unknown)
    assert "ETA: unknown" in rendered
    assert "Critical path: n/a" in rendered
    assert "Top 0 of 0 scored" in rendered
    assert "n/a" in rendered  # empty WSJF + missing next_upcoming

    done = ProjectReport(eta_iterations=0.0)
    assert "ETA: 0 iterations" in render_project_markdown(done)


def test_render_no_clock_token() -> None:
    report = compose_project_report(
        status_counts={"ready": 1},
        raid_summary={},
        schedule=compute_schedule([]),
        wsjf_scores=[],
        milestone_records=[],
        milestone_summary={},
    )
    rendered = render_project_markdown(report)
    assert not re.search(r"\d{4}-\d{2}-\d{2}", rendered)


# ----------------------------------------------------------------------
# 11-17: gather half (build_pm_project_report)
# ----------------------------------------------------------------------


def test_load_pm_tasks_missing_contract_returns_empty(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    assert pr._load_pm_tasks(workspace) == []
    # build does not raise on a missing contract.
    summary = build_pm_project_report(workspace)
    assert summary["ok"] is True


def test_load_pm_tasks_corrupt_json_returns_empty(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_raw_contract(workspace, "{invalid_json}")
    assert pr._load_pm_tasks(workspace) == []
    # Non-dict top level.
    _seed_raw_contract(workspace, "[1, 2, 3]")
    assert pr._load_pm_tasks(workspace) == []
    # tasks not a list.
    _seed_raw_contract(workspace, json.dumps({"tasks": "not-a-list"}))
    assert pr._load_pm_tasks(workspace) == []
    assert build_pm_project_report(workspace)["ok"] is True


def test_build_happy_path_writes_md_and_json_atomically(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_contract(
        workspace,
        [
            {"id": "a", "priority": 1, "status": "done"},
            {"id": "b", "priority": 2, "status": "in_progress", "dependencies": ["a"]},
        ],
    )
    raid = RaidRegister(workspace)
    raid.register(
        task_id="b",
        category=RaidCategory.RISK,
        title="t",
        severity=RaidSeverity.CRITICAL,
        probability=RaidProbability.LIKELY,
    )
    milestones = MilestoneRegister(workspace)
    milestones.register(name="Alpha", target_iteration=3)

    summary = build_pm_project_report(workspace, current_iteration=2)
    assert summary["ok"] is True

    md_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.md"
    json_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.json"
    assert md_path.exists()
    assert json_path.exists()
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["raid"]["open_critical_or_blocker"] == 1
    assert payload["milestones"]["total"] == 1
    # No stray temp companion.
    assert not (md_path.parent / (md_path.name + ".tmp")).exists()
    assert not (json_path.parent / (json_path.name + ".tmp")).exists()
    assert summary["total_tasks"] == 2


def test_status_vocab_adapter_done_counts_as_completed(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_contract(
        workspace,
        [
            {"id": "a", "status": "done"},
            {"id": "b", "status": "done"},
            {"id": "c", "status": "in_progress"},
        ],
    )
    summary = build_pm_project_report(workspace)
    json_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    # done -> completed: 2 of 3 complete.
    assert payload["rollup"]["completed_tasks"] == 2
    assert summary["percent_complete"] == pytest.approx(2 / 3, rel=1e-3)


def test_dependency_cycle_degrades_not_raises(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_contract(
        workspace,
        [
            {"id": "a", "status": "todo", "dependencies": ["b"]},
            {"id": "b", "status": "todo", "dependencies": ["a"]},
        ],
    )
    summary = build_pm_project_report(workspace)
    assert summary["degraded"] is True
    assert summary["makespan"] == 0.0
    json_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.json"
    assert json_path.exists()


def test_raid_pressure_projection_into_wsjf(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_contract(workspace, [{"id": "a", "status": "todo"}, {"id": "b", "status": "todo"}])
    raid = RaidRegister(workspace)
    raid.register(
        task_id="a",
        category=RaidCategory.ISSUE,
        title="t",
        severity=RaidSeverity.BLOCKER,
        probability=RaidProbability.ALMOST_CERTAIN,
    )
    build_pm_project_report(workspace)
    json_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.json"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    by_task = {e["task_id"]: e["raid_pressure"] for e in payload["top_wsjf"]}
    assert by_task["a"] > 0


def test_build_fail_closed_empty_stores(tmp_path: Path) -> None:
    workspace = str(tmp_path)
    _seed_contract(workspace, [])
    summary = build_pm_project_report(workspace)
    assert summary["ok"] is True
    md_path = Path(resolve_logical_path(workspace, "runtime/pm")) / "pm.status.md"
    assert md_path.exists()
    assert not (md_path.parent / (md_path.name + ".tmp")).exists()


# ----------------------------------------------------------------------
# 18-23: public surface, §8, boundary, floor-safety
# ----------------------------------------------------------------------

_EXISTING_PUBLIC_ALL = (
    "GeneratePmTaskContractCommandV1",
    "GetPmPlanningStatusQueryV1",
    "PMAgent",
    "PMService",
    "PmInvokeBackendPort",
    "PmPlanningError",
    "PmStatePort",
    "PmTaskContractGeneratedEventV1",
    "PmTaskContractResultV1",
    "ProcessHandle",
    "_should_promote_pm_quality_candidate",
    "autofix_pm_contract_for_quality",
    "detect_integration_verify_command",
    "evaluate_pm_task_quality",
    "get_pm_service",
    "reset_pm_service",
    "run_integration_verify_runner",
    "run_pm_planning_iteration",
)


def test_public_exports_additive() -> None:
    from polaris.cells.orchestration.pm_planning import public
    from polaris.cells.orchestration.pm_planning.public import (
        ProjectReport as PublicProjectReport,
        build_pm_project_report as public_build,
    )
    from polaris.cells.orchestration.pm_planning.public.service import (
        ProjectReport as ServiceProjectReport,
        build_pm_project_report as service_build,
    )

    assert PublicProjectReport is ProjectReport
    assert ServiceProjectReport is ProjectReport
    assert public_build is build_pm_project_report
    assert service_build is build_pm_project_report
    # Every pre-existing export still importable (additive-only regression pin).
    for name in _EXISTING_PUBLIC_ALL:
        assert name in public.__all__
        assert hasattr(public, name)
    assert "ProjectReport" in public.__all__
    assert "build_pm_project_report" in public.__all__


def test_no_business_literals_s8() -> None:
    source = Path(pr.__file__).read_text(encoding="utf-8").lower()
    tokens = set(re.findall(r"[a-z0-9]+", source))
    denylist = ("game", "card3d", "card", "express", "django", "react", "tenant", "tetris", "tank")
    for token in denylist:
        assert token not in tokens, f"S8 violation: business literal {token!r}"


def _imported_module_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_ast_import_boundary_relaxed() -> None:
    forbidden_delivery = "polaris." + "delivery"
    sibling = "orchestration." + "pm_planning.internal"
    storage = "polaris." + "kernelone.storage"
    imported = _imported_module_names(Path(pr.__file__))
    assert not any(module.startswith(forbidden_delivery) for module in imported)
    assert not any("chief_engineer" in module for module in imported)
    for module in imported:
        if module.startswith("polaris."):
            assert sibling in module or module == storage, f"unexpected polaris import: {module}"


def test_pure_composer_renderer_clock_env_free() -> None:
    # Scope the clock/env scan to ONLY the pure functions; the os.replace seam
    # legitimately lives in the gather half, so a whole-file scan would falsely fail.
    pure_sources = (
        inspect.getsource(compose_project_report),
        inspect.getsource(render_project_markdown),
        inspect.getsource(pr._render_health_block),
        inspect.getsource(pr._render_wsjf_block),
        inspect.getsource(pr._render_raid_block),
        inspect.getsource(pr._render_milestone_block),
    )
    for source in pure_sources:
        for token in ("os." + "environ", "getenv", "datetime", "time.time", "os.replace", "open("):
            assert token not in source, f"forbidden effect token in pure fn: {token!r}"


def test_floor_safety_no_live_path_references() -> None:
    internal_dir = Path(pr.__file__).parent
    cell_dir = internal_dir.parent
    live_paths = [
        internal_dir / "pm_agent.py",
        internal_dir / "pipeline_ports.py",
        internal_dir / "task_quality_gate.py",
        internal_dir / "shared_quality.py",
        cell_dir / "public" / "contracts.py",
        cell_dir / "public" / "pipeline.py",
        cell_dir / "service.py",
    ]
    for path in live_paths:
        if not path.exists():
            continue
        source = path.read_text(encoding="utf-8")
        assert "project_report" not in source
        assert "ProjectReport" not in source
        assert "build_pm_project_report" not in source


def test_milestones_untouched_pin() -> None:
    # Belt-and-suspenders: the composer imports the milestone core (use only).
    imported = _imported_module_names(Path(pr.__file__))
    assert any(module == "milestones" or module.endswith(".milestones") for module in imported)
    # WsjfScore is consumed from the sibling scorer, not reimplemented.
    assert isinstance(score_tasks_wsjf([], compute_schedule([])), list)
    assert WsjfScore  # imported symbol is live
