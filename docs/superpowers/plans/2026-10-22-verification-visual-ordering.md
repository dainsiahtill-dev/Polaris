# Verification and visual review ordering

Status: scoped implementation and ROOT qualification complete; main integration pending. Base: `a022eaa0cdfa9e29492a328af7a6cb50ae1922ce`. This is not native Bench delivery acceptance.

## Goal and evidence

Let authorized real checks execute before visual review, then require the resulting current candidate to be visually inspected before completion and independent QA approval. The cancelled original L1-01 is evidence of a bounded scheduling defect, not proof of model inability. Its three sealed cycles rebuilt 31 output files with identical final bytes and changed metadata; previous pixels became stale, verification was requested again, and QA was never reached.

Reuse `.polaris/audits/verification-visual-ordering-20261022/design-review.json` and the original run's immutable reports. Do not repeat the failed native Bench to rediscover the same mechanism.

## Global constraints

- Keep the existing EngineeringRunService, SQLite session facts, Cell and KernelOne owners. No new controller, ledger, phase protocol or model-maintained identifiers.
- Keep full candidate metadata, revision, permissions, profile, declared dependency and output binding. Do not ignore timestamps, relax equality, add special command rules or replay uncertain effects.
- A complete current durable verification can be reused by finalization; an explicit new native verify_project invocation still executes and records its own checks.
- Final Director completion and QA approval still require that role's own current durable pixels in a later actual physical provider request. QA also reads current source in an earlier turn. Same-turn captures and another role's images do not qualify.
- User-required checks, actual failures, cancel/deadline, host revalidation, no-replay and source/output invalidation remain effective. Optional visual capability stays optional when the host does not require it.
- Work directly on main; no branch/worktree, target edits, dependency installs, service restarts or generated receipts. ROOT alone owns Git staging/commits and this plan.
- All host-side experiments, caches, logs and captured artifacts stay under this repository's `.polaris/audits/verification-visual-ordering-20261022/implementation`. Use KFS and explicit UTF-8 for artifact I/O. Native browser isolation's existing anonymous tmpfs is a separately tracked B4 migration; this task must not create a new host-global temp/cache root or claim full storage convergence.
- The only substituted production participant in integrated acceptance is the model response, explicitly scripted. Actual service, tools, process sandbox, SQLite, browser images and native final request serialization remain real. Synthetic workspaces are labelled fixtures, never Bench deliveries.

## Task 1: Repair the existing scheduling boundary

### Architecture and owned files

Keep the existing topology: service owns execution and acceptance; role loop dispatches authorized tools; verifier owns completed current checks; browser/visual modules own exact current pixels. No schema or public API migration is needed.

Production edit scope, exactly these four files:

1. `src/backend/polaris/cells/roles/runtime/internal/engineering_loop.py`: remove the visual prerequisite only from the verify_current callback. Preserve review_current visual enforcement and native dispatch authority.
2. `src/backend/polaris/cells/roles/runtime/public/engineering_service.py`: remove duplicate pre-dispatch pixels from _verify_dispatch. In _verify_director, resolve current completed checks/ownership/UNKNOWN before visual completion; execute existing implicit host checks when applicable, then ask for post-check pixels. If only pixels are stale/missing, feedback must say checks already passed and ask to capture, inspect in a later turn and finish. Re-entering finalization must reuse the same current receipt without an implicit rebuild. Preserve _review_qa and conservative read-only _fresh_verification behavior.
3. `src/backend/polaris/cells/roles/runtime/internal/engineering_roles.py`: align Director instructions to verify, then capture after the last output-changing check, inspect later, complete. Retain QA's independent capture.
4. `src/backend/polaris/cells/roles/runtime/internal/engineering_visual.py`: only if its existing error text instructs the model to verify again after a successful check, make that guidance phase-neutral. Its binding/guard semantics must remain byte-for-byte unchanged unless ROOT explicitly extends scope.

Test edit scope:

- `src/backend/polaris/cells/roles/runtime/tests/test_engineering_visual_projection.py`: split the current mixed missing-image/FIFO premise into a fully-bindable verification-first positive and an unsafe-input refusal. Retain full-binding invalidation tests.
- New `src/backend/polaris/cells/roles/runtime/tests/test_engineering_verification_visual_ordering.py`: real public-service/native-tool/SQLite/process/browser/physical-request integration and controls.
- Existing `test_engineering_visual_loop.py` only if shared test setup needs a small reusable helper; no synthetic capture may replace the new real-browser integrated claim.
- Existing `test_engineering_browser.py`: replace only the historical global EVIDENCE_ROOT fixture constant with a stable repository-local `.polaris` test-workspace default. Preserve assertions and the actual browser path.
- Existing `test_engineering_visual_persistence.py`: the same fixture-only migration from its historical global workspace root to repository-local `.polaris/test-workspaces/visual-persistence`; preserve persistence assertions and actual storage behavior.
- Existing `test_engineering_session.py`: migrate only its historical global test-workspace fixture root to repository-local `.polaris/test-workspaces`; preserve all session assertions.

Other tests are read/run only. The existing engineering_support helper already uses repository-local `.polaris/test-workspaces`; that placement is allowed without a global tempfile monkeypatch. Stop and report a concrete interface gap before extending write scope. ROOT owns all versioned docs. Do not spawn your own reviewer.

### Test-first experiment

First seal native RED: with a fully observable fixture and required visual review, scripted Director requests actual build/core checks before any image. Verify that old code denies the check before a child command. A timeout or missing browser is not this RED.

The fixture builder rewrites declared outputs with deterministic equal bytes and observable metadata changes; its actual core command validates the output. Record command count physically. Use the existing scripted loopback provider/native codec, not fabricated visual audit rows.

GREEN arms:

1. Explicit verify-first: actual checks execute once, Director captures resulting outputs, its next physical request contains those exact image bytes, and QA independently reads source, captures and approves only in a later own image-bearing request. Current accepted outcome is produced by the existing service.
2. Host-required implicit checks: Director initially ends its reply, required checks execute before requesting post-check pixels, then finalization reuses that current completed result once.
3. Pre-build capture becomes stale after a real output rewrite under the full metadata binding; a fresh post-build capture/later request suffices without another build.

Necessary controls: no final Director pixels cannot complete acceptance; same-batch/other-role/stale/non-durable/corrupt QA images cannot approve; actual source/output/profile/scope change invalidates old evidence; FIFO/unreadable/symlink escape cannot produce a bindable executable candidate; failed/empty/removed required checks stay failed/missing; cancellation/unknown effects cannot trigger automatic replay. Reuse unchanged existing tests for established controls rather than duplicate them.

### Verification and stop conditions

Run full affected visual/verification/runtime test modules, including autonomous verification, ownership, QA execution and recovery, with all runner cache/TMP/bytecode rooted in this task's `.polaris` directory. Run Ruff lint and format check, targeted strict typing, and unchanged exception-budget gate. ROOT independently runs a finite adjacent integration gate and KernelOne ALL against a frozen candidate.

Report every failure and retry, exact commands/exit codes, physical checks and model exchanges, substitutions and unknown costs. Freeze edited source with SHA-256 copies and a full patch including new files. Required artifacts: report.json, commands.jsonl, source-manifest.json, full.patch, red/green logs and actual final-request/image-byte evidence. Report schema: mode, status, summary, scope, files_read, files_modified, commands_run, findings, architecture_blueprint, experiments, risks, next_action.

Stop research when native RED is causal, GREEN passes both check ownership paths and real image delivery, adjacent negative controls remain effective, and independent scoped review has no blocking findings. This qualifies the mechanism only. Fresh original120, semantic remote-model QA, project command/browser scratch migration and platform-wide KFS convergence remain separate required work.

## Falsifiers and remaining scope

Reject a fix that passes only through hash/metadata relaxation, fake PNG/events, another implicit build, hidden replay, relaxed authority or approval without current own pixels. If real post-build pixels still cannot finalize with the current receipt, report the exact first deviation rather than add retries/timeouts.

Continue B4 project command/browser scratch, B5 launch caches, DB/journal and shared-main offline migrations after this bounded interface closure; preserve all historical FAILED/CANCELLED/UNKNOWN results. Then freeze a new source/config/model candidate and resume the original120 serially with ROOT quality review. No all-platform or Bench success follows from this task alone.

## Actual qualification and continuation (2026-10-23)

The nine-file mechanism is independently approved with no blocking findings. Actual service, process sandbox, Chromium, SQLite and physical loopback requests passed explicit, implicit, stale-image and missing-image arms; only model responses were scripted. Each accepting arm executed one build/core pair and consumed Director and QA images in their own later requests. The missing-image arm retained passed command facts but did not accept. Full candidate metadata and QA source-read/ownership guards remain unchanged.

The original 288-case author gate retained one real adjacent regression: S1 ordinary atomic replacement rejected an old destination with hardlinks. ROOT authorized a separate two-file KFS repair, preserving the unchanged original role test. Ordinary replacement now replaces the authorized name without modifying outside aliases; private-stage/new-inode single-link guards remain. Two-file author gate:487 passed, one existing fork warning, no skips/deselections; independent scoped review approved. Its artifact bucket is `implementation/hardlink-repair`, separate from the immutable initial nine-file evidence.

ROOT verified all11 current/frozen file hashes and ran the combined current candidate:559 passed, two dependency/fork warnings; KernelOne ALL12 stages passed,368 tests passed and1 skipped. Session15978 endedexit0 and must not be replayed. ROOT reports and source hashes are under `.polaris/audits/verification-visual-ordering-20261022/implementation/root-integration/`. Author lint/format/strict types and unchanged exception-budget checks passed. The original failures and split environments remain visible; these numbers are not unique delivered projects.

Thirteen Python3.12 cold recovery-case failures were narrowed to one request-preparation frontier: the synthetic3-second request budget expires while synchronous provider-registry imports load requests/chardet, before a physical request/audit exists. The same representative Python3.13 case reaches its actual crash boundary. No timeout was increased; full Python3.12 cold qualification remains unproven. Native provider budgets and original120 requirements are unchanged. Browser module29 cases passed using the already installed Python3.12/Pillow; the other frozen author modules used the repository Python3.13. Evidence: `implementation/cold-child-audit/report.json`.

Ruling after the user's renewed Bench instruction: start the next original120 frozen batch after this qualified interface closure. Keep B4/B5, DB/journal and protected-main offline migrations active without making every independent migration or feature comparison an added L1-01 prerequisite. The next launch uses explicit host-owned project-local runtime/cache bindings and the existing shared authority, not a new default-storage claim. Each project runs alone, then ROOT personally evaluates its actual artifacts before the next project. Additional original mechanisms are queued in the existing feature evaluation document; long-term memory remains excluded.
