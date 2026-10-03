"""Inert CE shape recovery through native Factory/Kernel with immutable PM."""

from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Iterator
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.internal.factory_stage_executor import OrchestrationStageExecutor
from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
from polaris.cells.factory.pipeline.tests import test_ce_schema_residual_runtime as native_fixture
from polaris.cells.factory.pipeline.tests.test_ce_schema_residual_runtime import _NativeEndpoint
from polaris.cells.roles.runtime.public.contracts import RoleExecutionResultV1


def test_composed_group_split_emits_only_actual_inert_path_normalization() -> None:
    """Group-ID splitting must not invent a path correction for an unchanged row."""
    source = _NativeEndpoint._ce_payload()
    source["project_completion_contract"]["obligations"]["artifacts"].extend(
        {
            "obligation_id": obligation_id,
            "path": path,
            "semantic_role": "assets",
            "applicability": "not_applicable",
            "owner_task_id": None,
        }
        for obligation_id, path in (("inert", "assets/"), ("group", "a"), ("group", "b"))
    )
    before = deepcopy(source)
    prior = RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace="fixture",
        metadata={"structured_output": source},
    )
    owner = SimpleNamespace(
        workspace="fixture",
        _chief_engineer_structured_output_contract=OrchestrationStageExecutor._chief_engineer_structured_output_contract,
    )
    recovered = OrchestrationStageExecutor._recover_chief_engineer_portfolio_structural_result(
        owner,
        result=prior,
        portfolio_task_ids=("TASK-1", "TASK-2"),
    )
    assert recovered is not prior and recovered.ok
    evidence = recovered.metadata["chief_engineer_portfolio_structural_recovery"]
    finding = {"obligation_id": "inert", "source_path": "assets/", "recovered_path": "assets"}
    assert evidence["artifact_path_normalizations"] == [finding]
    assert "split_shared_artifact_obligation_ids" in evidence["repair_codes"]
    rows = recovered.metadata["structured_output"]["project_completion_contract"]["obligations"]["artifacts"]
    assert [row["path"] for row in rows[-3:]] == ["assets", "a", "b"]
    assert rows[-2]["obligation_id"] == "group"
    assert rows[-1]["obligation_id"] != "group"
    assert len({row["obligation_id"] for row in rows}) == len(rows)
    assert source == before
    source_hash = hashlib.sha256(
        json.dumps(before, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    recovered_hash = hashlib.sha256(
        json.dumps(
            recovered.metadata["structured_output"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    ).hexdigest()
    signals: list[dict[str, Any]] = []
    OrchestrationStageExecutor._append_chief_engineer_structural_recovery_signal(
        result=recovered,
        stage_signals=signals,
        task_id="CE",
    )
    assert signals[0]["artifact_path_normalizations"] == [finding]
    assert signals[0]["source_hash"] == evidence["source_hash"] == source_hash
    assert signals[0]["recovered_hash"] == evidence["recovered_hash"] == recovered_hash
    # Mutating the carried evidence cannot rewrite an already emitted signal.
    evidence["artifact_path_normalizations"][0]["recovered_path"] = "tampered"
    assert signals[0]["artifact_path_normalizations"] == [finding]
    assert signals[0]["provider_call_consumed"] is False


def test_structural_recovery_signal_keeps_exact_path_finding_without_authority() -> None:
    finding = {"obligation_id": "inert-resources", "source_path": "data/resources/", "recovered_path": "data/resources"}
    result = RoleExecutionResultV1(
        ok=True,
        status="completed",
        role="chief_engineer",
        workspace="fixture",
        metadata={
            "chief_engineer_portfolio_structural_recovery": {
                "recovered": True,
                "source_hash": "a" * 64,
                "recovered_hash": "b" * 64,
                "repair_codes": ["normalize_inert_artifact_terminal_separator"],
                "artifact_path_normalizations": [finding],
            }
        },
    )
    signals: list[dict[str, Any]] = []
    OrchestrationStageExecutor._append_chief_engineer_structural_recovery_signal(
        result=result, stage_signals=signals, task_id="CE"
    )
    assert signals[0]["artifact_path_normalizations"] == [finding]
    assert signals[0]["source_hash"] == "a" * 64
    assert signals[0]["recovered_hash"] == "b" * 64
    assert signals[0]["provider_call_consumed"] is False
    assert "handoff_ready" not in signals[0]


class _ArtifactShapeEndpoint(_NativeEndpoint):
    def response(self, request: dict[str, Any]) -> dict[str, Any]:
        text = "\n".join(str(row.get("content") or "") for row in request.get("messages", []))
        if "polaris.role_identity.v1:pm" in text:
            return super().response(request)
        self.requests.append(deepcopy(request))
        self.ce_calls += 1
        assert self.ce_calls <= 3, "CE recovery must remain bounded"
        payload = _NativeEndpoint._ce_payload()
        payload["project_completion_contract"]["obligations"]["artifacts"].append(
            {
                "obligation_id": "inert-resources",
                "path": "data/resources/",
                "semantic_role": "assets",
                "applicability": "not_applicable",
                "owner_task_id": None,
            }
        )
        if self.outcome == "foreign_owner":
            payload["project_completion_contract"]["obligations"]["artifacts"][0]["owner_task_id"] = "FOREIGN"
        self.outputs.append(deepcopy(payload))
        function = next(
            tool["function"] for tool in request["tools"] if tool["function"]["name"] == "submit_structured_role_output"
        )
        return {
            "id": "fixture-artifact-shape",
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
                                "id": f"shape-{self.ce_calls}",
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
def artifact_native_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[_ArtifactShapeEndpoint]:
    monkeypatch.setattr(native_fixture, "_NativeEndpoint", _ArtifactShapeEndpoint)
    yield from cast(Any, native_fixture.isolated_native_endpoint).__wrapped__(tmp_path, monkeypatch, request)


@pytest.mark.parametrize("artifact_native_endpoint", ["accept", "foreign_owner"], indirect=True)
def test_native_shape_recovery_preserves_pm_and_final_authority(
    tmp_path: Path, artifact_native_endpoint: _ArtifactShapeEndpoint
) -> None:
    async def run() -> None:
        catalog = tmp_path / ".polaris" / "catalog_contract.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps({"project_kind": "library"}), encoding="utf-8")
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                maintenance_reason="artifact-shape-native-test",
                streams=fact_stream_bootstrap_streams(),
            )
        )
        service = FactoryRunService(tmp_path)
        factory = await service.create_run(
            FactoryConfig(
                name="Library",
                description="Python library",
                stages=["pm_planning", "chief_engineer_review"],
            )
        )
        await service.start_run(factory.id)
        context = {
            "directive": "Build a Python cancellation library with behavior tests and README; not a CLI.",
            "timeout": 1200,
        }
        pm = await service.execute_stage(factory.id, "pm_planning", context)
        assert pm.status == "success", pm.output
        runtime = tmp_path / ".polaris" / "runtime"
        plan = runtime / "tasks" / "plan.json"
        before = plan.read_bytes()
        pm_hash = hashlib.sha256(before).hexdigest()
        ce = await service.execute_stage(factory.id, "chief_engineer_review", context)
        assert plan.read_bytes() == before
        assert hashlib.sha256(plan.read_bytes()).hexdigest() == pm_hash
        assert not artifact_native_endpoint.errors, artifact_native_endpoint.errors
        assert artifact_native_endpoint.ce_calls <= 3
        events = await service.get_run_events(factory.id)
        assert sum(e.get("type") == "stage_started" and e.get("stage") == "pm_planning" for e in events) == 1
        if artifact_native_endpoint.outcome == "accept":
            assert ce.status == "success", ce.output
            assert artifact_native_endpoint.ce_calls == 1
            review = json.loads(
                (runtime / "state" / "blueprints" / f"{factory.id}.review.json").read_text(encoding="utf-8")
            )
            assert review["generated_blueprints"] == 2
            recovery_signal = next(
                signal
                for signal in review["signals"]
                if signal["code"] == "chief_engineer.portfolio_structural_recovered"
            )
            assert recovery_signal["artifact_path_normalizations"] == [
                {
                    "obligation_id": "inert-resources",
                    "source_path": "data/resources/",
                    "recovered_path": "data/resources",
                }
            ]
            assert recovery_signal["provider_call_consumed"] is False
            assert (
                recovery_signal["source_hash"]
                == hashlib.sha256(
                    json.dumps(
                        artifact_native_endpoint.outputs[-1], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                    ).encode("utf-8")
                ).hexdigest()
            )
            files = list(runtime.rglob("ce_portfolio_*.json"))
            assert files
            portfolio = json.loads(files[0].read_text(encoding="utf-8"))
            rows = portfolio["project_completion_contract"]["obligations"]["artifacts"]
            inert = next(row for row in rows if row["obligation_id"] == "inert-resources")
            assert inert == {
                "obligation_id": "inert-resources",
                "path": "data/resources",
                "semantic_role": "assets",
                "applicability": "not_applicable",
                "owner_task_id": None,
            }
            assert portfolio["llm_blueprint_consumed"] is True
        else:
            assert ce.status != "success", ce.output
            assert not list(runtime.rglob("ce_portfolio_*.json"))
        assert service._physical_attempt_coordinator(factory.id).drain_snapshot().settled
        assert not (tmp_path / "data").exists()
        assert not (tmp_path / "src/cancel.py").exists()

    asyncio.run(run())
