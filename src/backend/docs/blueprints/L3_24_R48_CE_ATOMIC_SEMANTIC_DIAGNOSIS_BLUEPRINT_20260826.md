# L3-24 r48 CE atomic semantic diagnosis

Status: unit and exact-replay validated; fresh isolated verification pending.

## Exact-run failure

- Run: `factory_fb181a3f18bc`
- Stage: `chief_engineer_review`; Director never ran.
- First semantic repair saw only a help-only entrypoint and authorized only `entrypoint_upsert`.
- Its replacement introduced one empty argv item, so the second bounded repair again saw only the entrypoint.
- Only after the second repair did validation expose `prod_files=4 < 7`; the semantic-repair budget was exhausted.
- Both final provider requests had correct CE identity, PM authority, target files, structured submission transport, and successful provider responses.

## Root cause

`_chief_engineer_portfolio_output_errors` deliberately appended delivery-depth deficits only when all existing errors belonged to a narrow compatibility set. Entrypoint semantic errors were excluded even though the typed repair protocol can apply `entrypoint_upsert` and `artifact_upsert` atomically. Independent repairable residuals were serialized across a fixed two-round budget.

## Invariant and fix

1. Structural or unsupported output errors remain fail-closed and do not receive depth repair authority.
2. Entrypoint semantic errors may coexist with independently computed delivery-depth deficits.
3. One diagnosis authorizes only the union of typed operations proven by those errors.
4. Provider rounds remain bounded; the fix reduces calls instead of increasing the retry budget.

The minimal change adds entrypoint errors to the existing depth-compatible set. No generated project was modified.

## Evidence

- RED: entrypoint plus PM depth contract returned only the entrypoint error.
- GREEN: same candidate returns entrypoint plus prod/test depth deficits and authorizes `entrypoint_upsert` plus `artifact_upsert`.
- Exact r48 candidate replay: help-only `OBL-014` and `prod_files=4 < 7` now appear in one diagnosis.
- Targeted CE/Factory tests: 138 passed.
- Ruff and Mypy: passed.
- Fresh isolated L3-24 r49 remains required before closure.
