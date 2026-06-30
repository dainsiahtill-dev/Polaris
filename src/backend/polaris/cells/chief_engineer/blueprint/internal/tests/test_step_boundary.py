"""Tests for deterministic task-boundary hardening of construction steps."""

from __future__ import annotations

from typing import Any

from polaris.cells.chief_engineer.blueprint.internal.step_boundary import harden_task_boundary_steps
from polaris.cells.chief_engineer.blueprint.internal.step_contract import (
    normalize_construction_step,
    validate_construction_steps,
)

PARENT = "PM-0007"


def _step(
    sid: str,
    target: str,
    *,
    verify: str = "test -f target",
    signatures: list[str] | None = None,
    deps: list[str] | None = None,
) -> dict[str, Any]:
    return normalize_construction_step(
        {
            "step_id": sid,
            "target_file": target,
            "est_lines": 20,
            "signatures": signatures or [],
            "verify": verify.replace("target", target),
            "depends_on": deps or [],
        },
        parent_pm_task=PARENT,
        index=0,
    )


def test_mixed_artifact_roles_are_serialized_by_nearest_predecessor_phase() -> None:
    steps = [
        _step("S1", "package.json"),
        _step("S2", "tsconfig.json"),
        _step("S3", "src/index.ts", verify="npx tsc --noEmit", signatures=["export function simulate()"]),
        _step(
            "S4",
            "tests/behavior.test.ts",
            verify="npx tsc --noEmit",
            signatures=["describe behavior"],
        ),
        _step("S5", "README.md"),
    ]

    hardened = harden_task_boundary_steps(steps)

    assert hardened is not steps
    assert validate_construction_steps(hardened, parent_pm_task=PARENT) == []
    by_target = {step["target_file"]: step for step in hardened}
    assert by_target["package.json"]["artifact_role"] == "manifest"
    assert by_target["tsconfig.json"]["depends_on"] == [f"{PARENT}-S1"]
    assert by_target["src/index.ts"]["depends_on"] == [f"{PARENT}-S2"]
    assert by_target["tests/behavior.test.ts"]["depends_on"] == [f"{PARENT}-S3"]
    assert by_target["README.md"]["depends_on"] == [f"{PARENT}-S4"]


def test_source_terminal_fill_is_used_for_later_test_dependency() -> None:
    steps = [
        _step("S1", "package.json"),
        _step("S2-skel", "src/main.js", verify="node --check src/main.js", signatures=["function run()"]),
        _step(
            "S2-fill1",
            "src/main.js",
            verify="node --check src/main.js",
            signatures=["function run()"],
            deps=["S2-skel"],
        ),
        _step(
            "S3",
            "tests/product.test.js",
            verify="node --check tests/product.test.js",
            signatures=["test product"],
        ),
    ]

    hardened = harden_task_boundary_steps(steps)

    by_target = {step["target_file"]: step for step in hardened}
    assert by_target["src/main.js"]["depends_on"] == [f"{PARENT}-S2-skel", f"{PARENT}-S1"]
    assert by_target["tests/product.test.js"]["depends_on"] == [f"{PARENT}-S2-fill1"]


def test_single_artifact_role_returns_original_steps() -> None:
    steps = [
        _step("S1", "src/a.js", verify="node --check src/a.js", signatures=["function a()"]),
        _step("S2", "src/b.js", verify="node --check src/b.js", signatures=["function b()"]),
    ]

    assert harden_task_boundary_steps(steps) is steps


def test_generated_dependency_cycle_fails_open() -> None:
    steps = [
        _step("S1", "package.json", deps=["S2"]),
        _step("S2", "src/index.js", verify="node --check src/index.js", signatures=["function main()"]),
    ]

    assert harden_task_boundary_steps(steps) is steps
