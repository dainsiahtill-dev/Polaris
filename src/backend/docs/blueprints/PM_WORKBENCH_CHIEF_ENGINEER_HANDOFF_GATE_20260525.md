# PM Workbench Chief Engineer Handoff Gate

Date: 2026-05-25

## Problem

PM Workbench could start a PM run with Director auto-dispatch after only checking Director LLM readiness. Chief Engineer diagnostics can still report missing blueprint coverage, so the PM desktop could send users into a blocked Director handoff that was already detectable.

## Decision

Gate PM Workbench Director auto-dispatch on the same two desktop readiness contracts used by the downstream roles:

```text
PM Workbench
  -> GET /v2/director/diagnostics
  -> GET /v2/chief-engineer/diagnostics
  -> Director auto-dispatch allowed only when Director LLM is ready
     and Chief Engineer blueprint handoff coverage is ready
```

The UI keeps the existing PM Workbench readiness test id and adds a second compact status lane, `ce-blueprint`, next to `director-llm`.

## Verification

- PM Workbench tests cover the ready handoff path and verify the Chief Engineer diagnostics call.
- PM Workbench tests cover blocked auto-dispatch when Chief Engineer reports missing PM task blueprint coverage.
- Chief Engineer service tests continue to cover the typed diagnostics contract.
