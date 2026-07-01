from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from polaris.cells.roles.kernel.internal.kernel import transaction_turn_completion as completion
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.profile.public.service import RoleProfile, RoleTurnRequest


@dataclass
class _Request:
    workspace: str = "."
    task_id: str = "task-1"
    run_id: str = "run-1"
    context_override: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Profile:
    role_id: str = "director"
    model: str = "test-model"
    provider_id: str = "test-provider"


def test_completion_owner_commits_projection_and_boundary(monkeypatch: pytest.MonkeyPatch) -> None:
    committed: dict[str, Any] = {}
    verdict: dict[str, Any] = {}

    monkeypatch.setattr(
        completion,
        "get_output_parser",
        lambda _kernel: SimpleNamespace(
            parse_thinking=lambda content: SimpleNamespace(clean_content=content, thinking=None),
            extract_json=lambda _content: None,
        ),
    )
    monkeypatch.setattr(
        completion,
        "_commit_turn_to_snapshot",
        lambda **kwargs: committed.update(kwargs),
    )
    monkeypatch.setattr(
        completion,
        "append_role_turn_task_boundary_verdict",
        lambda **kwargs: verdict.update(kwargs),
    )

    context_gateway = MagicMock(record_projection_outcome=MagicMock(return_value={"route_weight": 0.42}))
    result = completion.build_transaction_turn_completion_result(
        kernel=RoleExecutionKernel.create_default(workspace="."),
        role="director",
        profile=cast(RoleProfile, _Profile()),
        request=cast(RoleTurnRequest, _Request()),
        fingerprint=SimpleNamespace(full_hash="abc"),
        turn_id="turn-1",
        tk_result={
            "kind": "final_answer",
            "visible_content": "done",
            "batch_receipt": {},
            "metrics": {"duration_ms": 7, "llm_calls": 1, "tool_calls": 0},
            "ledger": {"events": []},
            "llm_response_metadata": {"context_snapshot_ref": "ctx/ref"},
        },
        response_schema=None,
        runtime_tool_policy_audit={"tool_policy_mode": "native"},
        tool_filter_audit={"status": "kept"},
        context_gateway=context_gateway,
        context_result=SimpleNamespace(token_estimate=11),
    )

    assert result.content == "done"
    assert result.error is None
    assert result.execution_stats["duration_ms"] == 7
    assert result.execution_stats["tool_policy_mode"] == "native"
    assert result.metadata["projection_adaptive_weights_after_turn"] == {"route_weight": 0.42}
    assert committed["turn_id"] == "turn-1"
    assert committed["ledger"] == {"events": []}
    assert verdict["role"] == "director"
    assert verdict["run_id"] == "run-1"
    assert verdict["needs_followup_workflow"] is False
    context_gateway.record_projection_outcome.assert_called_once_with(success=True, tokens_used=11)
