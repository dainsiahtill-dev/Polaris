"""Forced-write command intent normalization tests."""

from __future__ import annotations

from polaris.cells.roles.kernel.internal.transaction.forced_write_command_normalizer import (
    extract_command_write_intent,
    normalize_forced_write_command_decision,
)
from polaris.cells.roles.kernel.public.turn_contracts import (
    BatchId,
    FinalizeMode,
    ToolBatch,
    ToolCallId,
    ToolEffectType,
    ToolExecutionMode,
    ToolInvocation,
    TurnDecision,
    TurnDecisionKind,
    TurnId,
)


def _execute_command(command: str) -> ToolInvocation:
    return ToolInvocation(
        call_id=ToolCallId("call-1"),
        tool_name="execute_command",
        arguments={"command": command, "timeout": 30},
        effect_type=ToolEffectType.WRITE,
        execution_mode=ToolExecutionMode.WRITE_SERIAL,
    )


def _decision(invocation: ToolInvocation) -> TurnDecision:
    return TurnDecision(
        turn_id=TurnId("turn-1"),
        kind=TurnDecisionKind.TOOL_BATCH,
        visible_message="",
        reasoning_summary=None,
        tool_batch=ToolBatch(
            batch_id=BatchId("batch-1"),
            invocations=[invocation],
            serial_writes=[invocation],
        ),
        finalize_mode=FinalizeMode.NONE,
        domain="code",
        metadata={"source": "test"},
    )


def test_extract_command_write_intent_cat_heredoc_then_redirect() -> None:
    invocation = _execute_command(
        "mkdir -p src/engine && cat <<'EOF' > src/engine/simulation.ts\n"
        "export const firefly = 'flower moon humidity';\n"
        "EOF"
    )

    intent = extract_command_write_intent(invocation)

    assert intent is not None
    assert intent.file == "src/engine/simulation.ts"
    assert intent.content == "export const firefly = 'flower moon humidity';"


def test_extract_command_write_intent_cat_redirect_then_heredoc() -> None:
    invocation = _execute_command('cat > "src/index.ts" <<EOF\nexport const ok = true;\nEOF')

    intent = extract_command_write_intent(invocation)

    assert intent is not None
    assert intent.file == "src/index.ts"
    assert intent.content == "export const ok = true;"


def test_normalize_forced_write_command_decision_converts_only_when_write_file_allowed() -> None:
    decision = _decision(_execute_command("cat <<'EOF' > src/index.ts\nexport const ok = true;\nEOF"))

    normalized, events = normalize_forced_write_command_decision(
        decision,
        allowed_tool_names={"execute_command", "write_file"},
    )

    assert len(events) == 1
    assert normalized.tool_batch is not None
    invocation = normalized.tool_batch.invocations[0]
    assert invocation.tool_name == "write_file"
    assert invocation.arguments == {
        "file": "src/index.ts",
        "content": "export const ok = true;",
        "encoding": "utf-8",
    }
    assert normalized.tool_batch.serial_writes == [invocation]
    assert normalized.metadata["tool_intent_normalizations"][0]["type"] == "forced_write_command_normalized"


def test_normalize_forced_write_command_decision_leaves_verification_command_unchanged() -> None:
    decision = _decision(_execute_command("npm test"))

    normalized, events = normalize_forced_write_command_decision(
        decision,
        allowed_tool_names={"execute_command", "write_file"},
    )

    assert events == ()
    assert normalized is decision


def test_normalize_forced_write_command_decision_requires_write_file_allowed() -> None:
    decision = _decision(_execute_command("cat <<'EOF' > src/index.ts\nexport const ok = true;\nEOF"))

    normalized, events = normalize_forced_write_command_decision(
        decision,
        allowed_tool_names={"execute_command", "edit_blocks"},
    )

    assert events == ()
    assert normalized is decision


def test_extract_command_write_intent_rejects_path_traversal() -> None:
    invocation = _execute_command("cat <<'EOF' > ../outside.ts\nexport const bad = true;\nEOF")

    assert extract_command_write_intent(invocation) is None


def test_extract_command_write_intent_rejects_unrelated_command_chain() -> None:
    invocation = _execute_command("echo unsafe && cat <<'EOF' > src/index.ts\nexport const ok = true;\nEOF")

    assert extract_command_write_intent(invocation) is None
