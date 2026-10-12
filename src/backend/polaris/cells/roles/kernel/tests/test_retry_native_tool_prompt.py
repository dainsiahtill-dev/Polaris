"""Mutation retries must not ask the model to serialize internal decisions."""

from polaris.cells.roles.kernel.internal.transaction.retry_context_builders import (
    append_retry_enforcement_hint,
    build_contract_retry_context,
    build_retry_write_after_bootstrap_context,
)


def test_retry_uses_callable_surface_without_internal_batch_format() -> None:
    tools = [{"type": "function", "function": {"name": "edit_file"}}]
    context = [
        {"role": "system", "content": "<role_definition>Director</role_definition>"},
        {"role": "user", "content": "Repair tests/math.test.ts using the supplied current contents."},
    ]
    retry = build_contract_retry_context(context, tools, forced_write_tool_name="edit_file")
    retry = append_retry_enforcement_hint(
        retry, allowed_tool_names={"edit_file"}, reason="no write call", forced_write_tool_name="edit_file"
    )
    system = "\n".join(row["content"] for row in retry if row["role"] == "system")
    assert "TOOL_BATCH" not in system
    assert "native tool" in system
    assert "prefer write_file" not in system
    assert "prefer edit_blocks" not in system
    assert "Allowed write tools in this turn: edit_file." in system
    assert "tests/math.test.ts" in system
    assert retry[1]["content"] == context[1]["content"]


def test_bootstrap_checks_preserve_role_and_do_not_grant_dependency_writes() -> None:
    role = "<role_definition>You are Director.</role_definition>"
    user = "Repair tests/math.test.ts using the task scope."
    context = [{"role": "system", "content": role}, {"role": "user", "content": user}]
    receipt = {
        "results": [
            {"tool_name": "file_exists", "status": "success", "result": {"path": "tests/math.test.ts", "exists": True}},
            {
                "tool_name": "file_exists",
                "status": "success",
                "result": {"path": "node_modules/vitest/package.json", "exists": True},
            },
        ]
    }
    followup = build_retry_write_after_bootstrap_context(
        original_context=context, bootstrap_receipt=receipt, forced_write_tool_name="edit_blocks"
    )
    text = followup[0]["content"]
    assert role in text
    assert "TOOL_BATCH" not in text
    assert "best-effort" not in text
    assert "Write targets must be selected from successfully-read files" not in text
    assert "do not provide file contents or write authority" in text
    assert followup[1]["content"] == user
