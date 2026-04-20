# ADR-0043: structural bug fixes must ship governance assets and gates

## Status: accepted and implemented (2026-03-25)

## Context

The 2026-03-25 `roles.kernel` turn-engine incident exposed two different
runtime failures:

1. `StreamEventType` drifted across a manual re-export chain and crashed
   `executor.py` at import time.
2. `[TOOL_CALL]` wrappers crossed parsing, UI, and transcript boundaries without
   an explicit contract and caused a loop in repeated tool execution.

The code fix alone was not enough. The repository had no durable artifact that
forced future changes to preserve:

- assumption tracking
- residual-risk tracking
- verify-pack discoverability
- contract-parity tests

## Decision

For structural bugs, Polaris now requires a governance loop in addition to
the code fix.

### Mandatory deliverables

1. Verification card (`verification card`)
2. ADR
3. Debt register entry
4. Cell `generated/verify.pack.json`
5. Regression tests and CI wiring

### Mandatory behaviors

1. High-risk fixes must record assumptions and a pre-mortem before closure.
2. Structural bugs must leave an auditable link between code, governance docs,
   and tests.
3. `verify.pack.json` becomes the Cell-local summary of current guarantees,
   linked governance assets, and open debt ids.
4. Governance tests must verify that these assets exist and reference each
   other consistently.

## Consequences

### Positive

- Structural fixes become discoverable from the affected Cell boundary.
- Regression coverage protects both behavior and governance drift.
- Debt remains visible instead of disappearing into chat history.

### Negative

- High-risk fixes take longer because they now include documentation and gate
  work.
- Some debt remains open even after a mitigation lands, because runtime-only
  contracts are not the same as fully typed contracts.

## Implementation Notes

This ADR is implemented by:

- `docs/governance/debt.register.yaml`
- `docs/governance/STRUCTURAL_BUG_PROTOCOL.md`
- `polaris/cells/roles/kernel/generated/verify.pack.json`
- `tests/architecture/test_structural_bug_governance_assets.py`
- `tests/architecture/test_kernelone_llm_contract_reexports.py`
