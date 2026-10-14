# Negative directed-effect operational closure

A synchronous executor can return a real failed command result after consuming
its one-use effect fence. The DEO owner records recovery and DEAD_LETTER, closes
the parent, and settles the TaskRuntime attempt as failed. This is a terminal
negative effect; it has no success EffectReceipt or commit_receipt. Requiring
those positive receipts pinned failed run resource release even though no
physical effect remained active.

The existing TaskRuntime read-only observer exposes these outcomes separately as
`ExecutionEffectClosureObservationV1.terminal_failures`, a tuple of frozen
`ExecutionEffectTerminalFailureObservationV1` descriptors. No positive receipt,
new state owner, ledger event, execution authority or control command is added.

A descriptor requires the existing strict DEO reducers to authenticate the
sealed member, exact claim fingerprint/binding, recovery, DEAD_LETTER and parent
close, including operation head and dead-letter/receipt counts. The current or
historical TaskRuntime terminal intent remains independently authenticated and
must have failed. Canonical role-owner turn facts must match source, complete
provenance, storage namespace, session, idempotency and outcome hash. The real
command failure must have a positive nonzero exit code, concrete physical error,
exact call/tool, matching raw/typed authority content, native envelope and
invocation argument hash, and no pending asynchronous work. The observer uses
the existing director.runtime public canonical immutable argument hash helper
to reproduce the producer recovery/resolution seals, preserving its recorded
400/80 character evidence boundaries. These are observations of owner-issued
facts; caller success flags or a DEAD_LETTER label alone do not suffice.

Run Ledger matches a failed tool result only to the independent typed negative
proof. Its exact typed and raw owner result hashes bind both serialized rows;
this narrowly supports their authenticated projection differences. Successful
mutations retain the existing strict EffectReceipt/commit checks. Negative
closure supplies no positive EffectReceipt count and does not satisfy missing
or failed QA, command or code evidence. `passed` remains false.

Active, foreign, missing physical error, altered binding/resolution, missing
parent close, raw/typed drift, foreign native invocation and malformed session
or decision shapes remain active/unknown with no observed negative proof.
Legitimate final role turns with no execution are skipped during discovery.
Double reads reject changed owner, parent, operation or session evidence.

The native18 epoch3 read-only audit authenticates five positive owner commits
and one negative CLI proof. The negative proof is operation `a02ad484...`,
DEAD_LETTER event `278e763...`, resolution hash `96173dc...`, parent close event
`1732ecf...` version 16 and failed TaskRuntime fact `cf0ab90...` sequence 152.
Its real exit code is 1. All 590 runtime files remain byte-identical after
observation. This component evidence does not qualify project delivery.

Focused validation includes a real private Python CLI exit 1, DEO owner
terminalization, observer and failed cleanup RED-to-GREEN, ten refusal cases,
and the existing terminal cleanup regressions. Runtime behavior and user
project files are never patched by this observer.
