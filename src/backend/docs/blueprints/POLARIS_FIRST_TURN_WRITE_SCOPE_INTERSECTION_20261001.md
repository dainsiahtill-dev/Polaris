# First-turn write scope intersection

Owner: roles.kernel; bounded P05/P06 repair, no new state/public API/effect.

Exact run factory_8edf12521449: CE3/3 succeeded; Director Task1 wrote six files
with successful physical receipts. Task2 final request ref5a8aafd7f948d6bcd31d9154
exposed write enum index.html/browser-main.ts/renderer.ts, but JobToken/envelope
allowed only index.html/browser-main.ts. renderer.ts is a reference scope.
The final-request scope gate correctly rejected before HTTP.

Root: first-turn candidate collection unions target_files and scope_paths;
missing-target detection and forced write pinning don't intersect admitted write
scope. Fix both discovery and last pinning seam using existing KernelOne
project_task_write_guidance, preserving only supplied candidates authorized by
the envelope. Do not add required existing files to the missing-file set.
Explicit empty authority denies; malformed present authority fails closed.
Legacy non-authoritative hints without an envelope retain existing behavior;
this helper never grants execution authority. Runtime path/JobToken/audit gates
stay unchanged. Scope references remain available for reads/context.

Tests: exact two-owned/one-reference fixture RED then GREEN; supplied-target
pinning cannot bypass filter; all file aliases share restricted enum; empty and
malformed authority; original guidance and first-turn materialization suites.
Then native request replay, Director-only same-run recovery preserving PM/CE and
Task1 receipts. Separate retry sibling-export projection_error is not explained
by missing disk artifacts; inspect original error before patching that layer.

Ruling: audit accepts an authorized subset for one call (missing/failed targets),
not a superset. The old all-owned-targets-per-call expectation conflates delivery
completeness with capability exposure and incorrectly blocks partial repair.
Replace that expectation with a RED/GREEN narrower-call test, keep foreign-target
and scope mismatch rejection, and leave TaskBoundary/verifier completion unchanged.
Existing required artifacts must not be reintroduced into missing-target enum.

Dynamic correction: f39c belongs to Task3/internal12, dependency parent Task2;
backend logs preserve dependency_artifact_receipt_missing for browser-main.ts and
index.html. Task1's six receipts/hashes are valid. No independent lost-export
repair is justified; dependency gate stays strict.

Recovery exposed a second producer of the same violation: snapshot
9f6192c135cda34679ee4769 pins the quality-repair single target renderer.ts even
though it remains reference-only. The final forced-tool merge repeats this pin
after first-turn filtering. Extend the existing scope invariant to construction
step and quality-repair single-target extraction. A forbidden explicit target
must raise before provider dispatch, never degrade to an unrestricted tool.
Repair-target selection must independently intersect admitted envelope scope;
only owned importers can be edited, foreign dependency files remain owner-routed.
No new authority, public API, budget, target edit or success exemption.

Verification extension: both single-target producers and the final forced-tool
merge reject foreign targets; authorized ./ aliases remain equivalent; empty and
malformed authority fail closed. Real adapter repair-loop tests cover target
selection before forced schema/prompt construction. Same-run Director recovery
has since reached QA (11:08Z); its TS2345 verifier failure remains open and is
not a fresh unattended completion proof.
