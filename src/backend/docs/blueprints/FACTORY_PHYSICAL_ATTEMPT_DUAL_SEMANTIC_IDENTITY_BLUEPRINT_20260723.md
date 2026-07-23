# Factory Physical Attempt Dual Semantic Identity Blueprint

Date: 2026-07-23  
Owner: Codex `/root`  
State: active / bench not schedulable

## Problem

Fresh isolated L1-04 R32 completed the Chief Engineer physical Provider
attempt, but Factory replay rejected the persisted lifecycle with
`factory_physical_attempt_replay_lifecycle_identity_mismatch`.

The replay boundary compares two deliberately different hash domains:

- `semantic_candidate_hash`: pre-evidence role semantic candidate bound by the
  role-evidence cutoff.
- `semantic_request_hash`: final qualified semantic request bound to the exact
  physical wire reservation.

R32 proved all other lifecycle/cutoff identity fields match while these two
hashes correctly differ. Existing tests hid the defect by assigning the same
value to both fields.

## Required Contract

Provider lifecycle start and terminal facts must persist both hashes.

1. `semantic_candidate_hash` must equal the role-evidence cutoff candidate.
2. `semantic_request_hash` must equal the final frozen Provider semantic
   request and continue to participate in the physical composite hash.
3. Start and terminal facts must preserve both values exactly.
4. Recovery/replay must compare like domains only.
5. Legacy lifecycle facts without `semantic_candidate_hash` remain readable
   only when the old single-hash equality is provable; otherwise replay stays
   fail-closed.
6. No Provider dispatch, target-project mutation, or Bench run is authorized
   while this bucket is active.

## Data Flow

`FactoryRoleSemanticCandidateV1.semantic_candidate_hash`

→ qualification proof

→ `FrozenFinalProviderAttemptV1.semantic_candidate_hash`

→ physical attempt start/terminal lifecycle payload

→ public lifecycle replay fact

→ Factory replay comparison against the role-evidence cutoff.

`semantic_request_hash` remains independently derived from the final frozen
semantic request and remains the physical reservation/composite-hash input.

## Acceptance Gates

- New-format lifecycle facts reject candidate/cutoff mismatch.
- New-format lifecycle facts allow candidate and final request hashes to differ.
- Start/terminal candidate drift rejects.
- Legacy facts are accepted only under the old provable equality rule.
- Lifecycle public replay, Factory replay, Role Kernel, Factory Pipeline,
  Ruff, mypy, compileall, and YAML gates pass.
- Independent post-edit structural review finds no weakened identity fence.

## Non-goals

- Do not weaken or remove semantic identity comparison.
- Do not alias the two hash domains.
- Do not edit generated target-project code.
- Do not run another Bench until this bucket and the separate CE terminalization
  bucket are both closed and a new pre-bench authorization exists.
