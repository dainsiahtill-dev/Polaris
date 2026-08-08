# Unattended Autonomous Development Foundation Roadmap

Status: active
Owner: Polaris platform maintainers
Scope: platform foundation only; target projects and Factory Bench remain external test inputs.

## Outcome

Polaris must be able to develop a project without a human watching a log while
remaining honest about what was delivered, what failed, why it failed, and
when the platform must stop instead of retrying.  The product claim is not
"steps resolved".  It is a repeated, auditable `COMPLETED_VERIFIED` outcome:

```text
Provider Request -> Tool Lifecycle -> Effect Receipt -> TaskBoundary
                 -> TaskRuntime -> Run Ledger -> QA -> Bench Report
```

The chain is only authoritative when every required axis is backed by its
owner's receipt/evidence.  A benchmark is an integration probe, never a
production fact source or a repair mechanism.

## Architecture

```text
                         +----------------------------------+
                         | External Supervisor               |
                         | policy / retry budget / stop rule |
                         +-----------------+----------------+
                                           |
                                           v
+-------------------+    +-----------------+----------------+    +-------------------------+
| Prevention        | -> | PM -> CE -> Director -> QA        | -> | Authoritative outcome   |
| CE handoff        |    | typed tools / leases / settle      |    | disk + receipts + ledger |
| final request pin |    +-----------------+----------------+    +------------+------------+
| write admission   |                      |                                  |
+-------------------+                      v                                  v
                                  +---------------------+         +--------------------------+
                                  | TaskBoundary/Runtime |         | Four pillars + N batches |
                                  | partial success safe |         | measure; never repair    |
                                  +---------------------+         +--------------------------+
```

### Non-negotiable invariants

1. Factory owns Factory-chain facts.  Consumers receive a hash-bound public
   observation, never caller-supplied Factory DTOs or reconstructed facts.
2. Disk delivery and verifier receipts remain authoritative after a timeout or
   settlement.  Runtime task state cannot erase already proven delivery unless
   a hard contract is violated.
3. `missing evidence` and `failed evidence` are distinct terminal semantics.
4. One residual has one primary `module_id`, one evidence package, one allowed
   next action, and one stop condition.  A retry may not silently switch
   modules.
5. A provider/model failure, a control-plane failure, and a product verifier
   failure must remain separately projected.
6. Deterministic repair is bounded: `Diagnostic -> Coverage -> Plan ->
   Compose -> Policy -> Execute -> Receipt -> Revalidate`.  It cannot invent
   stubs or claim success without a real verifier.
7. Supervisor policy lives outside the Run Ledger success condition.  Polaris
   exposes facts and typed operations; no external Agent report is a platform
   receipt.

## Delivery buckets and seals

| Bucket | Owner / state authority | Done only when |
| --- | --- | --- |
| GR1 | Factory public chain projection + Runtime Projection | direct owner facts are hash-bound, non-forgeable, and do not create a Cell cycle |
| GR1D | Cell graph | every `runtime.projection` to `factory.pipeline` reachability path is classified real/stale, then cut or deliberately re-owned without an allowlist |
| GR2 | `resident.autonomy` goal/attempt ledger | cross-process CAS, append-only receipt schema, lifecycle persistence, and no false `COMPLETED_VERIFIED` |
| GR3 | TaskRuntime / execution broker / TaskBoundary | lease, timeout, partial materialization, settle and receipt projection preserve completed work |
| GR4 | Verifier policy/execution + Run Ledger | modality is recorded as missing or failed correctly; commands have receipts; outcome axes reconcile |
| GR5 | CE/Director prevention | export handoff, final-request pin, and write admission reject incoherent materialization before M10 repair |
| GR6 | `orchestration.workflow_runtime` convergence + external Supervisor policy | durable single-leaf retries are owner-bound; only workflow-runtime may seal a structured model ceiling from ContextOS and content-addressed owner receipts |
| GR7 | Factory Bench | pre-bench gate passes, fresh isolated projects reach `COMPLETED_VERIFIED`, then N batches reveal no new general root cause |

Each bucket has a blueprint, verification card, focused tests, static graph
audit, and independent review.  A stable bucket is sealed after its N-batch
criterion; changes require explicit unseal rather than opportunistic edits.

## Unattended state machine

```text
READY -> ATTEMPT_ACTIVE -> SETTLING -> OUTCOME_PROJECTED
                                  |             |
                                  |             +-> DELIVERY_VERIFIED
                                  |             +-> CHAIN_INCOMPLETE
                                  |             +-> QA_PENDING
                                  |             +-> CONTROL_PLANE_FAIL
                                  v
                          ATTEMPT_BLOCKED / EXHAUSTED
```

- `DELIVERY_VERIFIED` needs owned files plus required verifier receipts.
- `CHAIN_INCOMPLETE` means delivery may exist but an owner projection has not
  converged; repair only the control plane, never the target project.
- `QA_PENDING` is not a QA failure.
- `CONTROL_PLANE_FAIL` cannot be rendered as a product/build failure.
- `ATTEMPT_BLOCKED` carries a typed primary residual; the Supervisor may open
  only its declared module, retry after bounded backoff, or stop at
  workflow-runtime's sealed `MODEL_CEILING_QUALIFIED` result. Bench substring
  matches and caller booleans are discovery candidates only.

## Mandatory final-request context audit

For every physical PM, CE, Director, or QA LLM call, persist and verify the
final provider request rather than a messages-only proxy:

- role identity, workspace, run and trace identities;
- messages, tools, `tool_choice`, response format and aliases;
- final token estimate, tool-schema tokens, window utilization and trimming;
- PM contract, CE blueprint, owned targets, prior verifier failure and
  workspace-quality coverage flags;
- durable `context_snapshot_ref` that can be read in the same workspace.

An absent required tool, cross-role system prompt, invalid snapshot reference,
or unexplained context trimming is a platform P0, not a model-quality claim.

## Supervisor policy

The Supervisor is deliberately outside the platform fact chain.  For each
project it performs:

1. Start one isolated instance and one attempt.
2. Read the authoritative outcome and attribution record.
3. If green, advance exactly one project in L1..L12 order.
4. If red, open only the primary residual's module under its repair budget.
5. Re-run focused module gates before consuming another physical attempt.
6. Stop and surface a typed escalation when the same residual changes class,
   repair coverage is exhausted, workflow-runtime returns a sealed model
   ceiling, or a hard control-plane contract is broken.

It never edits a generated target project, never turns a Bench gate into a
repair tool, and never interprets its own report as Polaris evidence.

## Bench admission and seal

`factory_bench` is schedulable only after GR1--GR6 relevant gates are green,
the worktree is stable, an isolated backend can boot, final-request context
audits are readable, and no stale/orphan lease belongs to the candidate
workspace.  Bench must use an isolated instance and must not claim main
ports `49977/5173`.

For each project, the four pillars are:

1. owned source artifacts exist;
2. dependency/environment preparation runs;
3. at least one build/test/lint verifier actually runs with a receipt;
4. a CLI, web, or API entrypoint actually executes.

Only a fresh isolated run that projects all facts into the authoritative
outcome is `COMPLETED_VERIFIED`.  Local tests, a partial chain, or a bench
report alone are insufficient.

## Explicit non-goals

- No PM-to-Director bypass, legacy deterministic repair restoration, or
  alternate state source.
- No benchmark-specific production fields, UI, or success semantics.
- No target-project code edits from the platform repair loop.
- No arbitrary retry expansion, timeout inflation, error swallowing, or
  fabricated verifier receipt.

## Current sequencing

Honest state (2026-08-05): **Formal L1 probe can run isolated** and emit
audits + residual packs.  **`COMPLETED_VERIFIED` is still not claimed** when
four pillars / chain fail.  Partial foundation buckets accelerate the path;
they do not invent green.

Accepted partial seals:

- **GR5B** — CE leaf construction authority closed.
- **GR3B-B1..B3** — receipt port, evidence owner, typed ledger projection.
- **GR3B-B4/B5 (partial)** — `run_managed_process` orchestrator with
  fail-before-spawn, present_failed, duplicate-launch refuse, terminate-once,
  projection-pending without re-spawn.  Full DEO claim/commit migrate deferred.
- **Formal-run admission** — `evaluate_formal_run_admission()` machine-checks
  critical surfaces (not COMPLETED_VERIFIED).
- **Structured model-ceiling authority** — KernelOne retains generic residual
  attribution only; `orchestration.workflow_runtime` re-reads the final
  provider request and directly queries attempt/failure/execution/control/
  environment/provider/repair owners. It emits a sealed terminal result only
  when every owner fact is exact and clear, and convergence owner-revalidates
  the result before accepting it. Missing owner query APIs fail closed as
  `CONTROL_PLANE_BLOCKED`; generic evidence receipts never substitute.

Remaining order (do not skip):

1. Wire B4 orchestrator into production TaskRuntime DEO claim/commit path
   (no silent generic append).
2. Close residual GR1C/GR1D/GR2 items that still forge projection/graph risk.
3. Harden GR4/M03/M09/M10 until L1 four pillars + chain green under isolated
   probe without forged success.
4. **GR6** finish durable receipt production/convergence and let the external
   Supervisor consume only owner-bound workflow outcomes and sealed ceiling
   results (never a KernelOne/Bench heuristic planner).
5. **GR7** N-batch + L1–L12 march only after repeated L1 `COMPLETED_VERIFIED`.

Still open elsewhere (do not collapse into GR3B): GR1C bootstrap composition,
GR1D SCC backlog, GR2 true lock-barrier audit residual.
