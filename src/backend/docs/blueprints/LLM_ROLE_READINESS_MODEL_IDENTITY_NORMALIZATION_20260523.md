# LLM Role Readiness Model Identity Normalization

Date: 2026-05-23
Status: Proposed

## Context

The desktop Factory runtime can show PM as blocked even after the PM role deep test has passed. The readiness projection and role startup gate currently compare the tested model and the configured role model with strict string equality.

This is too brittle for provider/model labels that differ only by display casing or surrounding whitespace, for example `qwen3-max` versus `Qwen3-Max`.

## Contract

1. Runtime readiness projection and role startup gates must compare role model identity after trimming surrounding whitespace and applying case-insensitive comparison.
2. Runtime readiness must keep the existing trimmed `tested_model` display value in the status payload for audit/debug UI.
3. Readiness must still block when the tested model and configured model represent different model names or versions, for example `MiniMax-M2.5` versus `MiniMax-M2.7-highspeed`.
4. Provider identity remains exact after existing trimming; this change only affects model identity comparison.

## Verification

- Add a regression test where PM is configured as `Qwen3-Max`, the successful deep test records `qwen3-max`, and `/llm/status` returns READY with no blocked PM role.
- Add a role-gate regression test proving the same case-only model difference does not raise HTTP 409 when starting PM workflows.
- Keep the existing stale-model regression proving different model versions still produce `model_mismatch` and BLOCKED.
- Run targeted lint, format, mypy, and pytest gates for the runtime projection and LLM regression suite.
