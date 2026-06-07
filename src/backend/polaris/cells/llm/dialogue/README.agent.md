

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
- `factory.cognitive_runtime`
- `llm.provider_runtime`
- `llm.tool_runtime`
- `policy.workspace_guard`

## State Ownership

- None

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `fs.write:runtime/cognitive_runtime/**`
- `llm.invoke:roles/*`
- `ws.outbound:runtime/*`

## Invariants

- role/docs dialogue output must pass schema/format validation
- docs dialogue/suggest must resolve Context OS before provider invocation and
  record Cognitive Runtime receipts after LLM completion
- callers should use public contracts/service, not `internal/**`
- no hidden write side-effect in query-only paths

## Typical Change Surface

- `public/contracts.py`
- `public/service.py`
- `internal/cognitive_evidence.py`
- `internal/role_dialogue.py`
- `internal/docs_dialogue.py`
- `internal/docs_suggest.py`

## Verification

- `polaris/tests/test_role_dialogue_validation_retry.py`
- `polaris/tests/test_docs_suggest.py`
- `polaris/tests/test_docs_dialogue_cognitive_runtime.py`
- `polaris/tests/test_interactive_interview_streaming_fallback.py`
