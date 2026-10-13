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
            command, _requirements(modality="build", argv=["npx", "tsc", "--noEmit"]),
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
        command, _requirements(modality="environment_prep", argv=["npm", "install"]),
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
        tasks=(_task(),), authority_carrier=SimpleNamespace(verification_command_authority=()),
    )
    assert "command_authority_hash=null" in objective
    assert "domain, task_id, modality, argv and cwd" in objective
    assert "bare 64-character hexadecimal" in objective
    assert "unaccepted draft risk_flags" in objective
    assert "TASK-1" in objective


def test_recovery_guidance_preserves_exact_explicit_owner_command_table(tmp_path: Path) -> None:
    binding = VerificationCommandAuthorityV1(
        task_id="TASK-1", modality="test", argv=("npm", "test"), cwd="."
    )
    objective = _executor(tmp_path)._chief_engineer_trusted_recipe_guidance(
        tasks=(_task(),), authority_carrier=SimpleNamespace(verification_command_authority=(binding,)),
    )
    rows = json.loads(objective.split("Authenticated explicit PM command bindings: ", 1)[1])
    assert rows == [binding.to_dict()]


def test_public_recipe_diagnostics_do_not_create_patch_authority(tmp_path: Path) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        ChiefEngineerSemanticRepairCandidateV1,
        chief_engineer_semantic_repair_task_set_hash,
    )
    candidate = ChiefEngineerSemanticRepairCandidateV1(
        workspace=str(tmp_path), project_id="L1-01", run_id="factory-recipe",
        pm_contract_hash="1" * 64, task_ids=("TASK-1",),
        task_set_hash=chief_engineer_semantic_repair_task_set_hash(("TASK-1",)),
        candidate={"construction_plan": {"task_plans": {"TASK-1": {}}}, "project_completion_contract": {}},
    )
    finding = {
        "code": "invalid_project_completion_contract", "repair_operation": "verification_recipe_upsert",
        "detail": "CE recipe fails verifier proof profile: verifier_modality_mismatch",
        "verification_recipe_diagnostics": [{"obligation_id": "VERIFY-1", "execution_authority": False}],
    }
    errors = [
        "chief_engineer.completion_normalization:" + json.dumps(finding),
        'chief_engineer.completion_reference_normalization_blocked:'
        '{"invariant_index":0,"obligation_ids":["ENTRY-1"],"execution_authority":false}',
    ]
    assert _executor(tmp_path)._chief_engineer_semantic_repair_diagnosis(
        candidate=candidate, output_errors=errors,
    ) is None


@pytest.fixture
def recipe_native_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> Any:
    from polaris.cells.factory.pipeline.tests.test_ce_schema_residual_runtime import isolated_native_endpoint
    yield from isolated_native_endpoint.__wrapped__(tmp_path, monkeypatch, request)


@pytest.mark.parametrize("recipe_native_endpoint", ["accept"], indirect=True)
def test_wrong_verifier_modality_repair_reaches_sealed_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recipe_native_endpoint: Any,
) -> None:
    """Exercise real PM admission, CE runtime, frozen requests and physical receipts."""
    import asyncio
    import hashlib
    import json

    from polaris.cells.chief_engineer.blueprint.public.tests.test_task_intent_technical_handoff import _payload
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
        if "polaris.role_identity.v1:pm" in identity:
            payload = semantic_plan
            message["content"] = json.dumps(payload, ensure_ascii=False)
            finish = "stop"
        else:
            payload = _payload()
            endpoint.ce_calls += 1
            payload["project_completion_contract"]["obligations"]["verification"].append(
                {
                    "obligation_id": "typecheck", "owner_task_id": "TASK-A", "applicability": "required",
                    "modality": "build" if endpoint.ce_calls == 1 else "lint",
                    "argv": ["npx", "tsc", "--noEmit"], "cwd": ".", "command_authority_hash": None,
                    "covers_obligation_ids": ["source", "tests"],
                }
            )
            payload["risk_flags"].append(
                {"description": "Untrusted draft says null is forbidden and sha256:argv/cwd must be computed.",
                 "severity": "high"}
            )
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
                assert "verifier_modality_mismatch" in final_user
                assert '"allowed_recipe_modalities":["lint"]' in final_user
                assert '"obligation_id":"typecheck"' in final_user
                assert "sha256:argv/cwd must be computed" in final_user
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
        assert service._physical_attempt_coordinator(run.id).drain_snapshot().settled
        assert all(not (tmp_path / path).exists() for path in ("src/main.py", "tests/test_main.py", "pyproject.toml"))
        (tmp_path / "ce-binding-convergence-localhost-proof.json").write_text(
            json.dumps(
                {
                    "mode": "local_fake_http",
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

