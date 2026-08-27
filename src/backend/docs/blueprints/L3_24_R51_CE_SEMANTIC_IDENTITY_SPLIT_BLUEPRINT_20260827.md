# L3-24 r51 CE semantic identity split blueprint

## Exact-run failure

Fresh isolated run `factory_590232b2123d` stopped in
`chief_engineer_review` before Director.  The final CE semantic-patch request
had the correct role identity, forced structured-output tool, frozen candidate,
PM authority, depth deficit, and allowed operations.  The provider returned a
required test artifact at `tests/test_cli_edge.cpp`, but reused the existing
optional Python harness ID `OBL-TEST-EXTRA`.

The composer failed closed with:

`semantic repair cannot mutate immutable semantic identity: obligation_id='OBL-TEST-EXTRA':fields=['path']`

## Dynamic cause

Two platform gaps combined:

1. The final provider request exposed current rows but did not state the
   machine-enforced immutable identity fields or the new-ID rule.
2. The existing delivery-depth split normalizer required equal
   `applicability`.  Applicability is not part of artifact identity.  Therefore
   an optional baseline row plus a required new physical path skipped the safe
   split and reached the immutable-path guard.

## Invariants

- Artifact identity is `(obligation_id, path, semantic_role, owner_task_id)`;
  applicability may change without redefining that identity.
- A reused ID with a new independently authorized path is normalized by
  preserving the baseline row and minting a deterministic ID for the new row.
- Provider context must publish existing identities and immutable fields.
- Ambiguous owner, role, path, or scope changes still fail closed.
- Generated Bench projects remain read-only.

## Verification

- TDD RED reproduced the exact optional-to-required path split failure.
- Exact r51-shaped regression now preserves the optional `.py` row and mints a
  required `.cpp` obligation.
- CE semantic repair plus Factory handoff tests: `139 passed`.
- Ruff and Mypy passed.
- Fresh isolated r52 is the live closure gate.
