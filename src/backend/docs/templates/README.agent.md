# Sample Example

## Purpose

Describe what this Cell does in one sentence.

## Kind

`capability`

## Public Inputs

- `SampleCommand`

## Public Outputs

- `SampleResult`
- `SampleCompletedEvent`

## Depends On

- `audit.evidence`

## State Ownership

- None

## Effects Allowed

- None

## Does Not

- access HTTP transport directly
- write outside workspace scope
- mutate another Cell's source-of-truth state

## Invariants

- all state changes are contract-driven
- all text file writes use explicit UTF-8

## Typical Change Surface

- `public/contracts/*`
- `internal/application/*`
- `internal/domain/*`

## Read Order for AI

1. `cell.yaml`
2. `generated/context.pack.json`
3. `public/contracts/*`
4. `public/api.py`
5. owned implementation files only if needed

## Verification

- `cells/sample/example/tests/test_contracts.py`
- `cells/sample/example/tests/test_behavior.py`

## Notes

Keep this file short, factual, and stable. It is an AI entrypoint, not a long-form design essay.
