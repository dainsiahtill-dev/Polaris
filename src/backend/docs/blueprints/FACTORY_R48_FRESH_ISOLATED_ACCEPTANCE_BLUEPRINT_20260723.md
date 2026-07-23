# Factory R48 — Fresh Isolated Acceptance

Status: `authorized_once`

Bench state: `schedulable_once`

## Objective

Run one fresh isolated L1-04 acceptance attempt on the exact pre-bench-verified
source fingerprint. Prove whether R46 keeps the workspace lease alive through
long stages and whether R47 removes the implicit Python verifier from every
physical role request and the materialized Go project.

## Frozen source

- Git HEAD: `a286f729ea15dcdad0c388ee15fadb42cab73b07`.
- Source fingerprint: `aab54ea3611e77cd`.
- The authorization is invalid after any source-fingerprint change.
- The Bench Agent must use the current checkout containing the verified R46/R47
  changes. It must not create a clean-HEAD source worktree that drops those
  changes.

## Execution contract

1. Project: `L1-04` only.
2. Exactly one attempt; consume authorization on success, failure, timeout, or
   operator cancellation.
3. `--launcher-instance-mode isolated`.
4. `--bench-session-reporting off`.
5. Never use or reconfigure reserved main ports `49977/5173`.
6. No target-project source edit. Polaris-only diagnosis and repair after a
   failed attempt.
7. No blind retry. A failed attempt first produces a machine-readable defect
   manifest and closes one new general root-cause bucket.

## Mandatory evidence

- Complete physical fact chain:
  `Provider Request -> Tool Lifecycle -> Effect Receipt -> TaskBoundary ->
  TaskRuntime -> Run Ledger -> QA -> Bench Report`.
- For every PM, Chief Engineer, Director, and QA Provider request:
  - readable 24-hex `context_snapshot_ref`;
  - role-correct system identity;
  - task-required tools and normalized schemas;
  - `tool_choice` and `response_format`;
  - final-request token/window accounting;
  - PM contract, CE blueprint, target files, failure feedback, and workspace
    quality coverage flags.
- R46:
  repeated heartbeat projection failures never stop exact-token durable lease
  renewal; no lease expiry or workspace-owner conflict during live work.
- R47:
  Go verification requests and artifacts use `main_test.go`, `go test ./...`,
  and `go run .`; no implicit `tests/test_product.py` or Python unittest.
- Real artifact gates:
  source files, dependency/environment preparation, at least one real
  build/test/lint command, and at least one CLI/Web/API entrypoint execution.

## Completion

Only `COMPLETED_VERIFIED` with the complete physical chain closes R48. Any
other result consumes this authorization and returns Bench state to
`not_schedulable` until the new defect bucket is fixed and the proof ladder is
reissued.
