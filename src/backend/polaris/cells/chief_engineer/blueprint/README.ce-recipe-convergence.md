# CE recipe and reference convergence

The existing completion compiler remains the authority owner. CE model proposals
supply semantic recipes; `VerificationCommandAuthorityV1` binds exact
owner/modality/argv/cwd hashes after existing verifier-profile validation.

`prepare_delegated_recipes` reports every independent verification and entrypoint
failure before returning any normalized authority. Diagnostic source paths identify
exact rows in the frozen candidate. Supplied forged hash claims remain rejected.

The existing semantic repair contract adds `verification_recipe_upsert` and
`verification_recipe_upserts`; provider rows omit machine command hashes. The
existing diagnosis hash binds `verification_recipe_diagnostics`. Composition
preserves owner, applicability, coverage, unrelated rows and their order. A
missing required recipe can be added only for its owner-issued diagnostic.
Diagnosed entrypoint commands use existing `entrypoint_upsert` while retaining
kind/source/runtime identity. No recipe repair removes obligations.

Behavior `consumer_task_ids` contain PM task IDs, not interface `consumer_kind`
values. For a task-local invariant the consumers can be empty. Repairing only
invalid consumer IDs preserves valid owner, statement, examples, coverage and
task bindings. Completion contracts and full semantic gates still revalidate;
a patch does not grant execution, produce a verifier pass or authorize Director.

Component regression: `PYTHONPATH=src/backend python -m pytest -q
src/backend/polaris/cells/chief_engineer/blueprint/public/tests
src/backend/polaris/cells/chief_engineer/blueprint/internal/tests
src/backend/polaris/cells/chief_engineer/blueprint/tests`.
Fresh isolated delivery and public release remain separately required.
