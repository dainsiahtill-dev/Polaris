# Paperclip postboot execution feedback

Recorded 2026-10-06 00:15 UTC. External engineering feedback only; not Polaris runtime, Ledger, ContextOS or Bench evidence.

## Routine workflow and one-time maintenance

Routine: root uses public Paperclip APIs to create/assign/wake an employee, employee uses its configured execution adapter, root inspects actual changes/tests before accepting. Bench execution consumes Polaris native Instance/Factory APIs. No second product scheduler, external-agent success gate or repeated migration procedure is needed.

Previous WSL/hold operations were a one-time Paperclip upgrade migration, not Polaris development requirements. Root mixed this recovery work with ordinary project coordination and made the workflow harder to follow. Subsequent Paperclip framework defects stay with its team; root does not change its code, disable isolation or keep issuing paid retries.

## Confirmed recovery

- Existing default instance restored through existing CLI, `--no-repair`, no source/package update. Health version2026.916.1+148.git.726894ccf, ready/authReady; localhost3100. Current launch is Vite dev middleware, not the prior static UI; no browser acceptance claim.
- Existing exact-scope migration CLI closed hold1afe989c-2798-4b2b-ae50-92f9287224a3, generation6fae6a43-b1c1-47ca-9fb5-ef91ce2ed645, exit0 at00:05:30.586Z. State legacy_migration_closed, proof host_boot_epoch_closed; no old-run deletion, namespace proof fabrication or provider replay.
-26employees retain gpt-6.1-sol/high. Original disabled timer/wake configuration restored after one explicit dispatch.

## Confirmed failure boundary

- New authorized qualification issue POL-9, ID0d3b2d1a-0d17-49d3-a8ee-e4023f986ff4. No product edits authorized. Public POST/company/issues followed by POST/agent/heartbeat/invoke actually launched run890edbc1-9a22-4a38-af7a-38c8ef7327d2.
- Actual adapter.invoke: `/home/dains/.local/bin/codex`, cwd Polaris original directory, modelgpt-6.1-sol, effort high, workspace-write sandbox. Thus not an API dispatch failure.
- Worker shell failed twice before reading files: `bwrap: Creating new namespace failed: nesting depth or /proc/sys/user/max_*_namespaces exceeded (ENOSPC)`.
- CodeGraph MCP call succeeded. Both attempted apply_patch report writes failed. Public runtime probes/pytest/Ruff/mypy never executed.
- Process status succeeded/exit0, but final worker report explicitly blocked_environment; issue currently blocked. Process exit is not task delivery. Root rejects acceptance.
- Scope hashes show no changed/unexpected product paths. No target edits or fresh Bench occurred. No active POL employees after this run.

## Read-only controls and uncertainty

Root host sysctl user.max_user/mnt/pid/net_namespaces each386102. Root user-namespace unshare and a minimal RO/cap-dropALL/network-isolated bwrap both exit0. This disproves the claim that the entire host cannot create namespaces. It does not identify the employee's exact ancestor namespace, effective quota, custody wrapper or Codex sandbox parameters responsible for ENOSPC.

An additional nested bwrap control failed with missing tmpfs mount layout; that control is invalid for judging nested namespace support. Preserve this test-author limitation, do not attribute it to Paperclip or claim nested support proven. No sysctl, sandbox bypass or unsafe permission change performed.

The earlier audit-profile PATCH500 remains a separate owner investigation: `Audit read-only source constraint: executable/home environment overrides are unsupported`, despite public saved agent having no command/home/env overrides. A WSL restart does not prove it fixed.

## Owner acceptance requested

Trace the exact effective employee launch/custody/Codex tool process and namespace limits. Verify a protected real employee can read an authorized file, write an engineering report, execute one tiny test, stop/drain and admit a next protected task in the same original directory. Keep current write protection and model binding; do not fix by disabling sandbox or globally relaxing namespace/security limits. Basic interpreter preflight alone did not qualify actual sandboxed tools.

When owner evidence is available, resume POL-9's unfinished qualification under fresh authorization; do not replay already-completed product work. Root then accepts actual tests/diff before issuing TS5110/owner-handoff/verifier implementation scopes. Until then, no further paid employee or Bench retries.

Evidence: `.superpowers/sdd/2026-10-06-paperclip-repair-frontier/postboot-{preflight,response,run,observation,scope}.json`; `postboot.py:34` creates the issue and `postboot.py:52` invokes the public API. These engineering artifacts may contain full worker logs and should not be forwarded unscreened.

## Independent readonly attempt, POL-10

To advance independent review without repeating the broken shell task, root assigned a distinct public-API MCP-only review to the existing independent reviewer. Issue POL-10, run801de40c-1d18-4daa-913d-161a345bccf1, typed read-only override, modelgpt-6.1-sol/high; original disabled heartbeat restored. No preset downgrade or shell fallback.

This attempt failed before adapter/model invocation: `adapter_failed`, `Audit read-only source constraint: executable/home environment overrides are unsupported`, processPid=null and no adapter.invoke. Source hashes remain unchanged; no review verdict or tests were produced. This is a confirmed Paperclip-side admission/adapter failure, not a Polaris test failure. Exact internal origin of the supplied/derived environment override remains unproved; namespace failure in POL-9 also has not been narrowed to a specific source hunk.

Root posted the confirmed evidence and owner acceptance requirements as a public POL-10 comment. A subsequent generic PATCH(status=blocked) returned422; that client transition was rejected and is not independently classified as a framework bug. Preserve actual issue state instead of fabricating a status transition. All POL runs are terminal; no further job should be launched merely to repeat these failures. Machine-readable observations are `mcp-review-observation.json`, `mcp-review-scope.json`, and the posted comment receipt `mcp-review-root-blocker-comment.json` under the existing engineering checkpoint.

## After owner96a6504a3 deployment: commands/writes work; handoff drain still unproved

User authorized use after deployment. Health149.git.ea0cc3abc reports commit96a6504a3. A real POL-9 run now executes shell and writes reports without ENOSPC. The missingpydantic failure was the selected interpreter baseline, not proof of failed namespace repair. Root explicitly authorized existing projectvenv3.13.12 (repo requires>=3.10); POL-11 physically ran107PASS, broad303PASS/6FAIL/309, realpublicprobes and sourceintegrity. These facts supersede the earlier conclusion that no employee tool path works. Readonly config/hello diagnostic is separately unresolved and should not be generalized to allwriters.

POL-12 actually implemented the bounded verifier-command provenance fix throughPaperclip: onlydiagnostics.py and newregressiontest changed,31newPASS/107focusPASS/78relatedPASS, broad307PASS/2FAIL/309. TrueRED/GREEN and allresiduals recorded; noexistingassertionchange, targetedit or fakeplatformreceipt. Implementation artifacts uploaded and hashes/scope preserved.

The live handoff then failed04:05:38Z: run58c55833-65f2-4cd2-a8b4-98e135fefe7a errorCodeissue_reassigned, errorProtected process namespace drain unverified, workspace_finalizefailed. CurrentparentPIDabsent alone cannot establish descendantnamespaceclosure. Assignee currentlyoriginalrepairengineer; exact reassigningactor/trigger is not yet proven. Inspect self-handoff/reviewreassignment and guardedcancel/finalization with the exactliveprocess/namespace identity. Keep completedimplementation evidence; do not ask to repeatcode edits or bypasswriterprotection. Board-facing ownerissuePOL-13 records this new integration blocker and requires verifiedsafe release plus normalprotectedhandoff regression before Root startsindependentQA or anotherwriter.
