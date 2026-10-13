"""Intent authority becomes exact CE handoff without speculative PM recipes."""

import json
from copy import deepcopy
from dataclasses import replace

import pytest
from polaris.cells.chief_engineer.blueprint.public import (
    BuildChiefEngineerBlueprintPortfolioCommandV1,
    ChiefEngineerBlueprintErrorV1,
    ChiefEngineerPortfolioTaskV1,
    GenerateTaskBlueprintCommandV1,
    VerificationCommandAuthorityV1,
    build_chief_engineer_blueprint_portfolio,
    generate_task_blueprint,
)
from polaris.cells.chief_engineer.blueprint.public.contracts import (
    _issue_chief_engineer_portfolio_authority_carrier,
    project_completion_catalog_snapshot_hash,
    project_completion_verifier_policy_snapshot_hash,
)


def _task():
    return ChiefEngineerPortfolioTaskV1(
        task_id="TASK-A",
        objective="Deliver deterministic CLI output",
        target_files=(),
        topology_authority="chief_engineer",
        verification_authority="chief_engineer",
        planning_contract_version="pm.task_intent.v1",
        observable_acceptance=("CLI accepts a name and prints a deterministic greeting",),
        explicit_constraints=("Keep data inside the workspace",),
    )


def _command(tmp_path, payload, *, task=None, tasks=None, commands=(), project_kind="application"):
    task = task or _task()
    tasks = tasks or (task,)
    catalog = {"project_kind": project_kind} if project_kind else {}
    catalog_path = tmp_path / ".polaris" / "catalog_contract.json"
    catalog_path.parent.mkdir(parents=True, exist_ok=True)
    if catalog:
        catalog_path.write_text(json.dumps(catalog), encoding="utf-8")
    policy = {
        "schema_version": "evidence_policy.v1",
        "source": "control_plane.verifier_policy.evidence_policy_compiler",
        "policy_hash": "b" * 64,
        "required_evidence_modalities": ["command"],
    }
    carrier = _issue_chief_engineer_portfolio_authority_carrier(
        workspace=str(tmp_path),
        run_id="run-intent",
        project_id="project-intent",
        pm_stage_event_id="pm-stage-intent",
        pm_contract_hash="a" * 64,
        tasks=tasks,
        catalog_snapshot=catalog,
        catalog_snapshot_hash=project_completion_catalog_snapshot_hash(catalog),
        verifier_policy_hash="b" * 64,
        verifier_policy_snapshot=policy,
        verifier_policy_snapshot_hash=project_completion_verifier_policy_snapshot_hash(policy),
        verification_command_authority=commands,
    )
    return BuildChiefEngineerBlueprintPortfolioCommandV1(
        workspace=str(tmp_path),
        run_id="run-intent",
        tasks=tasks,
        authority_carrier=carrier,
        llm_blueprint=payload,
    )


def _payload():
    return {
        "construction_plan": {
            "task_plans": {"TASK-A": {"steps": ["Implement CLI", "Verify CLI"]}},
            "project_interface_contract": {},
            "shared_behavior_contract": {"invariants": []},
        },
        "project_completion_contract": {
            "obligations": {
                "artifacts": [
                    {
                        "obligation_id": "source",
                        "path": "src/main.py",
                        "semantic_role": "entrypoint",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                    {
                        "obligation_id": "tests",
                        "path": "tests/test_main.py",
                        "semantic_role": "test",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                    {
                        "obligation_id": "manifest",
                        "path": "pyproject.toml",
                        "semantic_role": "manifest",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    },
                ],
                "entrypoints": [
                    {
                        "obligation_id": "cli",
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
                        "obligation_id": "environment",
                        "modality": "environment_prep",
                        "command_authority_hash": None,
                        "applicability": "required",
                        "covers_obligation_ids": ["manifest"],
                        "owner_task_id": "TASK-A",
                        "argv": ["python", "-m", "pip", "install", "-e", "."],
                        "cwd": ".",
                    },
                    {
                        "obligation_id": "check",
                        "modality": "test",
                        "command_authority_hash": None,
                        "applicability": "required",
                        "covers_obligation_ids": ["source", "tests"],
                        "owner_task_id": "TASK-A",
                        "argv": ["python", "-m", "pytest", "-q"],
                        "cwd": ".",
                    },
                ],
            }
        },
        "risk_flags": [],
    }


def test_light_task_is_semantic_and_does_not_require_technical_paths():
    task = _task()
    assert task.target_files == ()
    assert task.to_dict()["observable_acceptance"] == list(task.observable_acceptance)
    with pytest.raises(ValueError, match="target_files"):
        ChiefEngineerPortfolioTaskV1(task_id="legacy", objective="Legacy strict plan", target_files=())
    with pytest.raises(ValueError, match="delegat"):
        replace(task, verification_authority="pm")


def test_ce_authors_exact_artifacts_commands_and_preserves_pm_acceptance(tmp_path):
    task = _task()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), task=task))
    contract = portfolio.project_completion_contract
    assert contract is not None
    assert {a.path for a in contract.obligations.artifacts} == {"src/main.py", "tests/test_main.py", "pyproject.toml"}
    assert ("python", "-m", "pytest", "-q") in {c.argv for c in contract.verification_command_authority}
    assert ("python", "-m", "src.main") in {c.argv for c in contract.verification_command_authority}
    plan = portfolio.task_overlays["TASK-A"]["construction_plan"]
    assert plan["observable_acceptance"] == list(task.observable_acceptance)
    assert plan["explicit_constraints"] == list(task.explicit_constraints)
    assert set(plan["target_files"]) == {"src/main.py", "tests/test_main.py", "pyproject.toml"}
    assert set(portfolio.project_interface_contract.task_file_ownership["TASK-A"]) == set(plan["target_files"])
    assert portfolio.project_interface_contract.to_dict()["ownership_authority"] == "chief_engineer_completion_contract"
    assert portfolio.shared_behavior_contract is None
    assert portfolio.handoff_ready is False
    assert portfolio.execution_authorized is False


@pytest.mark.parametrize("argv", [["echo", "pass"], ["python", "-m", "compileall", "src"], ["npm", "run", "watch"]])
def test_ce_invalid_proof_recipe_fails_before_portfolio_persistence(tmp_path, argv):
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"][1]["argv"] = argv
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"(recipe|proof|profile)"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    assert not list((tmp_path / ".polaris" / "runtime" / "blueprints").glob("ce_portfolio_*.json"))


def test_ce_cannot_omit_explicit_user_command_constraint(tmp_path):
    required = VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("pytest", "-q"))
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"explicit.*command"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), commands=(required,)))


def test_ce_cannot_bind_an_unowned_recipe(tmp_path):
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"][1]["owner_task_id"] = "unknown"
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"(owner|delegat)"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))


def test_ce_artifact_scope_rejects_runtime_namespace(tmp_path):
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["artifacts"][0]["path"] = ".polaris/runtime/forged.json"
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"(scope|namespace|ownership)"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))


def test_exact_user_command_is_retained_when_ce_adds_another_recipe(tmp_path):
    payload = _payload()
    row = deepcopy(payload["project_completion_contract"]["obligations"]["verification"][1])
    row["obligation_id"] = "user-required"
    row["argv"] = ["pytest", "-q"]
    payload["project_completion_contract"]["obligations"]["verification"].append(row)
    required = VerificationCommandAuthorityV1(task_id="TASK-A", modality="test", argv=("pytest", "-q"))
    result = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, commands=(required,)))
    assert required in result.project_completion_contract.verification_command_authority
    assert required.authority_hash in {
        v.command_authority_hash
        for v in result.project_completion_contract.obligations.verification
        if v.applicability == "required"
    }


def test_semantic_pm_handoff_mints_ce_bound_job_token_and_recipe_scope(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    task = _task()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), task=task))
    pm_task = {
        "id": task.task_id,
        "title": task.objective,
        "goal": task.objective,
        "target_files": [],
        "scope_paths": [],
        "verification_commands": [],
        "acceptance": list(task.observable_acceptance),
        "metadata": {
            "planning_contract_version": "pm.task_intent.v1",
            "topology_authority": "chief_engineer",
            "verification_authority": "chief_engineer",
        },
    }
    result = generate_task_blueprint(
        GenerateTaskBlueprintCommandV1(
            task_id=task.task_id,
            workspace=str(tmp_path),
            objective=task.objective,
            run_id="run-intent",
            context={**portfolio.to_task_blueprint_context(), "pm_task_contract": pm_task},
            llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id),
        )
    )
    payload = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert payload["handoff_ready"] is True
    assert payload["job_token"]["blueprint_hash"] == payload["blueprint_hash"]
    assert payload["job_token"]["allowed_write_paths"] == list(result.target_files)
    expected = {row.command for row in portfolio.project_completion_contract.verification_command_authority}
    assert set(payload["allowed_commands"]) == expected
    assert set(payload["context"]["allowed_commands"]) == expected
    assert payload["execution_checklist"] == ["Implement CLI", "Verify CLI"]
    assert payload["acceptance_criteria"] == list(task.observable_acceptance)
    assert payload["job_token"]["capability_audit"]["command_authority_hashes"] == sorted(
        row.authority_hash for row in portfolio.project_completion_contract.verification_command_authority
    )


def test_regular_project_can_form_ce_contract_without_catalog(tmp_path):
    result = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), project_kind=None))
    assert result.project_completion_contract.project_kind == "application"
    assert not (tmp_path / ".polaris" / "catalog_contract.json").exists()


def test_explicit_user_scope_cannot_be_widened_by_ce_topology(tmp_path):
    task = replace(_task(), scope_paths=("src",))
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="scope"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload(), task=task))


def test_ce_recipe_hash_cannot_invent_command_authority(tmp_path):
    payload = _payload()
    row = payload["project_completion_contract"]["obligations"]["verification"][1]
    del row["argv"]
    del row["cwd"]
    row["command_authority_hash"] = "d" * 64
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="recipe"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))


def test_hash_only_explicit_command_still_obeys_control_plane_proof_profile(tmp_path):
    payload = _payload()
    authority = VerificationCommandAuthorityV1(
        task_id="TASK-A",
        modality="test",
        argv=("python", "-m", "compileall", "src"),
    )
    row = payload["project_completion_contract"]["obligations"]["verification"][1]
    row.pop("argv")
    row.pop("cwd")
    row["command_authority_hash"] = authority.authority_hash
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="profile"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, commands=(authority,)))


@pytest.mark.parametrize(
    ("modality", "argv", "covers"),
    [
        ("test", ["pytest", "-q"], ["tests"]),
        ("environment_prep", ["python", "-m", "pip", "install", "."], ["manifest"]),
        ("entrypoint", ["python", "-m", "src.main", "--color"], ["optional-cli"]),
    ],
)
def test_optional_ce_recipe_retains_capability_without_becoming_required(tmp_path, modality, argv, covers):
    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    if modality == "entrypoint":
        optional_entrypoint = deepcopy(obligations["entrypoints"][0])
        optional_entrypoint.update(
            obligation_id="optional-cli", applicability="optional", command="python -m src.main --color"
        )
        obligations["entrypoints"].append(optional_entrypoint)
    obligations["verification"].append(
        {
            "obligation_id": "optional-recipe",
            "modality": modality,
            "command_authority_hash": None,
            "applicability": "optional",
            "covers_obligation_ids": covers,
            "owner_task_id": "TASK-A",
            "argv": argv,
            "cwd": ".",
        }
    )
    contract = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload)).project_completion_contract
    authority = next(item for item in contract.verification_command_authority if item.argv == tuple(argv))
    bindings = [
        item for item in contract.obligations.verification if item.command_authority_hash == authority.authority_hash
    ]
    assert bindings
    assert {item.applicability for item in bindings} == {"optional"}
    assert {item.command for item in contract.obligations.verification if item.applicability == "required"} == {
        "python -m pytest -q",
        "python -m pip install -e .",
        "python -m src.main",
    }


def test_optional_environment_recipe_cannot_satisfy_required_application_preparation(tmp_path):
    payload = _payload()
    payload["project_completion_contract"]["obligations"]["verification"][0]["applicability"] = "optional"
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="environment_prep"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))


def test_explicit_user_entrypoint_command_cannot_be_weakened_by_optional_ce_entrypoint(tmp_path):
    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    optional_entrypoint = deepcopy(obligations["entrypoints"][0])
    optional_entrypoint.update(
        obligation_id="optional-cli", applicability="optional", command="python -m src.main --color"
    )
    obligations["entrypoints"].append(optional_entrypoint)
    required = VerificationCommandAuthorityV1(
        task_id="TASK-A", modality="entrypoint", argv=("python", "-m", "src.main", "--color")
    )
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="explicit user command constraint"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, commands=(required,)))


def test_optional_ce_artifacts_and_commands_are_capabilities_only_in_handoff(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import (
        GetBlueprintStatusQueryV1,
        get_blueprint_status,
        project_chief_engineer_task_blueprint,
    )
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    payload = _payload()
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append({"obligation_id": "optional-docs", "path": "docs/color.md", "semantic_role": "docs", "applicability": "optional", "owner_task_id": "TASK-A"})
    optional = deepcopy(obligations["verification"][1])
    optional.update(obligation_id="optional-test", applicability="optional", argv=["pytest", "-q"])
    obligations["verification"].append(optional)
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload))
    pm_task = {"id": "TASK-A", "title": "Deliver deterministic CLI output", "goal": "Deliver deterministic CLI output", "acceptance": ["CLI accepts a name and prints a deterministic greeting"], "product_acceptance": ["The complete product can be delivered"], "metadata": {"planning_contract_version": "pm.task_intent.v1"}}
    result = generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id="TASK-A", run_id="run-intent", objective=pm_task["goal"], context={**portfolio.to_task_blueprint_context(), "pm_task_contract": pm_task}, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, "TASK-A")))
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert "docs/color.md" in stored["target_files"]
    assert "docs/color.md" in stored["job_token"]["allowed_write_paths"]
    assert set(stored["required_write_targets"]) == {"src/main.py", "tests/test_main.py", "pyproject.toml"}
    assert stored["context"]["required_write_targets"] == stored["required_write_targets"]
    assert "docs/color.md" not in stored["job_token"]["required_artifacts"]
    assert set(stored["verification_commands"]) == {"python -m pytest -q", "python -m pip install -e ."}
    assert "pytest -q" in stored["allowed_commands"]
    assert "pytest -q" not in portfolio.task_overlays["TASK-A"]["construction_plan"]["verification_commands"]
    assert stored["acceptance_criteria"] == ["CLI accepts a name and prints a deterministic greeting"]
    assert stored["context"]["pm_task_contract"]["product_acceptance"] == ["The complete product can be delivered"]
    status = get_blueprint_status(GetBlueprintStatusQueryV1(task_id="TASK-A", workspace=str(tmp_path)))
    assert status.required_write_targets == tuple(stored["required_write_targets"])


def _readonly_fixture():
    task = replace(_task(), task_id="VERIFY", objective="Verify upstream CLI and tests without writing", dependencies=("TASK-A",), context_files=("src/main.py", "tests/test_main.py", "pyproject.toml"), observable_acceptance=("Existing CLI tests pass",))
    payload = _payload()
    payload["construction_plan"]["task_plans"]["VERIFY"] = {"steps": ["Read upstream artifacts", "Run the exact test recipe"]}
    payload["project_completion_contract"]["obligations"]["verification"].append({"obligation_id": "readonly-check", "modality": "test", "command_authority_hash": None, "applicability": "required", "covers_obligation_ids": ["source", "tests"], "owner_task_id": "VERIFY", "argv": ["python", "-m", "pytest", "-q"], "cwd": "."})
    return task, payload


def test_semantic_readonly_task_receives_exact_read_capability_without_write_ownership(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    task, payload = _readonly_fixture()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    assert portfolio.project_interface_contract.task_file_ownership["VERIFY"] == ()
    pm_task = {"id": task.task_id, "title": task.objective, "goal": task.objective, "target_files": [], "scope_paths": [], "dependencies": list(task.dependencies), "context_files": list(task.context_files), "acceptance": list(task.observable_acceptance), "metadata": {"planning_contract_version": "pm.task_intent.v1"}}
    result = generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id=task.task_id, run_id="run-intent", objective=task.objective, context={**portfolio.to_task_blueprint_context(), "pm_task_contract": pm_task}, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id)))
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert stored["handoff_ready"] is True
    assert stored["target_files"] == []
    assert stored["required_write_targets"] == []
    assert stored["job_token"]["allowed_write_paths"] == []
    assert set(stored["job_token"]["allowed_read_paths"]) == set(task.context_files)
    assert stored["job_token"]["capability_audit"]["ok"] is True
    assert set(stored["verification_commands"]) == {"python -m pytest -q"}
    assert stored["allowed_commands"] == ["python -m pytest -q"]
    assert not set(task.context_files) & set(stored["job_token"]["required_artifacts"])


@pytest.mark.parametrize("invalid", ["unknown_file", "unrelated_owner"])
def test_semantic_readonly_context_requires_exact_dependency_ownership(tmp_path, invalid):
    task, payload = _readonly_fixture()
    task = replace(task, context_files=("private.py",)) if invalid == "unknown_file" else replace(task, dependencies=())
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"(read|context|dependency|scope)"):
        build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))


def test_job_token_cannot_read_context_outside_ce_interface_binding(tmp_path):
    from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import _control_plane_job_token

    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, _payload()))
    targets = list(portfolio.project_interface_contract.task_file_ownership["TASK-A"])
    with pytest.raises(ValueError, match="read context"):
        _control_plane_job_token(
            workspace=str(tmp_path), task_id="TASK-A", blueprint_id="ce-test",
            blueprint_path="runtime/blueprints/ce-test.json", blueprint_hash="c" * 64,
            contract_hash="a" * 64, target_files=targets, scope_paths=targets,
            acceptance_criteria=["The CLI works"], project_type="application", language="python",
            payload={**portfolio.to_task_blueprint_context(), "run_id": "run-intent",
                     "planning_contract_version": "pm.task_intent.v1", "context_files": ["private.py"]},
        )


def test_task_market_consumer_preserves_validated_readonly_handoff(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import CEConsumer
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    task, payload = _readonly_fixture()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    pm_task = {"id": task.task_id, "title": task.objective, "goal": task.objective, "acceptance": list(task.observable_acceptance), "dependencies": list(task.dependencies), "metadata": {"planning_contract_version": "pm.task_intent.v1"}}
    blueprint = generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id=task.task_id, run_id="run-intent", objective=task.objective, context={**portfolio.to_task_blueprint_context(), "pm_task_contract": pm_task}, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id)))
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(blueprint.blueprint_id)
    market = MagicMock()
    market.claim_work_item.side_effect = [SimpleNamespace(ok=True, task_id=task.task_id, lease_token="lease-readonly", payload=stored), SimpleNamespace(ok=False)]
    market.acknowledge_task_stage.return_value = SimpleNamespace(ok=True, status="pending_exec")
    monkeypatch.setattr("polaris.cells.chief_engineer.blueprint.internal.ce_consumer.get_task_market_service", lambda: market)
    monkeypatch.setattr(CEConsumer, "_run_ce_preflight", lambda self, task_id, payload: {"blueprint_id": "bp-VERIFY"})
    monkeypatch.setenv("KERNELONE_CE_HANDOFF_ENFORCEMENT", "1")
    result = CEConsumer(workspace=str(tmp_path), worker_id="readonly-consumer", enable_adr_blueprint_store=False).poll_once()
    assert result[0]["ok"] is True, result
    final = BlueprintPersistence(str(tmp_path), ensure_directory=False).load("bp-VERIFY")
    assert final["handoff_ready"] is True
    handoff = market.acknowledge_task_stage.call_args[0][0].metadata
    assert handoff["job_token"]["allowed_write_paths"] == []
    assert set(handoff["job_token"]["allowed_read_paths"]) == set(task.context_files)
    assert handoff["allowed_commands"] == ["python -m pytest -q"]
    assert handoff["handoff_decision"]["allowed"] is True
    assert final["context_files"] == list(task.context_files)


@pytest.mark.parametrize("attack", ["rehash_read_scope", "changed_run"])
def test_readonly_handoff_revalidates_immutable_portfolio_authority(tmp_path, attack):
    from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import stable_hash
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint

    task, payload = _readonly_fixture()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    context = deepcopy(portfolio.to_task_blueprint_context())
    context["pm_task_contract"] = {"id": task.task_id, "title": task.objective, "goal": task.objective, "acceptance": list(task.observable_acceptance), "dependencies": list(task.dependencies), "context_files": list(task.context_files), "metadata": {"planning_contract_version": "pm.task_intent.v1"}}
    run_id = "run-intent"
    if attack == "rehash_read_scope":
        interface = context["project_interface_contract"]
        interface["task_read_context"][task.task_id].append("private.py")
        basis = {key: value for key, value in interface.items() if key not in {"project_interface_contract_id", "project_interface_contract_ref", "project_interface_contract_hash"}}
        interface["project_interface_contract_hash"] = stable_hash(basis)
        context["project_interface_contract_hash"] = interface["project_interface_contract_hash"]
    else:
        run_id = "another-run"
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match=r"(immutable|portfolio|authority|identity)"):
        generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id=task.task_id, run_id=run_id, objective=task.objective, context=context, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id)))


def test_semantic_readonly_handoff_cannot_turn_pm_context_path_into_write_capability(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    task, payload = _readonly_fixture()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    context = {**portfolio.to_task_blueprint_context(), "target_files": ["private.py"], "pm_task_contract": {"id": task.task_id, "title": task.objective, "goal": task.objective, "acceptance": list(task.observable_acceptance), "metadata": {"planning_contract_version": "pm.task_intent.v1"}}}
    result = generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id=task.task_id, run_id="run-intent", objective=task.objective, context=context, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id)))
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert stored["target_files"] == []
    assert stored["job_token"]["allowed_write_paths"] == []


def test_job_token_rechecks_owner_portfolio_after_interface_rehash(tmp_path):
    from polaris.cells.chief_engineer.blueprint.internal.ce_consumer import _control_plane_job_token, stable_hash

    task, payload = _readonly_fixture()
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    context = deepcopy(portfolio.to_task_blueprint_context())
    interface = context["project_interface_contract"]
    interface["task_read_context"][task.task_id].append("private.py")
    basis = {key: value for key, value in interface.items() if key not in {"project_interface_contract_id", "project_interface_contract_ref", "project_interface_contract_hash"}}
    interface["project_interface_contract_hash"] = stable_hash(basis)
    context["project_interface_contract_hash"] = interface["project_interface_contract_hash"]
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="immutable"):
        _control_plane_job_token(workspace=str(tmp_path), task_id=task.task_id, payload={**context, "run_id": "run-intent", "planning_contract_version": "pm.task_intent.v1", "context_files": interface["task_read_context"][task.task_id]}, blueprint_id="bp-test", blueprint_path="runtime/blueprints/bp-test.json", blueprint_hash="b" * 64, contract_hash="a" * 64, target_files=[], scope_paths=[], acceptance_criteria=["Existing tests pass"], project_type="application", language="python")


def test_ce_derives_upstream_inputs_for_test_writer_without_pm_context_hints(tmp_path):
    from polaris.cells.chief_engineer.blueprint.public import project_chief_engineer_task_blueprint
    from polaris.cells.chief_engineer.blueprint.public.service import BlueprintPersistence

    task, payload = _readonly_fixture()
    task = replace(task, context_files=())
    obligations = payload["project_completion_contract"]["obligations"]
    obligations["artifacts"].append({"obligation_id": "verify-test", "path": "tests/test_verify.py", "semantic_role": "test", "applicability": "required", "owner_task_id": task.task_id})
    obligations["verification"][-1]["covers_obligation_ids"].append("verify-test")
    portfolio = build_chief_engineer_blueprint_portfolio(_command(tmp_path, payload, tasks=(_task(), task)))
    context = {**portfolio.to_task_blueprint_context(), "pm_task_contract": {"id": task.task_id, "title": task.objective, "goal": task.objective, "acceptance": list(task.observable_acceptance), "metadata": {"planning_contract_version": "pm.task_intent.v1"}}}
    result = generate_task_blueprint(GenerateTaskBlueprintCommandV1(workspace=str(tmp_path), task_id=task.task_id, run_id="run-intent", objective=task.objective, context=context, llm_blueprint=project_chief_engineer_task_blueprint(portfolio, task.task_id)))
    stored = BlueprintPersistence(str(tmp_path), ensure_directory=False).load(result.blueprint_id)
    assert stored["job_token"]["allowed_write_paths"] == ["tests/test_verify.py"]
    assert set(stored["context_files"]) == {"src/main.py", "tests/test_main.py"}
    assert set(stored["job_token"]["allowed_read_paths"]) == {"tests/test_verify.py", "src/main.py", "tests/test_main.py"}
