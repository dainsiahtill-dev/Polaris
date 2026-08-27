# L3-24 r64-r66 rejected-owner stickiness

## Outcome

Closed a generic workspace-quality repair defect: a rejected candidate is now
retried against the exact file it mutated after byte restoration. It cannot
silently rotate to a sibling owner merely because the verifier effect was
classified as `stagnant` or `equal_count_swap`.

## Exact-run proof

- Factory run: `factory_308086628a50`
- Project: `L3-24`
- r64: equal-count rejection exposed missing owner feedback.
- PDB correction: an ordinary equal-count verifier outcome with no candidate
  transaction is not candidate rejection and must not emit rejection ownership.
- r65: a stagnant `cli_main.cpp` candidate was restored, then the next round
  drifted to `diary.cpp`; this proved the conditional fix was incomplete.
- r66: three consecutive candidates were rejected and restored on
  `src/inkwell/cipher.cpp`; all three emitted
  `candidate_rejection_target_files_for_next_round=["src/inkwell/cipher.cpp"]`.
  No owner drift occurred, and the existing non-progress fuse terminated the
  loop after three attempts.

## Platform invariant

When a candidate transaction is rejected, its physical mutation is rolled back
and its exact normalized `repair_target_files` becomes the bounded retry owner
for the unchanged diagnostic frontier. The rule is independent of the
verifier-effect label. Accepted candidates remain authoritative and do not use
this rollback projection.

## Verification

- Focused regression tests: 3 passed.
- Workspace-quality characterization suite: 110 passed.
- Ruff: passed.
- Mypy: passed.
- Live same-run r66: owner stickiness and rollback both observed.

## Remaining frontier

The current residual is different: the verifier reports only Moonlight
semantic assertions, but the initial selector still leases `cipher.cpp`.
No new patch is authorized until the exact final provider request, selector
inputs, and current verifier evidence dynamically prove the generic cause.
The generated Bench workspace remains read-only.
