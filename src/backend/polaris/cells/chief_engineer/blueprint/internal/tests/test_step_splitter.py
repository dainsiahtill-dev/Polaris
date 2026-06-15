"""Tests for the deterministic over-budget step splitter (I3-r29)."""

from __future__ import annotations

import re
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    _find_dependency_cycle,
    normalize_construction_step,
    validate_construction_steps,
)
from polaris.cells.chief_engineer.blueprint.internal.step_splitter import (
    _signature_symbol,
    split_oversize_steps,
)

PARENT = "PM-0001-1"

SIGS12 = [
    "function init()",
    "function createBricks()",
    "function handleMouseMove(e)",
    "function handleKeyDown(e)",
    "function update()",
    "function render()",
    "function checkCollisions()",
    "function loseLife()",
    "function resetBall()",
    "function gameOver()",
    "function levelComplete()",
    "function gameLoop(timestamp)",
]


def _step(target: str, sigs: list[str], est: int, *, sid: str = "S4", deps: list[str] | None = None) -> dict[str, Any]:
    return normalize_construction_step(
        {
            "step_id": sid,
            "target_file": target,
            "signatures": sigs,
            "est_lines": est,
            "verify": f"node --check {target}",
            "depends_on": deps or [],
            "title": "core game",
        },
        parent_pm_task=PARENT,
        index=0,
    )


def _ids(steps: list[dict[str, Any]]) -> list[str]:
    return [s["step_id"] for s in steps]


class TestSplitCorrectness:
    def test_oversize_js_step_splits_into_skeleton_plus_fills(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        ids = _ids(out)
        assert ids[0] == f"{PARENT}-S4-skel"
        assert ids[1:] == [f"{PARENT}-S4-fill{i}" for i in range(1, 5)]  # ceil(12/3)=4
        skeleton = out[0]
        assert skeleton["signatures"] == SIGS12  # skeleton declares every signature
        fill_sigs = [s for f in out[1:] for s in f["signatures"]]
        assert fill_sigs == SIGS12  # union of fills == all, no loss / no dup

    def test_skeleton_from_scratch_fills_edit_on_prior(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert "edit_on_prior" not in out[0]  # skeleton creates the file
        for fill in out[1:]:
            assert fill["edit_on_prior"] is True
            assert fill["edit_on_prior_owner"] == f"{PARENT}-S4-skel"

    def test_fills_chain_linearly_no_cycle(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert out[1]["depends_on"] == [f"{PARENT}-S4-skel"]
        for i in range(2, len(out)):
            assert out[i]["depends_on"] == [out[i - 1]["step_id"]]
        assert _find_dependency_cycle(out) == set()

    def test_skeleton_and_fills_under_ceiling(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        for s in out:
            assert 0 < s["est_lines"] <= 120

    def test_split_output_passes_gate(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert validate_construction_steps(out, parent_pm_task=PARENT) == []

    def test_skeleton_marked_stub_only(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert out[0]["skeleton_stub_only"] is True  # gateway renders a "stubs only" directive
        for fill in out[1:]:
            assert "skeleton_stub_only" not in fill

    def test_fills_marked_scope_only(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert "fill_scope_only" not in out[0]  # skeleton is not a fill
        for fill in out[1:]:
            assert fill["fill_scope_only"] is True  # gateway renders a bounded-edit directive

    def test_skeleton_inherits_step_depends_on(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200, deps=["S2"])], parent_pm_task=PARENT)
        assert out[0]["depends_on"] == [f"{PARENT}-S2"]


class TestTriggerBoundaries:
    def test_small_code_step_not_split(self) -> None:
        steps = [_step("main.js", ["function init()", "function update()", "function render()"], 60)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps

    def test_sole_writer_sig_only_leaf_is_kept_whole(self) -> None:
        # Over-fission suppression (batch-b1, 2026-06-15): 8 signatures but
        # est_lines=40 (sig-only trigger) on a SOLE-WRITER file is kept as ONE
        # coherent whole-file step — the weak Director cannot stitch one file across
        # N fill-turns. Identity preserved (nothing split).
        steps = [_step("main.js", SIGS12[:8], 40)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps

    def test_sig_only_split_still_fires_when_suppression_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_CE_SINGLE_FILE_NO_FISSION", "off")
        out = split_oversize_steps([_step("main.js", SIGS12[:8], 40)], parent_pm_task=PARENT)
        assert _ids(out)[0].endswith("-skel")

    def test_sole_writer_est_lines_trigger_leaf_is_kept_whole(self) -> None:
        # After the output-budget floor and reasoning-truncation re-ask fixes,
        # splitting one file across fill turns is more dangerous than one coherent
        # write. A sole-writer single-file leaf stays whole even when est_lines
        # crosses the splitter's line trigger.
        steps = [_step("main.js", SIGS12[:8], 110)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps

    def test_est_lines_trigger_can_still_split_when_suppression_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_CE_SINGLE_FILE_NO_FISSION", "off")
        out = split_oversize_steps([_step("main.js", SIGS12[:8], 110)], parent_pm_task=PARENT)
        assert _ids(out)[0].endswith("-skel")

    def test_multi_writer_same_file_sig_only_still_splits(self) -> None:
        # Two steps target the SAME file (multiplicity>1) → not a sole-writer leaf,
        # so the sig-only split is NOT suppressed.
        steps = [
            _step("main.js", SIGS12[:8], 40, sid="S4a"),
            _step("main.js", SIGS12[:6], 40, sid="S4b"),
        ]
        out = split_oversize_steps(steps, parent_pm_task=PARENT)
        assert any(sid.endswith("-skel") for sid in _ids(out))

    def test_doc_target_never_split(self) -> None:
        steps = [_step("readme.md", ["intro", "controls", "run", "scoring", "levels"], 200)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps

    def test_unknown_suffix_fails_open(self) -> None:
        steps = [_step("game.xyz", SIGS12, 200)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps


class TestFileAssemblyContractP1:
    """P1 (deterministic file-assembly protocol, codex 2026-06-15): the skeleton is the
    interface LAW (complete shell + one anchor per function); each fill carries the
    constrained-patch contract (anchor_ids / expected_signatures / allowed_region) the
    Director-side merger (P3) enforces, so a >120-line file is assembled by the SYSTEM,
    not re-derived from weak-model memory every fill-turn."""

    _SYMS = [
        "init",
        "createBricks",
        "handleMouseMove",
        "handleKeyDown",
        "update",
        "render",
        "checkCollisions",
        "loseLife",
        "resetBall",
        "gameOver",
        "levelComplete",
        "gameLoop",
    ]

    def test_skeleton_requires_full_shell_and_declares_every_anchor(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        skel = out[0]
        assert skel["file_shell_required"] is True
        assert skel["anchor_ids"] == self._SYMS

    def test_fill_carries_constrained_patch_contract(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        fill1 = out[1]
        assert fill1["anchor_ids"] == ["init", "createBricks", "handleMouseMove"]
        assert fill1["expected_signatures"] == SIGS12[:3]
        assert fill1["allowed_region"] == "anchors:init,createBricks,handleMouseMove"

    def test_fill_anchors_union_equals_skeleton_anchors(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        fill_anchors = [a for f in out[1:] for a in f["anchor_ids"]]
        assert fill_anchors == out[0]["anchor_ids"]  # complete coverage, no loss / no dup


class TestVerifyGeneration:
    def test_skeleton_verify_is_structural_syntax_check(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert "node --check main.js" in out[0]["verify"]
        assert 'grep -q "init" main.js' in out[0]["verify"]

    def test_fill_verify_greps_own_symbols(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        fill1 = out[1]
        assert "node --check main.js" in fill1["verify"]
        for sig in fill1["signatures"]:
            assert _signature_symbol(sig) in fill1["verify"]

    def test_signature_symbol_extraction_generic(self) -> None:
        assert _signature_symbol("function createBricks(rows, cols)") == "createBricks"
        assert _signature_symbol("def reset_ball(self)") == "reset_ball"
        assert _signature_symbol("const gameLoop = (ts) => {") == "gameLoop"
        assert _signature_symbol("class Paddle") == "Paddle"
        assert _signature_symbol("init()") == "init"  # no keyword → first identifier


class TestFailOpenGuards:
    def test_disabled_env_returns_input_unchanged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_CE_STEP_SPLIT", "off")
        steps = [_step("main.js", SIGS12, 200)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps

    def test_already_split_substep_not_resplit(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert split_oversize_steps(out, parent_pm_task=PARENT) is out  # idempotent

    def test_owned_elsewhere_target_not_split(self) -> None:
        steps = [_step("main.js", SIGS12, 200)]
        # main.js already owned by an earlier parent → cross-parent edit, do not split.
        out = split_oversize_steps(steps, parent_pm_task="PM-0001-2", owned_elsewhere={"main.js"})
        assert out is steps

    def test_too_many_substeps_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Force 1 sig/fill so 30 sigs -> 1 skel + 30 fills = 31 > 24 ceiling -> fail-open.
        monkeypatch.setenv("KERNELONE_STEP_SPLIT_SIGS_PER_FILL", "1")
        many = [f"function f{i}()" for i in range(30)]
        steps = [_step("main.js", many, 200)]
        assert split_oversize_steps(steps, parent_pm_task=PARENT) is steps


class TestFillBodyFloor:
    """Each fill carries a cumulative line floor so it cannot resolve on the
    skeleton's inherited stubs (review finding 2)."""

    def test_fill_verify_has_increasing_line_floor(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        floors = []
        for fill in out[1:]:
            match = re.search(r"wc -l < main\.js.*-ge (\d+)", fill["verify"])
            assert match, f"fill verify missing line floor: {fill['verify']}"
            floors.append(int(match.group(1)))
        assert floors == sorted(floors) and len(set(floors)) == len(floors)  # strictly increasing
        assert floors[-1] >= 12 * 6  # 12 sigs * _FILL_MIN_IMPL_LINES → a stub-only file fails it

    def test_skeleton_has_no_line_floor(self) -> None:
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        assert "wc -l" not in out[0]["verify"]  # skeleton writes stubs; no body floor

    def test_fill_floor_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("KERNELONE_STEP_SPLIT_FILL_MIN_IMPL_LINES", "10")
        out = split_oversize_steps([_step("main.js", SIGS12, 200)], parent_pm_task=PARENT)
        last = re.search(r"-ge (\d+)", out[-1]["verify"])
        assert last and int(last.group(1)) == 12 * 10


class TestDependentRepointing:
    def test_dependents_repointed_to_terminal_fill(self) -> None:
        big = _step("main.js", SIGS12, 200, sid="S1")
        dependent = normalize_construction_step(
            {
                "step_id": "S2",
                "target_file": "readme.md",
                "est_lines": 30,
                "verify": "test -f readme.md && grep -q intro readme.md",
                "depends_on": ["S1"],
            },
            parent_pm_task=PARENT,
            index=1,
        )
        out = split_oversize_steps([big, dependent], parent_pm_task=PARENT)
        readme = next(s for s in out if s["step_id"] == f"{PARENT}-S2")
        # S1 was split; its dependent now points at the terminal fill, not the gone "S1".
        assert readme["depends_on"] == [f"{PARENT}-S1-fill4"]
        assert validate_construction_steps(out, parent_pm_task=PARENT) == []  # no dangling dep
