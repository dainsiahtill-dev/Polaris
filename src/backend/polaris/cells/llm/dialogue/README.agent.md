

# LLM Dialogue Cell

## Purpose

Own role/docs dialogue prompt orchestration and response parsing/validation.

## Kind

`capability`

## Public Inputs

- `InvokeRoleDialogueCommandV1`
- `InvokeDocsDialogueCommandV1`
- `ValidateRoleOutputQueryV1`

## Public Outputs

- `DialogueTurnResultV1`
- `DialogueTurnCompletedEventV1`

## Depends On

- `context.engine`
- `llm.provider_runtime`
- `llm.tool_runtime`
- `policy.workspace_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `llm.invoke:roles/*`
- `ws.outbound:runtime/*`

## Invariants

- role/docs dialogue output must pass schema/format validation
- callers should use public contracts/service, not `internal/**`
- no hidden write side-effect in query-only paths

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/role_dialogue.py`
- `internal/docs_dialogue.py`
- `internal/docs_suggest.py`

## Verification

- `tests/test_role_dialogue_validation_retry.py`
- `tests/test_docs_suggest.py`
- `tests/test_interactive_interview_streaming_fallback.py`
