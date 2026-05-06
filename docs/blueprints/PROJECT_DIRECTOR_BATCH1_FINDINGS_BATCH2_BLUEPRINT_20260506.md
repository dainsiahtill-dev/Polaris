# Project Director Batch 1 Findings And Batch 2 Repair Blueprint - 2026-05-06

Status: active
Owner: Project Director (Codex)
Scope: Polaris landing reliability, security, runtime delivery, and acceptance truthfulness

## 1. Current Understanding

Polaris product landing is blocked less by missing UI surface and more by
truthfulness of the execution and acceptance chain. Batch 1 used five read-only
subagents to audit:

1. Electron startup and frontend runtime connection.
2. Backend HTTP route/auth/rate-limit/readiness contracts.
3. runtime.v2 WebSocket, NATS, cursor, and consumer lifecycle.
4. PM/Director/QA workflow and Director placeholder paths.
5. Test, CI, acceptance, and evidence package gates.

Current code paths under repair consideration:

- HTTP auth/RBAC: `require_auth` -> `require_role` -> role-chat admin endpoints.
- Director/QA chain: PM task market -> Director execution consumer -> QA consumer/service.
- Integration QA: PM shared quality command generation and execution semantics.
- runtime.v2: WebSocket `ACK` -> `JetStreamConsumerManager.ack()` cursor state.
- Acceptance evidence: full-chain audit spec, verification card schema, E2E runners.

## 2. Evidence Register

Confirmed by Batch 1 reports:

- `X-User-Role` is read from client headers and later trusted by RBAC role
  checks. Evidence: `src/backend/polaris/delivery/http/dependencies.py`,
  `src/backend/polaris/delivery/http/middleware/rbac.py`,
  `src/backend/polaris/delivery/http/routers/role_chat.py`.
- `DirectorExecutionConsumer._execute_task` is still a placeholder and can
  produce `changed_files: []` while advancing tasks to QA.
- QA can pass with `files_audited=0` because it extracts target files from the
  original payload instead of Director execution evidence.
- Python integration QA currently uses `pytest --collect-only -q` when tests
  exist, so failing assertions are not executed.
- PM quality gate `ok` is derived from critical issue count, not score >= 80.
- runtime.v2 consumer connection state can be truthy after subscribe failure
  because `is_connected` only checks `_jetstream is not None`.
- runtime.v2 ACK can advance current cursor to a future value not present in
  pending ACKs.
- Acceptance/E2E PASS language can be misread: real flow specs skip without
  `KERNELONE_E2E_USE_REAL_SETTINGS=1`, `pm-director-real-flow` accepts some
  non-pass reasons, and full-chain audit does not require tool-call evidence.
- The runtime readiness verification card does not match the active
  verification-card schema and checker.

High-probability but requiring more local proof before implementation:

- Direct `connectWebSocket()` usages in log panels may create parallel legacy
  runtime streams beside the singleton runtime transport.
- `useRuntimeConnection.workspaceRef` may drift across workspace changes.
- v2 JetStream publishing can coexist with process-local fanout and produce
  duplicate legacy/v2 events.
- `tail` is accepted by runtime.v2 subscribe but not applied to JetStream
  consumer replay bounds.

Rejected or updated assumptions:

- `/v2/role/{role}/chat` route absence is no longer a current fact; the route
  exists and has unit tests. Remaining role-chat risk is authorization and
  runtime semantics, not route existence.

## 3. Defect Analysis

### Defect A: Client Header Can Influence Server Role

Severity: Blocker

Trigger: a client with a valid backend token sends `X-User-Role: admin` or
`developer` to an endpoint protected by `require_role()`.

Root cause: authentication binds a broad `SimpleAuthContext`, but role
authorization still reads client-provided headers as identity facts.

Impact: privileged role-chat cache administration or future RBAC-protected
operations may be authorized from a forged header.

### Defect B: Director/QA Can Produce Acceptance Without Execution Evidence

Severity: Blocker

Trigger: task-market Director consumer path executes a task through its current
placeholder and sends it to QA with empty `changed_files`.

Root cause: execution evidence, changed files, and QA audit scope are not a
mandatory contract between Director and QA.

Impact: product chain can report progress or acceptance without actual work.

### Defect C: Integration QA Does Not Execute Tests

Severity: Blocker

Trigger: generated target project has tests with failing assertions.

Root cause: Python QA command uses `pytest --collect-only -q`, which validates
collection but does not run test bodies.

Impact: broken code can pass integration QA if it imports and collects.

### Defect D: Runtime v2 Cursor Can Skip Events

Severity: Major

Trigger: malformed or buggy client ACKs a cursor greater than any delivered
pending message.

Root cause: ACK path advances `current_cursor` to the requested value even when
no pending message was acknowledged.

Impact: reconnect can start after an event range that was never delivered.

### Defect E: Acceptance Gates Can Overstate PASS

Severity: Major

Trigger: default E2E or real-flow specs skip or accept non-terminal QA reasons.

Root cause: smoke, runtime, and acceptance result language is not fully enforced
by scripts and assertions.

Impact: operators can read a green command as product acceptance when only a
smoke or partial flow ran.

## 4. Batch 2 Repair Plan

Batch 2 uses at most five subagents, each with disjoint ownership. Workers are
not alone in the codebase and must not revert unrelated edits.

1. RBAC hardening worker
   - Ownership: HTTP auth/RBAC files and focused tests.
   - Goal: remove trust in `X-User-Role` for authorization; server role must
     come from trusted auth context or default non-admin identity.
   - Non-goal: full user/role management system.

2. Runtime v2 cursor/connection worker
   - Ownership: `ws_consumer_manager.py`, protocol tests.
   - Goal: subscribe failure must leave `is_connected=False`; future ACK must
     not advance cursor unless pending messages were acknowledged.
   - Non-goal: full replay/tail redesign.

3. Director/QA evidence worker
   - Ownership: Director task consumer and QA consumer/service tests.
   - Goal: placeholder/no-evidence execution must not become QA PASS for code
     tasks; QA must consider Director changed-file evidence.
   - Non-goal: broad Director architecture migration.

4. Integration QA worker
   - Ownership: PM shared quality integration QA command/tests.
   - Goal: tests must execute, not only collect; no-test fallback must remain
     explicit and traceable.
   - Non-goal: redesign all language-specific QA.

5. Acceptance evidence worker
   - Ownership: Electron E2E runner/spec evidence assertions and verification
     card schema compatibility.
   - Goal: prevent skipped/partial acceptance from being reported as PASS and
     make the active verification card pass schema checks.
   - Non-goal: CI pipeline restructuring unless tests prove runner paths.

## 5. Test Plan

Happy paths:

- Valid token can access normal authenticated endpoints.
- runtime.v2 SUBSCRIBE and delivered ACK still advance cursor normally.
- Integration QA passes a real passing pytest project.

Edge cases:

- Valid token plus forged `X-User-Role: admin` must not pass admin-only gates.
- ACK for a future cursor must not advance current cursor.
- Director no-op/docs-only tasks are explicit if allowed.

Exception cases:

- NATS subscribe failure leaves consumer disconnected.
- Integration QA failing test returns a failed result with stderr/stdout.
- Verification card checker reports the active runtime readiness card valid.

Regression cases:

- Existing focused HTTP/WS/PM/QA tests remain green.
- Electron smoke/runtime E2E still passes after acceptance script assertions.

## 6. Rollback Plan

Rollback must be file-scoped:

- Revert only files touched by the failed worker.
- Re-run the tests listed in that worker's final report.
- Preserve unrelated dirty worktree changes and generated assets.
- If Batch 2 integration finds conflicting edits, stop and report conflict
  rather than using `git reset` or broad restore.

## 7. Batch 2 Repair And Verification Register

Recorded by Batch 3 / Worker 5 on 2026-05-06. This is a documentation-only
audit update. The worktree is shared and dirty; no source code was changed for
this register.

Batch 2 issues now recorded as repaired or guarded:

1. RBAC/auth hardening: authorization must not trust client supplied
   `X-User-Role`; role checks are expected to come from trusted auth context or
   fail closed.
2. Runtime v2 cursor and connection guardrails: subscribe failure must not
   leave the runtime consumer connected, repeated subscribe must clean up prior
   state, and future ACKs must not advance cursor without pending evidence.
3. Director/QA evidence contract: placeholder or no-evidence Director execution
   must not become QA PASS for code tasks; QA must inspect Director execution
   evidence such as `changed_files`.
4. Integration QA execution semantics: Python tests must execute test bodies
   rather than only running `pytest --collect-only`.
5. Acceptance truthfulness: real Electron acceptance is split from smoke/runtime
   E2E and must fail closed when real settings are missing.

Batch 2 verification commands and observed results:

| Command / evidence | Result | Audit note |
| --- | --- | --- |
| `npm run test:e2e:acceptance` | FAIL in current artifacts | Real acceptance did execute far enough to produce Playwright failure artifacts; it is not accepted. |
| `test-results/electron/.last-run.json` | `status: failed`; two failed test ids | Current acceptance state is failed, not skipped and not PASS. |
| `test-results/electron/full-chain-audit-unattende-eaf39-trong-JSON-evidence-package/error-context.md` | FAIL artifact present | Snapshot shows `LLM 就绪检查未通过`, `required: pm, director, qa`, `blocked: pm, director, qa`, and `0 events`. |
| `test-results/electron/pm-director-real-flow-real-c864b--PM-and-Director-workspaces/error-context.md` | FAIL artifact present | Snapshot shows PM workspace with `0/0`, no tasks, `required: pm, director, qa`, `blocked: pm, director, qa`, and `0 events`. |
| `npm run test:e2e:acceptance` without `KERNELONE_E2E_USE_REAL_SETTINGS=1` | Expected exit 2 by script contract | This is a guardrail, not product acceptance. Missing real settings must not become skipped PASS. |

Focused tests reported by Batch 2 workers should remain part of the next
verification sweep, but this register does not claim them as full product
acceptance. The current acceptance gate is still red because the real
PM/Director/QA chain did not satisfy the Electron acceptance specs.

## 8. Current Real Acceptance Failures

Current real acceptance has two blocking failures:

1. `full-chain-audit.spec.ts` / `unattended full-chain audit with strong JSON
   evidence package` fails. Evidence path:
   `test-results/electron/full-chain-audit-unattende-eaf39-trong-JSON-evidence-package/`.
   Observable state: Director is disabled by `LLM 就绪检查未通过`; runtime panel
   reports `required: pm, director, qa` and `blocked: pm, director, qa`; event
   count is `0`.
2. `pm-director-real-flow.spec.ts` / `real PM -> Director flow reaches PM and
   Director workspaces` fails. Evidence path:
   `test-results/electron/pm-director-real-flow-real-c864b--PM-and-Director-workspaces/`.
   Observable state: PM workspace has no tasks (`0/0`), no PM/Director
   progress, runtime remains blocked on `pm, director, qa`, and event count is
   `0`.

These are acceptance failures, not test-infrastructure successes. Screenshots
and traces exist beside each `error-context.md` artifact and must be preserved
until the next successful acceptance run supersedes them.

## 9. Batch 3 Work Split

Batch 3 should keep ownership disjoint and continue the no-revert rule:

1. Worker 1: LLM readiness and real-settings bootstrap.
   - Confirm why the real acceptance run exposes `LLM 就绪检查未通过`.
   - Verify Electron fixture env, backend LLM status, and renderer readiness
     projection are aligned.
2. Worker 2: PM task materialization and workspace state.
   - Confirm why PM workspace remains `0/0` after the real flow starts.
   - Trace court/PM plan sync into runtime contracts and task projection.
3. Worker 3: Director unlock and task handoff.
   - Confirm Director remains disabled only because readiness is blocked, not
     because task-market handoff is missing.
   - Re-check `metadata.pm_task_id` evidence once PM tasks appear.
4. Worker 4: Runtime event visibility.
   - Explain `0 events` during the failed acceptance path.
   - Verify runtime.v2 / process-local stream delivery and UI subscription
     state after the Batch 2 runtime fixes.
5. Worker 5: Audit/documentation and acceptance truthfulness.
   - Keep this blueprint current with failed commands, evidence paths, and
     before/after acceptance state.
   - Prevent any report from counting skipped E2E, dry-run E2E, or smoke-only
     E2E as acceptance PASS.

## 10. Acceptance Truth Rule

`skipped` Electron E2E is never acceptance PASS. `npm run test:e2e` or
`npm run test:e2e:smoke` may establish smoke/runtime confidence, but product
acceptance requires the real PM/Director/QA specs to execute under
`KERNELONE_E2E_USE_REAL_SETTINGS=1` and pass without skipped full-chain cases.

Allowed labels:

- `SMOKE_PASS`: app opens and selected non-acceptance E2E checks pass.
- `RUNTIME_PASS`: runtime/probe/visibility checks pass.
- `ACCEPTANCE_FAIL`: real full-chain acceptance ran and failed.
- `ACCEPTANCE_BLOCKED`: real full-chain acceptance could not run because a
  required precondition such as real settings is absent.
- `ACCEPTANCE_PASS`: real full-chain acceptance ran and all required PM,
  Director, QA, evidence, and audit gates passed.
