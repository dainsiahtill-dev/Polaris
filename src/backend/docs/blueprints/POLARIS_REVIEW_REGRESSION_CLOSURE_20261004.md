# Six review regressions: corrective architecture

Status: approved master-plan corrective work; native evidence pending.
Scope: Polaris only, no generated Bench target engineering edits.
This document and external agent reports are engineering records, not runtime authority.

## Intent

Close all six user-supplied review findings without relaxing source authentication,
dependency drift detection, process drain, CE ownership, or receipts. Restore valid
Python/Rust verification and dependency-backed Director-only repair. Do not restart
successful PM/CE or start paid Bench during mutable-source debugging.

## Existing topology and responsibility

Factory quality orchestration owns preparation and repair claim lifecycle.
NativeValidationSession authenticates CE-bound baseline and candidate source inputs.
VerificationGroup supplies isolated execution with source/dependency/tool witnesses.
KernelOne owns filesystem, process isolation and physical drain primitives.
The NATS infrastructure adapter owns only its exact process/readiness lifecycle.
Graph owner for native validation is factory.pipeline; no new Cell or event rail.

## Required invariants

R1: executable content identity and invocation identity are distinct. Rustup cargo
symlinks keep the selected cargo invocation while the resolved binary and alias
binding remain checked. No unmounted executable or retargeted alias may pass.

R2: Python imports use sandbox-relative /workspace, not inherited host PYTHONPATH.
Host secrets/environment must remain excluded; original workspace remains unmounted
as writable source. The verifier may write only its disposable staging/output.

R3: configured Rustup toolchain/cache policy is reused from existing Cargo isolation.
Mount only declared, witnessed roots; pin RUSTUP_HOME/CARGO_HOME/toolchain selection.
No writable host toolchain/cache, network fallback, default-toolchain downloads,
or language-specific target source patching.

R4: authenticate candidate source independently of dependency preparation. Complete
authorized dependency preparation before opening the fresh candidate verification
group. Then freeze dependencies for that group. Preparation that changes source
without valid owner/effect evidence stays rejected; drift after freeze stays rejected.

R5: physical drain uncertainty still forbids rollback/disposal/settlement, but must
stop and await only the pending repair attempt's heartbeat. Preserve original drain
error, source/staging and unresolved authority. No perpetual abandoned renewal.

R6: readiness waits observe owned process exit concurrently with pending connect/
greeting probes. Preserve one total budget, early-exit diagnostics, repeated-cancel
cleanup, and external/shared NATS non-ownership. No HTTP realtime polling.

R7: native procfs scan treats a disappeared process entry as departed, not a global
unknown drain. This new blocker was independently reproduced by A and B before
and during their repair checks. Only FileNotFoundError/ProcessLookupError at that
entry read may be skipped; live members, permission failures and malformed data
remain conservative. Leader reap and pipe completion stay separately required.

## Final cross-component closure requirements

Configured Rustup logical selectors must resolve to the installed canonical identity
without downloads/default substitution. A previously absent, platform-admitted
dependency root must be discovered after authorized preparation, independently of
source/effect authentication and before that group's dependency freeze. Explicit
denial and later dependency drift remain hard failures.

Both verifier TOML import paths must honor declared Python>=3.10 with a checked
runtime compatibility dependency; simulated absence is not native3.10 qualification.
Shared NATS fresh-store creation must consume KFS directory effects rather than
business direct mkdir. Existing identity/fence/nonempty-store migration guards stay
unchanged. A missing KFS directory primitive requires explicit root design/scope
ruling, never fake files or architecture-gate baseline expansion.

The actual release detector hit is the business `_open_log` append `os.fdopen`,
not mkdir. A neutral guarded KFS append-handle capability must retain no-follow,
regular/single-link/UID/descriptor custody and close-on-error behavior. Directory
compliance remains required independently; it must not be cited as closing this
specific observed direct-write hit.

## Partition

- A: native_validation_group.py + native_validation_session.py and native-language tests.
- B: factory_workspace_quality_impl.py and separate repair lifecycle/preparation tests.
- C: nats/server_runtime.py and managed-startup/readiness tests.
- D: kernelone/process/process_tree.py and owned procfs/drain tests, after the
  newly observed ESRCH blocker; no overlapping writer with A/B/C.
- Root: shared docs, baseline captures, independent reviews and combined gates.

A must retain existing Session candidate/before_repair/run_command signatures.
B consumes those interfaces unchanged and may not edit either A source file.
Any new shared interface necessity is reported to root before changing scope.

## Acceptance

Each finding first gets a real failing regression/probe, then source fix and same
probe GREEN. Python entrypoint and Rustup Cargo must execute in the actual native
group; only unit mocks are insufficient. Dependency preparation and heartbeat
tests must exercise the actual Factory repair orchestration. NATS early exit must
race a pending native probe/owned process, not merely assert an error string.

All workers retain initial source bytes, task-only diff, exact hashes, RED/GREEN,
static gates, affected suite results and remaining limits. Root independently
reviews spec and quality, personally reruns combined gates, freezes reviewed source
and runs KernelOne release gate. A timed-out broader sweep is unrun/incomplete,
never green. Fresh isolated full-budget qualification is a separate next stage.
