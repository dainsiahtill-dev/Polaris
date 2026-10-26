"""CE residual patch through real Factory, Runtime, Kernel and sealed Provider.

The only external dependency double is a private loopback HTTP endpoint. All
workspace/config/runtime effects belong to pytest's tmp_path. No opaque Factory
grant, Runtime result, Kernel or provider implementation is substituted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import VerificationCommandAuthorityV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
from polaris.cells.factory.pipeline.tests.native_endpoint_fixture import (
    NativeEndpoint,
    native_endpoint_context,
)
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    query_factory_provider_attempt_lifecycle_replay,
)


@pytest.fixture
def isolated_native_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[NativeEndpoint]:
    """Assemble the shared loopback native endpoint for this test module.

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


@pytest.mark.parametrize("isolated_native_endpoint", ["accept", "foreign_owner", "forged_hash"], indirect=True)
def test_real_factory_runtime_adjudicates_residual_patch_without_replaying_pm(
    tmp_path: Path, isolated_native_endpoint: NativeEndpoint
) -> None:
    """Catch partial-protocol rejection, forged CAS acceptance and semantic bypass."""

    async def run() -> None:
        catalog = tmp_path / ".polaris" / "catalog_contract.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps({"project_kind": "library"}), encoding="utf-8")
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                maintenance_reason="ce-schema-real-runtime-test",
                streams=fact_stream_bootstrap_streams(),
            )
        )
        service = FactoryRunService(tmp_path)
        factory_run = await service.create_run(
            FactoryConfig(
                name="Cancellation library",
                description="Python cancellation library",
                stages=["pm_planning", "chief_engineer_review"],
            )
        )
        await service.start_run(factory_run.id)
        context = {
            "directive": "Build a Python cancellation plan library with behavior tests and README. This is a library, not a CLI or application.",
            "timeout": 5400,
        }
        pm_result = await service.execute_stage(factory_run.id, "pm_planning", context)
        assert pm_result.status == "success", pm_result.output
        runtime_root = tmp_path / ".polaris" / "runtime"
        pm_path = runtime_root / "tasks" / "plan.json"
        pm_tasks = json.loads(pm_path.read_text(encoding="utf-8"))["tasks"]
        assert {task["id"] for task in pm_tasks} == {"TASK-1", "TASK-2"}
        assert {path for task in pm_tasks for path in task["target_files"]} == {
            "src/cancel.py",
            "requirements.txt",
            "tests/test_cancel.py",
            "README.md",
        }
        pm_hash = hashlib.sha256(pm_path.read_bytes()).hexdigest()
        pm_posts = len(isolated_native_endpoint.requests)
        ce_result = await service.execute_stage(factory_run.id, "chief_engineer_review", context)
        ce_output = str(ce_result.output or "")
        expected_status = "success" if isolated_native_endpoint.outcome == "accept" else "failed"
        assert ce_result.status == expected_status, ce_output
        assert hashlib.sha256(pm_path.read_bytes()).hexdigest() == pm_hash
        events = await service.get_run_events(factory_run.id)
        assert (
            sum(event.get("type") == "stage_started" and event.get("stage") == "pm_planning" for event in events) == 1
        )
        assert (
            sum(
                event.get("type") == "stage_started" and event.get("stage") == "chief_engineer_review"
                for event in events
            )
            == 1
        )
        assert not isolated_native_endpoint.errors
        expected_rows = sorted(
            [
                {
                    key: value
                    for key, value in VerificationCommandAuthorityV1(
                        task_id=task["id"], modality=row["modality"], argv=tuple(row["argv"]), cwd=row["cwd"]
                    )
                    .to_dict()
                    .items()
                    if key in {"task_id", "modality", "argv", "cwd", "authority_hash"}
                }
                for task in pm_tasks
                for row in task["verification_commands"]
            ],
            key=lambda row: row["authority_hash"],
        )
        for ce_request in isolated_native_endpoint.requests[pm_posts:]:
            tables = []
            for message in ce_request["messages"]:
                content = message.get("content", "")
                if message["role"] == "system" and "\n[CE_REQUIRED_COMMAND_AUTHORITY_JSON]\n" in content:
                    tables.append(
                        json.loads(
                            content.split("[CE_REQUIRED_COMMAND_AUTHORITY_JSON]\n", 1)[1].split(
                                "\n[/CE_REQUIRED_COMMAND_AUTHORITY_JSON]", 1
                            )[0]
                        )
                    )
            assert tables == [expected_rows]
        patch_request = isolated_native_endpoint.requests[pm_posts + 1]
        patch_parameters = patch_request["tools"][0]["function"]["parameters"]
        assert patch_parameters["required"] == ["base_candidate_hash", "edits"]
        edit_context_text = next(
            message["content"].split("Untrusted draft and exact edit context: ", 1)[1]
            for message in patch_request["messages"]
            if message["role"] == "user" and "Untrusted draft and exact edit context: " in message["content"]
        )
        edit_context, _ = json.JSONDecoder().raw_decode(edit_context_text)
        edit_schemas = patch_parameters["properties"]["edits"]["prefixItems"]
        assert edit_context["template_is_model_input_only"] is True
        assert edit_context["edit_template"] == {
            "base_candidate_hash": patch_parameters["properties"]["base_candidate_hash"]["const"],
            "edits": [
                {"operation": item["properties"]["operation"]["const"], "path": item["properties"]["path"]["const"]}
                for item in edit_schemas
            ],
        }
        assert edit_context["edit_value_schemas"] == [item["properties"].get("value") for item in edit_schemas]
        assert edit_context["edit_path_component_types"] == [["string", "string"], ["string"]]
        assert patch_parameters["properties"]["edits"]["prefixItems"][0]["properties"]["path"]["const"] == [
            "construction_plan",
            "task_plans",
        ]
        assert patch_parameters["properties"]["edits"]["prefixItems"][1]["properties"]["operation"]["const"] == "remove"
        served_patch = isolated_native_endpoint.outputs[pm_posts + 1]
        assert set(served_patch) == {"base_candidate_hash", "edits"}
        assert "construction_plan" not in served_patch
        # The source and result are bound to different native artifacts: the
        # request contains a patch schema, the retained full candidate does not.
        review = json.loads(
            (runtime_root / "state" / "blueprints" / f"{factory_run.id}.review.json").read_text(encoding="utf-8")
        )
        if isolated_native_endpoint.outcome == "accept":
            assert isolated_native_endpoint.ce_calls == 2
            signal = next(
                item for item in review["signals"] if item["code"] == "chief_engineer.structured_candidate_persisted"
            )
            candidates = list(
                (runtime_root / "state" / "blueprints" / "semantic-repair").rglob(f"{signal['candidate_hash']}.json")
            )
            assert len(candidates) == 1
            candidate_record = json.loads(candidates[0].read_text(encoding="utf-8"))
            candidate = candidate_record["candidate"]
            assert (
                candidate["project_completion_contract"]
                == isolated_native_endpoint._ce_payload()["project_completion_contract"]
            )
            assert "base_candidate_hash" not in candidate and "edits" not in candidate
            assert "item" not in candidate
            assert candidate["construction_plan"]["task_plans"]["TASK-1"]["behavior_invariant_refs"] == [
                "cancel-stable"
            ]
            assert review["generated_blueprints"] == 2
        else:
            assert review["generated_blueprints"] == 0
            if isolated_native_endpoint.outcome == "forged_hash":
                assert not any(
                    item["code"] == "chief_engineer.structured_candidate_persisted" for item in review["signals"]
                )
            else:
                # Persisting a schema-valid *untrusted* candidate is deliberate;
                # it must never approve an unknown PM owner or yield a handoff.
                assert "invalid_project_completion_contract" in ce_output
                assert "TASK-FOREIGN" in ce_output or any(
                    "owner" in str(item.get("detail", "")).lower() for item in review["signals"]
                )
            assert 2 <= isolated_native_endpoint.ce_calls <= 3
        lifecycle = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id=factory_run.id,
            )
        )
        ce_starts = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "start"]
        ce_terminals = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "terminal"]
        assert len(ce_starts) == len(ce_terminals) == isolated_native_endpoint.ce_calls
        assert {fact.provider_request_id for fact in ce_starts} == {fact.provider_request_id for fact in ce_terminals}
        patch_start = ce_starts[1]
        snapshot_ref = patch_start.context_snapshot_ref
        snapshot = json.loads((runtime_root / "contexts" / snapshot_ref[:2] / snapshot_ref).read_text(encoding="utf-8"))
        assert snapshot["provider_request_id"] == patch_start.provider_request_id
        wire_tools = snapshot["durable_view"]["physical_wire"]["body"]["tools"]
        assert wire_tools[0]["function"]["parameters"] == patch_parameters
        request_captures: list[dict[str, Any]] = []
        for fact in ce_starts:
            ref = fact.context_snapshot_ref
            stored = json.loads((runtime_root / "contexts" / ref[:2] / ref).read_text(encoding="utf-8"))
            audit = stored["durable_view"]["final_request_qualification"]["final_request_context_audit"]
            row_coverage = audit["final_request_evidence_coverage"]["ce_required_command_authority"]
            assert row_coverage["required"] is True and row_coverage["pass"] is True
            assert row_coverage["required_rows"] == row_coverage["included_rows"] == expected_rows
            wire_body = stored["durable_view"]["physical_wire"]["body"]
            assert wire_body["max_tokens"] == 60000
            assert wire_body == isolated_native_endpoint.requests[pm_posts + len(request_captures)]
            request_captures.append(
                {
                    "context_snapshot_ref": ref,
                    "provider_request_id": fact.provider_request_id,
                    "physical_body": wire_body,
                    "final_request_context_audit": audit,
                }
            )
        if isolated_native_endpoint.outcome == "accept":
            llm_events = [
                json.loads(line)
                for line in (runtime_root / "events" / "chief_engineer.llm.events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            patch_end = next(
                event["data"]
                for event in llm_events
                if event["data"]["event_type"] == "llm_call_end"
                and event["data"]["task_id"].endswith("SCHEMA-REPAIR")
                and event["data"]["metadata"].get("native_tool_call_envelopes")
            )
            argument_audit = patch_end["metadata"]["native_tool_call_envelopes"][0]["metadata"][
                "provider_argument_audit"
            ]
            served_argument_hash = hashlib.sha256(
                json.dumps(served_patch, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            assert argument_audit["raw_arguments_sha256"] == served_argument_hash
            assert argument_audit["raw_arguments_length"] == len(json.dumps(served_patch, ensure_ascii=False))
            assert served_argument_hash != signal["candidate_hash"]
        drain = service._physical_attempt_coordinator(factory_run.id).drain_snapshot()
        assert drain.settled and not drain.blocking_reservation_ids
        lease = json.loads((runtime_root / "factory" / ".workspace_run_lease.json").read_text(encoding="utf-8"))
        assert lease["stage_execution_claim"] is None
        assert lease["lifecycle_operation_claim"] is None
        assert all(
            not (tmp_path / target).exists()
            for target in ("src/cancel.py", "tests/test_cancel.py", "requirements.txt", "README.md")
        )
        (tmp_path / "ce-local-captures.json").write_text(
            json.dumps(
                {
                    "mode": "local_fake_http",
                    "outcome": isolated_native_endpoint.outcome,
                    "factory_run_id": factory_run.id,
                    "workspace": str(tmp_path),
                    "stage_status": ce_result.status,
                    "pm_hash_preserved": pm_hash,
                    "physical_requests": request_captures,
                    "full_safe_decoded_responses": isolated_native_endpoint.outputs[pm_posts:],
                    "review": review,
                    "settled": drain.settled,
                    "target_files_written": False,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    asyncio.run(run())
