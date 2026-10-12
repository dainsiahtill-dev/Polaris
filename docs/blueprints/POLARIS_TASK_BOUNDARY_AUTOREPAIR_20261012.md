# Task boundary repair after materialization

Native L1-01 reached zero compiler diagnostics, a passing build and 21 passing
Node tests, but Factory rejected stale per-task entrypoint verdicts. Some still
reference removed `scripts/serve.js`; the current HTML genuinely references a
nonexistent `main.ts.js`. The HTML quality scanner silently misses that doubled
extension because it searches for `main.ts.ts` instead of the existing `main.ts`.

Use existing platform capabilities: KernelOne artifact-quality diagnostics,
Director's CE-authorized local repair loop, and the Run Ledger public
`reproject_task_boundary_verdict` operation. After actual verification, refresh
failed verdicts from their immutable predecessors and preserve all original
evidence and verifier requirements. Never convert a missing verdict, a live
task, or a failed verifier into success. Keep historical failures and CAS
coordinates. No PM/CE replay, target-project manual edit, new setting, approval,
service, or alternative evidence store.

First fix HTML diagnostic discovery and prove it without effects. Then connect
the existing reprojection operation to post-settle recovery and verify changed,
unchanged, missing, foreign and failed-evidence cases. Reprojection must reuse
source capability facts rather than inventing an authorization result. Check
focused scanner/repair/ledger tests and the native run before a fresh isolated
qualification attempt. Project build/test success alone does not prove delivery.

Final request `2488d87e60c4390327c54581` revealed another normal-task obstruction:
the Base64 heuristic treats slash-separated module names and artifact receipt
URIs as encoded instructions. Validate suspected implicit Base64 as UTF-8 text
and classify only decoded existing instruction-override patterns. Retain the
existing explicit-encoding and plain injection behavior. Reuse one detector in
gateway and compatibility assembler; preserve task bodies, source code, receipt
references, and encoded observations. No new trust override or runtime setting.

Native QA now exposes a separate compiled CLI path mismatch. Its repair pipeline
discarded `npm run start` provenance, selected a domain-source owner and treated
the configuration failure as a missing design contract. Preserve command
provenance through the existing diagnostic normalizer, resolve manifest-owned
failures to the committed package owner, and correct nested TypeScript emitted
path inference in the runtime planner. Reuse registered npm repair tools and
retain the real start check; no generated target may be edited by the operator.

After native build/test/start passed, QA report publication still called the
Director repair-context builder without a repair owner. QA publishes evidence
under its already-completed canonical task boundary; it does not need a new
workspace mutation context. Read the exact predecessor fact through the existing
FactStream public query, reuse its committed task/run/project metadata, and let
the QA owner rebuild and commit its evidence barrier and final verdict. Test
exact metadata conservation and rejection of another task's token, then perform
the native QA retry. No token, scope, gate policy, or success verdict is invented.

The native QA commit now succeeds, but completion used its lineage-only metadata
as the latest execution grant and then required every QA observation to carry a
write token. Keep the last actual committed token in the ledger projection.
Accept immutable QA report/verdict facts bound to their existing task boundary
without demanding a workspace execution token. Command/write gates retain their
original token checks; completion still requires actual owner-sealed artifact,
environment, build/test and entrypoint receipts. QA observations cannot create
missing execution receipts.

The same native completion query rejected ordinary Node entrypoints and
TypeScript checks, and the valid `tsc && node --test` package script. Reuse the
existing verifier policy owner to recognize relative Node script entrypoints,
application arguments, `npx tsc --noEmit`, and success-dependent build preparation
before a real test runner. Preserve manifest/runner hashes, exact command
authority, failed exit codes and zero-test rejection. Do not require commands
to be rewritten into a special package-script shape. Confirm policy decisions
against the native contract and run real passing, failing and empty Node tests.

Native r10 observation now passes the corrected policy and ledger checks. Its
21 artifact obligations have current authoritative receipts. Full project
completion remains unproven until the entrypoint and verification obligations
also have their own exact execution receipts. The manually recovered run is
diagnostic evidence; fresh isolated unattended qualification remains required.

Fresh r11 exposed another planning obstruction: `public/index.html` was an
explicit owned target with an entrypoint verifier, but PM demanded that HTML
and script targets have the same parent directory. The revised native response
(`fe68bf645eadeea231a8da3505d1bb00125c1a5a3d2de3b451cb4655671ad991`)
was otherwise valid and became seven normalized tasks. Recognize declared HTML
targets with explicit entrypoint verification without imposing a directory
layout; scripts may live elsewhere or be inline. Keep unsafe-path, prose-only,
documentation-only and invalid-verifier rejection. Check actual wiring later
through CE/Director and real verification. The original missing-HTML response
still fails correctly; source revalidation of the unchanged revised response
passes. Preserve the failed r11 evidence and use a new isolated qualification
run rather than rewriting its plan, tokens or terminal state.

Fresh r12 admitted PM in one call with three tasks and a nested HTML target, then
CE exposed a transport gap: MiniMax returned a complete XML-shaped result call
with control-token markers, but the parser produced no submission. Add lossless
reserved-result XML decoding under the existing caller JSON schema. Reject
duplicates, mixed text, partial envelopes and executable calls; keep full schema
and semantic validation. The result remains a non-effect protocol primitive.

The final CE retry also contained opaque receipt placeholders in user/assistant
history while its only callable tool was result submission. Omit those unusable
historical turns from that result-only request and retain readable history,
current instructions, system authority and all original stored evidence.
Remove only the known leading projection pointer when a current instruction is
attached after it. Executable turns retain their retrieval history.

The same PM response called a TypeScript no-emit check a test. Normalize that
command to lint before authority compilation; preserve argv/cwd and genuine
test commands. Recognize ordinary direct `npx tsc` build commands in verifier
policy. Compilation is still not real test execution. Source cohorts:128
result-transport/history checks and167 PM/verifier checks passed. Fresh native
delivery remains unproven until an untouched isolated run completes.

Native r13 reached complete PM and CE handoff, materialized source/tests and
installed dependencies. Its local repair removed a compiler error, but Node's
extensionless ESM test import remained. Existing coverage named the registered
import repair, while the normalizer discarded the `.ts` importer because the
missing path did not end in `.js`; multiline TAP also replaced the loader
exception with its generic failed-file summary. Preserve the exact loader
diagnostic and map the controlled `/workspace` mount back to the bound project.
Import dependencies remain read context rather than competing edit owners.
Reuse the existing runtime binding for extensionless imports. A test harness
outside an explicit TypeScript emit root consumes compiled modules when the
project declares a matching build; genuine native-source execution keeps `.ts`
imports. Native repair changes only the importing test.

After native import repair, static coverage wrongly blocked simple TypeScript
CommonJS outputs as unknown export surfaces. Recognize literal `exports.name =`
assignments and the exact `__esModule` marker without executing code. Dynamic
exports remain unknown and missing named exports remain real errors. Read-only
native scan now has no errors or incomplete reasons. Runtime/ownership cohort:
121 passed; static coverage cohort:96 passed. KernelOne full release gate:
368 passed,1 skipped,3 existing experimental-stream warnings.

Failure closeout then exposed a separate lifecycle defect: local row rework
changed a fulfilled completed session to suspended while preserving its completed
terminal intent and proof. Preserve terminal session status on row reopen; the
next execution claims a fresh identity. Legacy authorized local-rework sessions
may restore only their exact proof-bound original terminal outcome after the DEO
inactive pre-barrier validates it. Do not convert acceptance failure into success.
The native failed run closed through its owner APIs with zero active sessions and
zero conflicts. Settlement/reopen cohort:63 passed. This recovered r13 is
operator-assisted diagnostic evidence; a fresh isolated r14 is required.
