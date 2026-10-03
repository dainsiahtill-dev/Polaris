"""Actual Factory/Kernel wire cannot advertise a finally denied obligation."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
from polaris.cells.factory.pipeline.tests import test_ce_schema_residual_runtime as native_fixture
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    query_factory_provider_attempt_lifecycle_replay,
)

_BASE_ENDPOINT = native_fixture._NativeEndpoint


class _EffectiveEndpoint(_BASE_ENDPOINT):
    """Only remote model answers are substituted; runtime authority stays real."""

    @staticmethod
    def _ce_payload() -> dict[str, Any]:
        payload = _BASE_ENDPOINT._ce_payload()
        payload["project_completion_contract"]["obligations"]["artifacts"].append(
            {
                "obligation_id": "OBL-3",
                "path": "unowned-config.json",
                "semantic_role": "config",
                "applicability": "required",
                "owner_task_id": "TASK-1",
            }
        )
        payload["construction_plan"]["shared_behavior_contract"]["invariants"][0]["covered_obligation_ids"].append(
            "OBL-3"
        )
        return payload

    def response(self, request: dict[str, Any]) -> dict[str, Any]:
        role_text = "\n".join(str(message.get("content") or "") for message in request.get("messages", []))
        if self.ce_calls < 2 or "polaris.role_identity.v1:pm" in role_text:
            return super().response(request)
        self.requests.append(deepcopy(request))
        self.ce_calls += 1
        assert self.ce_calls == 3, "Unexpected repeated semantic repair"
        marker = "Exact base candidate patch context"
        start = role_text.index("{", role_text.index(marker))
        context, _ = json.JSONDecoder().raw_decode(role_text[start:])
        assert "OBL-3" not in context["allowed_completion_obligation_ids"]
        assert {"artifact-source", "artifact-test"}.issubset(context["allowed_completion_obligation_ids"])
        assert context["effective_obligation_view"]["final_roster_complete"] is True
        behavior = deepcopy(self._ce_payload()["construction_plan"]["shared_behavior_contract"]["invariants"][0])
        behavior["covered_obligation_ids"] = ["artifact-source", "artifact-test"]
        payload = {
            "base_candidate_hash": context["base_candidate_hash"],
            "diagnosis_hash": context["diagnosis_hash"],
            "artifact_upserts": [],
            "entrypoint_upserts": [],
            "entrypoint_remove_obligation_ids": [],
            "behavior_invariant_upserts": [behavior],
            "task_behavior_ref_replacements": {},
        }
        self.outputs.append(deepcopy(payload))
        function = next(
            tool["function"] for tool in request["tools"] if tool["function"]["name"] == "submit_structured_role_output"
        )
        return {
            "id": "fixture-effective-completion",
            "object": "chat.completion",
            "created": 0,
            "model": request.get("model", "gpt-4.1"),
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "tool_calls",
                    "message": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "fixture-effective-call",
                                "type": "function",
                                "function": {
                                    "name": function["name"],
                                    "arguments": json.dumps(payload, ensure_ascii=False),
                                },
                            }
                        ],
                    },
                }
            ],
            "usage": {"prompt_tokens": 128, "completion_tokens": 128, "total_tokens": 256},
        }


@pytest.fixture
def effective_native_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> Iterator[_EffectiveEndpoint]:
    monkeypatch.setattr(native_fixture, "_NativeEndpoint", _EffectiveEndpoint)
    # Reuse its real loopback server, process-local config and deny-external-network
    # isolation rather than duplicating transport/credential fixtures.
    fixture_body = cast(Any, native_fixture.isolated_native_endpoint).__wrapped__
    yield from fixture_body(tmp_path, monkeypatch, request)


@pytest.mark.parametrize("effective_native_endpoint", ["accept"], indirect=True)
def test_real_semantic_repair_wire_uses_final_obligation_roster(
    tmp_path: Path,
    effective_native_endpoint: _EffectiveEndpoint,
) -> None:
    async def run() -> None:
        catalog = tmp_path / ".polaris" / "catalog_contract.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps({"project_kind": "library"}), encoding="utf-8")
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                maintenance_reason="effective-obligation-native-runtime-test",
                streams=fact_stream_bootstrap_streams(),
            )
        )
        service = FactoryRunService(tmp_path)
        factory = await service.create_run(
            FactoryConfig(
                name="Cancellation library",
                description="Python cancellation library",
                stages=["pm_planning", "chief_engineer_review"],
            )
        )
        await service.start_run(factory.id)
        context = {
            "directive": "Build a Python cancellation plan library with behavior tests and README. This is a library, not a CLI or application.",
            "timeout": 1200,
        }
        pm = await service.execute_stage(factory.id, "pm_planning", context)
        assert pm.status == "success", pm.output
        runtime = tmp_path / ".polaris" / "runtime"
        plan = runtime / "tasks" / "plan.json"
        before_pm = hashlib.sha256(plan.read_bytes()).hexdigest()
        ce = await service.execute_stage(factory.id, "chief_engineer_review", context)
        assert not effective_native_endpoint.errors, effective_native_endpoint.errors
        assert ce.status == "success", ce.output
        assert hashlib.sha256(plan.read_bytes()).hexdigest() == before_pm
        review = json.loads(
            (runtime / "state" / "blueprints" / f"{factory.id}.review.json").read_text(encoding="utf-8")
        )
        assert review["generated_blueprints"] == 2
        assert effective_native_endpoint.ce_calls == 3
        assert not effective_native_endpoint.errors
        events = await service.get_run_events(factory.id)
        assert sum(e.get("type") == "stage_started" and e.get("stage") == "pm_planning" for e in events) == 1
        lifecycle = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id=factory.id,
            )
        )
        starts = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "start"]
        terminals = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "terminal"]
        assert len(starts) == len(terminals) == 3
        assert {fact.provider_request_id for fact in starts} == {fact.provider_request_id for fact in terminals}
        last = starts[-1]
        snapshot = json.loads(
            (runtime / "contexts" / last.context_snapshot_ref[:2] / last.context_snapshot_ref).read_text(
                encoding="utf-8"
            )
        )
        assert snapshot["provider_request_id"] == last.provider_request_id
        wire = snapshot["durable_view"]["physical_wire"]["body"]
        wire_text = "\n".join(str(row.get("content") or "") for row in wire["messages"])
        marker = "Exact base candidate patch context"
        parsed, _ = json.JSONDecoder().raw_decode(wire_text[wire_text.index("{", wire_text.index(marker)) :])
        assert "OBL-3" not in parsed["allowed_completion_obligation_ids"]
        assert parsed["effective_obligation_view"]["final_roster_complete"] is True
        drain = service._physical_attempt_coordinator(factory.id).drain_snapshot()
        assert drain.settled and not drain.blocking_reservation_ids
        assert not (tmp_path / "unowned-config.json").exists()
        # CE stage proof only: deliberately no Director/business-source effect.
        assert not (tmp_path / "src/cancel.py").exists()

    asyncio.run(run())
