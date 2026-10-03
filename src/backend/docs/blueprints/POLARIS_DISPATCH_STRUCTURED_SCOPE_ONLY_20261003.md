# Dispatch preserves structured authority

Fresh r04 Task2's persisted PM row declares only `index.html`. Its steps explicitly
say a historical renderer path is not mandatory. `_pm_task_rows_from_payload`
nevertheless extracts every filename from prose and augments both target_files and
scope_paths. Read-only replay of `_select_pm_task_payloads` proves the negated path
becomes a required target, while strict CE completion and original JobToken own a
different browser module. Four physical edits cannot satisfy that phantom target.

Bounded fix: dispatch copies structured PM rows unchanged. Remove the prose target
augmentation and its unreferenced parser. Natural-language filenames remain prompt
context, never new obligations or grants. Missing structured authority must be fixed
at PM/CE contract validation, not inferred during dispatch. No CE override of PM,
no new token, no relaxed scope, no target-project patch, no scoring change.

Regression inputs: Chinese/English negations, optional/referenced sibling paths,
affirmative filename instructions, explicit empty scopes and targets, and genuine
structured required paths. Verify original row/list content unchanged. Preserve
existing handoff, capability and Director-only retry tests. Update the old positive
test that encoded unsafe prose scope expansion into an explicit rejection assertion
while keeping its original setup and structured source target.

Architecture: PM structured contract -> unchanged dispatch row -> strict CE handoff
and token -> existing owner scope guards. Ownership stays orchestration.pm_dispatch;
public contracts, graph dependencies and state owners do not change.

Ruling: execute under approved unattended plan with TDD, without branch/commit.
Fresh completion remains unproven; r04 real QA failures stay in the denominator.
