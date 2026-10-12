"""Approved test commands retain coverage from their own semantic rows."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
)
from polaris.cells.chief_engineer.blueprint.public.tests.test_public_contractsa import (
    _command_authority,
    _cross_task_behavior_blueprint,
    _portfolio_command_authority,
)


def _serial_fixture(tmp_path: Path, *, extra_tests: tuple[VerificationCommandAuthorityV1, ...] = ()):
    tasks = (
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-A",
            objective="Implement source and its tests before QA",
            target_files=("src/main.py", "tests/test_main.py"),
            entrypoint_targets=("src/main.py",),
        ),
        ChiefEngineerPortfolioTaskV1(
            task_id="TASK-B",
            objective="Write independent QA smoke and audit",
            target_files=("tests/qa_smoke.py", "docs/audit.md"),
            dependencies=("TASK-A",),
        ),
    )
    impl = _command_authority("TASK-A", "test", ("pytest", "-q", "tests/test_main.py"))
    qa = _command_authority("TASK-B", "test", ("pytest", "-q", "tests/qa_smoke.py"))
    authorities = (
        _command_authority("TASK-A", "environment_prep", ("python", "-m", "pip", "install", "-e", ".")),
        _command_authority("TASK-A", "entrypoint", ("python", "-m", "src.main")),
        impl,
        qa,
        *extra_tests,
    )
    blueprint = deepcopy(_cross_task_behavior_blueprint(include_invariant=False))
    obligations = blueprint["project_completion_contract"]["obligations"]
    obligations["artifacts"][1]["owner_task_id"] = "TASK-A"
    obligations["artifacts"].extend(
        [
            {
                "obligation_id": "artifact-qa-smoke",
                "path": "tests/qa_smoke.py",
                "semantic_role": "test",
                "applicability": "required",
                "owner_task_id": "TASK-B",
            },
            {
                "obligation_id": "artifact-audit",
                "path": "docs/audit.md",
                "semantic_role": "docs",
                "applicability": "required",
                "owner_task_id": "TASK-B",
            },
        ]
    )
    test_row = next(row for row in obligations["verification"] if row["modality"] == "test")
    test_row.update(
        owner_task_id="TASK-A",
        command_authority_hash=impl.authority_hash,
        covers_obligation_ids=["artifact-main", "artifact-tests"],
    )
    obligations["verification"].append(
        {
            "obligation_id": "verify-qa",
            "modality": "test",
            "command_authority_hash": qa.authority_hash,
            "applicability": "required",
            "covers_obligation_ids": ["artifact-audit", "artifact-qa-smoke"],
            "owner_task_id": "TASK-B",
        }
    )
    for index, authority in enumerate(extra_tests):
        if authority.authority_hash != impl.authority_hash:
            obligations["verification"].append(
                {
                    "obligation_id": f"verify-extra-{index}",
                    "modality": "test",
                    "command_authority_hash": authority.authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": ["artifact-tests"],
                    "owner_task_id": "TASK-A",
                }
            )
    authority = _portfolio_command_authority(
        tasks=tasks,
        workspace=tmp_path,
        command_authority_override=authorities,
    )
    command = BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id="run-portfolio-1",
        tasks=tasks,
        llm_blueprint=blueprint,
        **authority,
    )
    return command, impl, qa


def test_serial_implementation_test_does_not_acquire_future_qa_coverage(tmp_path: Path) -> None:
    command, impl, qa = _serial_fixture(tmp_path)
    portfolio = build_chief_engineer_blueprint_portfolio(command)
    completion = portfolio.project_completion_contract
    assert completion is not None
    rows = {row.command_authority_hash: row for row in completion.obligations.verification if row.modality == "test"}
    assert rows[impl.authority_hash].owner_task_id == "TASK-A"
    assert rows[impl.authority_hash].covers_obligation_ids == ("artifact-main", "artifact-tests")
    assert rows[qa.authority_hash].owner_task_id == "TASK-B"
    assert rows[qa.authority_hash].covers_obligation_ids == ("artifact-audit", "artifact-qa-smoke")


@pytest.mark.parametrize("different_field", ["argv", "cwd", "duplicate"])
def test_distinct_command_identity_keeps_coverage_separate(tmp_path: Path, different_field: str) -> None:
    extra = VerificationCommandAuthorityV1(
        task_id="TASK-A",
        modality="test",
        argv=("pytest", "-q", "tests/test_main.py", "--maxfail=1")
        if different_field == "argv"
        else ("pytest", "-q", "tests/test_main.py"),
        cwd="subproject" if different_field == "cwd" else ".",
    )
    command, impl, qa = _serial_fixture(tmp_path, extra_tests=(extra,))
    completion = build_chief_engineer_blueprint_portfolio(command).project_completion_contract
    assert completion is not None
    rows = [row for row in completion.obligations.verification if row.modality == "test"]
    assert len(rows) == (2 if different_field == "duplicate" else 3)
    assert {row.command_authority_hash for row in rows} == {
        impl.authority_hash,
        qa.authority_hash,
        extra.authority_hash,
    }
    if different_field != "duplicate":
        extra_row = next(row for row in rows if row.command_authority_hash == extra.authority_hash)
        assert extra_row.owner_task_id == "TASK-A"
        assert extra_row.covers_obligation_ids == ("artifact-tests",)


def test_explicit_future_coverage_still_requires_dependency_complete_authority(tmp_path: Path) -> None:
    command, _, _ = _serial_fixture(tmp_path)
    row = next(
        row
        for row in command.llm_blueprint["project_completion_contract"]["obligations"]["verification"]
        if row["obligation_id"] == "verify-test"
    )
    row["covers_obligation_ids"].append("artifact-qa-smoke")
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="dependency-complete verifier owner"):
        build_chief_engineer_blueprint_portfolio(command)


def test_commandless_advisory_rows_bind_locally_to_existing_owner_commands(tmp_path: Path) -> None:
    extra = _command_authority("TASK-A", "test", ("pytest", "-q", "tests/test_main.py", "--maxfail=1"))
    command, impl, _ = _serial_fixture(tmp_path, extra_tests=(extra,))
    for row in command.llm_blueprint["project_completion_contract"]["obligations"]["verification"]:
        if row["modality"] == "test" and row["owner_task_id"] == "TASK-A":
            row["command_authority_hash"] = None
    completion = build_chief_engineer_blueprint_portfolio(command).project_completion_contract
    assert completion is not None
    rows = [
        row for row in completion.obligations.verification if row.modality == "test" and row.owner_task_id == "TASK-A"
    ]
    assert {row.command_authority_hash for row in rows} == {impl.authority_hash, extra.authority_hash}
    assert sorted(row.covers_obligation_ids for row in rows) == [
        ("artifact-main", "artifact-tests"),
        ("artifact-tests",),
    ]


def test_duplicate_seed_rows_merge_only_within_their_exact_authority(tmp_path: Path) -> None:
    command, impl, qa = _serial_fixture(tmp_path)
    rows = command.llm_blueprint["project_completion_contract"]["obligations"]["verification"]
    impl_seed = next(row for row in rows if row["obligation_id"] == "verify-test")
    impl_seed["covers_obligation_ids"] = ["artifact-tests"]
    rows.append({**impl_seed, "obligation_id": "verify-test-same-command", "covers_obligation_ids": ["artifact-main"]})
    completion = build_chief_engineer_blueprint_portfolio(command).project_completion_contract
    assert completion is not None
    tests = [row for row in completion.obligations.verification if row.modality == "test"]
    assert len(tests) == 2
    assert next(row for row in tests if row.command_authority_hash == impl.authority_hash).covers_obligation_ids == (
        "artifact-main",
        "artifact-tests",
    )
    assert next(row for row in tests if row.command_authority_hash == qa.authority_hash).covers_obligation_ids == (
        "artifact-audit",
        "artifact-qa-smoke",
    )
