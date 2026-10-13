"""The actual closing provider request preserves its caller's output contract."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel import transaction_factory
from polaris.cells.roles.kernel.internal.llm_caller.finalization_caller import FinalizationCaller
from polaris.cells.roles.kernel.internal.llm_caller.request_preparer import LLMRequestPreparer
from polaris.cells.roles.kernel.internal.tool_gateway import RoleToolGateway
from polaris.cells.roles.kernel.internal.transaction.delivery_contract import DeliveryContract, DeliveryMode
from polaris.cells.roles.kernel.internal.transaction.finalization import FinalizationHandler
from polaris.cells.roles.profile.public.service import RoleExecutionMode, RoleTurnRequest, profile_from_dict
from polaris.infrastructure.llm.providers import anthropic_provider
from polaris.kernelone.llm.contracts.core import Usage
from polaris.kernelone.llm.types import InvokeResult


async def _closing_body(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    mode: DeliveryMode,
    message: str,
) -> dict[str, Any]:
    profile = profile_from_dict(
        {
            "role_id": "pm",
            "display_name": "PM",
            "prompt_policy": {"core_template_id": "pm.core"},
            "tool_policy": {"whitelist": ["repo_rg"]},
            "data_policy": {"data_subdir": "pm"},
            "provider_id": "anthropic_compat",
            "provider_type": "anthropic_compat",
            "model": "MiniMax-M3",
            "context_policy": {"max_context_tokens": 1_000_000},
        }
    )
    (tmp_path / "notes.md").write_text("CURRENT_QUERY_OBSERVATION = 42\n", encoding="utf-8")
    gateway = RoleToolGateway(profile, workspace=str(tmp_path))
    observed = gateway.execute_tool("repo_rg", {"path": ".", "pattern": "CURRENT_QUERY_OBSERVATION"})
    assert observed["success"] is True
    receipts = [{"results": [{"tool_name": "repo_rg", "status": "success", "result": observed["result"]}]}]
    context = {"execution_deadline_epoch_seconds": 4_000_000_000, "provider_output_tokens": 60_000}
    original_context = deepcopy(context)
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=str(tmp_path),
        message=message,
        context_override=context,
        run_id="pm-output-contract",
        task_id="task-pm",
    )
    original_messages = [
        {"role": "system", "content": "You are PM. Retain the immutable original planning authority."},
        {"role": "user", "content": message},
    ]
    messages = FinalizationHandler._build_finalization_context(original_messages, receipts, DeliveryContract(mode=mode))
    captured: list[dict[str, Any]] = []

    def intercept(_url: str, _headers: Any, payload: dict[str, Any], *_args: Any) -> InvokeResult:
        captured.append(deepcopy(payload))
        return InvokeResult(
            ok=False, output="", latency_ms=0, usage=Usage.estimate("", ""), error="transport_intercepted"
        )

    class Invoker:
        async def call_finalization(self, **kwargs: Any) -> dict[str, Any]:
            caller: Any = self
            return await FinalizationCaller(caller).call(**kwargs)

        async def call(self, **kwargs: Any) -> Any:
            prepared = await LLMRequestPreparer(workspace=str(tmp_path))._prepare_llm_request(
                profile=kwargs["profile"],
                system_prompt=kwargs["system_prompt"],
                context=kwargs["context"],
                temperature=0.0,
                max_tokens=60_000,
                stream=False,
            )
            monkeypatch.setattr(anthropic_provider, "invoke_with_retry", intercept)
            anthropic_provider.AnthropicProvider().invoke(
                prepared.input_text,
                profile.model,
                {
                    **prepared.request_options,
                    "chat_messages": prepared.messages,
                    "base_url": "https://provider.test/anthropic",
                    "api_key": "fixture",
                },
            )
            raise RuntimeError("transport_intercepted")

    class KernelHost:
        directed_effect_runtime = None
        directed_effect_required = False
        workspace = str(tmp_path)

    host: Any = KernelHost()
    monkeypatch.setattr(transaction_factory, "get_llm_invoker", lambda _host: Invoker())
    # Preserve the production factory/provider closure. No kernel execution or
    # successful provider result is substituted by this network-only intercept.
    monkeypatch.setattr(transaction_factory, "TransactionKernel", lambda **kwargs: SimpleNamespace(**kwargs))
    assembly = transaction_factory.create_transaction_kernel(host, "pm", profile, request)
    with pytest.raises(RuntimeError, match="transport_intercepted"):
        await assembly.llm_provider({"messages": messages, "tools": None, "tool_choice": "none"})
    for key, value in original_context.items():
        assert context[key] == value
    assert request.run_id == "pm-output-contract" and request.task_id == "task-pm"
    assert len(captured) == 1
    assert captured[0].get("tools", []) == []
    assert captured[0]["max_tokens"] == 60_000
    return captured[0]


@pytest.mark.parametrize("mode", list(DeliveryMode))
async def test_actual_final_http_defers_to_original_pm_revision_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: DeliveryMode
) -> None:
    message = (
        "Return only the complete pm.task_revision.v1 JSON envelope. "
        "Keep base_hash=ORIGINAL_BASE_HASH and edit_manifest=ORIGINAL_EDIT_MANIFEST. "
        "Preserve all task IDs and command authority; no Markdown or extra prose."
    )
    body = await _closing_body(tmp_path, monkeypatch, mode=mode, message=message)
    wire = json.dumps(body, ensure_ascii=False)
    assert "pm.task_revision.v1" in wire
    assert "ORIGINAL_BASE_HASH" in wire and "ORIGINAL_EDIT_MANIFEST" in wire
    assert "CURRENT_QUERY_OBSERVATION = 42" in wire
    assert "You are PM" in wire
    assert "给出分析总结或 patch 提案" not in wire
    assert "可以输出 patch/diff" not in wire
    assert "给出分析、总结或建议" not in wire
    assert "只报告收据支持的修改和验证结果" not in wire
    assert "遵守原输出" in wire
    if mode == DeliveryMode.MATERIALIZE_CHANGES:
        assert "不要贴出完整的可执行代码" in wire
        assert "未执行的验证不得声称通过" in wire


def test_explicit_patch_request_and_bounded_failed_receipt_remain_caller_owned() -> None:
    messages = [{"role": "user", "content": "Return a unified diff only for the requested change."}]
    receipts = [{"results": [{"tool_name": "read_file", "status": "failed", "error": "missing notes.md"}]}]
    projected = FinalizationHandler._build_finalization_context(
        messages,
        receipts,
        DeliveryContract(mode=DeliveryMode.PROPOSE_PATCH),
        config=SimpleNamespace(max_per_tool_result_chars=12, max_total_result_chars=100),
    )
    wire = json.dumps(projected, ensure_ascii=False)
    assert messages[0]["content"] in projected[-1]["content"]
    assert "missing notes.md" in wire
    assert "可以输出 patch/diff" not in wire
    assert "遵守原输出" in wire
