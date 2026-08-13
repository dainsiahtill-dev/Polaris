# Director Python failure symbol-owner targeting

Status: Implementing  
Date: 2026-08-13  
Cells: `roles.adapters`

## Problem

An isolated L1-03 unittest run reported four source behavior failures. The
quality-repair request nevertheless authorized only `src/radio.py`. Director
made a real edit, the verifier stayed red, and the next turn reversed that
edit. The test path was observation evidence, while target selection promoted
the most recently changed direct importer to mutation authority.

Two generic defects amplified the error:

1. unittest verbose output with a test docstring was not recognized by the
   result-line parser;
2. `from src.package import Symbol` preferred a nonexistent
   `src/package.py` shim before checking the existing
   `src/package/__init__.py`.

## Architecture

```text
unittest failure + traceback
  -> parse failed test and exact traceback source line
  -> AST collect referenced class/function/method names
  -> bounded workspace Python definition index
  -> accept only unique non-test source owners
  -> task write-scope partition
  -> Director edit batch
  -> failed verifier only
```

## Invariants

1. A failing test file is not automatically a mutation target.
2. A broad importer loses to a uniquely resolved symbol owner.
3. Ambiguous symbols do not manufacture authority.
4. Existing packages win over speculative missing-module shims.
5. The repair remains on the same Director task; PM/CE are not restarted.
6. No target-project business rule is encoded in Polaris.

## Verification

- Regression for unittest docstring result lines.
- Regression for unique enum/function/method owner resolution.
- Regression that an existing package `__init__.py` wins over a missing
  sibling `.py` guess.
- Replay the real L1-03 diagnostic: expected targets are
  `src/models/weather.py`, `src/models/mood.py`, and
  `src/engine/forecast.py`; `src/radio.py` is excluded.
- Restart only the isolated instance and retry `qa_gate`.
