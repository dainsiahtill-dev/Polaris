from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from polaris.cells.roles.runtime.public import service as role_runtime_service
from polaris.cells.roles.runtime.public.contracts import (
    AggregateChatChoiceV1,
    AggregateChatCompletionsResultV1,
    AggregateChatMessageV1,
    RoleExecutionResultV1,
)
from polaris.delivery.http.routers import aggregate_chat
from polaris.kernelone.storage import resolve_runtime_path


class _AllowAllAuth:
    def check(self, _auth_header: str) -> bool:
        return True


def _build_client(workspace: str) -> TestClient:
    app = FastAPI()
    app.include_router(aggregate_chat.router)
    app.state.auth = _AllowAllAuth()
    app.state.app_state = SimpleNamespace(settings=SimpleNamespace(workspace=workspace))
    return TestClient(app)


def test_v1_chat_completions_returns_plan_only_aggregate_response(tmp_path) -> None:
    client = _build_client(str(tmp_path))

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "model": "polaris.aggregate_llm.v1",
            "messages": [
                {"role": "user", "content": "Fix ContextOS localization with Akashic fallback."},
            ],
            "domain": "code",
            "failure_signal": "localization_uncertain",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"].startswith("aggcmpl-")
    assert body["object"] == "chat.completion"
    assert body["model"] == "polaris.aggregate_llm.v1"
    assert body["metadata"]["execution_mode"] == "plan_only"
    assert body["aggregate_plan"]["aggregate_model_id"] == "polaris.aggregate_llm.v1"
    assert body["aggregate_plan"]["compute_policy"]["policy_id"] == "aggregate_compute_swap.v1"
    assert len(body["aggregate_plan"]["runtime_integrations"]) == 16
    assert body["aggregate_plan"]["cognitive_ledger"][0]["handoff_to"] == ["dialectic_self_heal_loop"]
    assert body["aggregate_plan"]["takeover_directive"]["lobe_id"] == "hippocampus_controller"
    content = json.loads(body["choices"][0]["message"]["content"])
    assert "hippocampus_controller" in content["execution_order"]
    assert content["compute_policy"]["default_priority"] == "local_self_heal_first_after_compiler_feedback"
    assert content["takeover_directive"]["trigger"] == "localization_uncertain"
    assert any(item["tech_id"] == "context_plane_isolation" for item in content["runtime_integrations"])
    assert all(item["entrypoints_verified"] for item in content["runtime_integrations"])
    assert any(
        check["check_type"] == "http_route" and check["ok"]
        for item in content["runtime_integrations"]
        for check in item["entrypoint_checks"]
    )
    assert "polaris.kernelone.akashic.semantic_memory.AkashicSemanticMemory" in content["required_capability_refs"]


def test_v1_chat_completions_passes_single_turn_command_to_service(monkeypatch, tmp_path) -> None:
    client = _build_client(str(tmp_path))
    captured: dict[str, Any] = {}

    async def fake_aggregate_chat_completions(command):
        captured["command"] = command
        return AggregateChatCompletionsResultV1(
            id="aggcmpl-test",
            object="chat.completion",
            model=command.model,
            choices=(
                AggregateChatChoiceV1(
                    index=0,
                    message=AggregateChatMessageV1(role="assistant", content="{}"),
                ),
            ),
            execution_result=RoleExecutionResultV1(
                ok=True,
                status="ok",
                role="director",
                workspace=command.workspace,
                output="executed",
            ),
            metadata={"execution_mode": command.execution_mode},
        )

    monkeypatch.setattr(aggregate_chat, "aggregate_chat_completions", fake_aggregate_chat_completions)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "messages": [
                {"role": "user", "content": "Execute the aggregate model through real runtime."},
            ],
            "execution_mode": "single_turn",
            "failure_signals": ["compile_failure"],
            "metadata": {"aggregate_execution_role": "chief_engineer"},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["execution_result"]["role"] == "director"
    command = captured["command"]
    assert command.execution_mode == "single_turn"
    assert command.failure_signals == ("compile_failure",)
    assert command.metadata["aggregate_execution_role"] == "chief_engineer"


def test_v1_chat_completions_passes_lobe_chain_command_to_service(monkeypatch, tmp_path) -> None:
    client = _build_client(str(tmp_path))
    captured: dict[str, Any] = {}

    async def fake_aggregate_chat_completions(command):
        captured["command"] = command
        return AggregateChatCompletionsResultV1(
            id="aggcmpl-chain-test",
            object="chat.completion",
            model=command.model,
            choices=(
                AggregateChatChoiceV1(
                    index=0,
                    message=AggregateChatMessageV1(role="assistant", content="{}"),
                ),
            ),
            execution_results=(
                RoleExecutionResultV1(
                    ok=True,
                    status="ok",
                    role="architect",
                    workspace=command.workspace,
                    output="constraint output",
                ),
            ),
            metadata={"execution_mode": command.execution_mode},
        )

    monkeypatch.setattr(aggregate_chat, "aggregate_chat_completions", fake_aggregate_chat_completions)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "messages": [
                {"role": "user", "content": "Run two aggregate lobes."},
            ],
            "execution_mode": "lobe_chain",
            "failure_evidence": {"compiler_output": "error TS2322"},
            "metadata": {"max_lobe_turns": 2},
        },
    )

    assert response.status_code == 200
    command = captured["command"]
    assert command.execution_mode == "lobe_chain"
    assert command.failure_evidence["compiler_output"] == "error TS2322"
    assert command.metadata["max_lobe_turns"] == 2
    assert response.json()["execution_results"][0]["role"] == "architect"


def test_v1_chat_completions_normalizes_failure_evidence_rows(monkeypatch, tmp_path) -> None:
    client = _build_client(str(tmp_path))
    captured: dict[str, Any] = {}

    async def fake_aggregate_chat_completions(command):
        captured["command"] = command
        return AggregateChatCompletionsResultV1(
            id="aggcmpl-evidence-test",
            object="chat.completion",
            model=command.model,
            choices=(
                AggregateChatChoiceV1(
                    index=0,
                    message=AggregateChatMessageV1(role="assistant", content="{}"),
                ),
            ),
            metadata={"execution_mode": command.execution_mode},
        )

    monkeypatch.setattr(aggregate_chat, "aggregate_chat_completions", fake_aggregate_chat_completions)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "messages": [
                {"role": "user", "content": "Explain the failed tool dispatch."},
            ],
            "failure_evidence": [
                {
                    "failure_class": "TOOL_DISPATCH_DROPPED",
                    "evidence_refs": ["provider_response:abc"],
                }
            ],
        },
    )

    assert response.status_code == 200
    command = captured["command"]
    assert command.failure_evidence["items"] == [
        {
            "failure_class": "TOOL_DISPATCH_DROPPED",
            "evidence_refs": ["provider_response:abc"],
        }
    ]
    assert command.failure_evidence["failure_classes"] == ("TOOL_DISPATCH_DROPPED",)
    assert command.failure_evidence["evidence_refs"] == ("provider_response:abc",)


def test_v1_chat_completions_lobe_chain_materializes_runtime_evidence_via_service(monkeypatch, tmp_path) -> None:
    client = _build_client(str(tmp_path))
    captured: list[Any] = []

    async def fake_stream_chat_turn(command):
        captured.append(command)
        yield {
            "type": "fingerprint",
            "profile_id": f"code.{command.role}",
            "profile_hash": f"hash-{len(captured)}",
            "bundle_id": "bundle-code",
            "bundle_version": "v1",
            "run_id": f"strategy-run-{len(captured)}",
            "turn_index": len(captured),
            "cognitive_strategy_override_applied": False,
        }
        yield {"type": "content_chunk", "content": f"{command.role} aggregate routed"}

    monkeypatch.setattr(role_runtime_service._DEFAULT_ROLE_RUNTIME_SERVICE, "stream_chat_turn", fake_stream_chat_turn)

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "messages": [
                {"role": "user", "content": "Materialize aggregate runtime evidence via HTTP."},
            ],
            "domain": "code",
            "execution_mode": "lobe_chain",
            "failure_signals": ["compile_failure"],
            "failure_evidence": {
                "compiler_output": "error TS2322",
                "changed_files": ["src/app.ts"],
                "test_command": "npm test",
            },
            "metadata": {"max_lobe_turns": 1},
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert captured
    assert body["metadata"]["execution_mode"] == "lobe_chain"
    assert body["metadata"]["stateful"] is True
    execution_result = body["execution_result"]
    runtime_metadata = execution_result["metadata"]["aggregate_runtime"]
    assert len(runtime_metadata["runtime_integrations_wired"]) == 16
    assert runtime_metadata["context_governance_status"] == "ok"
    assert runtime_metadata["distilled_knowledge_status"] == "ok"
    assert runtime_metadata["knowledge_distillation_status"] == "ok"
    assert execution_result["metadata"]["knowledge_distillation"]["knowledge_units_created"] >= 1
    assert execution_result["metadata"]["context_governance"]["retrieval_candidate_count"] >= 0
    command = captured[0]
    aggregate_context = command.context["aggregate_runtime_context"]
    assert aggregate_context["context_governance_pack"]["status"] == "ok"
    assert aggregate_context["contextos_attention_budget_pack"]["status"] == "ok"
    assert aggregate_context["task_market_projection_pack"]["status"] == "ok"
    knowledge_file = Path(resolve_runtime_path(str(tmp_path), "runtime/knowledge/distilled_knowledge.jsonl"))
    assert knowledge_file.exists()


def test_v1_chat_completions_rejects_unknown_execution_mode(tmp_path) -> None:
    client = _build_client(str(tmp_path))

    response = client.post(
        "/v1/chat/completions",
        headers={"Authorization": "Bearer test"},
        json={
            "messages": [
                {"role": "user", "content": "Execute the aggregate model."},
            ],
            "execution_mode": "execute",
        },
    )

    assert response.status_code == 400
    assert "single_turn" in response.text
