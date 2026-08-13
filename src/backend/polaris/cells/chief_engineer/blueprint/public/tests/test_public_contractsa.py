"""Unit tests for `chief_engineer/blueprint` public contracts."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import polaris.cells.chief_engineer.blueprint.public as blueprint_public
import polaris.cells.chief_engineer.blueprint.public.service as blueprint_service_module
import pytest
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    GetBlueprintStatusQueryV1,
    HandoffDecisionV1,
    ProjectKindAuthorityV1,
    QueryBlueprintProvenanceV1,
    QueryProjectCompletionContractV1,
    RegisterRiskCommandV1,
    RegisterTechDebtCommandV1,
    RiskRecordV1,
    RiskSeverity,
    RiskStatus,
    TaskBlueprintGeneratedEventV1,
    TaskBlueprintProvenanceSnapshotV1,
    TaskBlueprintResultV1,
    VerificationCommandAuthorityV1,
    _ChiefEngineerPortfolioAuthorityCarrierV1,
    _issue_chief_engineer_portfolio_authority_carrier,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)
from polaris.cells.chief_engineer.blueprint.public.service import (
    BlueprintPersistence,
    build_ce_handoff_decision,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
    get_blueprint_status,
    project_chief_engineer_task_blueprint,
    query_blueprint_provenance,
    query_project_completion_contract,
    validate_director_handoff_from_payload,
)
from polaris.cells.control_plane.run_ledger.public import stable_hash
from polaris.kernelone.quality.file_ownership_ledger import (
    FileOwnershipLedgerError,
    read_file_owners,
)
from polaris.kernelone.storage import resolve_storage_roots

_PM_CONTRACT_HASH = "a" * 64
_VERIFIER_POLICY_HASH = "b" * 64


def _command_authority(
    task_id: str,
    modality: str,
    argv: tuple[str, ...],
) -> VerificationCommandAuthorityV1:
    return VerificationCommandAuthorityV1(
        task_id=task_id,
        modality=modality,  # type: ignore[arg-type]
        argv=argv,
        cwd=".",
    )


def _library_completion_requirements(
    *artifact_paths: str,
    owner_task_ids: tuple[str, ...],
    test_path: str,
    test_owner_task_id: str,
) -> dict:
    assert len(owner_task_ids) == len(artifact_paths)
    artifact_rows = [
        {
            "obligation_id": f"artifact-{index}",
            "path": path,
            "semantic_role": "source",
            "applicability": "required",
            "owner_task_id": owner_task_ids[index - 1],
        }
        for index, path in enumerate(artifact_paths, start=1)
    ]
    test_artifact_id = "artifact-test"
    artifact_rows.append(
        {
            "obligation_id": test_artifact_id,
            "path": test_path,
            "semantic_role": "test",
            "applicability": "required",
            "owner_task_id": test_owner_task_id,
        }
    )
    return {
        "obligations": {
            "artifacts": artifact_rows,
            "entrypoints": [
                {
                    "obligation_id": "entrypoint-library-na",
                    "kind": "library",
                    "applicability": "not_applicable",
                    "owner_task_id": None,
                    "source_path": None,
                    "runtime_path": None,
                    "command": None,
                }
            ],
            "verification": [
                {
                    "obligation_id": "verify-build",
                    "modality": "build",
                    "command_authority_hash": _command_authority(
                        owner_task_ids[0], "build", ("python", "-m", "compileall", "src")
                    ).authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [artifact_rows[0]["obligation_id"]],
                    "owner_task_id": owner_task_ids[0],
                },
                {
                    "obligation_id": "verify-test",
                    "modality": "test",
                    "command_authority_hash": _command_authority(
                        test_owner_task_id, "test", ("pytest", "-q")
                    ).authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": [test_artifact_id],
                    "owner_task_id": test_owner_task_id,
                },
                {
                    "obligation_id": "verify-environment-na",
                    "modality": "environment_prep",
                    "command_authority_hash": None,
                    "applicability": "not_applicable",
                    "covers_obligation_ids": [],
                    "owner_task_id": None,
                },
            ],
        },
    }


def _application_completion_requirements() -> dict:
    return {
        "obligations": {
            "artifacts": [
                {
                    "obligation_id": "artifact-main",
                    "path": "src/main.py",
                    "semantic_role": "source",
                    "applicability": "required",
                    "owner_task_id": "TASK-A",
                },
                {
                    "obligation_id": "artifact-tests",
                    "path": "tests/test_main.py",
                    "semantic_role": "test",
                    "applicability": "required",
                    "owner_task_id": "TASK-B",
                },
            ],
            "entrypoints": [
                {
                    "obligation_id": "entrypoint-cli",
                    "kind": "cli",
                    "applicability": "required",
                    "owner_task_id": "TASK-A",
                    "source_path": "src/main.py",
                    "runtime_path": None,
                    "command": "python -m src.main",
                }
            ],
            "verification": [
                {
                    "obligation_id": "verify-environment",
                    "modality": "environment_prep",
                    "command_authority_hash": _command_authority(
                        "TASK-A", "environment_prep", ("python", "-m", "pip", "install", "-e", ".")
                    ).authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": ["artifact-main"],
                    "owner_task_id": "TASK-A",
                },
                {
                    "obligation_id": "verify-test",
                    "modality": "test",
                    "command_authority_hash": _command_authority("TASK-B", "test", ("pytest", "-q")).authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": ["artifact-tests"],
                    "owner_task_id": "TASK-B",
                },
                {
                    "obligation_id": "verify-entrypoint",
                    "modality": "entrypoint",
                    "command_authority_hash": _command_authority(
                        "TASK-A", "entrypoint", ("python", "-m", "src.main")
                    ).authority_hash,
                    "applicability": "required",
                    "covers_obligation_ids": ["entrypoint-cli"],
                    "owner_task_id": "TASK-A",
                },
            ],
        },
    }


def _portfolio_command_authority(
    *,
    tasks: tuple[ChiefEngineerPortfolioTaskV1, ...],
    project_kind: str = "application",
    workspace: Path | None = None,
    run_id: str = "run-portfolio-1",
    project_id: str = "project-portfolio",
    pm_contract_hash: str = _PM_CONTRACT_HASH,
    catalog_snapshot_override: dict[str, str] | None = None,
) -> dict:
    owner_task_ids = tuple(task.task_id for task in tasks)
    command_authority = tuple(
        _command_authority(task_id, modality, argv)
        for task_id in owner_task_ids
        for modality, argv in (
            ("environment_prep", ("python", "-m", "pip", "install", "-e", ".")),
            ("build", ("python", "-m", "compileall", "src")),
            ("test", ("pytest", "-q")),
            ("entrypoint", ("python", "-m", "src.main")),
        )
    )
    verifier_policy_snapshot = {
        "schema_version": "evidence_policy.v1",
        "source": "control_plane.verifier_policy.evidence_policy_compiler",
        "policy_hash": _VERIFIER_POLICY_HASH,
        "required_evidence_modalities": ["command"],
    }
    catalog_snapshot: dict[str, str] = dict(catalog_snapshot_override or {})
    if project_kind == "library" and catalog_snapshot_override is None:
        if workspace is None:
            raise AssertionError("library portfolio test must materialize its catalog snapshot")
        catalog_snapshot = {"project_kind": "library"}
        catalog_path = workspace / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog_snapshot), encoding="utf-8")
    elif catalog_snapshot_override is not None and workspace is not None:
        catalog_path = workspace / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog_snapshot), encoding="utf-8")
    carrier = _issue_chief_engineer_portfolio_authority_carrier(
        workspace=str(workspace or Path("/repo")),
        run_id=run_id,
        project_id=project_id,
        pm_stage_event_id=f"pm-stage-{run_id}",
        pm_contract_hash=pm_contract_hash,
        tasks=tasks,
        catalog_snapshot=catalog_snapshot,
        catalog_snapshot_hash=project_completion_catalog_snapshot_hash(catalog_snapshot),
        verifier_policy_hash=_VERIFIER_POLICY_HASH,
        verifier_policy_snapshot=verifier_policy_snapshot,
        verifier_policy_snapshot_hash=project_completion_verifier_policy_snapshot_hash(verifier_policy_snapshot),
        verification_command_authority=command_authority,
    )
    return {
        "authority_carrier": carrier,
    }


def _producer_v1_hashable(value):
    if isinstance(value, dict):
        return {
            str(key): _producer_v1_hashable(item)
            for key, item in value.items()
            if str(key) not in {"blueprint_hash", "capability_token", "job_token"}
        }
    if isinstance(value, (list, tuple)):
        return [_producer_v1_hashable(item) for item in value]
    return value


def _pm_task_payload() -> dict:
    return {
        "id": "TASK-1",
        "goal": "Implement the declared task contract.",
        "target_files": ["src/main.py", "tests/test_main.py"],
    }


def _blueprint_provenance_payload() -> dict:
    pm_task = _pm_task_payload()
    payload = {
        "schema_version": "chief_engineer.blueprint.v1",
        "blueprint_id": "ce_TASK-1_20260718010101000000",
        "task_id": "TASK-1",
        "run_id": "factory-run-1",
        "summary": "Implement the declared task contract.",
        "pm_task": pm_task,
        "pm_contract_hash": stable_hash(_producer_v1_hashable(pm_task)),
        "target_files": ["src/main.py", "tests/test_main.py"],
        "context": {
            "capability_token": {"token_id": "cap-1"},
            "nested": [{"job_token": {"token_id": "job-1"}, "kept": "yes"}],
        },
    }
    payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))
    return payload


def _blueprint_provenance_query(payload: dict | None = None) -> QueryBlueprintProvenanceV1:
    blueprint = payload or _blueprint_provenance_payload()
    blueprint_id = "ce_TASK-1_20260718010101000000"
    return QueryBlueprintProvenanceV1(
        blueprint=blueprint,
        expected_pm_task=_pm_task_payload(),
        expected_factory_run_id="factory-run-1",
        expected_task_id="TASK-1",
        expected_blueprint_id=blueprint_id,
        expected_logical_path=f"runtime/blueprints/{blueprint_id}.json",
    )


def _blueprint_provenance_snapshot_kwargs() -> dict:
    payload = _blueprint_provenance_payload()
    pm_contract_hash = stable_hash(_producer_v1_hashable(_pm_task_payload()))
    return {
        "logical_path": f"runtime/blueprints/{payload['blueprint_id']}.json",
        "factory_run_id": "factory-run-1",
        "task_id": "TASK-1",
        "blueprint_id": payload["blueprint_id"],
        "embedded_blueprint_hash": payload["blueprint_hash"],
        "recomputed_blueprint_hash": payload["blueprint_hash"],
        "matches": True,
        "pm_contract_hash": pm_contract_hash,
        "recomputed_pm_contract_hash": pm_contract_hash,
        "pm_task_canonical_hash": stable_hash(_pm_task_payload()),
        "target_files": ("src/main.py", "tests/test_main.py"),
    }




class TestGovernanceEnumFailClosed:
    """Tier-1 governance contracts must fail-closed on invalid enum input."""

    def test_invalid_severity_string_raises(self) -> None:
        with pytest.raises(ValueError):
            RiskRecordV1(
                risk_id="r1",
                task_id="t1",
                title="t",
                severity="apocalyptic",  # type: ignore[arg-type]
                owner="ce",
                mitigation="m",
                status=RiskStatus.OPEN,
                detected_at="2026-06-17T00:00:00Z",
            )

    def test_empty_severity_string_raises(self) -> None:
        # Fail-closed: an empty severity must NOT silently default to medium.
        with pytest.raises(ValueError):
            RegisterRiskCommandV1(
                task_id="t1",
                title="t",
                severity="",  # type: ignore[arg-type]
                owner="ce",
                mitigation="m",
                workspace="/repo",
            )

    def test_invalid_tech_debt_severity_raises(self) -> None:
        with pytest.raises(ValueError):
            RegisterTechDebtCommandV1(
                title="t",
                description="d",
                severity="nuclear",  # type: ignore[arg-type]
                surface="s",
                owner="ce",
                workspace="/repo",
            )

    def test_valid_severity_enum_passes(self) -> None:
        record = RiskRecordV1(
            risk_id="r1",
            task_id="t1",
            title="t",
            severity=RiskSeverity.BLOCKER,
            owner="ce",
            mitigation="m",
            status=RiskStatus.OPEN,
            detected_at="2026-06-17T00:00:00Z",
        )
        assert record.severity is RiskSeverity.BLOCKER


class TestHandoffDecisionContract:
    """Director-handoff decision contract invariants."""

    def test_negative_count_raises(self) -> None:
        with pytest.raises(ValueError):
            HandoffDecisionV1(
                allowed=False,
                blueprint_id="ce_x",
                blocker_count=-1,
                warning_count=0,
                open_blocker_risk_count=0,
            )

    def test_empty_blueprint_id_raises(self) -> None:
        with pytest.raises(ValueError):
            HandoffDecisionV1(
                allowed=True,
                blueprint_id="",
                blocker_count=0,
                warning_count=0,
                open_blocker_risk_count=0,
            )

    def test_to_dict_round_trips(self) -> None:
        decision = HandoffDecisionV1(
            allowed=False,
            blueprint_id="ce_x",
            blocker_count=2,
            warning_count=1,
            open_blocker_risk_count=1,
            task_id="t1",
            blockers=("a", "b"),
            reason="2 quality-gate blocker(s)",
            evaluated_at="2026-06-17T00:00:00Z",
        )
        data = decision.to_dict()
        assert data["allowed"] is False
        assert data["blocker_count"] == 2
        assert data["blockers"] == ["a", "b"]
        assert data["open_blocker_risk_count"] == 1

    def test_strict_ce_handoff_decision_binds_required_hashes(self, tmp_path) -> None:
        base_decision = HandoffDecisionV1(
            allowed=True,
            blueprint_id="ce_TASK-1",
            task_id="TASK-1",
            blocker_count=0,
            warning_count=0,
            open_blocker_risk_count=0,
            reason="handoff allowed",
            evaluated_at="2026-06-27T00:00:00Z",
        )
        blueprint = {
            "task_id": "TASK-1",
            "blueprint_id": "ce_TASK-1",
            "pm_contract_ref": "tasks/plan.json",
            "pm_contract_hash": "pm-contract-hash",
            "blueprint_ref": "runtime/blueprints/ce_TASK-1.json",
            "blueprint_hash": "blueprint-hash",
            "execution_profile_ref": "runtime/contracts/profile.json",
            "execution_profile_hash": "execution-profile-hash",
        }

        decision = build_ce_handoff_decision(
            str(tmp_path),
            blueprint=blueprint,
            blueprint_id="ce_TASK-1",
            task_id="TASK-1",
            base_decision=base_decision,
        )

        payload = decision.to_dict()
        assert decision.allowed is True
        assert payload["schema_version"] == "polaris.ce_handoff_decision.v1"
        assert payload["policy_version"] == "chief_engineer.handoff.v1"
        assert payload["bindings"] == {
            "pm_contract_ref": "tasks/plan.json",
            "pm_contract_hash": "pm-contract-hash",
            "blueprint_ref": "runtime/blueprints/ce_TASK-1.json",
            "blueprint_hash": "blueprint-hash",
            "execution_profile_ref": "runtime/contracts/profile.json",
            "execution_profile_hash": "execution-profile-hash",
        }
        assert payload["decision_hash"]

    def test_strict_ce_handoff_decision_fails_closed_without_required_bindings(self, tmp_path) -> None:
        base_decision = HandoffDecisionV1(
            allowed=True,
            blueprint_id="ce_TASK-2",
            task_id="TASK-2",
            blocker_count=0,
            warning_count=0,
            open_blocker_risk_count=0,
            reason="base gate allowed",
            evaluated_at="2026-06-27T00:00:00Z",
        )

        decision = build_ce_handoff_decision(
            str(tmp_path),
            blueprint={"task_id": "TASK-2", "blueprint_id": "ce_TASK-2"},
            blueprint_id="ce_TASK-2",
            task_id="TASK-2",
            base_decision=base_decision,
        )

        assert decision.allowed is False
        assert "missing required handoff binding: pm_contract_hash" in decision.blockers
        assert "missing required handoff binding: execution_profile_hash" in decision.blockers


class TestChiefEngineerBlueprintPortfolio:
    """Project-level CE advice must remain task-scoped and non-authoritative."""

    @staticmethod
    def _tasks() -> tuple[ChiefEngineerPortfolioTaskV1, ...]:
        return (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the shared provider and task A adapter",
                target_files=("src/shared.py", "src/a.py"),
                scope_paths=("src/a",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build the task B consumer",
                target_files=("src/shared.py", "src/b.py", "tests/test_portfolio.py"),
                scope_paths=("src/b", "tests/test_portfolio.py"),
                dependencies=("TASK-A",),
            ),
        )

    def test_entrypoint_targets_cannot_widen_pm_target_authority(self) -> None:
        with pytest.raises(ValueError, match="entrypoint_targets must be exact PM target_files"):
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build application",
                target_files=("src/main.py",),
                entrypoint_targets=("src/unowned.py",),
            )

    def test_public_command_rejects_caller_supplied_project_kind_authority(self, tmp_path: Path) -> None:
        forged = ProjectKindAuthorityV1(
            project_kind="library",
            source_ref="factory.committed_pm_catalog",
            source_hash="d" * 64,
            justification="caller_forged_library_exemption",
        )

        with pytest.raises(TypeError, match="project_kind_authority"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-forged-kind",
                tasks=self._tasks(),
                project_kind_authority=forged,  # type: ignore[call-arg]
            )

    def test_authority_carrier_issuer_is_not_a_public_cell_capability(self) -> None:
        assert not hasattr(blueprint_public, "_ChiefEngineerPortfolioAuthorityCarrierV1")
        assert not hasattr(blueprint_public, "_issue_chief_engineer_portfolio_authority_carrier")

    @pytest.mark.parametrize(
        "forged_field, forged_value",
        (
            ("project_id", "forged-project"),
            ("pm_contract_hash", "f" * 64),
            ("catalog_snapshot", {"project_kind": "library"}),
            ("catalog_snapshot_hash", "f" * 64),
            ("verifier_policy_hash", "f" * 64),
            ("verifier_policy_snapshot", {"policy_hash": "f" * 64}),
            ("verification_command_authority", ()),
        ),
    )
    def test_public_command_rejects_raw_authority_seed_fields(
        self,
        tmp_path: Path,
        forged_field: str,
        forged_value: object,
    ) -> None:
        kwargs = {
            "workspace": str(tmp_path),
            "run_id": "run-raw-authority-forge",
            "tasks": self._tasks(),
            forged_field: forged_value,
        }
        with pytest.raises(TypeError, match="unexpected keyword argument"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(**kwargs)  # type: ignore[arg-type]

    def test_authority_carrier_direct_construction_and_lookalike_fail_closed(self, tmp_path: Path) -> None:
        with pytest.raises(TypeError, match="owner-issued only"):
            _ChiefEngineerPortfolioAuthorityCarrierV1(
                _seal=object(),
                workspace=str(tmp_path),
                run_id="run-direct-forge",
                project_id="project-portfolio",
                pm_stage_event_id="pm-stage-direct-forge",
                pm_contract_hash=_PM_CONTRACT_HASH,
                tasks=self._tasks(),
                catalog_snapshot={},
                catalog_snapshot_hash=project_completion_catalog_snapshot_hash({}),
                verifier_policy_hash=_VERIFIER_POLICY_HASH,
                verifier_policy_snapshot={},
                verifier_policy_snapshot_hash="f" * 64,
                verification_command_authority=(),
            )

        class LookalikeCarrier:
            workspace = str(tmp_path)
            run_id = "run-direct-forge"
            tasks = self._tasks()

        with pytest.raises(TypeError, match="exact Factory-issued authority_carrier"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-direct-forge",
                tasks=self._tasks(),
                authority_carrier=LookalikeCarrier(),
                llm_blueprint={"project_completion_contract": _application_completion_requirements()},
            )

    def test_authority_carrier_cross_run_replay_fails_closed(self, tmp_path: Path) -> None:
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build application entrypoint",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build application tests",
                target_files=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        carrier = _portfolio_command_authority(tasks=tasks, workspace=tmp_path, run_id="run-owner-a")[
            "authority_carrier"
        ]
        with pytest.raises(ValueError, match="identity does not match"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-owner-b",
                tasks=tasks,
                authority_carrier=carrier,
                llm_blueprint={"project_completion_contract": _application_completion_requirements()},
            )

    def test_missing_catalog_defaults_to_owner_derived_application(self, tmp_path: Path) -> None:
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
                scope_paths=("src/main.py",),
                entrypoint_targets=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build the application tests",
                target_files=("tests/test_main.py",),
                scope_paths=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        portfolio_command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-missing-catalog",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, workspace=tmp_path, run_id="run-missing-catalog"),
            llm_blueprint={
                "construction_plan": {"implementation": ["Build the application"]},
                "project_completion_contract": _application_completion_requirements(),
            },
        )
        portfolio = build_chief_engineer_blueprint_portfolio(portfolio_command)

        completion = portfolio.project_completion_contract
        assert completion is not None
        assert completion.project_kind == "application"
        assert completion.project_kind_authority.justification == (
            "conservative_application_without_explicit_library_authority"
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as replay_error:
            build_chief_engineer_blueprint_portfolio(
                BuildChiefEngineerBlueprintPortfolioCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-missing-catalog",
                    tasks=tasks,
                    authority_carrier=portfolio_command.authority_carrier,
                    llm_blueprint=portfolio_command.llm_blueprint,
                )
            )
        assert replay_error.value.code == "project_completion_authority_replay"

    def test_unknown_model_hash_binds_unique_pm_owner_modality_authority(self, tmp_path: Path) -> None:
        """CE never owns opaque PM hashes; exact owner/modality resolves them safely."""

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
                scope_paths=("src/main.py",),
                entrypoint_targets=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build the application tests",
                target_files=("tests/test_main.py",),
                scope_paths=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        requirements = _application_completion_requirements()
        verification = requirements["obligations"]["verification"]
        verification[0]["command_authority_hash"] = "model-invented-opaque-hash"
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-bind-model-hash",
            tasks=tasks,
            **_portfolio_command_authority(
                tasks=tasks,
                workspace=tmp_path,
                run_id="run-bind-model-hash",
            ),
            llm_blueprint={
                "construction_plan": {"implementation": ["Build the application"]},
                "project_completion_contract": requirements,
            },
        )

        portfolio = build_chief_engineer_blueprint_portfolio(command)
        completion = portfolio.project_completion_contract
        assert completion is not None
        verifier = next(
            item for item in completion.obligations.verification if item.obligation_id == "verify-environment"
        )
        expected = _command_authority(
            "TASK-A",
            "environment_prep",
            ("python", "-m", "pip", "install", "-e", "."),
        )
        assert verifier.command_authority_hash == expected.authority_hash
        assert verifier.command == expected.command

    def test_completion_contract_composer_repairs_cross_owner_rows_and_drops_unexecutable_entrypoints(
        self,
        tmp_path: Path,
    ) -> None:
        """PM authority, not CE guesses, owns verifier commands and executable entrypoints."""

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
                scope_paths=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build an optional browser adapter",
                target_files=("src/web.py",),
                scope_paths=("src/web.py",),
                dependencies=("TASK-A",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-C",
                objective="Build the application tests",
                target_files=("tests/test_main.py",),
                scope_paths=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        requirements = _application_completion_requirements()
        obligations = requirements["obligations"]
        obligations["artifacts"][1]["owner_task_id"] = "TASK-C"
        obligations["artifacts"].append(
            {
                "obligation_id": "artifact-web",
                "path": "src/web.py",
                "semantic_role": "source",
                "applicability": "required",
                "owner_task_id": "TASK-B",
            }
        )
        obligations["entrypoints"].append(
            {
                "obligation_id": "entrypoint-web-advisory-only",
                "kind": "web",
                "applicability": "required",
                "owner_task_id": "TASK-B",
                "source_path": "src/web.py",
                "runtime_path": None,
                "command": None,
            }
        )
        obligations["verification"] = [
            row for row in obligations["verification"] if row["modality"] != "environment_prep"
        ]
        test_row = next(row for row in obligations["verification"] if row["modality"] == "test")
        test_row["owner_task_id"] = "TASK-A"
        test_row["command_authority_hash"] = None
        obligations["verification"].append(
            {
                "obligation_id": "verify-build",
                "modality": "build",
                "command_authority_hash": _command_authority(
                    "TASK-A", "build", ("python", "-m", "compileall", "src")
                ).authority_hash,
                "applicability": "required",
                "covers_obligation_ids": ["artifact-main", "entrypoint-web-advisory-only"],
                "owner_task_id": "TASK-A",
            }
        )

        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-normalize-completion-authority",
                tasks=tasks,
                **_portfolio_command_authority(
                    tasks=tasks,
                    workspace=tmp_path,
                    run_id="run-normalize-completion-authority",
                ),
                llm_blueprint={
                    "construction_plan": {"implementation": ["Build the application"]},
                    "project_completion_contract": requirements,
                },
            )
        )

        completion = portfolio.project_completion_contract
        assert completion is not None
        assert [item.obligation_id for item in completion.obligations.entrypoints] == ["entrypoint-cli"]
        build_verifier = next(item for item in completion.obligations.verification if item.modality == "build")
        assert "entrypoint-web-advisory-only" not in build_verifier.covers_obligation_ids
        test_verifier = next(item for item in completion.obligations.verification if item.modality == "test")
        assert test_verifier.owner_task_id == "TASK-C"
        assert (
            test_verifier.command_authority_hash
            == _command_authority("TASK-C", "test", ("pytest", "-q")).authority_hash
        )
        assert "artifact-tests" in test_verifier.covers_obligation_ids
        environment_verifier = next(
            item for item in completion.obligations.verification if item.modality == "environment_prep"
        )
        assert environment_verifier.applicability == "required"
        assert environment_verifier.command_authority_hash is not None

    def test_completion_contract_normalizes_sole_entrypoint_to_pm_command_authority(
        self,
        tmp_path: Path,
    ) -> None:
        """CE entrypoint semantics may vary, but executable command authority remains PM-owned."""

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
                scope_paths=("src/main.py",),
                entrypoint_targets=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build the application tests",
                target_files=("tests/test_main.py",),
                scope_paths=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        requirements = _application_completion_requirements()
        # Live L1-02 shape: the executable source is correctly declared as a
        # required source artifact, while the separate entrypoint obligation
        # binds that path to PM-owned command authority.  Artifact role wording
        # must not decide whether an otherwise exact entrypoint survives.
        assert requirements["obligations"]["artifacts"][0]["semantic_role"] == "source"
        entrypoint_row = requirements["obligations"]["entrypoints"][0]
        entrypoint_row["command"] = "python src/main.py --example model-wording"

        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-normalize-entrypoint-command",
                tasks=tasks,
                **_portfolio_command_authority(
                    tasks=tasks,
                    workspace=tmp_path,
                    run_id="run-normalize-entrypoint-command",
                ),
                llm_blueprint={
                    "construction_plan": {"implementation": ["Build the application"]},
                    "project_completion_contract": requirements,
                },
            )
        )

        completion = portfolio.project_completion_contract
        assert completion is not None
        assert len(completion.obligations.entrypoints) == 1
        assert completion.obligations.entrypoints[0].source_path == "src/main.py"
        assert completion.obligations.entrypoints[0].command == "python -m src.main"
        entrypoint_verifier = next(
            item for item in completion.obligations.verification if item.modality == "entrypoint"
        )
        expected = _command_authority("TASK-A", "entrypoint", ("python", "-m", "src.main"))
        assert entrypoint_verifier.command == expected.command
        assert entrypoint_verifier.command_authority_hash == expected.authority_hash

    def test_compiled_runtime_entrypoint_derives_from_pm_owned_source(self, tmp_path: Path) -> None:
        """Compiled output need not be a PM source target when source and command authority are exact."""

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
                scope_paths=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build application tests",
                target_files=("tests/test_main.py",),
                scope_paths=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        requirements = _application_completion_requirements()
        requirements["obligations"]["entrypoints"][0]["runtime_path"] = "dist/main.js"
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-derived-runtime-entrypoint",
                tasks=tasks,
                **_portfolio_command_authority(
                    tasks=tasks,
                    workspace=tmp_path,
                    run_id="run-derived-runtime-entrypoint",
                ),
                llm_blueprint={
                    "construction_plan": {"implementation": ["Build the application"]},
                    "project_completion_contract": requirements,
                },
            )
        )

        completion = portfolio.project_completion_contract
        assert completion is not None
        assert completion.obligations.entrypoints[0].source_path == "src/main.py"
        assert completion.obligations.entrypoints[0].runtime_path == "dist/main.js"

    def test_runtime_only_entrypoint_outside_pm_scope_fails_closed(self, tmp_path: Path) -> None:
        """A runtime locator cannot create authority without a PM-owned source locator."""

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build the application entrypoint",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build application tests",
                target_files=("tests/test_main.py",),
            ),
        )
        requirements = _application_completion_requirements()
        entrypoint = requirements["obligations"]["entrypoints"][0]
        entrypoint["source_path"] = None
        entrypoint["runtime_path"] = "dist/main.js"

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(
                BuildChiefEngineerBlueprintPortfolioCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-runtime-only-entrypoint",
                    tasks=tasks,
                    **_portfolio_command_authority(
                        tasks=tasks,
                        workspace=tmp_path,
                        run_id="run-runtime-only-entrypoint",
                    ),
                    llm_blueprint={
                        "construction_plan": {"implementation": ["Build the application"]},
                        "project_completion_contract": requirements,
                    },
                )
            )

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_library_authority_is_bound_to_run_pm_and_catalog_snapshot(self, tmp_path: Path) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-LIB",
            objective="Build the library",
            target_files=("src/lib.py", "tests/test_lib.py"),
            scope_paths=("src/lib.py", "tests/test_lib.py"),
        )

        def build(
            workspace: Path,
            *,
            project_id: str = "project-portfolio",
            run_id: str,
            pm_hash: str,
        ) -> ProjectKindAuthorityV1:
            authority = _portfolio_command_authority(
                tasks=(task,),
                project_kind="library",
                workspace=workspace,
                run_id=run_id,
                project_id=project_id,
                pm_contract_hash=pm_hash,
            )
            portfolio = build_chief_engineer_blueprint_portfolio(
                BuildChiefEngineerBlueprintPortfolioCommandV1(
                    workspace=str(workspace),
                    run_id=run_id,
                    tasks=(task,),
                    **authority,
                    llm_blueprint={
                        "construction_plan": {"implementation": ["Build the library"]},
                        "project_completion_contract": _library_completion_requirements(
                            "src/lib.py",
                            owner_task_ids=("TASK-LIB",),
                            test_path="tests/test_lib.py",
                            test_owner_task_id="TASK-LIB",
                        ),
                    },
                )
            )
            assert portfolio.project_completion_contract is not None
            return portfolio.project_completion_contract.project_kind_authority

        first = build(tmp_path / "first", run_id="run-library-a", pm_hash="a" * 64)
        second = build(tmp_path / "second", run_id="run-library-b", pm_hash="a" * 64)
        third = build(tmp_path / "third", run_id="run-library-a", pm_hash="c" * 64)
        fourth = build(
            tmp_path / "fourth",
            project_id="project-portfolio-other",
            run_id="run-library-a",
            pm_hash="a" * 64,
        )

        assert first.project_kind == "library"
        assert first.source_ref == "chief_engineer.committed_pm_catalog_snapshot"
        assert len({first.source_hash, second.source_hash, third.source_hash, fourth.source_hash}) == 4

    def test_live_catalog_drift_fails_before_contract_persistence(self, tmp_path: Path) -> None:
        authority = _portfolio_command_authority(
            tasks=self._tasks(), project_kind="library", workspace=tmp_path, run_id="run-catalog-drift"
        )
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.write_text(json.dumps({"project_kind": "application"}), encoding="utf-8")

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(
                BuildChiefEngineerBlueprintPortfolioCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-catalog-drift",
                    tasks=self._tasks(),
                    **authority,
                    llm_blueprint={
                        "construction_plan": {"implementation": ["Build the library"]},
                        "project_completion_contract": _library_completion_requirements(
                            "src/shared.py",
                            "src/a.py",
                            "src/b.py",
                            owner_task_ids=("TASK-A", "TASK-A", "TASK-B"),
                            test_path="tests/test_portfolio.py",
                            test_owner_task_id="TASK-B",
                        ),
                    },
                )
            )

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert "catalog drifted" in str(exc_info.value)

    def test_tampered_owner_receipt_fails_before_contract_persistence(self, tmp_path: Path) -> None:
        tasks = self._tasks()
        carrier = _portfolio_command_authority(tasks=tasks, workspace=tmp_path, run_id="run-receipt-tamper")[
            "authority_carrier"
        ]
        object.__setattr__(carrier, "catalog_receipt_hash", "f" * 64)
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-receipt-tamper",
            tasks=tasks,
            authority_carrier=carrier,
            llm_blueprint={
                "construction_plan": {"implementation": ["Build application"]},
                "project_completion_contract": _application_completion_requirements(),
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "invalid_project_completion_authority_carrier"
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_catalog_toctou_after_contract_build_fails_before_persistence(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Build application entrypoint",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Build application tests",
                target_files=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        catalog_snapshot = {"project_id": "project-portfolio", "project_kind": "application"}
        authority = _portfolio_command_authority(
            tasks=tasks,
            workspace=tmp_path,
            run_id="run-catalog-toctou",
            catalog_snapshot_override=catalog_snapshot,
        )
        original_build = blueprint_service_module._build_portfolio_completion_contract

        def _build_then_drift(command, requirements):
            contract = original_build(command, requirements)
            catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
            catalog_path.write_text(
                json.dumps({"project_id": "project-portfolio", "project_kind": "library"}),
                encoding="utf-8",
            )
            return contract

        monkeypatch.setattr(blueprint_service_module, "_build_portfolio_completion_contract", _build_then_drift)
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-catalog-toctou",
            tasks=tasks,
            **authority,
            llm_blueprint={
                "construction_plan": {"implementation": ["Build application"]},
                "project_completion_contract": _application_completion_requirements(),
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1, match="catalog drifted"):
            build_chief_engineer_blueprint_portfolio(command)

        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_catalog_project_retag_is_rejected(self, tmp_path: Path) -> None:
        catalog_snapshot = {"project_id": "other-project", "project_kind": "library"}
        catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
        catalog_path.parent.mkdir(parents=True, exist_ok=True)
        catalog_path.write_text(json.dumps(catalog_snapshot), encoding="utf-8")
        authority = _portfolio_command_authority(
            tasks=self._tasks(),
            workspace=tmp_path,
            run_id="run-project-retag",
            catalog_snapshot_override=catalog_snapshot,
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(
                BuildChiefEngineerBlueprintPortfolioCommandV1(
                    workspace=str(tmp_path),
                    run_id="run-project-retag",
                    tasks=self._tasks(),
                    **authority,
                    llm_blueprint={
                        "construction_plan": {"implementation": ["Build the library"]},
                        "project_completion_contract": _application_completion_requirements(),
                    },
                )
            )

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert "project_id" in str(exc_info.value)

    def test_builds_shared_task_overlays_and_interface_bindings(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-portfolio-1",
            tasks=self._tasks(),
            **_portfolio_command_authority(
                tasks=self._tasks(), project_kind="library", workspace=tmp_path, run_id="run-portfolio-1"
            ),
            llm_blueprint={
                "construction_plan": {
                    "preparation": ["Inspect the shared interface"],
                    "nested": {"shared": True, "owner": "shared"},
                    "files": [
                        "src/shared.py",
                        "src/a.py",
                        "src/b.py",
                        "../plan_escape.py",
                    ],
                    "project_interface_contract": {
                        "provider_declarations": [{"task_id": "TASK-A", "interface": "SharedProvider"}],
                        "consumer_declarations": [{"task_id": "TASK-B", "interface": "SharedProvider"}],
                    },
                    "task_plans": {
                        "TASK-A": {
                            "implementation": ["Implement provider adapter"],
                            "nested": {"owner": "task-a"},
                            "scope_for_apply": [
                                "src/a.py",
                                "../scope_escape.py",
                                "/tmp/absolute.py",
                            ],
                            "risk_flags": [
                                {
                                    "severity": "medium",
                                    "title": "Task A ordering",
                                    "mitigation": "Serialize updates",
                                }
                            ],
                        },
                        "TASK-B": {
                            "implementation": ["Implement consumer adapter"],
                            "scope_for_apply": ["src/b.py"],
                        },
                    },
                },
                "scope_for_apply": [
                    "src/shared.py",
                    "src/a.py",
                    "src/b.py",
                    "src/outside.py",
                ],
                "risk_flags": [
                    {
                        "severity": "HIGH",
                        "title": "Shared API drift",
                        "mitigation": "Pin interface",
                    }
                ],
                "project_completion_contract": _library_completion_requirements(
                    "src/shared.py",
                    "src/a.py",
                    "src/b.py",
                    owner_task_ids=("TASK-A", "TASK-A", "TASK-B"),
                    test_path="tests/test_portfolio.py",
                    test_owner_task_id="TASK-B",
                ),
            },
        )

        portfolio = build_chief_engineer_blueprint_portfolio(command)

        assert portfolio.llm_blueprint_consumed is True
        assert portfolio.usage_mode == "advisory_overlay"
        assert portfolio.authority == "advisory_only"
        assert portfolio.handoff_ready is False
        assert portfolio.execution_authorized is False
        assert portfolio.task_ids == ("TASK-A", "TASK-B")
        interface = portfolio.project_interface_contract
        assert interface.task_file_ownership == {
            "TASK-A": ("src/shared.py", "src/a.py"),
            "TASK-B": ("src/shared.py", "src/b.py", "tests/test_portfolio.py"),
        }
        assert interface.file_task_ownership["src/shared.py"] == ("TASK-A", "TASK-B")
        assert interface.provider_declarations == ({"task_id": "TASK-A", "interface": "SharedProvider"},)
        assert interface.consumer_declarations == ({"task_id": "TASK-B", "interface": "SharedProvider"},)
        assert portfolio.project_interface_contract_hash == interface.contract_hash
        assert portfolio.project_interface_contract_ref == interface.contract_ref
        completion = portfolio.project_completion_contract
        assert completion is not None
        assert completion.covered_task_ids == ("TASK-A", "TASK-B")
        assert completion.pm_contract_hash == _PM_CONTRACT_HASH
        assert completion.verifier_policy_hash == _VERIFIER_POLICY_HASH
        assert portfolio.project_completion_contract_hash == completion.contract_hash
        assert portfolio.project_completion_contract_ref == (f"{portfolio.portfolio_path}#project_completion_contract")

        for task_id in portfolio.task_ids:
            overlay = portfolio.task_overlays[task_id]
            assert overlay["portfolio_hash"] == portfolio.portfolio_hash
            assert overlay["project_interface_contract_hash"] == interface.contract_hash
            assert overlay["project_interface_contract_ref"] == interface.contract_ref
            assert overlay["project_completion_contract_hash"] == completion.contract_hash
            assert overlay["project_completion_contract_ref"] == portfolio.project_completion_contract_ref
            assert overlay["reference"]["portfolio_hash"] == portfolio.portfolio_hash
            assert overlay["reference"]["project_interface_contract_hash"] == interface.contract_hash
            assert overlay["reference"]["project_completion_contract_hash"] == completion.contract_hash
            assert overlay["handoff_ready"] is False
            assert overlay["execution_authorized"] is False

        task_a = project_chief_engineer_task_blueprint(portfolio, "TASK-A")
        assert set(task_a) == {"construction_plan", "scope_for_apply", "risk_flags"}
        assert "task_plans" not in task_a["construction_plan"]
        assert task_a["construction_plan"]["preparation"] == ["Inspect the shared interface"]
        assert task_a["construction_plan"]["implementation"] == ["Implement provider adapter"]
        assert task_a["construction_plan"]["nested"] == {"shared": True, "owner": "task-a"}
        assert task_a["scope_for_apply"] == ["src/shared.py", "src/a.py"]
        assert task_a["risk_flags"] == [
            "[high] Shared API drift (mitigation: Pin interface)",
            "[medium] Task A ordering (mitigation: Serialize updates)",
        ]

        task_a_advisory = portfolio.scope_advisory["TASK-A"]
        rejected_paths = {item["path"] for item in task_a_advisory["rejected_suggestions"]}
        assert rejected_paths >= {
            "src/b.py",
            "src/outside.py",
            "../scope_escape.py",
            "/tmp/absolute.py",
        }
        assert task_a_advisory["construction_plan_paths_outside_pm_authority"] == ["src/b.py"]
        assert task_a_advisory["construction_plan_rejected_paths"] == [
            {
                "path": "../plan_escape.py",
                "reason": "parent_traversal_not_allowed",
                "source": "construction_plan",
            }
        ]

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(portfolio.portfolio_id)
        assert persisted == portfolio.to_dict()
        assert persisted["project_interface_contract_hash"] == interface.contract_hash

        loaded_contract = query_project_completion_contract(
            QueryProjectCompletionContractV1(
                workspace=str(tmp_path),
                project_id="project-portfolio",
                run_id="run-portfolio-1",
                contract_hash=completion.contract_hash,
            )
        )
        assert loaded_contract == completion

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_project_completion_contract(
                QueryProjectCompletionContractV1(
                    workspace=str(tmp_path),
                    project_id="project-portfolio",
                    run_id="run-portfolio-1",
                    contract_hash="f" * 64,
                )
            )
        assert exc_info.value.code == "project_completion_contract_not_found"

    def test_generate_task_blueprint_projects_portfolio_evidence_to_top_level(
        self,
        tmp_path: Path,
    ) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-PROJECTION",
            objective="Build project interface audit projection",
            target_files=("src/interface_projection.py", "tests/test_interface_projection.py"),
            scope_paths=("src/interface_projection.py", "tests/test_interface_projection.py"),
        )
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-projection",
                tasks=(task,),
                **_portfolio_command_authority(
                    tasks=(task,), project_kind="library", workspace=tmp_path, run_id="run-projection"
                ),
                llm_blueprint={
                    "construction_plan": {
                        "implementation": ["Project shared interface evidence"],
                        "project_interface_contract": {
                            "provider_declarations": [],
                            "consumer_declarations": [],
                        },
                    },
                    "scope_for_apply": ["src/interface_projection.py"],
                    "risk_flags": [],
                    "project_completion_contract": _library_completion_requirements(
                        "src/interface_projection.py",
                        owner_task_ids=("TASK-PROJECTION",),
                        test_path="tests/test_interface_projection.py",
                        test_owner_task_id="TASK-PROJECTION",
                    ),
                },
            )
        )
        interface_payload = portfolio.project_interface_contract.to_dict()
        context = {
            "task_title": "Project interface audit projection",
            "target_files": ["src/interface_projection.py", "tests/test_interface_projection.py"],
            "scope_paths": ["src/interface_projection.py", "tests/test_interface_projection.py"],
            "acceptance_criteria": ["Portfolio evidence is directly auditable"],
            "execution_checklist": ["Persist top-level portfolio evidence"],
            "delivery_plan_document": {
                "schema_version": "polaris.delivery_plan_document.v1",
                "title": "Project interface audit projection",
                "user_journey": ["Generate portfolio", "Inspect task blueprint evidence"],
            },
            "delivery_depth_contract": {
                "schema_version": "polaris.delivery_depth_contract.v1",
                "behavior_contract": {"rule_matrix": ["Portfolio interface evidence remains directly auditable"]},
            },
            "task": task.to_dict(),
            **portfolio.to_task_blueprint_context(),
        }

        result = generate_task_blueprint(
            GenerateTaskBlueprintCommandV1(
                task_id=task.task_id,
                workspace=str(tmp_path),
                objective=task.objective,
                run_id="run-projection",
                context=context,
                llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
            )
        )

        persisted = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id or "")
        assert isinstance(persisted, dict)
        assert persisted["blueprint_portfolio_ref"] == portfolio.portfolio_path
        assert persisted["blueprint_portfolio_hash"] == portfolio.portfolio_hash
        assert persisted["project_interface_contract_ref"] == portfolio.project_interface_contract_ref
        assert persisted["project_interface_contract_hash"] == portfolio.project_interface_contract_hash
        assert persisted["project_interface_contract"] == interface_payload
        assert persisted["context"]["project_interface_contract"] == interface_payload
        assert persisted["project_completion_contract"] == portfolio.project_completion_contract.to_dict()
        assert persisted["project_completion_contract_ref"] == portfolio.project_completion_contract_ref
        assert persisted["project_completion_contract_hash"] == portfolio.project_completion_contract_hash
        assert persisted["context"]["project_completion_contract"] == (portfolio.project_completion_contract.to_dict())
        assert persisted["target_files"] == ["src/interface_projection.py", "tests/test_interface_projection.py"]
        assert persisted["scope_paths"] == ["src/interface_projection.py", "tests/test_interface_projection.py"]
        assert "portfolio_path" not in persisted
        job_token = persisted["job_token"]
        assert job_token["token_id"].startswith("job-")
        assert job_token["run_id"] == "run-projection"
        assert job_token["project_id"] == portfolio.project_completion_contract.project_id
        assert job_token["stage"] == "pending_exec"
        assert job_token["target_files"] == ["src/interface_projection.py", "tests/test_interface_projection.py"]
        assert job_token["allowed_write_paths"] == [
            "src/interface_projection.py",
            "tests/test_interface_projection.py",
        ]
        assert job_token["capability_audit"] == {"ok": True, "issues": []}
        assert job_token["blueprint_hash"] == persisted["blueprint_hash"]
        assert persisted["capability_token"] == job_token

        handoff = validate_director_handoff_from_payload(
            str(tmp_path),
            {"task_id": task.task_id, "blueprint_id": result.blueprint_id},
            require_strict=True,
        )
        assert handoff["allowed"] is True, handoff["reason"]
        assert handoff["job_token"] == job_token
        assert handoff["capability_token"] == job_token
        projection = handoff["task_completion_projection"]
        assert projection["schema_version"] == "polaris.task_completion_projection.v1"
        assert projection["task_id"] == task.task_id
        assert projection["project_contract_hash"] == portfolio.project_completion_contract_hash
        assert projection["project_contract_ref"] == portfolio.project_completion_contract_ref
        assert projection["owned_artifacts"] == [
            item.to_dict() for item in portfolio.project_completion_contract.obligations.artifacts
        ]
        assert projection["verification_execution_authority"]
        for row in projection["verification_execution_authority"]:
            verifier = next(
                item for item in projection["owned_verification"] if item["obligation_id"] == row["obligation_id"]
            )
            assert row["command_authority_hash"] == verifier["command_authority_hash"]
            assert row["owner_task_id"] == projection["task_id"]
        assert "obligations" not in projection
        assert projection["projection_hash"] == stable_hash(
            {key: value for key, value in projection.items() if key != "projection_hash"}
        )

    def test_task_completion_projection_contains_only_owned_and_referenced_artifacts(
        self,
        tmp_path: Path,
    ) -> None:
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Implement application source",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Implement application tests",
                target_files=("tests/test_main.py",),
                dependencies=("TASK-A",),
            ),
        )
        requirements = _application_completion_requirements()
        requirements["obligations"]["verification"][1]["covers_obligation_ids"] = [
            "artifact-main",
            "artifact-tests",
        ]
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-task-projection",
                tasks=tasks,
                **_portfolio_command_authority(
                    tasks=tasks,
                    workspace=tmp_path,
                    run_id="run-task-projection",
                ),
                llm_blueprint={
                    "construction_plan": {
                        "implementation": ["Implement source and tests"],
                        "project_interface_contract": {
                            "provider_declarations": [],
                            "consumer_declarations": [],
                        },
                    },
                    "scope_for_apply": ["src/main.py", "tests/test_main.py"],
                    "risk_flags": [],
                    "project_completion_contract": requirements,
                },
            )
        )
        contract = portfolio.project_completion_contract
        assert contract is not None
        projection = blueprint_service_module._project_task_completion_contract(
            {
                "project_completion_contract": contract.to_dict(),
                "project_completion_contract_ref": portfolio.project_completion_contract_ref,
                "project_completion_contract_hash": portfolio.project_completion_contract_hash,
            },
            task_id="TASK-B",
        )

        assert [item["obligation_id"] for item in projection["owned_artifacts"]] == ["artifact-tests"]
        assert [item["obligation_id"] for item in projection["dependency_artifacts"]] == ["artifact-main"]
        assert [item["obligation_id"] for item in projection["owned_verification"]] == ["verify-test"]
        assert {item["task_id"] for item in projection["verification_command_authority"]} == {"TASK-B"}
        assert "project_completion_contract" not in projection

    def test_task_contract_normalizes_duplicates_and_rejects_unsafe_paths(self) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-1",
            objective="Build one module",
            target_files=("./src/module.py", "src/module.py"),
            scope_paths=("src", "src"),
            dependencies=("TASK-0", "TASK-0"),
        )

        assert task.target_files == ("src/module.py",)
        assert task.scope_paths == ("src",)
        assert task.dependencies == ("TASK-0",)

        with pytest.raises(ValueError, match="workspace-relative"):
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-ABS",
                objective="Reject absolute path",
                target_files=("/tmp/outside.py",),
            )
        with pytest.raises(ValueError, match="parent traversal"):
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-DOTDOT",
                objective="Reject traversal",
                target_files=("src/module.py",),
                scope_paths=("../outside",),
            )

    def test_command_rejects_duplicate_task_ids(self) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-DUP",
            objective="Build duplicate detector",
            target_files=("src/duplicate.py",),
        )

        with pytest.raises(ValueError, match="duplicate task_id"):
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace="/repo",
                run_id="run-duplicate",
                tasks=(task, task),
            )

    @pytest.mark.parametrize(
        "attack",
        ("artifact_wrong_owner", "artifact_outside_pm_scope", "entrypoint_wrong_owner"),
    )
    def test_completion_paths_and_owners_must_match_exact_pm_task_authority(
        self,
        tmp_path: Path,
        attack: str,
    ) -> None:
        requirements = _application_completion_requirements()
        artifacts = requirements["obligations"]["artifacts"]
        entrypoints = requirements["obligations"]["entrypoints"]
        if attack == "artifact_wrong_owner":
            artifacts[0]["owner_task_id"] = "TASK-B"
        elif attack == "artifact_outside_pm_scope":
            artifacts.append(
                {
                    "obligation_id": "artifact-rogue",
                    "path": "src/rogue.py",
                    "semantic_role": "source",
                    "applicability": "required",
                    "owner_task_id": "TASK-A",
                }
            )
        else:
            entrypoints[0]["owner_task_id"] = "TASK-B"

        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Implement application source",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Implement application tests",
                target_files=("tests/test_main.py",),
            ),
        )
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-pm-path-owner-attack",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, workspace=tmp_path, run_id="run-pm-path-owner-attack"),
            llm_blueprint={
                "construction_plan": {
                    "task_plans": {
                        "TASK-A": {"implementation": ["Implement source"]},
                        "TASK-B": {"implementation": ["Implement tests"]},
                    }
                },
                "risk_flags": [],
                "project_completion_contract": requirements,
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    @pytest.mark.parametrize(
        "argv",
        (("echo", "ok"), ("python", "--version")),
    )
    def test_completion_verifier_cannot_substitute_fake_semantic_command(
        self,
        tmp_path: Path,
        argv: tuple[str, ...],
    ) -> None:
        with pytest.raises(ValueError, match="proof-of-work"):
            _command_authority("TASK-A", "environment_prep", argv)
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    @pytest.mark.parametrize(
        ("generated_path", "allowed"),
        (("generated/out.py", True), ("generated2/out.py", False)),
    )
    def test_pm_scope_authority_uses_path_components_not_string_prefixes(
        self,
        tmp_path: Path,
        generated_path: str,
        allowed: bool,
    ) -> None:
        requirements = _library_completion_requirements(
            "pyproject.toml",
            generated_path,
            owner_task_ids=("TASK-A", "TASK-A"),
            test_path="tests/test_generated.py",
            test_owner_task_id="TASK-B",
        )
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Generate scoped source",
                target_files=("pyproject.toml",),
                scope_paths=("generated",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Verify generated source",
                target_files=("tests/test_generated.py",),
            ),
        )
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id=f"run-scope-components-{allowed}",
            tasks=tasks,
            **_portfolio_command_authority(
                tasks=tasks,
                project_kind="library",
                workspace=tmp_path,
                run_id=f"run-scope-components-{allowed}",
            ),
            llm_blueprint={
                "construction_plan": {
                    "task_plans": {
                        "TASK-A": {"implementation": ["Generate source"]},
                        "TASK-B": {"implementation": ["Verify source"]},
                    }
                },
                "risk_flags": [],
                "project_completion_contract": requirements,
            },
        )

        if allowed:
            assert build_chief_engineer_blueprint_portfolio(command).project_completion_contract
        else:
            with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
                build_chief_engineer_blueprint_portfolio(command)
            assert exc_info.value.code == "invalid_project_completion_contract"

    def test_unknown_llm_task_plan_fails_closed(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-unknown-plan",
            tasks=self._tasks(),
            **_portfolio_command_authority(
                tasks=self._tasks(), project_kind="library", workspace=tmp_path, run_id="run-unknown-plan"
            ),
            llm_blueprint={
                "construction_plan": {"task_plans": {"TASK-UNKNOWN": {"implementation": ["Do not run"]}}},
                "scope_for_apply": [],
                "risk_flags": [],
                "project_completion_contract": _library_completion_requirements(
                    "src/shared.py",
                    "src/a.py",
                    "src/b.py",
                    owner_task_ids=("TASK-A", "TASK-A", "TASK-B"),
                    test_path="tests/test_portfolio.py",
                    test_owner_task_id="TASK-B",
                ),
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "unknown_blueprint_portfolio_task_plan"
        assert exc_info.value.details["unknown_task_ids"] == ["TASK-UNKNOWN"]
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_advisory_portfolio_without_project_completion_contract_fails_closed(
        self,
        tmp_path: Path,
    ) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-missing-completion-contract",
            tasks=self._tasks(),
            **_portfolio_command_authority(
                tasks=self._tasks(), workspace=tmp_path, run_id="run-missing-completion-contract"
            ),
            llm_blueprint={
                "construction_plan": {
                    "task_plans": {
                        "TASK-A": {"implementation": ["Implement A"]},
                        "TASK-B": {"implementation": ["Implement B"]},
                    },
                    "project_interface_contract": {
                        "provider_declarations": [],
                        "consumer_declarations": [],
                    },
                },
                "risk_flags": [],
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == []

    def test_llm_cannot_supply_project_completion_authority_fields(self, tmp_path: Path) -> None:
        completion_requirements = _library_completion_requirements(
            "src/shared.py",
            "src/a.py",
            "src/b.py",
            owner_task_ids=("TASK-A", "TASK-A", "TASK-B"),
            test_path="tests/test_portfolio.py",
            test_owner_task_id="TASK-B",
        )
        completion_requirements["pm_contract_hash"] = "c" * 64
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-authority-injection",
            tasks=self._tasks(),
            **_portfolio_command_authority(
                tasks=self._tasks(), project_kind="library", workspace=tmp_path, run_id="run-authority-injection"
            ),
            llm_blueprint={
                "construction_plan": {
                    "task_plans": {
                        "TASK-A": {"implementation": ["Implement A"]},
                        "TASK-B": {"implementation": ["Implement B"]},
                    },
                    "project_interface_contract": {
                        "provider_declarations": [],
                        "consumer_declarations": [],
                    },
                },
                "risk_flags": [],
                "project_completion_contract": completion_requirements,
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert "pm_contract_hash" in exc_info.value.details["unknown_fields"]

    def test_provider_cannot_choose_or_downgrade_factory_project_kind(self, tmp_path: Path) -> None:
        completion_requirements = _application_completion_requirements()
        completion_requirements["project_kind"] = "library"
        tasks = (
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-A",
                objective="Implement runnable application source",
                target_files=("src/main.py",),
            ),
            ChiefEngineerPortfolioTaskV1(
                task_id="TASK-B",
                objective="Implement application tests",
                target_files=("tests/test_main.py",),
            ),
        )
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-project-kind-downgrade",
            tasks=tasks,
            **_portfolio_command_authority(tasks=tasks, workspace=tmp_path, run_id="run-project-kind-downgrade"),
            llm_blueprint={
                "construction_plan": {
                    "task_plans": {
                        "TASK-A": {"implementation": ["Implement source"]},
                        "TASK-B": {"implementation": ["Implement tests"]},
                    }
                },
                "risk_flags": [],
                "project_completion_contract": completion_requirements,
            },
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            build_chief_engineer_blueprint_portfolio(command)

        assert exc_info.value.code == "invalid_project_completion_contract"
        assert "project_kind" in exc_info.value.details["unknown_fields"]

    def test_project_completion_context_tamper_fails_before_task_blueprint_persistence(
        self,
        tmp_path: Path,
    ) -> None:
        task = ChiefEngineerPortfolioTaskV1(
            task_id="TASK-TAMPER",
            objective="Reject a tampered completion contract",
            target_files=("src/tamper.py", "tests/test_tamper.py"),
        )
        portfolio = build_chief_engineer_blueprint_portfolio(
            BuildChiefEngineerBlueprintPortfolioCommandV1(
                workspace=str(tmp_path),
                run_id="run-tamper",
                tasks=(task,),
                **_portfolio_command_authority(
                    tasks=(task,), project_kind="library", workspace=tmp_path, run_id="run-tamper"
                ),
                llm_blueprint={
                    "construction_plan": {
                        "task_plans": {"TASK-TAMPER": {"implementation": ["Implement"]}},
                        "project_interface_contract": {
                            "provider_declarations": [],
                            "consumer_declarations": [],
                        },
                    },
                    "risk_flags": [],
                    "project_completion_contract": _library_completion_requirements(
                        "src/tamper.py",
                        owner_task_ids=("TASK-TAMPER",),
                        test_path="tests/test_tamper.py",
                        test_owner_task_id="TASK-TAMPER",
                    ),
                },
            )
        )
        context = {
            "target_files": ["src/tamper.py", "tests/test_tamper.py"],
            **portfolio.to_task_blueprint_context(),
        }
        context["project_completion_contract_hash"] = "f" * 64

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            generate_task_blueprint(
                GenerateTaskBlueprintCommandV1(
                    task_id=task.task_id,
                    workspace=str(tmp_path),
                    objective=task.objective,
                    run_id="run-tamper",
                    context=context,
                    llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
                )
            )

        assert exc_info.value.code == "invalid_blueprint_portfolio_context"

    def test_no_llm_portfolio_is_stable_offline_diagnostic_only(self, tmp_path: Path) -> None:
        command = BuildChiefEngineerBlueprintPortfolioCommandV1(
            workspace=str(tmp_path),
            run_id="run-offline",
            tasks=(
                ChiefEngineerPortfolioTaskV1(
                    task_id="TASK-OFFLINE",
                    objective="Inspect a PM task without a CE LLM response",
                    target_files=("src/offline.py",),
                    scope_paths=("tests",),
                ),
            ),
        )

        first = build_chief_engineer_blueprint_portfolio(command)
        second = build_chief_engineer_blueprint_portfolio(command)

        assert second.portfolio_id == first.portfolio_id
        assert second.portfolio_hash == first.portfolio_hash
        assert second.reference == first.reference
        assert first.llm_blueprint_consumed is False
        assert first.usage_mode == "offline_diagnostic_only"
        assert first.handoff_ready is False
        assert first.execution_authorized is False
        assert first.project_interface_contract.provider_declarations == ()
        assert first.project_interface_contract.consumer_declarations == ()
        assert first.project_completion_contract is None
        assert first.project_completion_contract_ref is None
        assert first.project_completion_contract_hash is None
        overlay = first.task_overlays["TASK-OFFLINE"]
        assert overlay["portfolio_hash"] == first.portfolio_hash
        assert overlay["project_interface_contract_hash"] == first.project_interface_contract_hash
        assert overlay["llm_blueprint_consumed"] is False
        assert overlay["usage_mode"] == "offline_diagnostic_only"
        assert overlay["handoff_ready"] is False
        assert overlay["execution_authorized"] is False

        pending_payload = first.to_dict()
        pending_payload["portfolio_hash"] = "pending"
        pending_payload["reference"]["portfolio_hash"] = "pending"
        for task_overlay in pending_payload["task_overlays"].values():
            task_overlay["portfolio_hash"] = "pending"
            task_overlay["reference"]["portfolio_hash"] = "pending"
        assert blueprint_service_module._portfolio_hash(pending_payload) == first.portfolio_hash
        assert blueprint_service_module._portfolio_hash(first.to_dict()) == first.portfolio_hash

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            project_chief_engineer_task_blueprint(first, "TASK-OFFLINE")
        assert exc_info.value.code == "blueprint_portfolio_offline_diagnostic_only"

        diagnostic = project_chief_engineer_task_blueprint(
            first,
            "TASK-OFFLINE",
            allow_offline_diagnostic=True,
        )
        assert diagnostic["construction_plan"]["diagnostic_only"] is True
        assert diagnostic["scope_for_apply"] == ["src/offline.py", "tests"]
        assert BlueprintPersistence(str(tmp_path), ensure_directory=False).list_all() == [first.portfolio_id]


class TestGenerateTaskBlueprintCommandV1HappyPath:
    def test_minimal(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
        )
        assert cmd.task_id == "task-1"
        assert cmd.workspace == "/repo"
        assert cmd.objective == "Implement login"

    def test_full(self) -> None:
        cmd = GenerateTaskBlueprintCommandV1(
            task_id="task-1",
            workspace="/repo",
            objective="Implement login",
            run_id="run-1",
            constraints={"max_time": 300},
            context={"user": "alice"},
        )
        assert cmd.run_id == "run-1"
        assert cmd.constraints == {"max_time": 300}
        assert cmd.context == {"user": "alice"}

    def test_constraints_are_copied(self) -> None:
        original = {"max_time": 300}
        cmd = GenerateTaskBlueprintCommandV1(task_id="task-1", workspace="/repo", objective="x", constraints=original)
        original.clear()
        assert cmd.constraints == {"max_time": 300}


class TestGenerateTaskBlueprintCommandV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="", workspace="/r", objective="x")

    def test_whitespace_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GenerateTaskBlueprintCommandV1(task_id="  ", workspace="/r", objective="x")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="", objective="x")

    def test_empty_objective_raises(self) -> None:
        with pytest.raises(ValueError, match="objective"):
            GenerateTaskBlueprintCommandV1(task_id="t", workspace="/r", objective="")


class TestGetBlueprintStatusQueryV1HappyPath:
    def test_minimal(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo")
        assert q.task_id == "task-1"
        assert q.workspace == "/repo"
        assert q.run_id is None

    def test_with_run_id(self) -> None:
        q = GetBlueprintStatusQueryV1(task_id="task-1", workspace="/repo", run_id="run-1")
        assert q.run_id == "run-1"


class TestGetBlueprintStatusQueryV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            GetBlueprintStatusQueryV1(task_id="", workspace="/repo")

    def test_empty_workspace_raises(self) -> None:
        with pytest.raises(ValueError, match="workspace"):
            GetBlueprintStatusQueryV1(task_id="task-1", workspace="")


class TestQueryBlueprintProvenanceV1:
    def test_valid_blueprint_returns_typed_snapshot(self) -> None:
        payload = _blueprint_provenance_payload()

        result = query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert isinstance(result, TaskBlueprintProvenanceSnapshotV1)
        assert result.schema_version == "chief_engineer.blueprint_provenance.v1"
        assert result.blueprint_schema_version == "chief_engineer.blueprint.v1"
        assert result.hash_scheme == "chief_engineer.blueprint_hash.v1"
        assert result.logical_path == f"runtime/blueprints/{payload['blueprint_id']}.json"
        assert result.factory_run_id == "factory-run-1"
        assert result.task_id == "TASK-1"
        assert result.blueprint_id == payload["blueprint_id"]
        assert result.embedded_blueprint_hash == payload["blueprint_hash"]
        assert result.recomputed_blueprint_hash == payload["blueprint_hash"]
        assert result.matches is True
        assert result.pm_contract_hash == stable_hash(_producer_v1_hashable(_pm_task_payload()))
        assert result.recomputed_pm_contract_hash == stable_hash(_producer_v1_hashable(_pm_task_payload()))
        assert result.pm_task_canonical_hash == stable_hash(_pm_task_payload())
        assert result.target_files == ("src/main.py", "tests/test_main.py")
        assert result.to_dict()["recomputed_pm_contract_hash"] == result.recomputed_pm_contract_hash

    def test_semantic_mutation_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["summary"] = "mutated after producer hash"

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_mismatch"

    def test_embedded_hash_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["blueprint_hash"] = "b" * 64

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_mismatch"

    @pytest.mark.parametrize(
        ("field", "value", "error_code"),
        [
            ("run_id", "other-run", "blueprint_provenance_run_id_mismatch"),
            ("task_id", "TASK-2", "blueprint_provenance_task_id_mismatch"),
            ("blueprint_id", "ce_other", "blueprint_provenance_blueprint_id_mismatch"),
            ("schema_version", "chief_engineer.blueprint.v2", "blueprint_provenance_schema_mismatch"),
        ],
    )
    def test_payload_identity_or_schema_mismatch_fails_closed(
        self,
        field: str,
        value: str,
        error_code: str,
    ) -> None:
        payload = _blueprint_provenance_payload()
        payload[field] = value
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == error_code

    def test_noncanonical_logical_path_fails_closed(self) -> None:
        query = _blueprint_provenance_query()
        forged = QueryBlueprintProvenanceV1(
            blueprint=query.blueprint,
            expected_pm_task=query.expected_pm_task,
            expected_factory_run_id=query.expected_factory_run_id,
            expected_task_id=query.expected_task_id,
            expected_blueprint_id=query.expected_blueprint_id,
            expected_logical_path=f".polaris/blueprints/{query.expected_blueprint_id}.json",
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(forged)

        assert exc_info.value.code == "blueprint_provenance_logical_path_mismatch"

    def test_missing_pm_task_fails_closed_without_alias_fallback(self) -> None:
        payload = _blueprint_provenance_payload()
        payload.pop("pm_task")
        payload["context"]["pm_task_contract"] = _pm_task_payload()
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_task_invalid"

    def test_pm_task_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["pm_task"]["goal"] = "mutated PM task"
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_task_mismatch"

    def test_embedded_pm_contract_hash_mismatch_fails_closed(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["pm_contract_hash"] = "b" * 64
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_pm_contract_hash_mismatch"

    def test_blueprint_target_files_must_exactly_match_expected_pm_targets(self) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = ["src/other.py", "tests/test_main.py"]
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_mismatch"

    @pytest.mark.parametrize(
        "unsafe_path",
        [
            "/absolute.py",
            "src\\main.py",
            "src/\x00main.py",
            "",
            ".",
            "..",
            "src/./main.py",
            "src/../main.py",
            "src//main.py",
            "src/\x01main.py",
            "e\u0301.py",
            "a" * 1025,
        ],
    )
    def test_blueprint_target_files_reject_unsafe_paths(self, unsafe_path: str) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = [unsafe_path]
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize("invalid_targets", [None, "src/main.py", ("src/main.py",)])
    def test_blueprint_target_files_reject_missing_or_non_list(self, invalid_targets: object) -> None:
        payload = _blueprint_provenance_payload()
        if invalid_targets is None:
            payload.pop("target_files")
        else:
            payload["target_files"] = invalid_targets
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize(
        "invalid_targets",
        [
            ["src/main.py", "src/main.py"],
            [f"src/file-{index}.py" for index in range(513)],
        ],
    )
    def test_blueprint_target_files_reject_duplicates_and_overflow(self, invalid_targets: list[str]) -> None:
        payload = _blueprint_provenance_payload()
        payload["target_files"] = invalid_targets
        payload["blueprint_hash"] = stable_hash(_producer_v1_hashable(payload))

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_target_files_invalid"

    @pytest.mark.parametrize(
        "invalid_expected_targets",
        [None, "src/main.py", ("src/main.py",), ["src/../main.py"]],
    )
    def test_expected_pm_target_files_reject_missing_non_list_or_unsafe(
        self,
        invalid_expected_targets: object,
    ) -> None:
        expected_pm_task = _pm_task_payload()
        if invalid_expected_targets is None:
            expected_pm_task.pop("target_files")
        else:
            expected_pm_task["target_files"] = invalid_expected_targets
        valid_query = _blueprint_provenance_query()
        query = QueryBlueprintProvenanceV1(
            blueprint=valid_query.blueprint,
            expected_pm_task=expected_pm_task,
            expected_factory_run_id=valid_query.expected_factory_run_id,
            expected_task_id=valid_query.expected_task_id,
            expected_blueprint_id=valid_query.expected_blueprint_id,
            expected_logical_path=valid_query.expected_logical_path,
        )

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(query)

        assert exc_info.value.code == "blueprint_provenance_expected_target_files_invalid"

    @pytest.mark.parametrize("embedded_hash", ["", "A" * 64, "a" * 63, "g" * 64, 7])
    def test_invalid_embedded_hash_fails_closed(self, embedded_hash: object) -> None:
        payload = _blueprint_provenance_payload()
        payload["blueprint_hash"] = embedded_hash

        with pytest.raises(ChiefEngineerBlueprintErrorV1) as exc_info:
            query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert exc_info.value.code == "blueprint_provenance_hash_invalid"

    def test_nested_ignored_keys_preserve_producer_v1_compatibility(self) -> None:
        payload = _blueprint_provenance_payload()
        original_hash = payload["blueprint_hash"]
        payload["context"]["capability_token"] = {"token_id": "cap-rotated"}
        payload["context"]["nested"][0]["job_token"] = {"token_id": "job-rotated"}

        result = query_blueprint_provenance(_blueprint_provenance_query(payload))

        assert result.matches is True
        assert result.recomputed_blueprint_hash == original_hash

    def test_query_and_service_do_not_mutate_input(self) -> None:
        payload = _blueprint_provenance_payload()
        before = deepcopy(payload)
        query = _blueprint_provenance_query(payload)

        result = query_blueprint_provenance(query)

        assert result.matches is True
        assert payload == before
        assert query.blueprint == before
        assert query.blueprint is not payload
        assert query.expected_pm_task == _pm_task_payload()

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("expected_factory_run_id", 7),
            ("expected_factory_run_id", "x" * 257),
            ("expected_task_id", "task\ncontrol"),
            ("expected_task_id", "e\u0301"),
            ("expected_blueprint_id", "../unsafe"),
            ("expected_blueprint_id", "x" * 257),
            ("expected_logical_path", "/absolute/blueprint.json"),
            ("expected_logical_path", "runtime//blueprints/value.json"),
            ("expected_logical_path", "x" * 1025),
        ],
    )
    def test_query_dto_rejects_unbounded_or_unsafe_identity_and_path(
        self,
        field: str,
        invalid_value: object,
    ) -> None:
        valid = _blueprint_provenance_query()
        values = {
            "blueprint": valid.blueprint,
            "expected_pm_task": valid.expected_pm_task,
            "expected_factory_run_id": valid.expected_factory_run_id,
            "expected_task_id": valid.expected_task_id,
            "expected_blueprint_id": valid.expected_blueprint_id,
            "expected_logical_path": valid.expected_logical_path,
        }
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError)):
            QueryBlueprintProvenanceV1(**values)


class TestTaskBlueprintProvenanceSnapshotV1StrictDto:
    @pytest.mark.parametrize("invalid_matches", [1, 0, "true", None])
    def test_matches_requires_exact_bool(self, invalid_matches: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values["matches"] = invalid_matches

        with pytest.raises(TypeError, match="matches"):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("schema_version", "chief_engineer.blueprint_provenance.v2"),
            ("blueprint_schema_version", "chief_engineer.blueprint.v2"),
            ("hash_scheme", "chief_engineer.blueprint_hash.v2"),
        ],
    )
    def test_schema_and_hash_scheme_are_frozen(self, field: str, invalid_value: str) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises(ValueError, match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("embedded_blueprint_hash", "A" * 64),
            ("recomputed_blueprint_hash", "a" * 63),
            ("pm_contract_hash", "g" * 64),
            ("recomputed_pm_contract_hash", 7),
            ("pm_task_canonical_hash", ""),
        ],
    )
    def test_all_hash_fields_require_lower_sha256(self, field: str, invalid_value: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError), match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        ("field", "invalid_value"),
        [
            ("factory_run_id", 7),
            ("factory_run_id", "x" * 257),
            ("task_id", "task\ncontrol"),
            ("task_id", "e\u0301"),
            ("blueprint_id", "../unsafe"),
            ("logical_path", "/absolute/blueprint.json"),
            ("logical_path", "runtime//blueprints/value.json"),
            ("logical_path", "x" * 1025),
        ],
    )
    def test_identity_and_logical_path_are_strict(self, field: str, invalid_value: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values[field] = invalid_value

        with pytest.raises((TypeError, ValueError), match=field):
            TaskBlueprintProvenanceSnapshotV1(**values)

    @pytest.mark.parametrize(
        "invalid_target_files",
        [
            ["src/main.py"],
            ("src/main.py", "src/main.py"),
            ("src/../main.py",),
            tuple(f"src/file-{index}.py" for index in range(513)),
        ],
    )
    def test_target_files_are_bounded_unique_safe_tuple(self, invalid_target_files: object) -> None:
        values = _blueprint_provenance_snapshot_kwargs()
        values["target_files"] = invalid_target_files

        with pytest.raises((TypeError, ValueError), match="target_files"):
            TaskBlueprintProvenanceSnapshotV1(**values)


class TestTaskBlueprintGeneratedEventV1HappyPath:
    def test_construction(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
        )
        assert evt.event_id == "evt-1"
        assert evt.blueprint_path == "/repo/.blueprint/task-1.yaml"
        assert evt.risk_level is None

    def test_with_risk_level(self) -> None:
        evt = TaskBlueprintGeneratedEventV1(
            event_id="evt-1",
            task_id="task-1",
            workspace="/repo",
            blueprint_path="/repo/.blueprint/task-1.yaml",
            generated_at="2026-03-24T10:00:00Z",
            risk_level="medium",
        )
        assert evt.risk_level == "medium"


class TestTaskBlueprintGeneratedEventV1EdgeCases:
    def test_empty_event_id_raises(self) -> None:
        with pytest.raises(ValueError, match="event_id"):
            TaskBlueprintGeneratedEventV1(
                event_id="",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="/bp",
                generated_at="2026-03-24T10:00:00Z",
            )

    def test_empty_blueprint_path_raises(self) -> None:
        with pytest.raises(ValueError, match="blueprint_path"):
            TaskBlueprintGeneratedEventV1(
                event_id="e1",
                task_id="task-1",
                workspace="/repo",
                blueprint_path="",
                generated_at="2026-03-24T10:00:00Z",
            )


class TestTaskBlueprintResultV1HappyPath:
    def test_success(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_path="/bp.yaml",
        )
        assert res.ok is True
        assert res.blueprint_id is None
        assert res.blueprint_path == "/bp.yaml"
        assert res.blueprint_hash == ""
        assert res.recommendations == ()
        assert res.risks == ()

    def test_with_blueprint_id(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="generated",
            blueprint_id="bp-1",
        )
        assert res.blueprint_id == "bp-1"

    def test_failure(self) -> None:
        res = TaskBlueprintResultV1(
            ok=False,
            task_id="task-1",
            workspace="/repo",
            status="failed",
            summary="Blueprint generation failed",
        )
        assert res.ok is False
        assert res.status == "failed"

    def test_recommendations_normalized_to_tuple(self) -> None:
        res = TaskBlueprintResultV1(
            ok=True,
            task_id="task-1",
            workspace="/repo",
            status="ok",
            recommendations=["use cache", "add retry"],  # type: ignore[arg-type]
        )
        assert res.recommendations == ("use cache", "add retry")


class TestTaskBlueprintResultV1EdgeCases:
    def test_empty_task_id_raises(self) -> None:
        with pytest.raises(ValueError, match="task_id"):
            TaskBlueprintResultV1(ok=True, task_id="", workspace="/repo", status="ok")

    def test_empty_status_raises(self) -> None:
        with pytest.raises(ValueError, match="status"):
            TaskBlueprintResultV1(ok=True, task_id="task-1", workspace="/repo", status="")


class TestChiefEngineerBlueprintErrorV1:
    def test_default_values(self) -> None:
        err = ChiefEngineerBlueprintErrorV1("blueprint generation failed")
        assert str(err) == "blueprint generation failed"
        assert err.code == "chief_engineer_blueprint_error"
        assert err.details == {}

    def test_custom_code_and_details(self) -> None:
        err = ChiefEngineerBlueprintErrorV1(
            "timeout",
            code="blueprint_timeout",
            details={"task_id": "task-1"},
        )
        assert err.code == "blueprint_timeout"
        assert err.details == {"task_id": "task-1"}

    def test_empty_message_raises(self) -> None:
        with pytest.raises(ValueError, match="message"):
            ChiefEngineerBlueprintErrorV1("")

    def test_empty_code_raises(self) -> None:
        with pytest.raises(ValueError, match="code"):
            ChiefEngineerBlueprintErrorV1("error", code="  ")


