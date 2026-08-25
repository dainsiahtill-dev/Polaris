# Factory CE Pre-Freeze Behavior Feasibility

Status: implemented; unit and exact-candidate replay verified; fresh isolated bench pending.

## Problem

The Factory CE transport validator accepted a schema-valid shared behavior
contract that linked a production owner to a test consumer but covered only a
test verifier. The authoritative portfolio builder later rejected the same
candidate because no invariant covered both a production artifact and a test
artifact. The semantic-repair loop therefore never saw the repairable defect.

## Architecture

```text
final CE provider payload
  -> Factory pre-freeze output validation
     -> schema/reference validation
     -> cross-task production/test behavior feasibility projection
        -> stable typed diagnosis
        -> same-CE semantic patch (bounded)
  -> Chief Engineer authoritative portfolio builder
  -> immutable blueprint handoff
```

## Responsibilities

- `factory_ce_evidence.py`: read-only pre-freeze parity check over the final
  provider payload. It does not persist authority or weaken the authoritative
  Chief Engineer validator.
- `factory_stage_executor/_mixin_02.py`: preserves the specific failure class,
  authorizes only behavior-invariant/ref upserts, and tells the provider to bind
  both the production and test sides.
- `chief_engineer.blueprint`: remains the sole owner of authoritative portfolio
  feasibility and immutable blueprint persistence.

## Invariants

1. A cross-task test invariant must name a production owner and the test owner
   as consumer.
2. Coverage must reach at least one required production source/entrypoint
   artifact and one required test artifact, directly or through a required test
   verifier.
3. Detection happens before immutable portfolio freeze.
4. Repair stays in the same Chief Engineer run and cannot change PM authority.
5. Generic tool normalization is not changed; r46 transport/tool schema was not
   the failing layer.

## Verification

- Regression test reproduces the exact missing production/test coverage shape.
- Positive control adds the production artifact and passes.
- Exact r46 persisted candidate replays to the specific diagnosis and bounded
  operation set.
- Ruff, Mypy, CE characterization, and authoritative feasibility tests pass.

