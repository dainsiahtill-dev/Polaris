# No-effect repair continuation

## Intent and evidence

Unattended development must retain validated PM/CE and repair only the failed
Director owner. Fresh r04 `factory_31d7535627e3` reached quality_gate but stopped
with `native_validation_candidate_effects_missing`, with 4328 seconds remaining.
The quality report lost its repair-round detail (`attempted=false`, `rounds=[]`).
Real build/test/start failed; no completion is claimed. Native validation must
remain strict about every claimed physical mutation.

## Bounded design

Factory quality loop already distinguishes no mutation from verified candidate
effects, settles no-op attempts failed, and offers bounded same-owner continuation.
It currently constructs a candidate before reaching that distinction. An attempt
with no mutation rows, no mutation claim and no write-progress claim is not a
candidate. Route it through the existing no-op settlement/continuation path.
An attempt with any mutation row or positive mutation/progress claim still enters
strict native candidate validation. Missing/forged receipts remain fail-closed.

Flow: verifier failure -> owner repair -> effects present: strict candidate
validation and verifier; no effects: failed/no-progress settlement and existing
bounded Director continuation. No PM/CE restart, no extra permissions, no artifact
fabrication, no verifier waiver, no change to Bench scoring.

## Ownership and verification

Modify only factory.pipeline `factory_workspace_quality_impl.py` and a focused
integration test. Reuse real CE persistence, sealed baseline, bwrap command runner,
TaskRuntime attempt fixture and existing settlement. Mock only the provider/repair
producer to issue an empty result; do not mock candidate validation or source hashes.

RED: empty repair attempt aborts as candidate-not-qualified instead of recording
no-op round. GREEN: same attempted owner is settled failed and existing continuation
is exercised; unchanged verifier is not rerun. Negative: claimed mutation with
missing effect receipts still rejects. Retain existing committed-candidate,
artifact-registration-failure and cancellation tests. Full fresh success remains
unproven until the next isolated run completes all delivery gates.

Ruling: implement under the approved unattended master plan, without another design
approval, branches or automatic commits. Production changes begin only after r04
runner terminal exit1 was observed. Its target project remains untouched.

## Further exact-run correction: committed effects lost after planning

Additional r04 FactStream evidence shows a succeeded physical DEO receipt during
quality repair at00:41:45.869514Z. Thus zero-effect handling alone does not prove
closure of r04. `_apply_workspace_quality_deterministic_repairs` collects physical
batch receipts but returns the original deferred `results`. Candidate extraction
therefore sees plans while the mutation claim is true. Fix producer output to carry
the original committed batch's raw physical rows; retain planning/count/error
evidence separately. Never recreate receipt bodies, hashes or ownership from disk.
Successful source effects feed strict native validation; failed members remain
visible failed evidence rather than poisoning a partial candidate's positive proof.
Missing physical rows with a positive commit claim still fail closed.

Test the actual producer with a real owner-bound DEO write, frozen CE baseline and
persisted receipts. Its returned raw rows must pass unmocked native validation.
