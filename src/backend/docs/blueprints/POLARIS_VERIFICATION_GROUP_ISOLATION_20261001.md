# Verification groups must not mutate delivered source workspaces

Owner: factory.pipeline verification orchestration. Process cancellation belongs
to KernelOne process owner. Existing command/receipt authorities remain owners.
Architectural correction under the approved unattended master plan.

## Proven problem

Exact factory_8edf12521449: rejected tsconfig candidate caused tsc/TS6059 to
produce tests/verify.test.js, declaration and map. Six owned source files were
restored, but compiler outputs survived. Later validation therefore examined a
different workspace than the recorded pre-candidate baseline. Reusing that old
baseline was not valid rollback evidence.

## Decision

Use one disposable input snapshot per verification group/candidate epoch.
Build/test/start share that copy and consume its generated outputs. A later
candidate starts a new snapshot. The live workspace is never RW-mounted into
the command sandbox. Outputs remain disposable; no automatic output promotion,
source cleanup, new Director grant or rewritten target code.

Reuse the existing native_validation_sandbox copy/bubblewrap mechanism and
run_process_tree_safe. Do not use the broker's existing live-RW sandbox as
proof of isolation. Cross-Cell receipt/input-hash capabilities must be consumed
through runtime.execution_broker public contracts, never its internals.

## Inputs and evidence

Inputs are current CE/receipt-owned authored paths plus the current policy-gated
candidate's explicit source effects. Exclude unknown historical compiler outputs;
extension alone cannot decide source versus output. Preserve authored JS when
actually declared. Copy through existing KFS/storage capability; reject root and
symlink escape. Record source/candidate identity, input path hashes and aggregate
hash, dependency identity, exact argv/environment policy, exit code, output hash,
timeout/cancellation/drain status. Compare source identities after capture/run;
drift cannot become authoritative success. No caller-provided fake receipt seal.

Dependency and toolchain roots are read-only; runtime/config credentials and
the original workspace are not mounted. Network is disabled for verification.
Dependency preparation stays a distinct existing authorized step; it is not a
hidden verifier fallback. Unavailable isolation returns a precise failure, never
live execution. This is filesystem/process isolation, not a claim of container
security beyond the implemented mounts and namespaces.

## Lifecycle

Open source-bound group, stage inputs, execute sequential commands, bind actual
results, drain all process descendants, close/dispose the copy. Cancellation
must reach the physical process owner; cancelling asyncio.to_thread alone is not
drain proof. Never remove staging before the writer tree is known terminal.

Process owner interface: single-use `ProcessTreeRunControl()` exposes only
`cancel()`. `run_process_tree_safe(..., cancel_control=None)` remains compatible
with existing calls. Only the spawn owner binds process/group identity; caller
PID and `drained=True` are not authority. Preserve the group identity after its
leader exits, escalate termination when needed, and finish worker/pipe drain
before reporting terminal. Factory awaits a shielded physical worker, requests
cancel on outer cancellation, resists repeated cancellation while draining, then
disposes staging and rethrows. Unproven drain retains staging and fails closed.

Restore source candidates only through existing guard authority, then revalidate
the actual restored source state. Do not reuse the pre-round result as new proof.

## Verification

Actual tsc include/rootDir failure retains TS6059 with zero live file changes.
One copy's build output is consumed by test/start. Authored JS is included but
unknown stale emitted JS is excluded. Source drift, absolute path/symlink escape,
unavailable sandbox, failed verifier, timeout, cancellation and grandchild late
write all remain explicit and fail closed. Failure receipt is failed, not missing.
Existing Cargo isolation and project-authority tests remain strict. Local sandbox
tests do not close fresh L1-L12 or all-role context/physical-request qualification.

## Read-only source baseline across repair epochs

Dynamic r02 query: immutable CE contract has ten authored obligations. Four
current artifact receipts remain queryable, while Task1's six unchanged source
artifacts no longer match the current repair JobToken/policy revision. Current
receipt invalidation remains correct for completion, but must not erase the
historical provenance needed to stage a verifier baseline.

Add `QueryProjectArtifactSourceBaselineV1` / `query_project_artifact_source_baseline`
in the existing execution broker owner. It validates exact current CE
owner/path identity, authenticates the existing full receipt provenance chain,
and selects an originally sealed receipt whose full identity and hash match
current source bytes. Return a separate private-sealed
`ProjectArtifactSourceBaselineV1`, never a current `ProjectArtifactReceiptV1`.
It is read-only source evidence: no write grant, verifier capability or
completion eligibility. Do not relax current receipt queries or mint a fresh
receipt. Absent database/key stays absent; baseline query creates no state.
Wrong identity, bytes, chain MAC, path/symlink or owner remains fail-closed.

## Native tool-result type preservation

Real prepare/claim/fence/mutate/commit fixture showed an additional upstream
defect: ToolBatchRuntime thaw inferred empty immutable sequences as maps because
`all(...)` over an empty tuple is true. Four policy arrays changed from `[]` to
`{}`, so projected physical bytes no longer matched the owner receipt hash.
Fix the typed Map/Sequence producer dispatch. Do not guess container kinds in
the verifier or relax physical-result-hash validation. Literal container tests
and the real production DEO fixture must both pass before integration.

## Registration transaction and resumed CE handoff

Dynamic real-guard/owner probes reproduced two existing failures: absent
task-local completion projection returned no artifact receipts but allowed
successful settlement; an owner receipt commit error occurred after guard
acceptance, leaving changed source with an old receipt. Projection and authored
obligations are mandatory. Register through the existing owner before accepting
the guard; failures restore candidates and settle failed without PM/CE restart.

Drained-owner rehydration must revalidate the existing CE handoff with
`require_strict=True` and preserve its task-local completion slice. Live readonly
replay demonstrated missing slice before, six TASK1 obligations after. Replace
both top-level and metadata carriers with that validated slice; a stale top-level
value must not shadow it. This repairs future registration, not the unproven
historical source provenance of existing render.ts bytes.

## Rejected alternative

Deleting live outputs after execution requires authoritative per-output creation
receipts and CAS ownership. Those are missing in the current path. It cannot be
implemented as a filename heuristic or extended Director scope.
