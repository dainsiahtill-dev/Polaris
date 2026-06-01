# Director Proposal Prompt Contract Fix

Date: 2026-05-31
Status: Proposed for immediate implementation

## Problem

The Director runtime code-generation bridge asks the model to return only parsable file
operations. The shared role prompt still injects the ADR-0080 `SESSION_PATCH` working-memory
contract and tool-policy guidance. In the proposal-to-apply path this creates a contradictory
contract:

- the bridge says not to output `SESSION_PATCH`, shell commands, or tool-call text
- the shared role prompt says to append a `SESSION_PATCH` block when the task is unfinished
- the runtime disables internal tool rounds, so tool-policy guidance is not useful for this call

The observed full-chain E2E failure shows a Director LLM call cancelled after a long wait and
the workflow timing out before any file operation was produced.

## Target Architecture

Keep the shared role prompt behavior for normal interactive role turns. Add a narrow prompt
profile for Director runtime code generation:

```text
Director tasking
  -> CodeGenerationEngine proposal-to-apply bridge
  -> RoleExecutionKernel
  -> PromptBuilder(include_working_memory_contract=false, include_tool_policy=false)
  -> LLM returns PATCH_FILE/fenced file sections only
  -> FileApplyService validates and applies files
```

## Module Responsibilities

- `director.execution`: declares the runtime code-generation bridge context explicitly.
- `roles.kernel`: builds the final prompt and honors bridge-specific prompt-layer flags.
- `llm.dialogue`: continues to pass context through the public role dialogue entry point.

## Verification Plan

1. Unit-test that the bridge request suppresses `SESSION_PATCH` and tool-policy layers.
2. Unit-test that ordinary role prompts still include `SESSION_PATCH`.
3. Run focused Director code-generation and role-kernel tests.
4. Re-run the full-chain Electron E2E after the focused gate passes.

