# L3-24 r61 CE Verifier Identity Collision

## Exact-run evidence

- Factory run: `factory_892f18bf3999`
- Terminal stage: `chief_engineer_review`
- Provider calls: 3
- Generated target remained read-only during debugging.
- Final request snapshots: `ea82a861a5a9048ff93ca579`,
  `81d8103de2e2acd5a860c958`, `1bed7fb273c80df01bbe8471`.

The first CE response required structural recovery. Its schema-valid candidate
then failed delivery depth because it declared one physical test file while the
level contract required two. The typed semantic repair added
`tests/test_cipher.cpp`, but final portfolio compilation failed with:

```text
invalid project completion contract: duplicate obligation_id across project completion obligations
```

## Dynamic root cause

The frozen candidate already used `OBG-TEST-PYTHON` twice:

- artifact: `tests/test_product.py`, semantic role `test`;
- verifier: modality `test`, covering `OBG-TEST-PYTHON`.

The semantic patch did not introduce this collision. Completion normalization
rebuilt verifier rows but tracked used IDs only within the verifier collection.
It therefore preserved the provider seed ID even though that ID was already an
artifact delivery fact. `ProjectCompletionObligationsV1` correctly enforces
global uniqueness and rejected the final contract.

This explains the late failure: schema recovery and delivery-depth repair both
operated on a structurally valid but not yet authoritative candidate; the
cross-collection collision was visible only when final completion authority
was hydrated.

## Generic invariant

Verification obligations are derived evidence identities. When a provider
reuses a physical artifact or entrypoint ID for a verifier:

1. preserve the physical delivery identity;
2. mint a stable verifier identity under `verification-authority-*`;
3. preserve `covers_obligation_ids` against the physical identity;
4. reserve verifier IDs against artifacts, entrypoints, and prior verifiers;
5. keep direct DTO construction fail-closed for callers bypassing CE authority
   normalization.

## Implementation and verification

- `_build_portfolio_completion_contract` now claims every build, lint, test,
  environment, and entrypoint verifier ID from one global completion namespace.
- Exact r61-shaped regression failed before the fix and passes after it.
- Related contract tests: `3 passed`.
- Full CE public contract tests: `190 passed`.
- CE semantic repair tests: `77 passed`.
- Ruff and Mypy: clean.

## Next live gate

Run fresh isolated L3-24 r62. CE must produce a non-empty blueprint portfolio,
all completion obligation IDs must be globally unique, and the chain must reach
Director. Any new failure requires a fresh exact-run dynamic trace before edits.
