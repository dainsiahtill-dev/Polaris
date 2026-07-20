# Factory Bench Fresh Workspace Identity Blueprint

Status: implementation verified; pre-Bench scheduling blocked
Date: 2026-07-20
Classification: structural / P0
Scope: internal `factory_bench` harness only
Provider/Bench: `not_schedulable`

## 1. Defect

Fresh Bench setup deleted a project runtime directory after the isolated
backend had already enrolled that directory in the KernelOne lock authority.
Recreating the same path produced a different inode while the immutable
authority still described the original inode. The next settlement replay then
failed closed with `stream_identity_drift`.

Captured evidence:

- runtime root:
  `/home/dains/.cache/kernelone/.polaris/projects/l1-04-66ad094c7ae0/runtime`
- authority token: `f3018f69fe780038ecd794b3`
- enrolled identity: device `2096`, inode `5149286`
- actual identity: device `2096`, inode `5580745`
- failure: `event stream lock or descriptor capability failed`

## 2. Root cause

`purge_project_runtime()` recursively removed keyed runtime directories to
avoid cross-run prompt contamination. `run_factory_chain()` called that purge
after the isolated backend was launched and healthy. This violated the lock
authority invariant that a storage identity remains bound to one physical
runtime-root identity.

The lock primitive is correct: same-path inode replacement must remain a
fail-closed identity drift. Ordinary acquire/provision must not repair, rotate,
or silently rebind authority.

## 3. Authoritative design

```text
fresh Bench attempt
  -> allocate unique physical project workspace
     (bench run id + project id + cryptographic nonce)
  -> write immutable catalog metadata
  -> launch isolated backend for that exact workspace
  -> backend enrolls one lock authority for its runtime root
  -> Factory chain uses the same workspace without deleting runtime state

director_resume
  -> reuse only the sole identity-bound workspace under the supplied
     run-id/project-id identity
  -> preserve enrolled runtime identity and trusted PM/CE evidence
```

Rules:

1. Freshness is an identity-allocation concern, not a deletion concern.
2. A fresh run must allocate its workspace before isolated backend launch.
3. Normal Bench execution must never recursively delete a provisioned runtime
   root or its lock authority.
4. `director_resume` remains the only reuse path. It accepts exactly one
   run-scoped workspace only when immutable catalog metadata exactly binds the
   raw run id, project id, nonce, device, and inode. Historical legacy
   `<bench-root>/<project-id>` workspaces predate this binding and are rejected
   with an explicit migration-required error. No silent legacy trust or
   migration is allowed. Missing, stale, or ambiguous candidates fail closed.
5. KernelOne `stream_identity_drift` remains fail-closed.
6. Existing failed instances, runtime roots, and authorities remain evidence;
   this bucket does not garbage-collect them.
7. Authority retirement/garbage collection, if required, needs a separate
   versioned offline protocol with ownership, quiescence, and receipts. It is
   not an automatic Bench recovery path.

## 4. Minimal implementation

- Add a bounded fresh-workspace allocator under the Bench work directory.
- Bind sanitized run/project path components to the complete raw identities
  with SHA-256 suffixes; sanitization collisions cannot alias a workspace.
- Create the nonce directory with parent `dirfd`, `O_NOFOLLOW`, `mkdirat`, and
  `fstat`/`lstat` identity comparison; collisions retry, escape or exhaustion
  fails closed.
- Write `.catalog_meta.json` once with `O_CREAT|O_EXCL|O_NOFOLLOW`, explicit
  UTF-8, and directory/file `fsync`. Resume validates it and never overwrites it.
- Bind the isolated-launch receipt to workspace device/inode and revalidate the
  exact immutable catalog immediately before receipt issue; never resample and
  bless a replacement inode. Revalidate again before accepting the launched
  instance.
- Select fresh versus resume workspace before launch.
- Remove destructive runtime purge from API and legacy chain paths.
- Do not modify KernelOne lock acquisition or target-project code.

## 5. Verification

Required evidence:

1. Two fresh allocations for the same project/run are distinct and exist.
2. Both allocations resolve below the authorized Bench work directory.
3. Repeated `main()` isolated launches use different physical workspace and
   runtime-root identities.
4. `run_factory_chain()` and legacy `run_chain()` do not delete external
   runtime state.
5. Resume reuses one exact identity-bound workspace; legacy/unbound metadata is
   rejected pending a separate receipt-backed migration protocol.
6. Existing same-path inode replacement test still rejects drift.
7. Root-anchored catalog read/write and launch validation reject ancestor
   relocation plus symlink substitution, as well as missing, mutated, or
   unhashed catalog evidence.
8. Static purge audit detects direct helpers and nested shell deletion commands.
9. Focused tests, Ruff, format check, Mypy/compileall pass.
10. A Provider-free isolated backend smoke reaches
   `/v2/runtime/fingerprint` on a newly allocated workspace, with matching
   instance/workspace identity; no fresh Bench is run while `not_schedulable`.
11. Every real PM/Chief Engineer/Director/QA call must have a readable
   `context_snapshot_ref`, and its final provider request must be audited for
   role-correct system identity, messages, tools, `tool_choice`,
   `response_format`, final-request token/window accounting, and required
   coverage flags. This remains a pre-Bench blocker and cannot be replaced by
   prompt summaries or messages-only estimates.

Current evidence:

- workspace lifecycle focused tests: `26 passed`
- full runner suite: `163 passed`
- standalone runner tests: `4 passed`
- KernelOne identity/drift subset: `7 passed`
- Ruff: passed
- Mypy for the runner: passed
- Provider-free isolated startup: `/v2/runtime/fingerprint` returned 200 with
  matching instance/workspace identity; no `stream_identity_drift` or
  application-startup failure
- independent security review: `PASS` (`P0=0`, `P1=0`, `P2=0`)
- final provider-request matrix: not yet closed; Bench remains
  `not_schedulable`

## 6. Pre-mortem

- Reusing `<bench-root>/<project-id>` would recreate the original defect.
- Trusting legacy metadata without device/inode binding would silently bypass
  the new authority boundary; it is therefore rejected, not auto-upgraded.
- Deleting only the authority would weaken auditability and race a live owner.
- Rebinding on acquire would convert physical identity drift into silent data
  aliasing.
- Putting the nonce only in instance metadata would not change runtime-root
  identity.
- Allocating after backend launch would preserve the fatal ordering bug.
