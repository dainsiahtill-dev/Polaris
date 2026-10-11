"""Prospective allowance uses exactly the trusted context dispatched to runtime."""

from hashlib import sha256

import pytest
from polaris.cells.roles.adapters.internal.pm_adapter import PMAdapter
from polaris.cells.roles.adapters.tests.test_pm_factory_controlled_identity import (
    _isolated_external_delivery as _isolated_external_delivery,
)
from polaris.cells.roles.kernel.tests.test_pm_generation_contracts import outcome


@pytest.mark.parametrize(
    "context,budget",
    [
        ({"run_id": "pm-child"}, 60000),
        ({"run_id": "pm-child", "max_output_tokens": 64000}, 64000),
        (
            {"run_id": "pm-child", "llm_max_tokens": 3072, "max_tokens": 1024, "metadata": {"llm_max_tokens": 2048}},
            3072,
        ),
        (
            {"run_id": "pm-child", "metadata": {"task_execution_contract": {"context_budget": {"max_tokens": 1536}}}},
            1536,
        ),
    ],
)
async def test_budget_resolves_before_render_and_reuses_policy_snapshot(tmp_path, monkeypatch, context, budget):
    adapter = PMAdapter(workspace=str(tmp_path))
    captured = []
    original_render = adapter._build_pm_message
    seen_render = []

    def render(*args, **kwargs):
        seen_render.append(kwargs.get("output_budget_tokens"))
        return original_render(*args, **kwargs)

    monkeypatch.setattr(adapter, "_build_pm_message", render)

    async def external_call(message, *, context):
        captured.append((message, context))
        text = '{"tasks":['
        return {
            "response": text,
            "metadata": {
                "generation_outcome": outcome(text=text, call=("a" if len(captured) == 1 else "b") * 32, budget=budget)
            },
        }

    monkeypatch.setattr(adapter, "_call_role_llm", external_call)
    directive = "要求完整保留\n" + "独特约束甲乙丙" * 3000
    result = await adapter._run_pm_stage(
        "lifecycle",
        directive,
        {"llm_max_tokens": 99999, "metadata": {"max_tokens": 99999, "generation_outcome": outcome()}},
        context,
    )
    assert seen_render == [budget]
    assert len(captured) == 2
    policies = [{k: v for k, v in c.items() if k != "mode"} for _, c in captured]
    assert policies[0] == policies[1]
    for message, dispatched in captured:
        assert directive in message
        assert f"Prospective output allowance: {budget} tokens" in message
        assert dispatched["run_id"] == "pm-child"
        assert dispatched["llm_max_tokens"] == budget
        assert "generation_outcome" not in dispatched
    assert "Previous output excerpt" not in captured[1][0]
    assert '{"tasks":[' not in captured[1][0]
    assert "max_tokens" in captured[1][0]
    assert result["success"] is False and result["tasks_created"] == 0
    assert adapter.task_runtime.list_observable_task_rows() == []
    signals = result["quality_gate"]["signals"]
    assert not any(s["code"] == "pm.contracts.revision_base" for s in signals)
    facts = [s for s in signals if s["code"] == "pm.generation.outcome"]
    assert len(facts) == 2
    assert facts[0]["response_sha256"] == sha256(b'{"tasks":[').hexdigest()


@pytest.mark.parametrize("elapsed,want_calls", [(700, 2), (800, 1), (1600, 0)])
async def test_pm_correction_cannot_outlive_admitted_stage_deadline(tmp_path, monkeypatch, elapsed, want_calls):
    from polaris.cells.roles.adapters.internal import pm_adapter as module

    now = [10000.0 + (elapsed if want_calls == 0 else 0)]
    monkeypatch.setattr(module, "time", type("Clock", (), {"time": staticmethod(lambda: now[0])})(), raising=False)
    adapter = PMAdapter(workspace=str(tmp_path))
    calls = []

    async def external_call(message, *, context):
        calls.append(context)
        now[0] = 10000.0 + elapsed
        return {"response": '{"tasks":[', "metadata": {}}

    monkeypatch.setattr(adapter, "_call_role_llm", external_call)
    result = await adapter._run_pm_stage(
        "lifecycle",
        "Full original requirement.",
        {},
        {
            "run_id": "pm-child",
            "metadata": {
                "llm_max_tokens": 60000,
                "llm_call_timeout_seconds": 750,
                "pm_planning_deadline_epoch_seconds": 11530.0,
            },
        },
    )
    assert len(calls) == want_calls
    assert result["success"] is False and result["tasks_created"] == 0
    assert adapter.task_runtime.list_observable_task_rows() == []
    if want_calls < 2:
        assert "pm_planning_budget_deadline_exhausted" in str(result)
    else:
        assert [c["llm_max_tokens"] for c in calls] == [60000, 60000]
        assert [c["llm_call_timeout_seconds"] for c in calls] == [750, 750]


def test_initial_and_replacement_render_preserve_full_directive(tmp_path):
    adapter = PMAdapter(workspace=str(tmp_path))
    directive = "甲" * 12000 + "独特中间要求" + "乙" * 12000
    initial = adapter._build_pm_message([], directive, output_budget_tokens=4000)
    retry = adapter._build_pm_retry_message(
        directive=directive,
        quality={"score": 0, "critical_issues": ["pm_contract_json_invalid"]},
        previous_output="UNTRUSTED-PARTIAL" * 400,
        output_budget_tokens=4000,
    )
    assert directive in initial and directive in retry
    assert "UNTRUSTED-PARTIAL" not in retry
    assert "pm_contract_json_invalid" in retry


def test_initial_and_replacement_share_optional_prose_rule(tmp_path):
    adapter = PMAdapter(workspace=str(tmp_path))
    initial = adapter._build_pm_message([], "Implement model", output_budget_tokens=4000)
    retry = adapter._build_pm_retry_message(
        directive="Implement model", quality={"score": 0}, previous_output="bad", output_budget_tokens=4000
    )
    for message in (initial, retry):
        assert "scope_paths/target_files" in message
        assert "每个任务必须含 goal/scope/steps/acceptance" not in message
        assert "独特" in message
