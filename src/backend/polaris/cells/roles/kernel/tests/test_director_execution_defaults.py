"""Director keeps observation and verification tools under its existing authority."""

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.roles.kernel.internal.kernel.core import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.transaction_factory import create_transaction_kernel
from polaris.cells.roles.kernel.internal.transaction.tool_surface import plan_transaction_tool_surface
from polaris.cells.roles.profile.public.service import RoleTurnRequest, profile_from_dict


def _profile() -> Any:
    return profile_from_dict(
        {
            "role_id": "director",
            "display_name": "Director",
            "prompt_policy": {"core_template_id": "director.core"},
            "tool_policy": {
                "whitelist": ["read_file", "repo_rg", "repo_tree", "edit_file", "write_file", "execute_command"]
            },
            "data_policy": {"data_subdir": "director"},
            "model": "qwen-small-int4",
        }
    )


@pytest.mark.parametrize("existing", [False, True])
@pytest.mark.parametrize("mode", ["turn", "stream"])
def test_default_director_keeps_full_policy_permitted_tool_surface(tmp_path: Path, existing: bool, mode: str) -> None:
    if existing:
        (tmp_path / "answer.py").write_text("VALUE = 0\n", encoding="utf-8")
    context: dict[str, Any] = {
        "delivery_mode": "materialize_changes",
        "construction_step": {"target_file": "answer.py", "last_failure": "wrong value" if existing else ""},
        "target_files": ["answer.py"],
        "llm_max_tokens": 20000,
    }
    request = RoleTurnRequest(
        workspace=str(tmp_path), message="Implement and verify answer.py", context_override=context
    )
    plan = plan_transaction_tool_surface(
        role="director",
        profile=_profile(),
        request=request,
        context_result=SimpleNamespace(messages=[], token_estimate=0),
        messages=[],
        workspace=str(tmp_path),
        mode=mode,
    )
    names = {schema["function"]["name"] for schema in plan.tool_definitions}
    assert {"read_file", "repo_rg", "repo_tree", "edit_file", "write_file", "execute_command"} <= names
    assert plan.tool_choice_override is None
    assert context["llm_max_tokens"] == 20000
    assert "director_first_call_output_budget" not in context
    assert plan.conflict_error is None


@pytest.mark.parametrize(
    "context,expected",
    [
        ({}, True),
        ({"continuous_execution": {"enabled": False}}, False),
        ({"tool_contract": {"single_batch": True}}, False),
        ({"director_execution_mode": "legacy_single_batch"}, False),
    ],
)
def test_director_factory_defaults_to_existing_continuous_execution(
    tmp_path: Path, context: dict[str, Any], expected: bool
) -> None:
    kernel = RoleExecutionKernel(workspace=str(tmp_path), llm_invoker=SimpleNamespace())
    request = RoleTurnRequest(workspace=str(tmp_path), message="Implement answer.py", context_override=context)
    transaction = create_transaction_kernel(kernel, "director", _profile(), request)
    assert transaction.config.continuous_execution is expected


def test_explicit_first_write_slice_remains_available(tmp_path: Path) -> None:
    context: dict[str, Any] = {
        "director_execution_mode": "legacy_single_batch",
        "delivery_mode": "materialize_changes",
        "construction_step": {"target_file": "answer.py"},
        "target_files": ["answer.py"],
    }
    plan = plan_transaction_tool_surface(
        role="director",
        profile=_profile(),
        request=RoleTurnRequest(workspace=str(tmp_path), message="Implement answer.py", context_override=context),
        context_result=SimpleNamespace(messages=[], token_estimate=0),
        messages=[],
        workspace=str(tmp_path),
        mode="turn",
    )
    assert {schema["function"]["name"] for schema in plan.tool_definitions} == {"write_file"}
    assert plan.tool_choice_override == {"type": "function", "function": {"name": "write_file"}}


def test_continuation_policy_accepts_prior_committed_locator_bundle() -> None:
    from polaris.cells.roles.kernel.public.llm_caller_contracts import prepare_role_session_continuation_policy

    _context, metadata = prepare_role_session_continuation_policy(
        {},
        {"role_session_id": "session-1"},
        role="director",
        host_kind="director_adapter",
        previous_result=None,
        prior_results=(),
    )
    assert "_kernel_task_continuation_evidence" not in metadata


@pytest.mark.parametrize(
    "required,authority,expected",
    [
        (None, "chief_engineer", True),
        ([], "chief_engineer", False),
        ((), "chief_engineer", False),
        (["answer.py"], "chief_engineer", True),
        ([], "", True),
    ],
)
def test_verification_only_task_requires_explicit_ce_empty_write_obligations(
    tmp_path: Path,
    required: Any,
    authority: str,
    expected: bool,
) -> None:
    kernel = RoleExecutionKernel(workspace=str(tmp_path), llm_invoker=SimpleNamespace())
    request = RoleTurnRequest(
        workspace=str(tmp_path),
        message="Verify current behavior",
        context_override={
            "target_files": ["answer.py"],
            "required_write_targets": required,
            "verification_authority": authority,
        },
    )
    transaction = create_transaction_kernel(kernel, "director", _profile(), request)
    assert transaction.config.materialization_required is expected


@pytest.mark.asyncio
async def test_successful_write_keeps_channel_open_for_real_verification(tmp_path: Path) -> None:
    from polaris.cells.roles.kernel.internal.transaction.ledger import TransactionConfig
    from polaris.cells.roles.kernel.internal.transaction_kernel import TransactionKernel

    async def provider(request: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": "",
            "model": "fixture",
            "usage": {},
            "tool_calls": [
                {
                    "id": "write",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": '{"file":"answer.py","content":"VALUE = 42"}',
                    },
                }
            ],
        }

    async def tools(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        from hashlib import sha256

        body = arguments["content"].encode("utf-8")
        (tmp_path / "answer.py").write_bytes(body)
        return {
            "success": True,
            "tool": name,
            "result": {"success": True, "file": "answer.py"},
            "effect_receipt": {
                "operation": "write",
                "status": "success",
                "file": "answer.py",
                "new_hash": sha256(body).hexdigest(),
            },
        }

    kernel = TransactionKernel(
        llm_provider=provider,
        tool_runtime=tools,
        config=TransactionConfig(
            role_id="director",
            domain="code",
            workspace=str(tmp_path),
            continuous_execution=True,
        ),
    )
    result = await kernel.execute(
        "write-turn",
        [{"role": "user", "content": "[mode:materialize] Implement answer.py and verify behavior"}],
        [
            {"type": "function", "function": {"name": "write_file"}},
            {"type": "function", "function": {"name": "execute_command"}},
        ],
    )
    assert result["kind"] == "continue_multi_turn"
    assert result["finalization"]["needs_followup_workflow"] is True
    assert (tmp_path / "answer.py").read_text(encoding="utf-8") == "VALUE = 42"
