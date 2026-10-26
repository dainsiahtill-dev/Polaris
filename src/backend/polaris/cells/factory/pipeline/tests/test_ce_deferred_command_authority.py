"""CE bootstrap planning never invents pre-blueprint command authority."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.factory.pipeline.internal import factory_stage_executor as stage_executor_module
from polaris.cells.factory.pipeline.internal.factory_run_service import FactoryConfig, FactoryRun, FactoryRunStatus
from polaris.cells.factory.pipeline.tests._characterization_helpers import _executor, _factory_stage_context
from polaris.cells.factory.pipeline.tests.native_endpoint_fixture import (
    NativeEndpoint,
    native_endpoint_context,
)
from polaris.cells.factory.pipeline.tests.test_semantic_pm_ce_handoff import _intent
from polaris.cells.roles.kernel.internal.context_gateway.context_override_processor import ContextOverrideProcessor
from polaris.cells.roles.kernel.internal.context_gateway.gateway_helpers import ce_command_planning_rows
from polaris.cells.roles.kernel.internal.llm_caller.context_audit._payloads import _ce_required_command_row_coverage


class _RequestCaptured(BaseException):
    """Stop at the production Factory-to-runtime boundary without model I/O."""


async def _capture_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, task: dict[str, Any]) -> Any:
    executor = _executor(tmp_path)
    original_authority = executor._load_chief_engineer_portfolio_authority

    async def authority(**kwargs: Any) -> Any:
        committed = await original_authority(**kwargs)
        return replace(
            committed,
            verification_command_authority=executor._chief_engineer_verification_command_authority(kwargs["pm_tasks"]),
        )

    monkeypatch.setattr(executor, "_load_chief_engineer_portfolio_authority", authority)
    executor._write_json_artifact("tasks/plan.json", {"tasks": [task]})
    captured: list[Any] = []

    class CaptureRuntime:
        async def execute_role_task(self, command: Any) -> Any:
            captured.append(command)
            raise _RequestCaptured

    monkeypatch.setattr(stage_executor_module, "RoleRuntimeService", CaptureRuntime)
    run = FactoryRun(
        id="factory-ce-deferred-command-bootstrap",
        config=FactoryConfig(name="project-ce-deferred-command-bootstrap"),
        status=FactoryRunStatus.RUNNING,
        created_at="2026-10-13T00:00:00+00:00",
    )
    with pytest.raises(_RequestCaptured):
        await executor._execute_chief_engineer_review(run, _factory_stage_context())
    assert len(captured) == 1
    return captured[0]


@pytest.mark.asyncio
async def test_delegated_ce_bootstrap_omits_nonexistent_required_command_table(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _intent()
    task["explicit_constraints"] = ["Use Python", "tsc --noEmit", "npm install && npm run build"]
    command = await _capture_bootstrap(tmp_path, monkeypatch, task)
    assert "ce_required_command_authority" not in command.context
    assert "ce_required_command_authority" not in command.context["pm_task_contract"]
    assert "verification_command_authority" not in command.context["project_completion_authority"]
    assert command.context["pm_task_contract"]["tasks"][0]["explicit_constraints"] == task["explicit_constraints"]
    assert command.context["portfolio_tasks"][0]["verification_authority"] == "chief_engineer"
    projected = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(command.context)
    assert projected is not None
    coverage = _ce_required_command_row_coverage(command, [projected])
    assert coverage == {"required": False, "pass": True, "required_rows": [], "included_rows": []}


@pytest.mark.asyncio
async def test_explicit_typed_command_table_is_preserved_exactly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task = _intent()
    task["verification_commands"] = [{"modality": "test", "argv": ["python", "-m", "unittest", "discover"], "cwd": "."}]
    command = await _capture_bootstrap(tmp_path, monkeypatch, task)
    rows = command.context["ce_required_command_authority"]
    assert rows == command.context["pm_task_contract"]["ce_required_command_authority"]
    assert rows[0]["argv"] == ["python", "-m", "unittest", "discover"]
    assert rows[0]["cwd"] == "."
    projected = ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(command.context)
    assert projected is not None
    coverage = _ce_required_command_row_coverage(command, [projected])
    assert coverage["required"] and coverage["pass"]
    assert coverage["included_rows"] == rows


@pytest.mark.parametrize("claimed_phase", ["bootstrap", "deferred", "director", "chief_engineer"])
def test_explicit_empty_authority_cannot_be_bypassed_by_declared_phase(claimed_phase: str) -> None:
    context = {"ce_required_command_authority": [], "verification_recipe_state": claimed_phase}
    with pytest.raises(ValueError, match="ce_required_command_authority_invalid"):
        ContextOverrideProcessor(detect_prompt_injection=True).process_context_override(context)
    with pytest.raises(ValueError, match="ce_required_command_authority_invalid"):
        ce_command_planning_rows([])


@pytest.fixture
def deferred_native_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[NativeEndpoint]:
    """Assemble the shared loopback native endpoint for the deferred CE test.

    The endpoint resource, its network deny, provider configuration and
    teardown are owned by native_endpoint_fixture; this fixture only performs
    pytest assembly of that shared resource.
    """
    with native_endpoint_context(
        tmp_path=tmp_path,
        monkeypatch=monkeypatch,
        outcome=str(request.param),
        endpoint_class=NativeEndpoint,
    ) as endpoint:
        yield endpoint


@pytest.mark.parametrize("deferred_native_endpoint", ["accept", "retry"], indirect=True)
def test_delegated_ce_factory_request_reaches_sealed_localhost_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    deferred_native_endpoint: Any,
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
    from polaris.cells.factory.pipeline.public.service import FactoryRunService
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
    endpoint = deferred_native_endpoint

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
            if endpoint.outcome == "retry":
                if endpoint.ce_calls == 1:
                    payload["construction_plan"].pop("task_plans")
                    payload["item"] = "undeclared portfolio member"
                else:
                    original = endpoint.outputs[1]
                    base_hash = hashlib.sha256(
                        json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                    ).hexdigest()
                    payload = {
                        "base_candidate_hash": base_hash,
                        "edits": [
                            {
                                "operation": "add",
                                "path": ["construction_plan", "task_plans"],
                                "value": _payload()["construction_plan"]["task_plans"],
                            },
                            {"operation": "remove", "path": ["item"]},
                        ],
                    }
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
        expected_calls = 2 if endpoint.outcome == "retry" else 1
        assert len(starts) == len(terminals) == endpoint.ce_calls == expected_calls
        if endpoint.outcome == "retry":
            assert "item" in endpoint.outputs[pm_posts]
            assert "item" not in endpoint.outputs[pm_posts + 1]
            review = json.loads((root / "state" / "blueprints" / f"{run.id}.review.json").read_text(encoding="utf-8"))
            assert any("schema" in item["code"] for item in review["signals"])
        captures = []
        for index, fact in enumerate(starts):
            ref = fact.context_snapshot_ref
            snapshot = json.loads((root / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
            body = snapshot["durable_view"]["physical_wire"]["body"]
            audit = snapshot["durable_view"]["final_request_qualification"]["final_request_context_audit"]
            assert body == endpoint.requests[pm_posts + index]
            coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
            assert coverage["required"] is False and coverage["pass"] is True
            assert "polaris.role_identity.v1:chief_engineer" in body["messages"][0]["content"]
            tool_names = {tool["function"]["name"] for tool in body["tools"]}
            assert "submit_structured_role_output" in tool_names
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
        (tmp_path / "ce-deferred-localhost-proof.json").write_text(
            json.dumps(
                {
                    "mode": "local_fake_http",
                    "factory_run_id": run.id,
                    "pm_status": pm.status,
                    "ce_status": ce.status,
                    "ce_physical_starts": len(starts),
                    "ce_physical_terminals": len(terminals),
                    "captures": captures,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    asyncio.run(exercise())


@pytest.mark.parametrize("invalid", ["legacy", "metadata_only", "verification_delegation", "empty_acceptance"])
def test_retry_feasibility_does_not_upgrade_invalid_pm_delegation(invalid: str) -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
    )

    task = _intent()
    if invalid == "legacy":
        task["metadata"]["planning_contract_version"] = ""
        task.pop("schema_version")
    elif invalid == "metadata_only":
        task.pop("schema_version")
    elif invalid == "verification_delegation":
        task["metadata"]["verification_authority"] = "pm"
    else:
        for key in ("observable_acceptance", "acceptance_criteria", "acceptance"):
            task[key] = []
    with pytest.raises(ValueError):
        project_chief_engineer_delivery_depth_feasibility_from_pm_tasks({}, pm_tasks=[task])


def test_retry_feasibility_preserves_semantic_depth_authority() -> None:
    from polaris.cells.chief_engineer.blueprint.public import (
        project_chief_engineer_delivery_depth_feasibility_from_pm_tasks,
    )
    from polaris.cells.chief_engineer.blueprint.public.tests.portfolio_payload_fixture import (
        synthetic_task_intent_portfolio_payload as _payload,
    )

    task = _intent()
    task["id"] = "TASK-A"
    task["delivery_depth_contract"] = {"language": "python", "minimums": {"min_prod_files": 2, "min_test_files": 1}}
    result = project_chief_engineer_delivery_depth_feasibility_from_pm_tasks(_payload(), pm_tasks=[task])
    assert result["actual"] == {"prod_files": 1, "test_files": 1}
    assert result["ok"] is False
    assert result["deficits"] == [{"metric": "prod_files", "actual": 1, "required": 2, "deficit": 1}]
