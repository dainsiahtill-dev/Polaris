# L1-04 CE unowned advisory verifier closure (2026-08-14)

## Symptom

Fresh isolated L1-04 run `factory_18f4e4f4f659` completed PM planning but failed
before Director dispatch with:

```text
ChiefEngineerBlueprintErrorV1: invalid project completion contract:
active verification cannot be bound to one committed PM command authority;
obligation_id='VER-go-vet'; modality='lint'; candidate_count=0; command_count=0
```

The downstream `source=0`, `task_boundary_verdict_missing`, and M06 residuals
were consequences, not the first failing owner.

## Final-request evidence

Context snapshot `894d07edfa01f75a670f2df9` showed a correct Chief Engineer
identity, the complete PM contract and target set, the structured-output tool,
an exact tool choice, and a 5,620-token final provider request. The model
returned all three task blueprints plus a structurally complete project
completion contract. Context or tool omission was therefore excluded.

## Root cause

The CE added a useful advisory `go vet` verifier, but PM had committed no
`lint` command authority. The portfolio composer treated every active CE
verification row as executable and rejected the entire portfolio when it could
not bind that row. CE owns semantic advice; PM owns executable command
authority. The old behavior forced a false choice between inventing authority
and killing a valid portfolio.

## Invariant and fix

When a CE verification modality has zero committed PM command authorities, the
composer preserves the row as an explicit non-executable `not_applicable`
declaration with no command, authority hash, owner, or covered obligations.
It never guesses a command and never widens PM authority. If PM does expose
authorities for the modality but they remain ambiguous, composition still
fails closed.

Project completion still requires at least one real required build/test/lint
verifier, and application projects still require a real test artifact and test
verifier. Therefore a portfolio made entirely of unowned advisory verifiers
cannot pass.

## Verification

- Unit regression: unowned CE lint advice becomes non-executable while the
  PM-owned build/test/entrypoint contract remains valid.
- Existing authority ambiguity and tamper tests remain fail-closed.
- Live acceptance: retry the same L1-04 run from Chief Engineer; PM must not be
  rerun, Director must receive the resulting portfolio.

## Second CE residual: provider `$text` envelope noise

The same-stage retry moved beyond the old `VER-go-vet` failure, then the bounded
CE schema-repair call failed on an otherwise strict artifact object containing
an unexpected string member named `$text`. The final request remained complete
and used the exact `submit_structured_role_output` schema. Runtime evidence
showed the key was already present in native provider tool arguments.

`roles.kernel.structured_output_transport` now removes only this exact provider
noise key when the matching caller schema explicitly uses
`additionalProperties: false` and does not declare `$text`. It recursively
follows declared object properties and array item schemas, records the
normalization policy in transport evidence, and then applies the full caller
schema. Any other unknown member, missing required field, wrong type, or open
object remains unchanged and therefore fails closed normally.
