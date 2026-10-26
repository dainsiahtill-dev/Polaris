"""CE recipe recovery keeps model choices separate from command authority."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import ChiefEngineerBlueprintErrorV1, VerificationCommandAuthorityV1
from polaris.cells.chief_engineer.blueprint.public.service._delegated_recipes import prepare_delegated_recipes
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor


def _task() -> Any:
    return SimpleNamespace(
        task_id="TASK-1", planning_contract_version="pm.task_intent.v1", verification_authority="chief_engineer"
    )


def _requirements(*, modality: str, argv: list[str], claimed_hash: str | None = None) -> dict[str, Any]:
    return {
        "obligations": {
            "verification": [
                {
                    "obligation_id": "VERIFY-1",
                    "owner_task_id": "TASK-1",
                    "applicability": "required",
                    "modality": modality,
                    "argv": argv,
                    "cwd": ".",
                    "command_authority_hash": claimed_hash,
                    "covers_obligation_ids": ["SOURCE-1"],
                }
            ],
            "entrypoints": [],
        }
    }


def test_modality_rejection_projects_exact_owner_policy_feedback(tmp_path: Path) -> None:
    command = SimpleNamespace(workspace=str(tmp_path), tasks=(_task(),))
    with pytest.raises(ChiefEngineerBlueprintErrorV1) as caught:
        prepare_delegated_recipes(
            command,
            _requirements(modality="build", argv=["npx", "tsc", "--noEmit"]),
            SimpleNamespace(verification_command_authority=()),
        )
    diagnostic = caught.value.details["verification_recipe_diagnostics"][0]
    assert diagnostic["obligation_id"] == "VERIFY-1"
    assert diagnostic["owner_task_id"] == "TASK-1"
    assert diagnostic["argv"] == ["npx", "tsc", "--noEmit"]
    assert diagnostic["cwd"] == "."
    assert diagnostic["modality"] == "build"
    assert diagnostic["profile_feedback"]["error_code"] == "verifier_modality_mismatch"
    assert diagnostic["allowed_recipe_modalities"] == ["lint"]
    assert diagnostic["profile_feedback"]["guidance_only"] is True
    assert diagnostic["profile_feedback"]["execution_authority"] is False


def test_null_proposal_binds_bare_server_hash_without_model_computation(tmp_path: Path) -> None:
    command = SimpleNamespace(workspace=str(tmp_path), tasks=(_task(),))
    prepared, authorities = prepare_delegated_recipes(
        command,
        _requirements(modality="environment_prep", argv=["npm", "install"]),
        SimpleNamespace(verification_command_authority=()),
    )
    assert prepared["obligations"]["verification"][0]["command_authority_hash"] == (
        "dbca9b0afac7d6506d62a93c0ccfabca0ce578bcc8242ac8554790d6bfcbf61c"
    )
    assert len(authorities) == 1


@pytest.mark.parametrize("claim", ["sha256:fake", "sha256:" + "0" * 64, "0" * 64])
def test_fake_hash_claim_still_rejected(tmp_path: Path, claim: str) -> None:
    with pytest.raises(ChiefEngineerBlueprintErrorV1, match="does not bind exact argv/cwd"):
        prepare_delegated_recipes(
            SimpleNamespace(workspace=str(tmp_path), tasks=(_task(),)),
            _requirements(modality="environment_prep", argv=["npm", "install"], claimed_hash=claim),
            SimpleNamespace(verification_command_authority=()),
        )


def test_recovery_guidance_restores_delegated_null_rule_from_trusted_tasks(tmp_path: Path) -> None:
    objective = _executor(tmp_path)._chief_engineer_trusted_recipe_guidance(
        tasks=(_task(),),
        authority_carrier=SimpleNamespace(verification_command_authority=()),
    )
    assert "command_authority_hash=null" in objective
    assert "domain, task_id, modality, argv and cwd" in objective
    assert "bare 64-character hexadecimal" in objective
    assert "unaccepted draft risk_flags" in objective
    assert "TASK-1" in objective


def test_recovery_guidance_preserves_exact_explicit_owner_command_table(tmp_path: Path) -> None:
    binding = VerificationCommandAuthorityV1(task_id="TASK-1", modality="test", argv=("npm", "test"), cwd=".")
    objective = _executor(tmp_path)._chief_engineer_trusted_recipe_guidance(
        tasks=(_task(),),
        authority_carrier=SimpleNamespace(verification_command_authority=(binding,)),
    )
    rows = json.loads(objective.split("Authenticated explicit PM command bindings: ", 1)[1])
    assert rows == [binding.to_dict()]


def test_public_recipe_diagnostics_do_not_create_patch_authority(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        ChiefEngineerSemanticRepairCandidateV1,
        chief_engineer_semantic_repair_task_set_hash,
    )

    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path),
        project_id="L1-01",
        run_id="factory-recipe",
        pm_contract_hash="1" * 64,
        task_ids=("TASK-1",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-1",)),
        candidate={"construction_plan": {"task_plans": {"TASK-1": {}}}, "project_completion_contract": {}},
    )
    finding = {
        "code": "invalid_project_completion_contract",
        "repair_operation": "verification_recipe_upsert",
        "detail": "CE recipe fails verifier proof profile: verifier_modality_mismatch",
        "verification_recipe_diagnostics": [{"obligation_id": "VERIFY-1", "execution_authority": False}],
    }
    errors = [
        "chief_engineer.completion_normalization:" + json.dumps(finding),
        "chief_engineer.completion_reference_normalization_blocked:"
        '{"invariant_index":0,"obligation_ids":["ENTRY-1"],"execution_authority":false}',
    ]
    assert (
        _executor(tmp_path)._chief_engineer_semantic_repair_diagnosis(
            candidate=candidate,
            output_errors=errors,
        )
        is None
    )


@pytest.fixture
def recipe_native_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Any:
    from polaris.cells.factory.pipeline.tests.test_ce_schema_residual_runtime import isolated_native_endpoint

    yield from isolated_native_endpoint.__wrapped__(tmp_path, monkeypatch, request)


@pytest.mark.parametrize("recipe_native_endpoint", ["accept"], indirect=True)
@pytest.mark.parametrize(
    "recipe_failure",
    [
        "verification_modality",
        "entrypoint_collection",
        "bad_install",
        "html_entrypoint",
        "consumer_task_ids",
        "verification_command_change",
    ],
)
def test_wrong_verifier_modality_repair_reaches_sealed_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recipe_native_endpoint: Any,
    recipe_failure: str,
) -> None:
    """Exercise real PM admission, CE runtime, frozen requests and physical receipts."""
    import asyncio
    import hashlib
    import json

    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )
    from polaris.cells.events.fact_stream.public import (
        BootstrapFactStreamWorkspaceCommandV1,
        bootstrap_fact_stream_workspace,
        fact_stream_bootstrap_streams,
    )
    from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
    from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
        QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
        QueryFactoryProviderAttemptLifecycleReplayV1,
        query_factory_provider_attempt_lifecycle_replay,
    )

    semantic_plan = {
        "schema_version": "pm.task_intent.v1",
        "product_goal": "Deliver deterministic CLI output",
        "required_requirements": ["CLI accepts a name and prints a deterministic greeting"],
        "optional_requirements": [],
        "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
        "explicit_constraints": ["Use Python", "Keep data inside the workspace"],
        "tasks": [
            {
                "id": "TASK-A",
                "title": "Implement CLI",
                "goal": "Deliver deterministic CLI output",
                "acceptance": ["CLI accepts a name and prints a deterministic greeting"],
                "depends_on": [],
            }
        ],
    }
    endpoint = recipe_native_endpoint

    def response(body: dict[str, Any]) -> dict[str, Any]:
        endpoint.requests.append(body)
        identity = body["messages"][0]["content"]
        message: dict[str, Any] = {"role": "assistant", "content": None}
        payload: dict[str, Any]
        if "polaris.role_identity.v1:pm" in identity:
            payload = semantic_plan
            message["content"] = json.dumps(payload, ensure_ascii=False)
            finish = "stop"
        else:
            payload = _payload()
            endpoint.ce_calls += 1
            if recipe_failure in {"entrypoint_collection", "verification_command_change"} and endpoint.ce_calls == 1:
                payload["project_completion_contract"]["obligations"]["entrypoints"][0]["command"] = "npm run build"
            payload["project_completion_contract"]["obligations"]["verification"].append(
                {
                    "obligation_id": "typecheck",
                    "owner_task_id": "TASK-A",
                    "applicability": "required",
                    "modality": "build"
                    if (recipe_failure == "verification_modality" and endpoint.ce_calls == 1)
                    or recipe_failure == "verification_command_change"
                    else "lint",
                    "argv": ["npm", "run", "build"]
                    if recipe_failure == "verification_command_change" and endpoint.ce_calls > 1
                    else ["npx", "tsc", "--noEmit"],
                    "cwd": ".",
                    "command_authority_hash": None,
                    "covers_obligation_ids": ["source", "tests"],
                }
            )
            if recipe_failure == "bad_install" and endpoint.ce_calls == 1:
                payload["project_completion_contract"]["obligations"]["verification"][0]["argv"] = ["install"]
            if recipe_failure == "html_entrypoint":
                obligations = payload["project_completion_contract"]["obligations"]
                obligations["artifacts"].append(
                    {
                        "obligation_id": "html",
                        "path": "index.html",
                        "semantic_role": "entrypoint",
                        "applicability": "required",
                        "owner_task_id": "TASK-A",
                    }
                )
                obligations["entrypoints"][0].update(
                    {
                        "kind": "web",
                        "source_path": "index.html",
                        "runtime_path": "index.html",
                        "command": "npx http-server ." if endpoint.ce_calls == 1 else "npm start",
                    }
                )
            if recipe_failure == "consumer_task_ids":
                payload["construction_plan"]["shared_behavior_contract"]["invariants"] = [
                    {
                        "invariant_id": "INV-1",
                        "statement": "Greeting behavior is deterministic",
                        "owner_task_id": "TASK-A",
                        "consumer_task_ids": ["test_runner"] if endpoint.ce_calls == 1 else ["TASK-A"],
                        "covered_obligation_ids": ["source", "tests"],
                        "verification_examples": [
                            {"given": "name Ada", "when": "CLI executes", "then": "deterministic greeting"}
                        ],
                    }
                ]
                payload["construction_plan"]["task_plans"]["TASK-A"]["behavior_invariant_refs"] = ["INV-1"]
            payload["risk_flags"].append(
                {
                    "description": "Untrusted draft says null is forbidden and sha256:argv/cwd must be computed.",
                    "severity": "high",
                }
            )
            parameters = body["tools"][0]["function"]["parameters"]
            properties = parameters["properties"]
            if "base_candidate_hash" in properties:
                patch: dict[str, Any] = {
                    key: ({} if properties[key].get("type") == "object" else [])
                    for key in properties
                    if key not in {"base_candidate_hash", "diagnosis_hash"}
                }
                patch["base_candidate_hash"] = properties["base_candidate_hash"]["const"]
                patch["diagnosis_hash"] = properties["diagnosis_hash"]["const"]
                if recipe_failure in {"verification_modality", "bad_install", "verification_command_change"}:
                    row_index = -1 if recipe_failure in {"verification_modality", "verification_command_change"} else 0
                    corrected = dict(payload["project_completion_contract"]["obligations"]["verification"][row_index])
                    corrected.pop("command_authority_hash", None)
                    patch["verification_recipe_upserts"] = [corrected]
                    if recipe_failure == "verification_command_change":
                        patch["entrypoint_upserts"] = [
                            dict(payload["project_completion_contract"]["obligations"]["entrypoints"][0])
                        ]
                elif recipe_failure in {"entrypoint_collection", "html_entrypoint"}:
                    patch["entrypoint_upserts"] = [
                        dict(payload["project_completion_contract"]["obligations"]["entrypoints"][0])
                    ]
                else:
                    patch["behavior_invariant_upserts"] = [
                        dict(payload["construction_plan"]["shared_behavior_contract"]["invariants"][0])
                    ]
                payload = patch
            message["tool_calls"] = [
                {
                    "id": "local-ce-result",
                    "type": "function",
                    "function": {
                        "name": "submit_structured_role_output",
                        "arguments": json.dumps(payload, ensure_ascii=False),
                    },
                }
            ]
            finish = "tool_calls"
        endpoint.outputs.append(payload)
        return {
            "id": f"local-deferred-{len(endpoint.requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish}],
            "usage": {"prompt_tokens": 128, "completion_tokens": 128, "total_tokens": 256},
        }

    monkeypatch.setattr(endpoint, "response", response)
    from polaris.cells.factory.pipeline.internal.factory_stage_executor._mixin_02 import _Mixin02

    original_compile = _Mixin02._build_chief_engineer_portfolio_from_candidate
    compiler_observations = []

    def tracked_compile(self: Any, **kwargs: Any) -> Any:
        observation = {"candidate": kwargs["structured_output"], "error_code": None}
        compiler_observations.append(observation)
        try:
            result = original_compile(self, **kwargs)
        except Exception as exc:
            observation["error_code"] = getattr(exc, "code", type(exc).__name__)
            raise
        observation["portfolio_hash"] = result.portfolio_hash
        return result

    monkeypatch.setattr(_Mixin02, "_build_chief_engineer_portfolio_from_candidate", tracked_compile)

    async def exercise() -> None:
        catalog = tmp_path / ".polaris" / "catalog_contract.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps({"project_kind": "application"}), encoding="utf-8")
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                maintenance_reason="ce-deferred-recipes-localhost-test",
                streams=fact_stream_bootstrap_streams(),
            )
        )
        service = FactoryRunService(tmp_path)
        run = await service.create_run(
            FactoryConfig(
                name="CLI task intent", description="Python CLI", stages=["pm_planning", "chief_engineer_review"]
            )
        )
        await service.start_run(run.id)
        context = {
            "directive": "Create a Python CLI accepting a name and printing a deterministic greeting. Keep data inside the workspace and provide behavior tests.",
            "timeout": 5400,
        }
        pm = await service.execute_stage(run.id, "pm_planning", context)
        assert pm.status == "success", pm.output
        root = tmp_path / ".polaris" / "runtime"
        pm_path = root / "tasks" / "plan.json"
        pm_hash = hashlib.sha256(pm_path.read_bytes()).hexdigest()
        admitted = json.loads(pm_path.read_text(encoding="utf-8"))["tasks"]
        assert all(
            task["metadata"]["verification_authority"] == "chief_engineer" and task["verification_commands"] == []
            for task in admitted
        )
        pm_posts = len(endpoint.requests)
        ce = await service.execute_stage(run.id, "chief_engineer_review", context)
        assert ce.status == "success", ce.output
        assert len(compiler_observations) == (2 if recipe_failure == "consumer_task_ids" else 1)
        assert compiler_observations[-1]["portfolio_hash"]
        if recipe_failure == "consumer_task_ids":
            assert compiler_observations[0]["error_code"] == "blueprint_portfolio_behavior_contract_infeasible"
            before = compiler_observations[0]["candidate"]
            after = compiler_observations[1]["candidate"]
            assert before["project_completion_contract"] == after["project_completion_contract"]
            assert before["risk_flags"] == after["risk_flags"]
            invariant_before = dict(before["construction_plan"]["shared_behavior_contract"]["invariants"][0])
            invariant_after = dict(after["construction_plan"]["shared_behavior_contract"]["invariants"][0])
            invariant_before.pop("consumer_task_ids")
            invariant_after.pop("consumer_task_ids")
            assert invariant_before == invariant_after
        assert hashlib.sha256(pm_path.read_bytes()).hexdigest() == pm_hash
        assert not endpoint.errors
        replay = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id=run.id,
            )
        )
        starts = [fact for fact in replay.facts if fact.role == "chief_engineer" and fact.phase == "start"]
        terminals = [fact for fact in replay.facts if fact.role == "chief_engineer" and fact.phase == "terminal"]
        expected_calls = 2
        assert len(starts) == len(terminals) == endpoint.ce_calls == expected_calls
        captures = []
        for index, fact in enumerate(starts):
            ref = fact.context_snapshot_ref
            snapshot = json.loads((root / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
            body = snapshot["durable_view"]["physical_wire"]["body"]
            audit = snapshot["durable_view"]["final_request_qualification"]["final_request_context_audit"]
            assert body == endpoint.requests[pm_posts + index]
            if index == 1:
                final_user = body["messages"][-1]["content"]
                assert "command_authority_hash=null" in final_user
                assert "unaccepted draft risk_flags" in final_user
                if recipe_failure in {"verification_modality", "entrypoint_collection", "verification_command_change"}:
                    assert "verifier_modality_mismatch" in final_user
                elif recipe_failure in {"bad_install", "html_entrypoint"}:
                    assert "untrusted_verifier_command" in final_user
                else:
                    assert "test_runner" in final_user and "known_task_ids" in final_user
                if recipe_failure == "verification_modality":
                    assert '"allowed_recipe_modalities":["lint"]' in final_user
                    assert '"obligation_id":"typecheck"' in final_user
                elif recipe_failure == "entrypoint_collection":
                    assert '"collection":"entrypoints"' in final_user
                    assert '"allowed_recipe_collections":["verification"]' in final_user
                    assert '"obligation_id":"cli"' in final_user
                    assert "remove or relocate" in final_user
                    assert "required in an unaccepted draft" in final_user
                assert "sha256:argv/cwd must be computed" not in final_user
                assert "model must not compute, invent or prefix" in final_user
            coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
            assert coverage["required"] is False and coverage["pass"] is True
            assert "polaris.role_identity.v1:chief_engineer" in body["messages"][0]["content"]
            tool_names = {tool["function"]["name"] for tool in body["tools"]}
            assert tool_names == {"submit_structured_role_output"}
            assert tool_names.isdisjoint({"execute_command", "write_file", "edit_file", "delete_file"})
            captures.append(
                {
                    "context_snapshot_ref": ref,
                    "provider_request_id": fact.provider_request_id,
                    "provider_request": body,
                    "audit": audit,
                }
            )
        final_patch = endpoint.outputs[-1]
        assert "construction_plan" not in final_patch
        assert "project_completion_contract" not in final_patch
        if recipe_failure == "entrypoint_collection":
            assert (
                final_patch["entrypoint_upserts"]
                == _payload()["project_completion_contract"]["obligations"]["entrypoints"]
            )
            assert not final_patch.get("verification_recipe_upserts")
        elif recipe_failure in {"verification_modality", "bad_install", "verification_command_change"}:
            expected_modality = {
                "verification_modality": "lint",
                "bad_install": "environment_prep",
                "verification_command_change": "build",
            }[recipe_failure]
            assert final_patch["verification_recipe_upserts"][0]["modality"] == expected_modality
            assert "command_authority_hash" not in final_patch["verification_recipe_upserts"][0]
        elif recipe_failure == "html_entrypoint":
            assert final_patch["entrypoint_upserts"][0]["kind"] == "web"
            assert final_patch["entrypoint_upserts"][0]["command"] == "npm start"
        else:
            assert final_patch["behavior_invariant_upserts"][0]["consumer_task_ids"] == ["TASK-A"]
        assert any(
            "base_candidate_hash" in body["tools"][0]["function"]["parameters"]["properties"]
            for body in endpoint.requests[pm_posts:]
        )
        assert service._physical_attempt_coordinator(run.id).drain_snapshot().settled
        assert all(not (tmp_path / path).exists() for path in ("src/main.py", "tests/test_main.py", "pyproject.toml"))
        (tmp_path / "ce-binding-convergence-localhost-proof.json").write_text(
            json.dumps(
                {
                    "mode": "local_fake_openai_compat_http",
                    "compiler_observations": compiler_observations,
                    "recipe_failure": recipe_failure,
                    "factory_run_id": run.id,
                    "pm_status": pm.status,
                    "ce_status": ce.status,
                    "ce_physical_starts": len(starts),
                    "ce_physical_terminals": len(terminals),
                    "original_and_repaired_typed_outputs": endpoint.outputs[pm_posts:],
                    "pm_artifact_sha256": pm_hash,
                    "captures": captures,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    asyncio.run(exercise())
