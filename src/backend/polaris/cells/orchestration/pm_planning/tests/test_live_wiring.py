"""Tests for the gated WSJF live-wiring glue (``internal/live_wiring.py``).

These tests pin the floor-safety contract (gate-off returns the SAME list
object, opens no file, never reorders), the gate predicate truth table, the
gate-on highest-WSJF-first reorder (computed from the real cores, not a
hand-rolled constant), permutation/no-mutation/stability invariants, the
defensive guard (cycle / OSError / malformed task never raise), RAID influence,
single-construction of the register, the §7 boundary (no forbidden imports), the
§8 no-domain-literals discipline, and the +1/+1 shape of the pipeline.py edit.
"""

from __future__ import annotations

import ast
import copy
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.orchestration.pm_planning.internal import live_wiring as lw
from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
    DependencyCycleError,
)
from polaris.cells.orchestration.pm_planning.internal.live_wiring import (
    reorder_tasks_by_wsjf,
)

_GATE_ENV = "KERNELONE_PM_WSJF_ORDER"
_MODULE_PATH = Path(lw.__file__)
_PIPELINE_PATH = _MODULE_PATH.parent.parent / "pipeline.py"


def _task(task_id: str, **fields: Any) -> dict[str, Any]:
    """Build a minimal PM contract task dict."""
    task: dict[str, Any] = {"id": task_id}
    task.update(fields)
    return task


@pytest.fixture(autouse=True)
def _gate_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure each test starts with the gate environment variable unset."""
    monkeypatch.delenv(_GATE_ENV, raising=False)


# ----------------------------------------------------------------------------
# Gate-off (floor-safety) contract.
# ----------------------------------------------------------------------------


def test_wsjf_order_disabled_by_default(tmp_path: Path) -> None:
    """Gate unset -> returns the SAME list object (byte-identity proof)."""
    tasks = [_task("a", priority=1), _task("b", priority=2)]
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks


def test_gate_off_order_and_content_identical(tmp_path: Path) -> None:
    """Gate off: a higher-WSJF task positioned later is NOT promoted."""
    # 'b' would outrank 'a' under WSJF (smaller job, higher value), yet off-gate
    # the input order and object identity must be preserved exactly.
    tasks = [
        _task("a", priority=1, value=1.0, estimated_effort=8.0),
        _task("b", priority=9, value=10.0, estimated_effort=1.0),
    ]
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks
    assert [t["id"] for t in result] == ["a", "b"]


def test_gate_off_does_not_touch_disk(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Off path constructs NO RaidRegister (no file/mkdir effect)."""

    def _boom(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("RaidRegister must not be constructed on the gate-off path")

    monkeypatch.setattr(lw, "RaidRegister", _boom)
    tasks = [_task("a"), _task("b")]
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks


def test_non_list_input_returns_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A non-list input is returned unchanged for BOTH gate states."""
    bad_mapping: Any = {"not": "a list"}
    for token in (None, "1"):
        if token is None:
            monkeypatch.delenv(_GATE_ENV, raising=False)
        else:
            monkeypatch.setenv(_GATE_ENV, token)
        assert reorder_tasks_by_wsjf(None, str(tmp_path)) is None  # type: ignore[arg-type]
        assert reorder_tasks_by_wsjf(bad_mapping, str(tmp_path)) is bad_mapping


# ----------------------------------------------------------------------------
# Gate predicate truth table.
# ----------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On", "YES", " on "])
def test_gate_predicate_truthy(value: str) -> None:
    """Recognized truthy tokens enable the gate (case/whitespace insensitive)."""
    assert lw._wsjf_order_enabled(value) is True


@pytest.mark.parametrize(
    "value",
    [None, "", "0", "false", "off", "no", "2", "enabled", "disable", "maybe"],
)
def test_gate_predicate_falsy(value: str | None) -> None:
    """Unset/empty/unrecognized tokens leave the gate OFF (fail-closed)."""
    assert lw._wsjf_order_enabled(value) is False


def test_gate_predicate_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no explicit value the predicate reads the environment variable."""
    monkeypatch.setenv(_GATE_ENV, "true")
    assert lw._wsjf_order_enabled() is True
    monkeypatch.delenv(_GATE_ENV, raising=False)
    assert lw._wsjf_order_enabled() is False


# ----------------------------------------------------------------------------
# Gate-on reorder behavior.
# ----------------------------------------------------------------------------


def test_gate_on_reorders_highest_wsjf_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate on: order equals the real WSJF highest-first ranking."""
    from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
        compute_schedule,
    )
    from polaris.cells.orchestration.pm_planning.internal.wsjf import score_tasks_wsjf

    monkeypatch.setenv(_GATE_ENV, "1")
    tasks = [
        # High priority but a big job and low value -> low WSJF.
        _task("big", priority=1, value=1.0, estimated_effort=16.0),
        # Low priority but a tiny job and high value -> high WSJF.
        _task("tiny", priority=9, value=20.0, estimated_effort=1.0),
        _task("mid", priority=5, value=5.0, estimated_effort=4.0),
    ]
    expected = [s.task_id for s in score_tasks_wsjf(tasks, compute_schedule(tasks))]

    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert [t["id"] for t in result] == expected
    # Sanity: the tiny/high-value task outranks the big/low-value one.
    assert result[0]["id"] == "tiny"


def test_gate_on_is_permutation_no_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Gate on: result is a permutation of the same dicts, none mutated."""
    monkeypatch.setenv(_GATE_ENV, "on")
    tasks = [
        _task("a", priority=3, value=2.0, estimated_effort=4.0),
        _task("b", priority=1, value=9.0, estimated_effort=1.0),
        _task("c", priority=2, value=1.0, estimated_effort=8.0),
    ]
    snapshot = copy.deepcopy(tasks)
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))

    # Every input dict appears exactly once (same object identities).
    assert {id(t) for t in result} == {id(t) for t in tasks}
    assert len(result) == len(tasks)
    # No task dict mutated.
    for original, snap in zip(tasks, snapshot, strict=True):
        assert original == snap


def test_gate_on_stable_for_equal_rank(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Tasks sharing one normalized id keep input relative order (stable secondary key).

    Two distinct dicts with the SAME id collapse onto one ``rank`` entry
    (``rank.setdefault`` keeps the first occurrence). The ``original_index``
    secondary key then orders the duplicates deterministically by input position,
    proving stability without relying on the score's own ``task_id`` tiebreak.
    """
    monkeypatch.setenv(_GATE_ENV, "yes")
    dup_first = _task("dup", priority=5, value=2.0, estimated_effort=2.0)
    dup_second = _task("dup", priority=5, value=2.0, estimated_effort=2.0)
    other = _task("other", priority=1, value=20.0, estimated_effort=1.0)
    tasks = [dup_first, other, dup_second]

    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    # Both 'dup' dicts share rank; their relative order matches input position.
    # Compare by identity (the two dup dicts are ==-equal, so list.index would
    # collapse them).
    positions = {id(task): index for index, task in enumerate(result)}
    assert positions[id(dup_first)] < positions[id(dup_second)]
    # The high-WSJF 'other' still leads.
    assert result[0] is other


def test_gate_on_empty_id_task_is_scored_and_survives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An empty-id task is scored by the cores (task_id="") and survives exactly once.

    The underlying scorer emits a ``WsjfScore`` for every dict, including one
    with an empty id, so the reorder ranks it by its real WSJF rather than
    discarding it. The reorder must remain a faithful permutation of the score
    order, computed from the real cores (not a hand-rolled constant).
    """
    from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
        compute_schedule,
    )
    from polaris.cells.orchestration.pm_planning.internal.wsjf import score_tasks_wsjf

    monkeypatch.setenv(_GATE_ENV, "1")
    blank = _task("", value=99.0, estimated_effort=1.0)
    tasks = [_task("x", value=2.0, estimated_effort=2.0), blank]
    expected = [s.task_id for s in score_tasks_wsjf(tasks, compute_schedule(tasks))]

    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert {id(t) for t in result} == {id(t) for t in tasks}
    assert [t["id"] for t in result] == expected


# ----------------------------------------------------------------------------
# Defensive guard: the gate-on path NEVER raises into planning.
# ----------------------------------------------------------------------------


def test_gate_on_defensive_on_cycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A depends_on cycle makes compute_schedule raise -> input returned unchanged."""
    monkeypatch.setenv(_GATE_ENV, "1")
    tasks = [
        _task("a", depends_on=["b"]),
        _task("b", depends_on=["a"]),
    ]
    # Confirm the underlying core really raises on this input.
    from polaris.cells.orchestration.pm_planning.internal.dependency_validator import (
        compute_schedule,
    )

    with pytest.raises(DependencyCycleError):
        compute_schedule(tasks)

    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks


def test_gate_on_defensive_on_raid_oserror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An OSError from RaidRegister.summarize degrades to a no-op."""
    monkeypatch.setenv(_GATE_ENV, "1")

    class _BoomRegister:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def summarize(self, *, task_id: str | None = None) -> dict[str, Any]:
            raise OSError("disk gone")

    monkeypatch.setattr(lw, "RaidRegister", _BoomRegister)
    tasks = [_task("a", value=1.0), _task("b", value=2.0)]
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks


def test_gate_on_defensive_on_malformed_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A task whose id access raises TypeError degrades to a no-op."""
    monkeypatch.setenv(_GATE_ENV, "1")

    class _HostileTask(dict):  # type: ignore[type-arg]
        def get(self, *_args: Any, **_kwargs: Any) -> Any:
            raise TypeError("hostile task")

    tasks: list[dict[str, Any]] = [_HostileTask(), _task("b")]
    result = reorder_tasks_by_wsjf(tasks, str(tmp_path))
    assert result is tasks


# ----------------------------------------------------------------------------
# RAID influence + register lifetime.
# ----------------------------------------------------------------------------


def test_raid_influence_promotes_task(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An open CRITICAL/BLOCKER RAID entry boosts a task's WSJF ranking."""
    from polaris.cells.orchestration.pm_planning.internal.raid_register import (
        RaidCategory,
        RaidProbability,
        RaidRegister,
        RaidSeverity,
    )

    workspace = str(tmp_path)
    # Two peers with identical WSJF-relevant fields. Without RAID pressure they
    # tie on (-wsjf, job_size) and the cores break the tie by ``task_id``, so
    # 'peer_a' (sorts first) leads 'peer_z'. The id choice makes the alphabetical
    # tiebreak fixed, so any flip is attributable solely to RAID pressure.
    tasks = [
        _task("peer_z", priority=5, value=2.0, estimated_effort=2.0),
        _task("peer_a", priority=5, value=2.0, estimated_effort=2.0),
    ]

    # Baseline (gate on, no RAID entries): tie broken by id -> peer_a first.
    monkeypatch.setenv(_GATE_ENV, "1")
    baseline = reorder_tasks_by_wsjf([dict(t) for t in tasks], workspace)
    assert [t["id"] for t in baseline] == ["peer_a", "peer_z"]

    # Seed an OPEN BLOCKER risk for 'peer_z' so its risk-reduction component (and
    # thus WSJF) is boosted above its peer, overriding the id tiebreak.
    register = RaidRegister(workspace)
    register.register(
        task_id="peer_z",
        category=RaidCategory.RISK,
        title="open blocker pressure",
        severity=RaidSeverity.BLOCKER,
        probability=RaidProbability.LIKELY,
    )

    result = reorder_tasks_by_wsjf([dict(t) for t in tasks], workspace)
    assert [t["id"] for t in result] == ["peer_z", "peer_a"]


def test_raidregister_constructed_once(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The register is built once; summarize runs once per DISTINCT non-empty id."""
    monkeypatch.setenv(_GATE_ENV, "1")

    ctor_calls: list[dict[str, Any]] = []
    summarize_ids: list[str | None] = []

    class _SpyRegister:
        def __init__(self, workspace: str, *, ensure_directory: bool = True) -> None:
            ctor_calls.append({"workspace": workspace, "ensure_directory": ensure_directory})

        def summarize(self, *, task_id: str | None = None) -> dict[str, Any]:
            summarize_ids.append(task_id)
            return {"open_critical_or_blocker": 0}

    monkeypatch.setattr(lw, "RaidRegister", _SpyRegister)
    tasks = [
        _task("a", value=1.0, estimated_effort=1.0),
        _task("b", value=2.0, estimated_effort=1.0),
        _task("a", value=3.0, estimated_effort=1.0),  # duplicate id
        _task("", value=4.0),  # empty id -> not summarized
    ]
    reorder_tasks_by_wsjf(tasks, str(tmp_path))

    assert len(ctor_calls) == 1
    assert ctor_calls[0]["ensure_directory"] is False
    # Distinct non-empty ids only, in first-seen order; no duplicate, no empty.
    assert summarize_ids == ["a", "b"]


# ----------------------------------------------------------------------------
# Boundary (§7) + domain-literal (§8) discipline.
# ----------------------------------------------------------------------------


def test_boundary_no_forbidden_imports() -> None:
    """live_wiring.py imports only stdlib + the three sibling internals."""
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    allowed_stdlib = {"__future__", "os", "typing"}
    allowed_siblings = {".dependency_validator", ".raid_register", ".wsjf"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name in allowed_stdlib, alias.name
        elif isinstance(node, ast.ImportFrom):
            module = ("." * node.level) + (node.module or "")
            assert module in allowed_stdlib or module in allowed_siblings, module
            assert "polaris.delivery" not in module
            assert "chief_engineer" not in module
            assert "project_report" not in module


def test_no_domain_literals() -> None:
    """§8: the module carries no project/business/domain tokens."""
    source = _MODULE_PATH.read_text(encoding="utf-8").lower()
    forbidden = (
        "express",
        "django",
        "flask",
        "fastapi",
        "react",
        "tenant",
        "invoice",
        "checkout",
        "tetris",
        "card3d",
    )
    for token in forbidden:
        assert token not in source, token


# ----------------------------------------------------------------------------
# Pipeline edit shape (+1 import line, +1 call line).
# ----------------------------------------------------------------------------


def test_pipeline_edit_shape() -> None:
    """pipeline.py imports the helper and calls it exactly once, after the sort."""
    source = _PIPELINE_PATH.read_text(encoding="utf-8")
    assert "reorder_tasks_by_wsjf" in source
    assert "from polaris.cells.orchestration.pm_planning.internal.live_wiring import" in source

    call = "normalized_tasks = reorder_tasks_by_wsjf(normalized_tasks, workspace_full)"
    assert source.count(call) == 1

    # The call must appear AFTER the priority sort and BEFORE the tasks assignment.
    sort_anchor = 'key=lambda task: normalize_priority(task.get("priority"), fallback=9999),'
    assign_anchor = 'normalized["tasks"] = normalized_tasks'
    assert source.index(sort_anchor) < source.index(call) < source.index(assign_anchor)
