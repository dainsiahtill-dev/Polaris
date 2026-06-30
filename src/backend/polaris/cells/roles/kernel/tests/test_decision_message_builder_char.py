"""Characterization tests for ``_build_decision_messages`` branches.

These pin the CURRENT behavior of the decision-message synthesis before the
``transaction/decision_message_builder.py`` extraction (REMAINING_06 blueprint
step 1). They cover the branch-matrix coverage gaps not exercised by
``test_transaction_kernel_facade.py``:

* implementing-phase HARD GATE injection (+ allowed/forbidden tool wording)
* MATERIALIZE positive-only task-contract filtering (NEGATIVE lines stripped)
* control-plane message exclusion from the data plane
* empty tool_definitions short-circuit

UTF-8 编码验证: 本文所有文本使用 UTF-8。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from polaris.cells.roles.kernel.internal.transaction.delivery_contract import (
    DeliveryContract,
    DeliveryMode,
)
from polaris.cells.roles.kernel.internal.transaction.ledger import TurnLedger
from polaris.cells.roles.kernel.internal.turn_transaction_controller import (
    TransactionConfig,
    TurnTransactionController,
)
from polaris.kernelone.context.prompt_safety import format_tool_failure_summary, parse_tool_failure_summary


def _make_controller() -> TurnTransactionController:
    return TurnTransactionController(
        llm_provider=AsyncMock(return_value={}),
        tool_runtime=AsyncMock(return_value={}),
        config=TransactionConfig(domain="code"),
    )


def test_build_decision_messages_no_tool_definitions_returns_data_plane_only() -> None:
    controller = _make_controller()
    context = [
        {"role": "user", "content": "请实现 app.py"},
        {"role": "system", "content": "control hint", "metadata": {"plane": "control"}},
    ]

    messages = controller._build_decision_messages(context, [])

    # No tools => early return; only the non-control data-plane messages survive.
    assert len(messages) == 1
    assert messages[0]["role"] == "user"
    assert all(m.get("metadata", {}).get("plane") != "control" for m in messages)


def test_build_decision_messages_injects_implementing_hard_gate() -> None:
    controller = _make_controller()
    context = [
        {"role": "user", "content": "当前阶段: implementing\n请实现 src/app.ts"},
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]

    messages = controller._build_decision_messages(context, tool_definitions)

    system_messages = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    assert any("HARD GATE (Implementing Phase)" in text for text in system_messages)
    # Forbidden broad-exploration wording present
    assert any("Broad exploration tools (glob, repo_rg, repo_tree) are FORBIDDEN" in text for text in system_messages)
    # Allowed targeted-read wording present
    assert any("ALLOWED: You may call read_file or repo_read_head" in text for text in system_messages)
    # The constraint carries the dedicated control-plane kind marker.
    assert any(
        m.get("metadata", {}).get("kind") == "execution_constraint" for m in messages if m.get("role") == "system"
    )


def test_build_decision_messages_materialize_strips_negative_task_contract_lines() -> None:
    controller = _make_controller()
    user_content = "Create src/main.ts and write the implementation."
    context = [
        {
            "role": "user",
            "content": user_content,
            "metadata": {"tool_contract": {"single_batch": True, "required_tools": ["write_file"]}},
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    ledger = TurnLedger(turn_id="turn_materialize_positive_filter")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    # In MATERIALIZE (non-SUPER) mode any positive task-contract block emitted must
    # not contain NEGATIVE markers (INVALID / HARD GATE / rejected / read-only ...).
    positive_blocks = [
        str(m.get("content") or "")
        for m in messages
        if m.get("role") == "system" and m.get("metadata", {}).get("kind") == "task_contract_positive"
    ]
    for block in positive_blocks:
        lowered = block.lower()
        assert "invalid" not in lowered
        assert "hard gate" not in lowered
        assert "rejected" not in lowered
        assert "read-only" not in lowered
    positive_text = "\n".join(positive_blocks)
    assert "TEMPLATE [General-Mutation]" in positive_text
    assert "call write_file immediately with the complete file body" in positive_text
    assert "edit_blocks/edit_file/search_replace/repo_apply_diff" in positive_text
    assert "read/list/execute-only batch is invalid" not in positive_text


def test_build_decision_messages_materialize_guard_is_write_first_for_create_tasks() -> None:
    controller = _make_controller()
    context = [{"role": "user", "content": "[mode:materialize]\nCreate src/main.ts"}]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    ledger = TurnLedger(turn_id="turn_materialize_write_first_guard")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    system_text = "\n".join(str(m.get("content") or "") for m in messages if m.get("role") == "system")
    assert "search → read → write" not in system_text
    assert "emit write_file/edit_file in this batch" in system_text
    assert "Targeted reads are allowed only when exact existing content is required" in system_text


def test_build_decision_messages_quality_repair_removes_read_first_templates() -> None:
    controller = _make_controller()
    user_content = (
        "MATERIALIZATION QUALITY REPAIR MODE:\n"
        "Artifact quality scan failed: npm package manifest script references missing local entrypoint.\n"
        "Do not read files first. Do not list directories. Do not explore. Do not explain.\n"
        "Emit exactly one write_file tool call for package.json."
    )
    context = [
        {
            "role": "user",
            "content": user_content,
            "metadata": {"tool_contract": {"single_batch": True, "required_tools": ["write_file"]}},
        }
    ]
    tool_definitions = [
        {"type": "function", "function": {"name": "read_file"}},
        {"type": "function", "function": {"name": "write_file"}},
    ]
    ledger = TurnLedger(turn_id="turn_quality_repair_conflict_filter")
    ledger.set_delivery_contract(DeliveryContract(mode=DeliveryMode.MATERIALIZE_CHANGES, requires_mutation=True))

    messages = controller._build_decision_messages(context, tool_definitions, ledger)

    system_messages = [str(m.get("content") or "") for m in messages if m.get("role") == "system"]
    assert any("MATERIALIZATION QUALITY REPAIR OVERRIDE" in text for text in system_messages)
    assert any("SINGLE-BATCH materialization quality repair" in text for text in system_messages)
    control_text = "\n".join(system_messages)
    assert "search → read → write" not in control_text
    assert "Step 1: read_file" not in control_text
    assert "TOOL FAILURE RECOVERY PROTOCOL" not in control_text
    assert "Immediately call read_file" not in control_text


def test_build_decision_messages_compacts_repeated_tool_failure_summaries() -> None:
    controller = _make_controller()
    failure = format_tool_failure_summary(
        {
            "tool": "write_file",
            "error_type": "tool_failure",
            "reason": "write_file failed",
            "prompt_safe": True,
            "receipt_detail": "omitted; see runtime tool_result event for audit evidence",
        }
    )
    context = [{"role": "tool", "content": failure} for _ in range(10)] + [
        {"role": "user", "content": "Continue with the targeted repair."}
    ]
    tool_definitions = [{"type": "function", "function": {"name": "write_file"}}]

    messages = controller._build_decision_messages(context, tool_definitions)

    failure_messages = [message for message in messages if parse_tool_failure_summary(message.get("content"))]
    assert len(failure_messages) == 1
    digest = parse_tool_failure_summary(failure_messages[0]["content"])
    assert digest is not None
    assert digest["schema_version"] == "tool_failure_summary_digest.v1"
    assert digest["failure_count"] == 10
    assert digest["failures"][0]["count"] == 10
    assert any(message.get("role") == "user" and "targeted repair" in message.get("content", "") for message in messages)


def test_build_decision_messages_excludes_control_plane_from_data_plane() -> None:
    controller = _make_controller()
    context = [
        {"role": "user", "content": "总结 README"},
        {
            "role": "assistant",
            "content": "control-only",
            "metadata": {"plane": "control"},
        },
    ]
    tool_definitions = [{"type": "function", "function": {"name": "read_file"}}]

    messages = controller._build_decision_messages(context, tool_definitions)

    # The control-plane assistant message must not appear as a copied data-plane
    # message; only NEW control messages this builder appends carry plane=control.
    assert not any(m.get("content") == "control-only" for m in messages)
