# runtime.execution_broker

## Purpose

`runtime.execution_broker` is the cell-layer unified execution gateway.
All runtime subprocess/thread/offload submissions in business cells should
route through this cell instead of calling `subprocess`/thread primitives
directly.

## Boundary

- Owns process launch/wait/terminate/cancel orchestration at cell layer.
- Owns log stream draining into UTF-8 text logs.
- Reuses `polaris.kernelone.runtime.execution_facade` as technical substrate.
- Does not own business task state (`runtime.state_owner` still owns writes).

## Public Surface

- `polaris.cells.runtime.execution_broker.public.contracts`
- `polaris.cells.runtime.execution_broker.public.service`
- `polaris.cells.runtime.execution_broker.public.project_verification`
- `polaris.cells.runtime.execution_broker.public.bootstrap` (composition only)

## Rules

1. All text log writes must remain explicit UTF-8.
2. All subprocess launches must include deterministic metadata.
3. Callers should pass workspace in command metadata for auditability.
4. Project artifact/command receipts are private-sealed owner facts. Caller
   evidence, pass/fail flags, generic audit receipts, lookalikes, and retagged
   dataclasses are never authority.
5. `RunProjectVerificationCommandV1` cannot be caller-constructed. Bootstrap
   binds one authority port; before every spawn it re-resolves exact
   workspace/project/run/contract/obligation/owner task, canonical argv/cwd,
   CE authority hash, committed JobToken set, and execution-policy hash.
6. Public runner injection is forbidden. Tests may monkeypatch only the private
   broker runner. Timeout is finite, positive, and capped at 3600 seconds.
7. Receipt identity binds the immutable full contract input closure, exit code,
   timeout, output hash, profile-specific proof result, JobToken set, and policy
   hash. Artifact drift invalidates query; exit zero without positive test proof
   remains a failed receipt.
   Missing receipt and present failed receipt remain distinct.
8. The broker reports physical effects only. It never emits a final project
   completion verdict; generic `audit.evidence` is a mirror, not authority.
9. Receipt provenance lives under platform-owned KernelOne storage, never the
   target workspace. Events are append-only and HMAC chained; query validates
   the full effect kind, request identity, receipt body, and content hash.
10. A physical verifier attempt requires an atomically consumed one-use
    capability. The capability, launch metadata, and receipt bind the attempt
    lease, authority revision, JobToken set, verifier profile, and current
    `control_plane.verifier_policy` decision hash.
11. Transient spawn failures use bounded attempt leases. Expired or retryable
    attempts may advance to a new fenced attempt; one live attempt cannot spawn
    twice and semantic/policy failures remain fail-closed. Execution id, PID,
    and Linux process start token are persisted before wait; an expired exact
    process is terminated before a replacement attempt is reserved. An
    attempt-private sandbox launch gate prevents target argv from executing
    until that durable process fence is committed.
12. Physical verifier commands run only in the fail-closed Bubblewrap adapter.
    Platform config/runtime authority and workspace-local `.polaris` are hidden,
    HOME is disposable, the environment is cleared then rebuilt from a narrow
    allowlist, the exact contract cwd is used, and copied authoritative inputs
    are mounted read-only over the writable workspace. Missing OS isolation is
    a hard launch failure, never an unsandboxed fallback.
13. Verifier policy binds the selected executable path, its resolved realpath,
    and SHA-256 digest. Workspace/ephemeral fake toolchains are rejected; the
    broker executes that exact path and revalidates its identity before and
    after the physical effect.
14. Physical completion does not freeze authority. Before committing a receipt,
    and again on every public receipt query, the broker re-resolves the current
    contract/JobToken/policy owner. Revoked or drifted authority invalidates the
    result; QA must query the current receipt before accepting it.
15. Long-lived entrypoints use a fenced process-liveness readiness probe. A
    ready PID/start-token pair is terminated by its owning broker and the probe,
    identity, and controlled-termination facts are sealed into the receipt;
    ordinary timeout alone is never entrypoint success.
