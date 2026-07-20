# Role Final Provider Request Audit Gate

> Status: B3.3 CLOSED — B3.4 specification CLEAR; physical-attempt parity/budget implementation active
> Bucket: one isolated platform-hardening bucket; Bench remains `not_schedulable`
> Encoding: UTF-8

## Objective

Every real Architect, PM, Chief Engineer, Director, or QA LLM call reached by a role-chain
verification must have one readable, workspace-bound ContextOS snapshot whose
full final provider request is qualified. A route/model observation, token
summary, event hash, or messages-only projection is not sufficient evidence.

The required fact chain for this bucket is:

`role policy -> frozen post-binding/post-compression provider attempt -> durable
24-hex context_snapshot_ref -> provider invocation -> complete current-run attempt
inventory -> context.engine public query -> per-attempt qualification -> Factory
hard gate`

This bucket does not run or schedule Bench. It adds the fail-closed verifier
that pre-Bench and future isolated Bench runs must consume.

## Current evidence and defect

- `roles.kernel` builds `llm.provider_request_snapshot.v1` and
  `llm.final_request_context_audit.v1` before provider invocation.
- `context.engine.public.query_final_provider_request_audit` resolves the
  active runtime root, matching Instance Registry roots, and the KernelOne
  system cache, then returns the stored messages/provider request.
- Factory `collect_llm_events` projects `context_snapshot_ref`, audit/evidence
  hashes, role identity, required refs/tools, and coverage state.
- Factory `build_llm_route_audit` only proves configured provider/model routes.
  It does not open or qualify every stored final request.
- `project_final_request_refs` is a projection only. A terminal call may retain
  `final_request_evidence_coverage_pass=False`, a missing/unreadable snapshot,
  missing role identity, or incomplete request fields without creating a hard
  Factory gate failure.

Therefore a successful role route can currently be counted without proving
the complete final provider request was qualified.

### B3.2 closure evidence (2026-07-19)

- One strict public ACK-to-proof resolution path, Invoker-owned semantic
  identity, canonical provider-visible role evidence block, complete
  post-injection recomputation and immutable semantic freeze are live for
  Architect, PM, Chief Engineer, Director and QA.
- Gateway/acquire/resolve await-time authority drift and cross-port ACK/proof
  splitting fail closed. Sync, structured and stream paths perform zero
  physical transport after Factory semantic freeze.
- Main-agent gates: `382` focused, `299` affected LLM, `569` metadata/catalog,
  and `1116` complete Factory Pipeline tests passed. Independent specification,
  correctness/concurrency and fixture rereviews returned no P0/P1/P2.
- `FPR=N/A`. B3.2 does not authorize Provider or Bench; B3.4-B3.5 must still
  qualify every physical attempt and readable final-request snapshot after the
  now-closed B3.3 propagation-only bucket.

### B3.3 zero-transport boundary (2026-07-19)

- B3.3 propagates one exact runtime-private sidecar through sync, structured,
  fallback and stream paths. It does not enable the transport-capable
  `FinalProviderAttemptGate`.
- The B3.2 public hard stop remains active. The propagation port also rejects
  every sync/async/blocking/stream dispatch before `send` or `open_stream` with
  `factory_role_semantic_request_frozen_physical_dispatch_not_enabled`.
- Factory Instructor direct-SDK structured calls stay disabled; stream binding
  must cover async-generator iteration and cleanup; role-binding or any
  semantic-changing retry must obtain a newly prepared freeze/port pair.
- `22` RED cases cover invariant pairing, all sync/structured/fallback seams,
  stream/reconnect/provider retry, ContextVar cleanup/isolation, recursive
  non-leakage, five-role identity, cache denial and zero outbound.
- `FPR=N/A` remains mandatory. Per-physical-attempt budget/parity is B3.4;
  complete token/window/coverage/alias/readable-snapshot qualification is
  B3.5. Provider and Bench remain forbidden.

### B3.3 closure evidence (2026-07-19)

- The exact runtime-private port is propagated through private sync,
  structured/manual, retry/fallback and stream/reconnect seams; ordinary calls
  retain their legacy executor signature and Factory public `call()` still
  stops before the private dispatch seam.
- Semantic-changing retries obtain a fresh cutoff/freeze/port and clear both
  snake_case and camelCase prior-attempt snapshot/degraded/receipt context.
  Metadata, request context and ContextOS audit use one fresh projection id.
- Factory cache and Instructor direct SDK bypasses are denied. Stream binding
  and owned-generator cleanup cover nested, concurrent, cancellation,
  `GeneratorExit` and cross-task `aclose` cases.
- Main-agent gate: `333 passed`; Ruff, format, seven-source mypy
  `--no-incremental`, compileall and diff checks passed. Independent spec and
  quality/security rereviews returned `CLEAR` with P0/P1/P2 all zero.
- `FPR=N/A`; zero provider transport occurred. Provider/Bench remain forbidden
  and Bench remains `not_schedulable`. Only B3.4 is active.

## Ownership and dependency decision

1. `context.engine` owns workspace-bound snapshot resolution and the typed
   per-snapshot qualification query. It must not depend on Factory or Bench.
2. KernelOne's sync/stream executors own the canonical semantic-request freeze
   after role binding, compression, tool normalization, response-format
   construction, and option clamping. They do not claim to see the final
   provider wire payload: provider adapters may translate it or retry after
   mutating fields such as `max_tokens`. The infrastructure provider/CLI/SDK
   transport boundary must therefore invoke an injected generic physical-
   dispatch port immediately before every observable outbound HTTP request,
   SDK method attempt, or subprocess launch. Governed SDK paths must disable
   hidden automatic retries so Polaris owns each retry around the hooked method
   call. A CLI/provider path whose internal request/retry cannot be disabled or
   exposed is not eligible for a verified governed role chain and fails closed
   by capability check. The v2 snapshot binds both the immutable canonical
   semantic request and that exact post-translation, post-mutation wire/CLI/SDK
   request. No earlier roles.kernel snapshot may claim final-request authority.
3. `roles.kernel` owns role-specific policy facts and implements the
   policy/lifecycle ports injected into KernelOne and the provider transport.
   Factory implements an injected causal-cutoff port for current-run evidence.
   KernelOne/infrastructure define only generic ports and fail closed for
   Architect/PM/CE/Director/QA physical snapshot/lifecycle evidence when they
   are absent;
   neither imports a Cell implementation. The Factory cutoff port is mandatory
   whenever a signed Factory verification binding is present. A non-Factory
   role call cannot satisfy Factory/Bench verification but is not falsely
   assigned Factory evidence semantics.
4. `factory.pipeline` consumes `context.engine.public` and normalized LLM
   events to build an internal verification gate. It must not read ContextOS
   files directly and must not invent a Bench-specific storage path.
5. `kernelone.events.final_request_evidence` owns the versioned machine role
   policy plus pure canonical JSON/hash/evidence-integrity helpers. It does not
   resolve workspace paths or decide a Factory verdict.
6. `factory_bench` only projects the platform audit into its internal report
   and folds it into the existing Factory gates. It does not create production
   Bench semantics or a new fact source.

## Producer invariant: no snapshot, no provider call

Every Architect, PM, Chief Engineer, Director, and QA physical provider attempt is strict
for snapshot and lifecycle evidence at the transaction/transport boundary.
Callers cannot opt out. A Factory verification call additionally carries an
unforgeable Factory run/lease binding and must obtain its causal cutoff; removal
or downgrade of that binding fails closed rather than turning it into an
ordinary role call. Add
`RoleFinalRequestPolicyV1` and `RoleFinalRequestPolicyFactsV1` in
`kernelone.events.final_request_evidence`. Its frozen version-1 table is:

- Architect requires admission-time `pm_raw_intent` present.
- PM requires `pm_raw_intent`.
- Chief Engineer requires `pm_contract`, `target_files`, and a
  `workspace_quality` slot.
- Director requires `pm_contract`, `ce_blueprint`, `target_files`,
  `failure_feedback`, and `workspace_quality` slots.
- QA requires `pm_contract`, `ce_blueprint`, `target_files`,
  `verifier_receipts`, `failure_feedback`, and `workspace_quality` slots.

Version 1 has no time-varying conditional required-ref set. Dynamic evidence
slots are always provider-visible. When no item exists at request time, the
slot carries canonical `state="absent_at_request_time"`, an empty item list,
and the bound execution-envelope/TaskBoundary authority hash; omission is not
equivalent to absence. QA's `verifier_receipts` and `workspace_quality` slots
must be `present`; the other dynamic slots may be explicitly absent. This
static role table prevents later QA failures or quality receipts from
retroactively changing an earlier attempt's obligations.

This chain-anchor table and Factory causal cutoff apply only when the call
carries the signed Factory verification binding. Ordinary `/v2/role/*/chat` or
other non-Factory role sessions still require the universal full physical
snapshot, canonical role identity, wire-semantic equivalence, and lifecycle
pair, but use `verification_scope="role_session"` with a session/run-scoped
segmented ledger and no fabricated Architect/PM/CE/Director/QA chain anchors.
Such calls
cannot satisfy a Factory reached-role obligation, cannot be imported into its
inventory, and cannot be upgraded by merely copying a Factory run id. This
preserves interactive role behavior without weakening verified chains.

Producer slot candidates come from the roles.kernel execution
envelope/TaskBoundary bindings, but producer watermarks are never trusted to
prove absence. Before the semantic request is frozen, the executor must call an
injected `FactoryRoleEvidenceCutoffPort` with a new `request_freeze_id`, current
run/role, and candidate anchor refs. The Factory implementation acquires the
run lifecycle fence, queries the current canonical sources itself, and durably
appends one `FactoryRoleEvidenceCutoffV1` fact to the run's strict role-evidence
authority ledger. That fact binds `request_freeze_id`, role, Factory fencing
token, stage-claim nonce, turn/logical `call_id`, canonical semantic candidate
hash, allowed physical-attempt budget, the complete source-head vector, exact
expected anchor hashes/states, and canonical source fact ids/sequences/hashes.
It is ACKed before the executor releases the fence or permits provider
translation.

`FactoryRunService.execute_stage` constructs the run-bound cutoff port while it
holds the per-run lock during stage claim, injects it into the stage context,
and releases the lock before `_execute_stage_logic` as today. Each cutoff call
reacquires that lock briefly, verifies the same active lease/stage claim and
non-draining state, captures canonical source heads, appends the cutoff ACK,
then releases it before any LLM/provider await. This avoids reentrant lock
deadlock while preventing a Factory call from dropping or forging its run
binding. Heartbeats and terminal drain use the same lock/fence.

The cutoff facts use a second run-scoped native segmented logical FactStream,
`factory.role_evidence_authority.<run-hash>`, with the same strict global
sequence/hash, namespace guard, and `pinned_audit_no_delete` retention as the
provider-attempt ledger. The physical lifecycle start binds its cutoff fact id,
sequence, content hash, and `request_freeze_id`; Factory qualification requires
both ledgers to agree.

PM intent is reconstructed from persisted Factory admission; PM contract, CE
blueprint, and target files from persisted stage facts and referenced
artifacts; failure feedback, workspace quality, and QA verifier receipts from
their canonical stage/receipt facts. The cutoff is a causal index, not a second
truth source: Factory verifies every referenced canonical fact/hash. Source
heads are read by the Factory authority, never supplied by the role producer;
a fact with sequence at or below its captured source head belongs to the cut,
while a later sequence is explicitly post-cut. Cross-source references are
validated not to point beyond the head vector. Therefore
`absent_at_request_time` means absent at this Factory-issued semantic-request
vector cut—not at an untrusted producer timestamp or a later network retry. A
stale-but-valid producer watermark cannot change the Factory-issued head vector
and fails when its anchors disagree with the cutoff.

The envelope/TaskBoundary authority hash remains an integrity binding between
provider-visible anchors, semantic request, physical snapshots, and lifecycle
facts; it is not independent semantic proof. Every physical retry carries the
same `request_freeze_id`/cutoff fact but a new `provider_request_id`. Factory
later reloads the cutoff and canonical source facts and independently recomputes
the static role policy, expected anchors, and allowed states. `context.engine`
does not import Factory, TaskRuntime, or Run Ledger; it only recomputes common
policy/hash/snapshot integrity from typed expected facts supplied by the
Factory query. This keeps dependencies acyclic while trusting neither snapshot
`required_refs` nor producer-carried watermarks as an oracle.

The one-to-many relationship is explicit: one cutoff authorizes exactly one
immutable semantic request and its bounded physical retries. Reuse with another
call/turn/role/semantic hash, exceeding the recorded attempt budget, or use
after stage-claim/lease invalidation fails closed. Each physical start binds
its unique `provider_request_id` to that cutoff sequence/hash; this is the
causal bridge requested by Factory audit without pretending retries are new
semantic contexts.

### A009B execution buckets

A009B is implemented in three strictly ordered buckets. A bucket may not
pre-enable a later bucket, and every intermediate state remains fail-closed.

1. **A009B1 — fenced cutoff authority ledger.** Add the typed async
   `FactoryRoleEvidenceCutoffPort`, immutable cutoff body/locator/ACK contracts,
   and a Factory-owned implementation backed only by
   `factory.role_evidence_authority.<run-hash>`. The port is constructed from
   the live Factory service's workspace lease, fencing token, stage, and stage
   claim nonce. Every call reacquires the run lock, reloads the run and current
   lease, requires the same ACTIVE owner and exact stage claim, asks an injected
   Factory-owned source resolver for canonical facts/heads, canonicalizes the
   bounded cutoff body, appends ordered 1024-byte fsync fragments, and appends
   one small idempotent commit manifest only after the complete fragment vector
   is durable. The commit is the sole authoritative cutoff; strict re-read
   reconstructs and validates the canonical UTF-8 body/hash/vector before
   deriving the locator/ACK from the commit event id/global sequence/hash.
   Partial fragments never authorize a request. The request may carry
   role/run/turn/call/freeze identity, the pre-anchor semantic candidate hash,
   attempt budget, and execution-envelope authority hash; it may not carry
   authoritative source heads, policy facts, evidence states, or anchors.
   Exact replay returns the same fact. Same freeze identity with a different
   candidate, role, call, budget, claim, or source result fails closed. This
   bucket does not bind the port into role execution and does not enable
   provider-visible anchors.
2. **A009B2 — canonical role source reconstruction.** Its initial four-role
   closure was subsequently extended with the distinct Architect policy and
   admission-time `pm_raw_intent`; all current gates are five-role. This bucket is
   ordered into two fail-closed sub-buckets after independent review found that
   artifact paths and mutable `run.json` fields cannot prove historical source
   identity:

   1. **A009B2a — producer provenance freeze.** Before any resolver is enabled,
      close **A009B2a1** (strict event chain plus admission genesis) completely,
      then **A009B2a2** (PM/CE artifact bindings plus event-first snapshot
      ordering); the two may not be enabled together without the A009B2a1 proof.
      A009B2a1 upgrades new-run authoritative Factory events to
      `factory.event_chain.v1`. Every stored record carries exact fields
      `chain_schema_version="factory.event_chain.v1"`, positive-integer
      `chain_sequence`, lower-case 64-hex `chain_previous_hash` (`64*"0"` only
      at genesis), and lower-case 64-hex `chain_event_hash`. The event hash is
      `canonical_role_final_request_hash({"domain":"polaris.factory.event_chain.v1","event":record_without_chain_event_hash})`.
      Append validates the complete bounded prefix (maximum 4096 records and
      8 MiB), performs sequence CAS while holding a stable OS lock file across
      strict read, compare, append, and fsync (the existing process-local
      `_acquire_file_lock` is not authority), rejects legacy or
      malformed chain drift, and fsyncs both the file and its parent on create.
      The complete authority-provision, stream-enrollment, anchor-lock, and
      stream-lock acquisition path shares one finite monotonic five-second
      deadline; timeout, cancellation, or a non-finite budget fails closed and
      cannot publish or create a mutable run snapshot.
      Existing unchained runs may remain readable for compatibility but are
      permanently ineligible for Factory verification. Append one
      `factory_run_admitted` fact at sequence `1` during `create_run`. Its
      detached payload is exactly
      `{factory_run_id, created_at, name, description}` plus a canonical SHA-256;
      later mutable `FactoryRun.metadata` is excluded. Before a successful
      `pm_planning` or `chief_engineer_review` `stage_completed` fact is
      appended, compute and embed one exact strict union DTO
      `factory.stage_artifact_bindings.v1` in that same fact. The outer object
      has only `schema_version: str`, `factory_run_id: str`,
      `stage: Literal["pm_planning","chief_engineer_review"]`,
      `items: list[object]`, and lower-case 64-hex
      `binding_vector_sha256: str`. PM `items` is exactly one
      `kind="pm_contract"` row with fields `logical_source_path` (exactly
      `tasks/plan.json`), `immutable_snapshot_ref`,
      `document_schema_version` (exactly `pm.plan_artifact.v1`), positive exact
      integers `utf8_byte_count` and `task_count`, and lower-case 64-hex
      `raw_sha256`, `canonical_json_sha256`, `task_id_vector_sha256`, and
      `target_files_projection_sha256`. CE `items` is exactly one
      `kind="pm_stage_event"` row, then one `kind="ce_review_manifest"` row,
      then one `kind="ce_blueprint"` row for every manifest entry. The PM-event
      row contains exact `event_id`, positive exact `chain_sequence`, lower-case
      64-hex `chain_event_hash`, `pm_immutable_snapshot_ref: str`, and lower-case
      64-hex `pm_raw_sha256`, `pm_canonical_json_sha256`,
      `pm_task_id_vector_sha256`, and
      `pm_target_files_projection_sha256`, copied from the referenced successful
      PM binding. The review row
      contains exact logical path
      `runtime/state/blueprints/<factory_run_id>.review.json`, immutable ref,
      document schema `factory.chief_engineer_review.v2`, positive exact
      `utf8_byte_count: int`, `total_tasks: int`, and
      `generated_blueprints: int`, plus lower-case 64-hex `raw_sha256` and
      `canonical_json_sha256`. Each blueprint row
      contains exact non-negative `ordinal`, exact logical source path and
      immutable ref, schema `chief_engineer.blueprint.v1`, positive exact byte
      count, raw/canonical hashes, exact non-empty `blueprint_id`, `task_id`, and
      `factory_run_id`, `hash_scheme="chief_engineer.blueprint_hash.v1"`, and
      lower-case 64-hex `embedded_blueprint_hash`,
      `recomputed_blueprint_hash`, `embedded_pm_contract_hash`,
      `recomputed_pm_contract_hash`, `embedded_pm_task_canonical_sha256`,
      `expected_pm_task_canonical_sha256`,
      `embedded_pm_task_projection_sha256`, and
      `target_files_projection_sha256`. Each row contains exactly `kind` plus
      the keys enumerated for that kind; missing and unknown keys are rejected.
      Counts/ordinals use `type(value) is int` (never bool/float), hashes use
      lower-case 64-hex strings, and refs/identities use exact bounded strings.
      No additional union kind is accepted.

      Hash domains are frozen. `raw_sha256=sha256(exact_utf8_bytes)`. Artifact
      canonical hashes are `canonical_role_final_request_hash` over exactly
      `{domain, document_schema_version, document}`; `domain` is respectively
      `polaris.factory.stage_artifact.pm_contract.v1`,
      `polaris.factory.stage_artifact.ce_review_manifest.v1`, or
      `polaris.factory.stage_artifact.ce_blueprint.v1`, and `document` is the
      complete strict JSON object.
      The PM task-id vector is sorted by exact Unicode code point and hashed as
      `canonical_role_final_request_hash({"domain":"polaris.factory.pm_task_id_vector.v1","factory_run_id":run_id,"task_ids":ids})`.
      A task projection is exactly `{task_id, target_files}` with target files
      normalized as strict POSIX logical paths and sorted by exact Unicode code
      point; the document target projection sorts task rows by exact task id and
      hashes
      `{"domain":"polaris.factory.pm_target_files_projection.v1","factory_run_id":run_id,"tasks":rows}`.
      The per-task projection hashes
      `{"domain":"polaris.factory.pm_task_projection.v1","factory_run_id":run_id,"task":row}`.
      The embedded and expected PM-task canonical hashes use
      `{"domain":"polaris.factory.ce_pm_task.v1","factory_run_id":run_id,"task":strict_task_object}`.
      Blueprint self-hash validation uses the producer's exact version-1
      recursive algorithm: remove every mapping key named `blueprint_hash`,
      `capability_token`, or `job_token` at every depth, then apply the CE
      producer `stable_hash`; Factory may consume this only through the pure
      typed CE public provenance query and may not reimplement it. The full
      binding vector hash is
      `canonical_role_final_request_hash({"domain":"polaris.factory.stage_artifact_binding_vector.v1","schema_version":"factory.stage_artifact_bindings.v1","factory_run_id":run_id,"stage":stage,"items":items})`;
      its self hash is outside `items` and therefore excluded. CE order is the
      PM-event row, review row, then review-manifest blueprint array order
      (`ordinal=0..n-1`), never glob/latest/timestamp/alias order.

      The CE producer contract is also part of A009B2a2. PM stage-time
      validation normalization must be persisted before the PM binding is
      frozen. `FactoryStageExecutor._task_blueprint_context` supplies that exact
      persisted task as `pm_task_contract`; CE `_task_payload_from_context`
      must consume that named slot without alias fallback, so persisted
      blueprint `pm_task` equals the exact expected PM task and its embedded
      `pm_contract_hash` independently recomputes. The pure typed
      `QueryBlueprintProvenanceV1 -> TaskBlueprintProvenanceSnapshotV1` public
      query performs no file I/O/latest lookup/mutation and fail-closes exact
      schema, path, run/task/blueprint identity, producer-v1 hash, PM-task,
      PM-contract-hash, and target-files validation with typed error codes.
      Missing, malformed, unsafe, cross-run, duplicate, or changed artifacts
      fail the successful stage transition before the event gains authority.
      Paths alone, stage metadata, mirrors, latest aliases, fallback roots, and
      sidecars never count as bindings. PM/review/each blueprint is bounded to
      4 MiB, tasks and blueprints to 512, aggregate blueprint bytes to 64 MiB,
      target paths to 512 per task and 8192 total, logical paths to 1024 UTF-8
      bytes, and identities to 256 UTF-8 bytes. Strict parsing rejects invalid
      UTF-8, duplicate JSON keys, NaN/Infinity, non-object roots, absolute or
      non-canonical paths, backslashes, dot/traversal/NUL components, symlinks,
      multiple hard links, non-regular files, descriptor/path identity drift,
      cross-run identities, duplicate/missing/extra manifest rows, alias task
      ids, invalid embedded hashes, or an embedded/recomputed hash mismatch.
      Factory also preflights the remaining 8 MiB event-chain capacity.
      Because current PM/CE producers do not share the Factory event lock, each
      strict descriptor snapshot is first copied byte-for-byte into a
      Factory-owned content-addressed immutable snapshot. Its exact logical ref
      is
      `runtime/<factory_run_id>/artifacts/stage-bindings/sha256/<first-two-hex>/<raw_sha256>.json`
      below the Factory Store runtime root; the content key is the exact raw
      bytes SHA-256, not a canonical JSON hash. FactoryStore enrolls and
      acquires this exact ref under the same stable lock authority used for the
      event stream, creates it O_EXCL/no-follow, writes exact bytes once, fsyncs
      the file and every created parent, re-reads under the retained lease, and
      requires byte-for-byte equality, size, and raw hash before returning. An
      existing same-key object is reused only after exact byte recheck; a
      mismatch is `factory_artifact_snapshot_hash_collision` and is never
      overwritten, appended, or repaired. Partial or orphan objects are
      permanently non-authoritative and may only be garbage-collected by a
      separate policy after proving no event reference. Source descriptors are
      closed before snapshot locks; snapshot locks are acquired and released
      one at a time in binding-vector order; all snapshot locks are released
      before the event-stream lock, so no inverse/nested lock order exists.
      Immediately before authoritative append, Factory re-opens every immutable
      ref under its lock and rechecks exact bytes/hash. The event binds both
      source path/hash and immutable snapshot ref/hash; A009B2b later requires
      the current source to recompute to the same binding. Descriptor-only
      `fstat` stability is not sufficient evidence against atomic replacement.
      Transaction order is authoritative-event first, mutable snapshot second:
      do not pre-create the run or `events` directory; the locked authoritative
      append creates and fsyncs `events -> run -> Factory base`, then creates
      `artifacts`/`checkpoints`, and only then saves
      `run.json`; for stages, read one stable no-follow regular-file snapshot,
      validate/hash artifacts, append+fsync the bound stage fact, then persist
      `run.json` and checkpoint, append one
      `factory_stage_persistence_committed` marker, and only then publish the
      committed success event and allow downstream scheduling. Candidate
      run/result/event values are detached deep copies; append failure cannot
      mutate the caller or persisted snapshot. The pending `stage_completed`
      payload carries exact object `persistence_intent` with keys
      `schema_version="factory.stage_persistence_intent.v1"`,
      `factory_run_id: str`, `stage: str`, lower-case 64-hex
      `stage_result_canonical_sha256`, exact `checkpoint_ref: str`, and its own
      lower-case 64-hex `persistence_intent_sha256`. The run ref is exactly
      `runtime/<factory_run_id>/run.json`; the
      checkpoint ref is exactly
      `runtime/<factory_run_id>/checkpoints/<run-status>_<updated-at-with-colons-replaced-by-underscores>.json`.
      The stage-result hash is exactly
      `canonical_role_final_request_hash({"domain":domain,"document":strict_json_object})`,
      where `domain="polaris.factory.stage_result.v1"`. The
      persistence-intent hash wraps exactly
      `{schema_version,factory_run_id,stage,stage_result_canonical_sha256,checkpoint_ref}`
      in domain `polaris.factory.stage_persistence_intent.v1`; its self hash is
      excluded. It intentionally excludes run/checkpoint hashes because those
      objects are not durably available until after the event-first append;
      including them would create a recursive event/run pointer hash. After the
      stage event is committed, Factory constructs the run/checkpoint objects
      with the committed stage-event identity, saves and strictly re-reads them,
      then computes `run_snapshot_canonical_sha256` and
      `checkpoint_canonical_sha256` as
      `canonical_role_final_request_hash({"domain":domain,"document":strict_json_object})`
      using domains `polaris.factory.run_snapshot.v1` and
      `polaris.factory.run_checkpoint.v1`. These are canonical JSON hashes, not
      raw-byte hashes. The commit marker has exact keys
      `type="factory_stage_persistence_committed"`,
      `schema_version="factory.stage_persistence_committed.v1"`,
      `factory_run_id`, `stage`, `stage_completed_event_id`, positive exact
      `stage_completed_chain_sequence`, lower-case 64-hex
      `stage_completed_chain_event_hash`, `persistence_intent_sha256`,
      `run_snapshot_canonical_sha256`, `checkpoint_ref`, and
      `checkpoint_canonical_sha256`.

      A historical stage commit remains valid from the exact stage event,
      matching commit marker, and immutable checkpoint; later legal mutations
      to current `run.json` never need to equal an old stage snapshot. At the
      commit cut—before downstream scheduling—the just-written run and
      checkpoint must be strictly re-read and equal the hashes recorded by the
      later commit marker. The committed run metadata
      then carries one exact monotonic `last_factory_stage_commit` object with
      schema `factory.last_stage_commit.v1`, the stage event id/sequence/hash,
      persistence-intent hash, checkpoint ref, and stage. It deliberately
      does not contain the later commit-marker identity: entry guards resolve
      the unique matching later marker from the strict chain. Later
      start/pause/heartbeat metadata mutations must preserve that object
      byte-for-byte; the next valid stage commit replaces it monotonically.
      Entry guards compare current run only to this latest commit pointer and
      independently validate the referenced immutable checkpoint/chain facts.
      Any stage event after the last valid commit marker
      that lacks its own matching marker is a pending quarantine predicate. The
      pending event alone is never a schedulable success. An
      artifact-binding failure becomes explicit failed result
      `factory_stage_artifact_binding_failed` and never persists or publishes a
      success. A post-event snapshot/checkpoint failure aborts and quarantines
      the run by appending `factory_run_quarantined` with schema
      `factory.run_quarantined.v1`, exact run/stage, failed-step enum
      (`save_run`, `checkpoint`, `commit_marker`, or
      `cancelled_before_commit_ack`), committed stage event id/sequence/hash,
      persistence-intent hash, exact error type bounded to 256 UTF-8 bytes,
      redacted error message bounded to 2048 UTF-8 bytes, and timestamp; the
      success event is never published and the execution claim is not released.
      Event append and post-event save/checkpoint/marker operations are run in
      cancellation-resistant shielded tasks and awaited to terminal settlement.
      Cancellation before the commit-marker ACK appends quarantine and then
      re-raises; cancellation after the ACK cannot revoke the already complete
      transaction and publication is replayable. If quarantine append itself
      fails or is cancelled, the durable unmatched pending stage event remains
      the authoritative quarantine predicate, so no mutable metadata is needed
      to fail closed. Strict entry guards reject either an explicit quarantine
      fact or any pending/missing/mismatched persistence commit at
      `execute_stage`, `start_run`, `recover_run`, `retry_run_from_stage`,
      `execute_resume`, `complete_run`, `settle_terminal_run`,
      `recover_stale_workspace_owner`, and every automatic scheduling caller.
      `cancel_run` and terminal cleanup may only preserve isolation; they may
      not clear quarantine, release the execution claim, or authorize another
      stage. Publish
      failure is a non-authoritative fanout failure and is replayable after the
      durable event/snapshot/checkpoint commit. A009B2b requires strict
      event/current-state/snapshot agreement. Half records are never silently
      repaired into authority. Strict run/checkpoint re-read accepts at most
      4 MiB each and rejects invalid UTF-8, duplicate JSON keys, NaN/Infinity,
      non-object roots, symlink/hard-link/non-regular objects, or identity drift.
   2. **A009B2b — resolver and absence proof.** Only after A009B2a is verified,
      implement the Factory-owned resolver for persisted admission intent, PM
      contract, CE blueprint, target files, failed-gate feedback,
      workspace-quality evidence, and verifier receipts. It reads canonical
      stores/artifacts and current heads itself, validates current-run
      identities and the A009B2a stage-time hashes, materializes the frozen
      static policy in exact slot order, and produces an explicit
      `absent_at_request_time` proof at the captured vector cut. Producer
      metadata, sidecars, candidate digests, and `request_facts` conflict
      telemetry are never source authority.

      A009B2b closes in three serial sub-buckets. **A009B2b-0** first makes the
      dynamic canonical source eligible: `execution.control_plane` writes use
      strict per-record integrity plus `fsync`, and a strict public query
      returns the exact stored `EventEnvelope` without compatibility
      `run_id`/`task_id` injection. A pre-existing non-strict stream is not
      silently upgraded or trusted; it fails closed. **A009B2b-1** then wires
      the seven-kind resolver. The three dynamic views share exactly one
      unfiltered `QueryFactEventsV1(stream="execution.control_plane",
      offset=0, limit=4096, event_type=None, run_id=None, task_id=None,
      strict_integrity=True)` result; `total == len(events)`,
      `next_offset == 0`, consecutive global sequence, the existing 4096-record
      and 8-MiB bounds, and every stored digest are mandatory. Ordinary
      FactStream proves sequence continuity plus per-record integrity, not a
      rolling previous-hash chain. **A009B2b-0R** then makes the authoritative
      append and its rebuildable Run Ledger projection replay-safe. The stable
      logical identity is bound to the exact run id plus the prepared nested
      event id/content id; callers that intend two semantically equal but
      distinct events must provide distinct event ids. If the canonical fact
      append succeeds but projection persistence or the returned ACK fails, a
      retry must return the same physical FactStream event id/sequence/digest
      and the same projection append identity without adding a second canonical
      fact or a second projection row. Concurrent identical retries converge to
      that same result; the same logical identity with different semantic
      content fails closed. Replay detection and projection deduplication occur
      under their owning stream/file locks, never through an unlocked
      read-then-append check. **A009B2b-1** may construct the resolver only
      after both source eligibility and replay safety are verified. No source
      cut or absence proof is enabled earlier.

      **A009B2b-0R closure (2026-07-19):** the strict public append now uses a
      stable Fact idempotency key plus projection append identity, reserves
      bounded capacity before Fact mutation, and holds the projection flock
      across the Fact append and projection commit. Binary projection recovery
      validates every complete canonical row, preserves incomplete UTF-8 tails,
      and before any repair proves a bounded run-wide strict Fact sequence whose
      ordered authority rows exactly equal the complete projection prefix; the
      sole unprojected final authority must be the current request and own the
      strict tail prefix. Write/flush/fsync ambiguity, orphan or disguised Facts,
      duplicate identities, reordered authorities, stalled/cyclic pagination and
      capacity overflow all fail closed without new Fact or projection mutation.
      Fresh main-agent acceptance passed 401 RunLedger/FactStream tests, 394
      TaskRuntime tests and 3 exact Factory legacy tests; Ruff, format, mypy,
      compileall and scoped diff checks passed. Independent final specification
      review returned CLEAR and code-quality review returned APPROVED. This
      bucket has no physical provider call, so FPR=N/A. A009B2b-1 is now the
      current bucket; live binding and per-attempt full provider-request audit
      remain disabled.

   The version-1 source map is frozen before implementation:

   - `pm_raw_intent` is the immutable `factory_run_admitted` Factory event,
     cross-checked against current `FactoryRun.id/config/created_at`, never
     reconstructed from mutable `run.json` or `run.metadata` alone. Its
     canonical hash covers the detached name and description that formed the PM
     directive plus the Factory run identity and creation time.
   - `pm_contract` is the strict `tasks/plan.json` document referenced by the
     latest successful current-run `pm_planning` stage fact. The Factory event
     prefix supplies fact id/sequence/hash and the A009B2a binding supplies the
     stage-time byte/canonical hashes; the current referenced UTF-8 JSON must
     independently recompute to those exact hashes.
   - `target_files` is a deterministic task-id/target-file projection derived
     from that same validated PM contract. It has its own canonical ref/hash and
     source-view head; producer-carried target lists do not count.
   - `ce_blueprint` is the current-run
     `runtime/state/blueprints/<factory-run-id>.review.json` manifest referenced
     by the latest successful `chief_engineer_review` stage fact. The resolver
     validates the manifest run id and every referenced immutable blueprint id,
     path, run id, embedded hash, PM-contract binding, target-file binding, and
     current byte/canonical hash against the stage-time A009B2a binding before
     anchoring the aggregate manifest.

   Static item identities are frozen. Let
   `run_digest = sha256(factory_run_id UTF-8)`. `pm_raw_intent` uses canonical
   ref
   `factory.role_evidence.item.<run_digest>.pm_raw_intent.<sha256(factory_event_id UTF-8)>.v1`
   and the admission canonical hash. `pm_contract` uses the immutable PM
   snapshot ref and the PM canonical-JSON hash. `target_files` uses
   `factory.role_evidence.item.<run_digest>.target_files.<pm_stage_event_hash>.v1`
   and the deterministic target-files projection hash. `ce_blueprint` uses
   `factory.role_evidence.item.<run_digest>.ce_blueprint.<binding_vector_sha256>.v1`
   and that binding-vector hash. A CE cut contains one aggregate item, not one
   item per blueprint. All four static items bind the corresponding captured
   Factory event id/sequence/event hash and share the same captured strict
   Factory-chain head while retaining distinct source-view refs.

   CE binding is one-to-one: the review manifest and ordered binding vector
   contain exactly the same non-empty blueprint id/task id/logical path triples,
   with no missing, extra, or duplicate identity. Every task id exists in the
   exact latest successful PM contract; target-files hashes use one frozen
   deterministic projection from those PM bytes. Embedded `blueprint_hash` is
   non-empty lower-case 64-hex and is recomputed only through the CE public
   provenance query using producer-v1 recursive exclusion of mapping keys
   `blueprint_hash`, `capability_token`, and `job_token`. Raw hash is
   `sha256(exact UTF-8 bytes)`; canonical/aggregate hashes use
   `canonical_role_final_request_hash` over strict JSON (duplicate keys, NaN,
   and non-JSON scalars rejected) with explicit schema/domain fields. Artifact
   paths are exact normalized POSIX logical paths: PM is exactly
   `tasks/plan.json`, review is exactly
   `runtime/state/blueprints/<factory_run_id>.review.json`, and blueprint files
   remain below the declared run-scoped blueprint root. Absolute paths,
   backslashes, dot segments, NUL, aliases, symlinks, non-regular files, KFS
   escape, or fstat drift fail closed.
   - `failure_feedback`, `workspace_quality`, and `verifier_receipts` are
     filtered views over strict `execution.control_plane` FactStream records,
     not the rebuildable NDJSON ledger or a QA prompt sidecar. Their head is the
     last record of one unfiltered, offset-zero, strict query captured under the
     FactStream stream lock. The resolver requires the returned `total` to equal
     the complete event vector, `next_offset=0`, continuous global sequences,
     and a configured bounded maximum; overflow or truncation fails closed. It
     never composes a separate head query with a later filtered read, avoiding a
     head/read TOCTOU window. Items keep the original global event
     id/sequence/content hash. Failed gate facts form feedback;
     `stage=workspace_validation` gate facts form workspace quality; gate facts
     carrying physical requirements/modalities/commands/receipts form verifier
     receipts. Facts are accepted only when their nested run/job token binds
     the current Factory run.

   Dynamic-view predicates are exact. An accepted record must be an
   `EventEnvelope` with `event_type="gate_evaluated"`, payload schema
   `execution.control_plane.fact.v1`, nested `event.event_type="gate_evaluated"`,
   `payload.run_id == event.job_token.run_id`,
   `event.job_token.factory_run_id == current_factory_run_id`, and
   `event.stage == event.job_token.stage`. `failure_feedback` selects only
   `gate.ok is False`;
   `workspace_quality` selects only `event.stage == "workspace_validation"`;
   `verifier_receipts` selects only records whose `physical_evidence` contains
   a non-empty `requirements`, `entrypoint`, `commands`, `modalities`, a
   positive exact-integer `command_count`, or at least one non-empty canonical
   receipt field from `effect_receipt`, `effect_receipts`, `tool_receipts`,
   `write_receipts`, `command_receipts`, `batch_receipt`, `batch_receipts`,
   `repair_receipts`, `director_repair_receipts`,
   `repair_kernel_receipts`, `deterministic_repair_receipts`,
   `environment_prep_receipts`, or `director_environment_prep_receipts`. Each
   item's source hash is
   `EventEnvelope.integrity_digest_for_record(record)`. The seven source-view
   refs use the exact grammar
   `factory.role_evidence.source.<sha256(factory_run_id)>.<ref_kind>.v1`, where
   `ref_kind` is one of the seven frozen policy slot names; no two slots may
   share a ref even when they share one physical source head.

   Each selected dynamic item uses canonical ref
   `<source-view-ref>.fact.<global-sequence>.<event-id>` and binds the exact
   outer EventEnvelope id/sequence/digest. Its canonical hash is the validated
   nested control-plane event content id, accepted only after recomputing the
   canonical nested event identity and requiring exact equality. All three
   dynamic slots share the same physical head: the final exact EventEnvelope
   id/sequence/digest from that one captured query, or empty id, sequence zero,
   and 64 zeroes for a truly empty physical stream. A non-empty physical stream
   with no selected records still produces `absent_at_request_time` against
   that real final head. It never substitutes a timestamp, filtered head,
   producer sidecar, or rebuildable ledger projection.

   Factory run events are read as a strict bounded UTF-8 prefix and their stored
   sequence/previous-hash/event-hash chain is revalidated, so insertion,
   deletion, reordering, malformed JSON, duplicate ids, cross-run records, or a
   referenced-artifact hash change fails closed. Empty dynamic views use a real captured source head plus an empty
   item vector; they are not inferred from producer timestamps. The same
   physical source may back multiple policy views, but every slot has a unique
   canonical source-view ref so the complete vector remains unambiguous.
   Resolution keys only from the signed Factory authority and captured
   `FactoryRun`; `authority.factory_run_id` must equal `FactoryRun.id`.
   `request.run_id` is the distinct controlled child-role/fanout run identity:
   it is hashed into the cutoff body but never selects Factory sources and is
   bound to the controlled child only in A009B3. Ordinary role/session calls
   without the Factory port cannot construct this resolver cut.

   **A009B2b-1 closure (2026-07-19):** Factory now owns one canonical,
   read-only seven-source resolver. It strictly revalidates the complete
   Factory event chain, immutable PM/CE artifact snapshots and provenance,
   derives the PM target-file view from the same PM bytes, and captures all
   three dynamic views from exactly one unfiltered strict
   `execution.control_plane` query with the frozen sequence, digest, record and
   byte bounds. The four static views share the captured Factory-chain head,
   the three dynamic views share the captured physical FactStream head, and all
   seven retain distinct canonical source-view refs. `request.run_id` never
   selects Factory authority. `RequestPreparer` still raises
   `factory_role_evidence_cutoff_not_enabled`, so no provider request is
   authorized by this closure. Fresh main-agent acceptance passed 178 resolver,
   provenance, authority and RunService tests plus the exact cutoff-disabled
   Kernel regression (179 total); Ruff, format, mypy, compileall and scoped
   diff checks passed. Independent specification review returned CLEAR after
   209 tests, and independent code-quality review returned APPROVED after 178
   tests. FPR=N/A because no physical Provider call occurs.

   **A009B3-A0 integration prerequisite (closed, 2026-07-19):** production HTTP
   lifespan, Factory settlement create/start, Roles CLI modes, and Director
   CLIs were audited as explicit FactStream bootstrap composition roots. Direct
   test seams now use the public maintenance API against their exact workspace;
   ordinary append/query/head still cannot provision authority and continue to
   fail closed before bootstrap. Failed stage settlement retains the exact
   ACTIVE stage/attempt/nonce claim, canonical TaskRuntime settlement plus
   explicit reconciliation clears it, and only terminal settlement under a
   closed Run Ledger barrier releases the workspace. Independent review also
   found and closed two test-hygiene defects: executor construction no longer
   performs maintenance against `Path(".")`, and HTTP client fixtures restore
   all 20 process-environment keys changed by active Settings plus the storage
   root cache. Fresh main-agent verification passed 5 FactStream/NATS negative
   and lifespan tests, 475 affected integration tests, and the complete Factory
   Pipeline suite at `1056 passed in 559.05s`; scoped Ruff, format and diff
   checks passed, the changed Router test is mypy-clean, and independent final
   review returned CLEAR. FPR=N/A because no physical Provider call occurs.
   Live role binding remains forbidden until A009B3 independently qualifies
   every physical Architect, PM, Chief Engineer, Director, and QA provider attempt.

   **BASE-FS-SEG-001 integration prerequisite (closed, 2026-07-19):** ordinary
   FactStream provision, enrollment, and workspace bootstrap now preflight the
   complete requested stream tuple before any maintenance-store or KernelOne
   effect. All three reserved namespace roots, their suffixed streams, and
   `.segmented` ordinary names fail closed with
   `segmented_stream_api_required`; reserved-only and mixed batches both prove
   zero authority or partial lock-key effect. The dedicated segmented API keeps
   its stricter namespace contract, does not provision missing workspace
   authority, and remains the only path that enrolls segmented control streams.
   Fresh main-agent verification passed 155 FactStream public tests, 96 Factory
   role-evidence tests, 54 provider-attempt tests, the 18-test KernelOne
   architecture gate, the aggregate KernelOne release gate, and the complete
   Factory Pipeline suite at `1056 passed in 557.31s`; scoped static gates
   passed. Independent specification review returned `CLEAR` and code-quality
   review returned `APPROVED`. FPR=N/A because no physical provider call
   occurred. Historical dormant ordinary lock keys are not deleted or repaired
   by this bucket and remain explicit cutover inventory debt.
3. **A009B3 — live role binding and semantic injection.** B3.0-B3.3 are closed:
   the typed port is bound at every controlled role seam, `RequestPreparer`
   creates one pre-anchor candidate, awaits the cutoff ACK, resolves the exact
   proof, injects the canonical policy/slot/anchor block, recomputes the final
   message/input/token/digest projection, and freezes one semantic request.
   Background tasks inherit runtime-private authority only at controlled
   creation seams; recovery without reconstructed authority remains blocked.
   B3.3 now propagates that frozen authority to every private dispatch seam
   while retaining zero transport. B3.4-B3.5 must bind physical-attempt
   lifecycle, retry budget and complete-request qualification, then replace the deliberate
   `factory_role_semantic_request_frozen_physical_dispatch_not_enabled` stop
   without permitting any unaudited physical attempt.

   Qualification is per physical provider attempt, including retries and
   fallbacks; one qualified attempt never authorizes another. Before dispatch,
   every real Architect, PM, Chief Engineer, Director, or QA attempt independently proves
   the exact final provider request: role-correct first system message and
   metadata, complete messages, exact tool schemas, `tool_choice`,
   `response_format`, argument aliases/normalization contract, final-request
   token count and context-window utilization, and the role policy's seven-kind
   coverage projection from the immutable cutoff. The persisted
   `context_snapshot_ref` must be a readable 24-hex final-request snapshot key
   for that same workspace/attempt. Missing, malformed, stale, cross-role,
   clipped, or messages-only evidence fails before provider dispatch and is
   recorded as a failed attempt, never PASS. Buckets with no physical provider
   call record FPR as `N/A`; `N/A` can never satisfy a provider qualification
   or Factory completion gate.

A009B1 RED coverage must include stale/wrong fencing token, non-ACTIVE lease,
wrong/missing/released stage claim, draining race, malformed/non-JSON candidate,
source-resolver failure, append/fsync failure, strict re-read corruption,
cross-run namespace isolation, exact idempotent replay, conflicting replay,
same-freeze concurrency, different-freeze monotonic sequencing, lease expiry
during source resolution/fragment append, incomplete fragment recovery,
commit-ACK loss, full-head/storage/workspace/stream drift, strict DTO/scalar
type substitution, and the real FactStream 4KiB record ceiling for PM, Chief
Engineer, Director, and QA. All such failures return no ACK and confer no
request authority.

**A009B1 closure evidence (2026-07-18).** The fenced ledger is implemented with
bounded fragment/commit facts, exact ACTIVE claim revalidation without lease
renewal, exact-base typed cross-Cell DTO validation, complete `StageResult`
private-port leak rejection, and synchronized Cell/catalog/context-pack assets.
Fresh main-agent verification passed 216 related real/focused tests, the 54-test
A009A cutoff-disabled regression set, catalog governance with zero issues, Cell
governance (`31 passed, 6 skipped`), Ruff, Ruff format, mypy for five scoped
sources, compileall, JSON/YAML parsing, and `git diff --check`. Independent
reviews returned `PASS` and `APPROVED`. A009B1 remains non-authorizing at the
provider boundary: the production source resolver is unavailable and
`RequestPreparer` still raises `factory_role_evidence_cutoff_not_enabled`.

**A009B2a1 closure evidence (2026-07-18).** New Factory runs now start with
one immutable, detached admission genesis in a bounded, strict
`factory.event_chain.v1` hash chain. Strict read/CAS/append/file-fsync and
created-parent fsync share descriptor-safe authority; recovery and retry derive
state only from that strict chain, while the compatibility reader remains
non-authoritative. Cancellation does not escape until a surviving append worker
has reached terminal settlement, and any worker failure remains attached as the
`CancelledError` cause. KernelOne provision, fresh/existing anchor enrollment,
and anchor/stream acquisition reject non-finite budgets and share one monotonic
deadline; Factory consumes one end-to-end five-second budget without phase
reset. Fresh main-agent verification passed 335 focused/interaction tests in
six sets: 35 event-chain, 57 locked-file, 93 maintenance/FactStream/retention,
108 A009B1 interaction, 37 Store, and 5 create/JetStream/recovery/retry tests.
Ruff, Ruff format, mypy on four production sources, compileall, JSON parsing,
and `git diff --check` passed. Independent code-quality review returned
`APPROVED`; the dedicated lock review returned `PASS`. A009B2a1 performed no
real Architect, PM, Chief Engineer, Director, or QA LLM call and no Bench run, so complete
provider-request qualification is explicitly `N/A` for this bucket, not
evidence that any role snapshot has been accepted. Provider-visible anchors
remain disabled until A009B2a2, A009B2b, and A009B3 close.

**A009B2a2 closure evidence (2026-07-19).** Successful PM and Chief Engineer
stage facts now bind strict versioned producer artifacts, exact source
byte/canonical hashes, and Factory-owned immutable content snapshots. Stage
completion is an event-first detached transaction whose authoritative
`stage_completed` intent is followed by the mutable run snapshot, one exact
immutable checkpoint, strict re-read/hash verification, a durable commit
marker, and only then derived publication. Unmatched intents, persistence
failure, and cancellation before the commit ACK remain quarantined; one shared
commit arbiter linearizes cancellation against append/fsync/strict post-read.
Automatic router mutations use one Service-owned per-run lock and preserve
concurrent terminal state plus the last committed pointer. Checkpoint DTOs
reuse the unique internal `FactoryRunStatus` enum and reject valid-shaped refs
whose status is not one of the seven current lifecycle values. Fresh
main-agent verification passed 268 tests: 52 stage-persistence/Service, 35
event-chain, 108 role-evidence authority/admission/Service, 47 provenance, and
26 Store tests. Ruff check/format, mypy on five production sources, compileall,
and scoped `git diff --check` passed. Independent final specification and
code-quality reviews both returned `CLEAR`. This bucket performed no real PM,
Chief Engineer, Director, or QA provider call and no Bench run, so complete
provider-request qualification is `N/A`, never `PASS`. Provider-visible
anchors remain disabled until A009B2b and A009B3 close.

Each provider-visible item uses
`polaris.final_request_evidence_anchor.v1` with `ref_kind`, canonical ref/hash,
source fact schema/version, current run id, and role. System identity uses
`polaris.role_identity.v1:<canonical-role>` in the first system message.

`pm_raw_intent` and all other required evidence must have provider-visible,
structured anchors in the frozen messages/tool schemas. Control-plane
sidecars are only the expected-evidence input; they cannot count as included
evidence unless the same anchor/hash is present in the provider-bound payload.

KernelOne defines immutable `FrozenFinalProviderAttemptV1`,
`FinalProviderAttemptGatePort`, and `ProviderAttemptLifecyclePort`. The
provider layer receives typed sync/async `PhysicalProviderDispatchPort`
facades over the same roles.kernel implementation. Blocking helpers call the
sync facade in their existing worker context; async/stream helpers await the
async facade and may not block the event loop with filesystem I/O. Both share
one semantic freeze/cutoff handle and one lifecycle implementation. The
sync/stream executors create the semantic candidate only after provider
binding, message compression, option clamping, tool normalization, and
response-format construction; the physical port validates policy, freezes the
provider-specific request, persists it, and returns the only dispatchable
view. It contains:

- a Factory-authorized `request_freeze_id` for the canonical semantic request
  and a unique `provider_request_id` created by the physical-dispatch port for
  each actual transport attempt, even when provider-internal retries or
  fallbacks reuse a logical `call_id`. The port creates the physical id once
  immediately before outbound dispatch and passes it through wire snapshot,
  receipt, and lifecycle; `_executor_base.build_request_observability_fields`
  consumes the bound identity and may not derive a second stable-hash id;
- current `factory_run_id`/`run_id`, `turn_id`, `call_id`, attempt number, role,
  provider, model, workflow-chain bindings, and the policy/envelope authority
  hash, plus the canonical source-fact ids/sequences/hashes and stage-journal
  watermark from which every evidence slot was derived. These references are
  lookup keys for later independent Factory reconstruction, not self-proving
  evidence;
- complete canonical JSON-safe messages, complete tool schemas including descriptions and
  parameter schemas, tool choice, complete response format, and the complete
  durable JSON-safe projection of the post-clamp config handed to the provider
  adapter after centralized recursive secret redaction. Known engine transport controls
  (timeout/retry/backoff/callback/client handles) are projected in a separate
  labeled block; every JSON-safe provider-semantic field, including
  `thinking`, `service_tier`, `anthropic_beta`, `anthropic_version`,
  `disable_tool_choice`, and nested `request_overrides`, is retained and hashed.
  Unknown JSON-safe fields are retained by default rather than silently
  dropped. API keys, authorization headers, credentials, and transport secrets
  are replaced by typed redaction markers and never land on disk. A value that
  cannot be serialized or safely redacted blocks dispatch with
  `provider_config_not_snapshot_safe`;
- the exact provider-specific outbound representation after translation and
  retry-time mutation: endpoint identity without secret query material,
  recursively redacted header map, and full JSON body for HTTP providers; or
  executable identity, redacted argv/environment, and complete stdin/request
  object for governed CLI/SDK providers. It is stored beside—not substituted
  for—the canonical semantic request. A provider transport that cannot produce
  a safe complete representation is not eligible for governed role dispatch;
- a typed provider-specific `ProviderWireSemanticProjectionV1` that maps the
  outbound representation back to canonical system/user/assistant messages,
  full tool schemas, tool choice, response format, and semantic options. The
  physical gate recomputes this projection and requires equivalence with the
  frozen semantic request, allowing only versioned lossless provider-format
  transforms. Dropped/replaced system identity, tools, schema bodies,
  `tool_choice`, response format, or evidence anchors blocks outbound dispatch;
- recomputed canonical request/audit/evidence/authority hashes;
- token/window/context-quality audit and provider-visible evidence coverage;
- one atomically stored, locally re-read, canonical 24-hex snapshot reference;
- for Factory verification, one fsync `ContextSnapshotAuditPinV1` created before
  outbound dispatch. The workspace-bound pin binds Factory run/fence,
  `request_freeze_id`, `provider_request_id`, ref, resolved storage identity,
  full snapshot content hash, composite request hash, and
  `pinned_audit_no_delete`. Retention sweep must validate and honor the pin;
  pin write/re-read failure blocks dispatch.

The canonical stored schema becomes `llm.provider_request_snapshot.v2`.
Summaries may remain as derived convenience fields, but never replace the full
payload. `FrozenFinalProviderAttemptV1` holds two views created atomically by
one constructor: (a) a process-local immutable dispatch view containing the
actual credentials/transport handles and exact config consumed by the adapter,
which is never serialized or persisted; and (b) a deterministic durable
redacted view derived from that dispatch view, containing the complete
canonical semantic context, exact provider-specific transport representation,
and typed redaction markers. Separate semantic-request and physical-wire hashes
plus a composite request hash bind both durable views; secrets are neither
persisted nor hashed. The transport can consume only the frozen object's
dispatch view—never the original mutable dict or a separately rebuilt
payload—so both views share one immutable physical construction boundary.
Tests mutate the original semantic config and provider payload after freeze and
prove neither dispatch nor durable view drifts.

Each actual provider HTTP/SDK/CLI attempt must consume a valid physical frozen
attempt; raw/unfrozen governed role requests are rejected at the transport
boundary. The common operation order is:

`build canonical semantic candidate -> Factory causal cutoff ACK -> semantic
policy gate -> provider translation/retry mutation -> physical dispatch hook ->
freeze dispatch+durable views -> persist atomically -> validate 24-hex ref ->
re-read -> create/re-read audit pin -> enforce role, identity, evidence, schema and both hashes -> append
durable attempt_start -> perform one outbound attempt -> append durable
attempt_terminal`.

Provider registration gains a typed capability declaration for
`governed_physical_dispatch_audit`. Registry startup and Factory preflight
enumerate every configured Architect/PM/Chief Engineer/Director/QA binding and
classify every registered mode exactly once as `governed_supported` or
`factory_disabled_opaque`. A `governed_supported` path is rejected if it does
not implement the hook or retains hidden SDK retries; static tests exercise one
intercepted governed-role inference attempt per supported mode and prove every
actual retry crosses again. Opaque CLI/agent SDK stays inventoried but
Factory-disabled unless it exposes one governed callback per internal HTTP
request. Codex/Gemini CLI PTY, non-PTY, winpty and fallback `Popen` branches
therefore prove outbound process/HTTP count zero and do not count as successful
physical-attempt coverage. An outer `retries=0` config or subprocess count is
not sufficient; only a version-locked, non-opaque, single-request SDK transport
with independent boundary tests may use disabled built-in retries as sufficient
evidence. Capability self-declaration is not evidence. A fallback may select
only another governed-supported provider; it cannot silently escape to an
unhooked transport. Health, `list_models` and administrative probes are
excluded and cannot satisfy coverage.

B3.4 uses one Factory-owned coordinator per active `factory_run_id`; no
per-call/freeze/role/stage coordinator and no process-global singleton are
legal. Budget is isolated and aggregated by exact
`execution_authority_hash`: every freeze, retry, fallback, reconnect and
fanout route under the same grant shares the same fixed budget. The exact role
binding carries one runtime-only `FactoryPhysicalAttemptControlPort`; all
sidecars for the grant hold that same control-port/coordinator identity.
Factory imports only `roles.kernel.public`; provider adapters consume only the
generic KernelOne physical dispatch runtime port and never calculate Factory
identity or budget.

The current split `mint_attempt_identity()` then later `register()` sequence is
forbidden. `reserve()` atomically validates run/scope/role/controlled child,
live claim/grant/hash/budget and
`reserved = RESERVED + START_PERSISTING`, `ambiguous = START_AMBIGUOUS`,
`committed = count(distinct provider_request_id whose reservation has an
authoritative durable start receipt)`. That identity set is the sole committed
budget source; overlapping live/recovered state ancestry never adds another
unit. Therefore
`committed + reserved + ambiguous < budget`, reserves
capacity, mints one hash-local ordinal/request id and makes it drain-visible.
Reservation is not a physical attempt and does not permit transport.
`begin_start` acquires an active authority lease and, under grant-then-
coordinator locks, revalidates claim/grant and the complete reservation/attempt
identity before entering `START_PERSISTING`. Only then may start persistence run
outside both locks. Strict start fsync is the attempt/budget linearization
point; an exact typed start receipt commits the reservation and returns the
only one-shot lease that can enter HTTP/SDK/CLI/stream transport. Lease and
receipts exact-match reservation id, request id, run, role, child, freeze,
authority hash, budget, ordinal and semantic/wire/composite hashes.

Close/revoke atomically aborts plain `RESERVED`. If `begin_start` won first,
close/revoke waits for abort or terminal and cannot invalidate a committed
attempt early. A definite start-write failure aborts without consumption or
transport. An ambiguous fsync outcome becomes `START_AMBIGUOUS`, freezes new
admission for that hash, never dispatches and blocks drain. On restart, strict
lifecycle replay reconstructs committed/remaining budget before admission.
Replay proving a start exists may append one cancelled recovery terminal but
never redispatch; proof of no start may abort; uncertainty quarantines the run.
Recovered structure never converts that run to success. Terminal fsync must
ACK before success may escape; failure remains a typed drain blocker.

Restart never reconstructs the Factory-private nonce or revives a live grant
registry row. Factory first installs a strict replay fence that forbids live
stage/grant mutation and new admission for the recovered run, then captures
one vector containing the Factory stage-event head, committed role-evidence
cutoff head, provider-attempt lifecycle head and current run/stage fence.

Factory reads each source strictly to that vector and builds a private
immutable detached replay-only grant-view set. Each element is keyed by
`(execution_authority_hash, request_freeze_id, cutoff_fact_id,
cutoff_sequence, cutoff_event_hash)` and derives from exactly one committed
`FactoryRoleEvidenceCutoffBodyV1` plus exact matching Factory stage
claim/event/persistence facts. Multiple legal freeze/cutoff elements under one
authority hash are retained, and budget/ordinal/lifecycle reconstruction is
aggregated across the complete set by that hash. An idempotent re-read of one
fact identity collapses to one element; a distinct second fact claiming the
same freeze/cutoff identity or different bytes/hash is a duplicate conflict.

A strict roles.kernel public lifecycle replay query returns only the
independently verified lifecycle snapshot at its captured head; lifecycle
cannot create, reconstruct or self-prove Factory authority. Every lifecycle
fact must exact-match one replay-view element plus the Factory facts for run,
role, controlled child, authority hash, budget, authority-local ordinal and
freeze. Missing, duplicate, cross-view or drifting facts quarantine before
admission.

After candidate construction, Factory immediately re-reads all three heads and
the run/stage fence, then compare-and-installs the coordinator only if the
complete vector is unchanged. Drift discards the candidate and performs zero
admission or recovery-terminal append; full replay restarts from a new vector.
The only retry authority is immutable Factory-owned
`FactoryPhysicalAttemptReplayPolicyV1` with
`schema_version="factory.physical_attempt_replay_policy.v1"`,
`max_full_replays=3` total candidate builds including the initial build, and
`total_deadline_seconds=30.0` from a monotonic clock. No caller/env/provider
override exists. Either bound exhausting quarantines with
`factory_physical_attempt_replay_head_unstable` and zero coordinator admission,
recovery-terminal append, reservation or outbound. The cancelled recovery
terminal requires expected-previous-lifecycle-head CAS under the same replay
fence; CAS or Factory/cutoff head/fence drift leaves it unapplied and fails
closed.

Replay-only elements carry no nonce, live capability or reservation/dispatch
method. They only validate identity, reconstruct consumed budget and authorize
one cancelled recovery terminal for a proven durable start. The old grant and
entire recovered `factory_run_id` remain permanently dead for new authority:
no stage in that run may mint a replacement grant or perform outbound. Any
later outbound belongs to an entirely new Factory run with a new
`factory_run_id`, admission, stage claim/fence, grant and cutoff. Unknown view,
cross-view identity, incomplete authority-hash aggregation, duplicate ordinal
or ordinal regression quarantines and permits zero new reservation/outbound.

Lock order is always grant authority then run coordinator. Both locks exclude
await, sync I/O, fsync, snapshot/FactStream/storage locks, provider/SDK calls,
subprocess and callbacks; storage code cannot call back into the coordinator
while holding its lock.

The legal state graph is:

```text
RESERVED
  |-- ABORTED
  `-- START_PERSISTING
        |-- ABORTED
        |-- START_AMBIGUOUS
        |     |-- ABORTED
        |     `-- RECOVERED_START_ABORTING
        |           |-- TERMINAL_ACKED
        |           `-- TERMINAL_PERSISTENCE_FAILED
        `-- START_COMMITTED
              |-- TERMINAL_ACKED
              |-- TERMINAL_PERSISTENCE_FAILED
              `-- RECOVERED_START_ABORTING # replay-fenced unmatched durable start only
                    |-- TERMINAL_ACKED      # cancelled recovery terminal; never dispatch
                    `-- TERMINAL_PERSISTENCE_FAILED
```

The replay-only edge into `RECOVERED_START_ABORTING` is legal from
`START_AMBIGUOUS` after strict proof of the durable start, or from an unmatched
`START_COMMITTED` reconstructed from one authoritative durable start with no
authoritative terminal. It exists only behind the restart replay fence. A
`START_COMMITTED` with a matching terminal reconstructs directly as
`TERMINAL_ACKED` and cannot receive another cancelled terminal.

`wait_settled` is true only when all reservations are `ABORTED` or
`TERMINAL_ACKED`; persisting, ambiguous, committed, recovery-aborting and
terminal-failure states block or raise. A live, non-recovered green completion
drain proves
`transport entries = unique provider_request_id = starts = terminals =
consumed budget`. Reserved/aborted/qualification-rejected paths are zero
physical attempts. B3.4 uses only a synthetic opaque pin seam; B3.5 inserts and
validates the complete same-workspace snapshot/pin contract before enablement.
For a recovered failed run, structural settlement instead proves
`total_consumed_budget = |all_authoritative_durable_start_ids|`;
`all_authoritative_durable_start_ids =
preexisting_authoritative_terminal_ids disjoint-union
unmatched_recovery_start_ids`; and
`cancelled_recovery_terminal_ids = unmatched_recovery_start_ids`. Thus all
starts have one terminal after recovery, but only the unmatched subset receives
a new cancelled terminal; new transport entries are exactly zero. Pre-crash
transport cannot be inferred from a durable start or satisfy green evidence.
Recovered cancelled terminal ACK never refunds its budget unit or changes the
failed/quarantined run to success.

`attempt_start` and `attempt_terminal` are keyed by
`provider_request_id`, never logical `call_id`, and include current run/turn,
call id, ref, request hash, role, provider, model, attempt number,
policy/envelope authority hash, exact attempt budget, hash-local ordinal,
canonical evidence source-fact refs and watermarks, status, and event sequence.
A durable start must exist before network dispatch. A return, error,
cancellation, or stream close must append exactly one durable terminal in
`finally` before its result is accepted.

Async cancellation is a governed terminal path, not an escape hatch. The
sync/async transport wrapper reserves and commits every opened physical attempt
through the injected run-scoped coordinator implementing the KernelOne
`ProviderAttemptInFlightDrainPort`, keyed by `provider_request_id`. FactoryRunService
creates exactly one coordinator per active run and injects that same instance
with the cutoff/physical-dispatch ports; non-Factory role sessions receive a
separate session-scoped coordinator. No Cell imports roles.kernel internals or
uses a process-global registry. Async helpers
catch `CancelledError`, close/cancel the underlying response or subprocess,
and `await asyncio.shield(...)` around the fsync terminal append before
re-raising cancellation; async generators do the same from `aclose`/`finally`.
Blocking work delegated to a thread remains registered until that worker emits
its terminal ACK, even if the awaiting coroutine is cancelled. Factory drain
awaits `wait_settled(factory_run_id)` on that injected coordinator plus the
durable ledger pairing. After process death, strict lifecycle replay rebuilds
all per-authority committed/remaining budget before admission. An unmatched
start is never redispatched: strict proof of the start permits only one
cancelled recovery terminal, while uncertainty quarantines the run. Structural
pairing may converge, but the recovered/quarantined run remains failed and
cannot complete successfully.

The authoritative implementation is an awaited
`StrictProviderAttemptLifecycleStore` in
`final_provider_attempt_lifecycle.py`. It appends typed facts through the
`events.fact_stream.public` boundary, with strict integrity, fsync durability,
and idempotency key `provider_request_id:phase`; a public FactStream append ACK
is required before progress. It never uses one global physical stream: the
logical ledger `roles.kernel.provider_attempts` is implemented as a run-scoped,
strict, native segmented FactStream so the KernelOne 4096-record/8-MiB limit
cannot permanently exhaust all future requests.

For each Factory run there is exactly one enrolled logical stream,
`roles.kernel.provider_attempts.factory.<run-hash>`; each non-Factory role
session uses `roles.kernel.provider_attempts.session.<session-hash>`. The
Factory inventory accepts only the signed `factory` namespace and exact active
run hash. The FactStream public Cell adds
typed
`EnsureSegmentedFactLedgerCommandV1`, `AppendSegmentedFactEventCommandV1`,
`QuerySegmentedFactLedgerHeadV1`, and `QuerySegmentedFactEventsV1` contracts;
their immutable results are `SegmentedFactLedgerReadyV1`,
`SegmentedFactEventAppendedV1`, `SegmentedFactLedgerHeadV1`, and
`SegmentedFactQueryResultV1` (including captured global-head bounds);
their service implementation delegates to a new KernelOne
`SegmentedJsonlEventStore`. It provisions no lock authority: workspace
bootstrap remains the only authority provisioner, while ensure may only enroll
the deterministic run-scoped logical key under existing authority. The
provider-attempt and Factory role-evidence-authority namespaces are
capability-guarded: ordinary
`append_fact_event`, `query_fact_events`, `query_fact_stream_head`, and raw
physical segment names are rejected with
`segmented_stream_api_required`, so no undeclared segment can exist outside the
authoritative inventory.

`SegmentedJsonlEventStore` owns deterministic internal segment files under the
one logical stream lock. Each segment accepts at most 511 fact records plus one
content-addressed seal, and every lifecycle fact must serialize below a fixed
4-KiB contract limit, safely below 8 MiB with store envelopes. The seal binds
segment content, previous seal, and the exact next segment index. The store
assigns one continuous logical sequence across segments; local position is
storage evidence only. Segment names are derived consecutively and opened by
exact next index. A full strict scan validates contiguous segments, sealed
non-tail segments, and continuous global sequences/hash links on first open or
restart, cursor/seal/index mismatch, explicit strict verification, and Factory
terminal audit—not before every healthy append. KernelOne may
enumerate only this logical stream's private segment directory under its lock
to reject unexpected filenames, unlinked later segments, or gaps; that is
internal storage integrity validation, never a Factory inventory input.

Idempotency is logical-stream-wide, not physical-file-local. Under the single
logical lock, healthy append validates the atomic cursor against storage
identity, current tail seal/size/hash, and expected global sequence, then does
only incremental tail work. Idempotency locators are separate
content-addressed files sharded by the hash of `provider_request_id:phase`;
updating one key never rewrites a global index. Each locator binds logical
stream, event id, global seq, segment/local position, and event hash, and the
pointed event is rechecked before reuse. Cursor and locators are derived
indexes, never authority; missing/stale/mismatched state triggers one strict
segment scan and atomic rebuild under the lock, after which append returns to
the bounded fast path. An ambiguous error after segment fsync
must re-read the same logical stream/key and return the existing ACK; it cannot
switch to a new segment or create a second start. Terminal append locates its
authoritative start by `provider_request_id` from the ledger, not a process-only
handle, so restart and rollover cannot mis-pair it. Concurrent append/rollover
is serialized by the logical stream lock plus expected global sequence CAS.

Factory captures one typed logical head containing total count, global seq,
tail segment/local seq, and a chained content hash. The first strict page opens
a `SegmentedFactReadSnapshotV1` at that head; each result returns a hashed
continuation binding the head, next segment/byte offset, last global seq, and
running chain hash. Later pages seek from that continuation rather than
rescanning the prefix, and the final running hash must equal the captured head.
Factory performs one sequential full strict scan to that head, validates exact
start/terminal pairing, then phase 3 rechecks head/fence drift; total audit work
is O(events + segments), not O(events x pages). Internal stream-directory
listing, role JSONL globs, Director logs, and `events_tail` are never inventory
sources.

These run-scoped ledgers and their dynamic lock keys have explicit
`pinned_audit_no_delete` retention. This bucket adds no deletion, compaction,
unenrollment, or best-effort archive claim; FactStream owns disk-capacity
accounting and exposes `segment_count`/`storage_bytes` in the typed head plus
typed filesystem-capacity failures. Any future cleanup is a separate
governed bucket and must first synchronously archive all lifecycle records and
readable snapshots with content-addressed ACK, prove replay, then add explicit
retention/unenrollment contracts. A head vector alone is never archival proof.

Context snapshot retention participates in the same pin contract.
`context_store_retention.py` must ignore age/count eviction for a ref with a
valid Factory audit pin, report malformed/orphan pin state fail-closed, and
never replace pinned content at the same ref. Pins are permanent in this bucket
and are included in capacity accounting. There is no release-on-COMPLETED;
future archival must prove the lifecycle facts, snapshot bytes, and hashes are
readable from the archive before a separate governed release operation exists.

Append/enrollment failure blocks dispatch for start and converts any
post-dispatch result to a typed failed lifecycle for terminal. The existing
fire-and-forget `LLMEventEmitter` remains a derived UI/telemetry projection only
and cannot satisfy inventory. For N actual outbound HTTP/SDK/CLI attempts, the
authoritative run-scoped ledger must contain exactly N distinct start/terminal
pairs.

Snapshot write/read failure, malformed ref, evidence failure, role-system
identity mismatch, or hash drift must make outbound HTTP/SDK/CLI attempt count
remain zero.
This applies uniformly to sync, streaming, structured, retry, required-tool,
response-format, reasoning, role-binding, and all other fallback paths.
Legacy four-argument snapshot callbacks fail closed for governed roles.
Instructor/manual retry, provider-helper retry, stream reconnect, SDK retry,
CLI retry, and role-binding fallback all route through the same physical-
dispatch port; every outbound attempt receives a new provider request id,
wire snapshot, and lifecycle pair. No invocation bypass remains.

Governed Instructor structured calls set SDK/provider hidden retries and
Instructor `max_reasks` to zero. Schema/validation failure returns typed
evidence to the KernelOne caller, which may prepare an explicit new semantic or
physical retry through the normal cutoff/dispatch path. Instructor never owns a
direct `chat.completions.create`/Responses client for governed roles and may not
mutate/reask a request behind the physical hook.

## Typed public qualification contract

Add a `context.engine` public query/result pair for one dispatched provider
attempt (not merely one hash lookup or logical call):

- input: `workspace`, current `factory_run_id`/`run_id`, `turn_id`, `call_id`,
  `request_freeze_id`, `provider_request_id`, canonical
  `context_snapshot_ref`, expected role,
  provider/model, request hash, expected audit/evidence/authority hashes, and
  Factory-reconstructed `RoleFinalRequestPolicyFactsV1` plus its canonical
  policy hash and Factory-issued cutoff fact/source-head evidence;
- output: immutable status, `qualified`, canonical role, context path/source,
  counts, token/window summary, and closed `failure_codes`;
- the implementation must reuse `query_final_provider_request_audit` and
  `context_snapshot_candidates`; no duplicate storage resolver;
- invalid ref, not found, unreadable, invalid snapshot, or missing provider
  request remains fail-closed and preserves searched-path evidence.

Qualification requires all of the following:

1. ref is exactly one canonical 24-character lowercase hex key and the
   workspace-bound snapshot is readable;
2. stored provider request schema is `llm.provider_request_snapshot.v2`;
3. expected role is one of `architect`, `pm`, `chief_engineer`, `director`, `qa`; stored role
   matches it; and the first provider-bound system message contains the
   canonical role identity marker for exactly that role;
4. stored full `messages` is a non-empty list and its count matches both the
   snapshot and final audit;
5. complete canonical `tools`, `tool_choice`, `response_format`, and sent
   provider option fields are present, and the complete provider-specific
   HTTP/SDK/CLI representation is present after translation/retry mutation;
   full schema/body fields are validated and counts match derived summaries
   (zero tools is allowed only when role/task policy requires no native tool).
   The provider wire semantic projection must be canonically equivalent to the
   semantic request and its role/evidence/tool/response-format obligations;
6. final audit schema is `llm.final_request_context_audit.v1` and contains
   final message/tool-schema/response-format token estimates, positive context
   window, utilization, headroom, coverage, request metadata, and context
   quality;
7. final-request evidence coverage exists and `pass is True`;
8. role identity is true and `role_id == expected_role_id == stored role`;
9. `missing_required_refs`, `missing_required_tools`, unexpected tool pruning,
   and registry missing-schema tools are all empty;
10. request/audit counts and token arithmetic are internally consistent;
11. semantic, physical-wire, and composite `request_hash` values are exactly 64
    lowercase hex and equal canonical recomputations over both full views;
12. audit/evidence/authority hashes are canonical recomputations, not trusted
    projections; workflow-chain bindings, `request_freeze_id`,
    `provider_request_id`, and Factory cutoff fact agree across request,
    snapshot, lifecycle, and terminal route;
13. provider-visible evidence anchors satisfy the explicit role policy; a
    control-plane sidecar alone never satisfies coverage; expected refs and
    allowed absent/present states are independently recomputed from the static
    role policy and current-run canonical facts at the Factory-issued cutoff. The
    producer-carried envelope authority hash must be internally consistent but
    cannot prove its own semantic truth.

The attempt identity in the stored snapshot must bind the queried current
run/turn/logical call/provider request;
stored provider/model/request hash must bind the terminal route event. Audit,
evidence, and authority hashes are recomputed from canonical JSON, never
trusted because an event reported a non-empty string. Workflow-chain hashes
must agree with the request evidence for that call.

Unknown/malformed values must add a closed failure code; qualification never
defaults to pass.

## Per-call Factory audit

Extend normalized LLM events with stable attempt identity (`factory_run_id`,
`run_id`, `turn_id`, `call_id`, `request_freeze_id`, `provider_request_id`,
`factory_evidence_cutoff_fact_id`, `event_id`, `seq`, `semantic_request_hash`,
`physical_wire_hash`, `request_hash`, `role_policy_version`,
`policy_authority_hash`, and
provider-dispatched state)
without removing raw evidence. Build one
`llm_final_request_context_audit` record that:

- enumerates every provider-dispatched attempt only from the strict run-scoped
  segmented `roles.kernel.provider_attempts` logical FactStream. It captures
  the logical segmented head in the pre-terminal barrier, then uses strict
  paginated FactStream reads until that exact global head, requiring continuous
  global sequences, valid segment/hash continuity, and complete start/terminal
  pairing. Role LLM JSONL, Director dispatch logs, and
  `audit_bundle.events_tail` are diagnostic mirrors only; they can never
  create, remove, complete, or qualify an inventory item;
- derives reached roles from a Factory-owned
  `FactoryRoleLlmAuditObligationsV1` query in
  `factory.pipeline.internal.final_request_audit`, using only the current run's
  persisted stage journal/status and canonical
  Architect/PM/CE/Director/QA stage facts;
  the Bench script imports this platform query and owns no role-obligation
  logic;
- filters inventory strictly to the current Factory run and rejects historical
  or cross-run evidence;
- treats `(current run, provider_request_id, request_hash,
  context_snapshot_ref)` as the physical attempt identity; `call_id` is only a
  logical parent and can have multiple independently audited fallback/retry
  attempts;
- deduplicates only true mirror records of the same physical attempt;
- requires a terminal record for every observed dispatched attempt;
- calls the typed `context.engine` qualification query for every unique
  physical attempt;
- reloads each Factory-issued cutoff fact, verifies its run fence/source heads
  and canonical source-fact hashes, and rejects any producer-supplied stale
  watermark or anchor disagreement;
- compares event role/ref/audit/evidence authority fields with the qualified
  snapshot and rejects missing/false coverage or identity evidence;
- reports `calls_observed`, `calls_qualified`, per-role totals, per-call
  failure codes, and searched-path/read errors;
- fails if a required reached role has no qualified call;
- never samples, selects only the latest call, or treats duplicated/mirrored
  events as additional proof.

The inventory itself is fail-closed: unavailable current-run inventory,
missing run/provider-request identity, a reached role with zero dispatched
attempts, start without terminal, terminal without a dispatched start,
conflicting mirrors, or audited-attempt count drift all fail. Identical mirrors
of the same attempt may be deduplicated. Distinct provider request ids remain
distinct even when they share one call id and otherwise identical route/token
data. Cache hits and non-provider events are explicitly classified and cannot
masquerade as dispatched attempts or satisfy the real-call requirement.

All current-run inventory discovery, event normalization, physical-attempt
dedupe, obligation derivation, and per-attempt qualification orchestration move
to `factory.pipeline.internal.final_request_audit`. The dependency direction is
fixed: `factory_run_service -> final_request_audit`; `bench_gates` and
`run_factory_bench` may only import/project its platform result.
`final_request_audit` must not import any Bench-named module or script.

The exact production call point is `FactoryRunService.complete_run` in
`factory_run_service.py`, before persisting `FactoryRunStatus.COMPLETED`.
Extract/add an awaited `_prepare_pre_terminal_audit_barrier` that runs for every
status branch, including current `PENDING` runs. It persists the terminal-drain
intent, waits for the stage executor to clear `_stage_in_flight`, verifies child
session settlement, flushes and re-reads the current-run stage journal and
physical-attempt lifecycle log, and returns their authoritative high-watermark
hashes. A PENDING run with no stage/attempt evidence is explicitly classified
as `no_role_attempts_expected`; that classification cannot satisfy a Bench or
verified-delivery role obligation. Any PENDING run with role/attempt evidence
must pass the same barrier and audit as all other states.

The barrier uses an explicit three-phase lock/fencing protocol to avoid
deadlocking `execute_stage` on the same per-run lock:

1. under `run_lock`, claim a unique lifecycle operation nonce, record draining,
   capture the workspace lease fencing token and initial journal watermark,
   persist, then release `run_lock`;
2. outside `run_lock`, await stage/child/provider-attempt settlement and flush
   the stage journal, Factory role-evidence authority ledger, and
   provider-attempt ledger; capture their strict heads. Enumerate snapshot refs
   only from the captured attempt ledger, then compute the candidate per-attempt
   qualification including each readable pin/storage identity/content hash;
3. reacquire `run_lock`, reload the run, and require the same nonce, fencing
   token, settled stage state, and unchanged audited high-watermarks. For a
   success candidate, re-read every captured ContextOS pin and snapshot through
   `context.engine`, re-run qualification, and require identical storage
   source/content/composite hashes before persisting COMPLETED. Any missing pin,
   retention race, mutation, drift, or fencing conflict fails closed (or
   restarts the bounded protocol); it never writes COMPLETED from stale audit.

Phase 2 includes the Factory-owned current-run obligations/inventory query and
per-attempt qualification at the captured head vector; its result is the
candidate audit consumed by phase 3. Phase 3 may persist
`complete_run(success=True)` only after revalidating that candidate under the
same nonce, fencing token, settled stage state, and unchanged stage-journal,
role-evidence-ledger, provider-attempt-ledger, and pinned-snapshot evidence. Any missing
or failed attempt changes the lifecycle
outcome to failed with typed
`factory.final_provider_request_audit_failed` metadata/evidence; it can never
persist a successful orchestration lifecycle.
The existing post-terminal `_finalize_terminal_drain` remains responsible for
TaskRuntime reset and lease release, not for proving the pre-COMPLETED audit.
This gate does not grant delivery verification: Run Ledger/QA remains the only
`COMPLETED_VERIFIED` authority.

The Factory projection/report and Bench consumer expose the same hard gate
named `llm_final_request_context_audit`. `all_checks_passed` and any future
`COMPLETED_VERIFIED` verdict require this platform audit to pass in addition to
`llm_route_audit`; a failed lifecycle cannot later be promoted by Run Ledger.
The hard block applies only to `success=True -> COMPLETED`. Explicit failed or
cancelled closure records the audit defect diagnostically and continues FAILED/
CANCELLED cleanup; missing terminal evidence must never make a failed run
uncloseable.

## Exact write set

Implementation is serial. Phase A must pass independent review before Phase B
starts; the two phases are one FPR bucket but never edit concurrently.

### Phase A — producer freeze and durable physical lifecycle

- `src/backend/polaris/kernelone/events/final_request_evidence.py`
- `src/backend/polaris/kernelone/events/tests/test_final_request_evidence.py`
- `src/backend/polaris/kernelone/events/sourcing/segmented_file_store.py`
- `src/backend/polaris/kernelone/events/sourcing/__init__.py`
- `src/backend/polaris/kernelone/events/tests/test_segmented_sourcing_store.py`
- `src/backend/polaris/kernelone/llm/engine/contracts.py`
- `src/backend/polaris/kernelone/llm/engine/_executor_base.py`
- `src/backend/polaris/kernelone/llm/engine/executor.py`
- `src/backend/polaris/kernelone/llm/engine/stream/executor.py`
- `src/backend/polaris/kernelone/llm/engine/context_store_retention.py`
- `src/backend/polaris/kernelone/llm/engine/tests/test_executor.py`
- `src/backend/polaris/kernelone/llm/engine/stream/tests/test_executor.py`
- `src/backend/polaris/kernelone/llm/engine/tests/test_final_request_receipt.py`
- `src/backend/polaris/kernelone/llm/engine/tests/test_context_store_retention.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/context_audit.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_preparer.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/request_facts.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/invoker.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/stream_engine.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/event_emitter.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_gate.py`
- `src/backend/polaris/cells/roles/kernel/internal/llm_caller/final_provider_attempt_lifecycle.py`
- `src/backend/polaris/cells/roles/kernel/cell.yaml`
- `src/backend/polaris/cells/roles/kernel/context.pack.json`
- `src/backend/polaris/cells/roles/kernel/generated/descriptor.pack.json`
- `src/backend/polaris/cells/roles/kernel/generated/verify.pack.json`
- `src/backend/polaris/cells/factory/pipeline/internal/final_request_evidence_authority.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_stage_executor.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_final_request_evidence_authority.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_factory_stage_executor_characterization.py`
- `src/backend/polaris/tests/test_factory_run_service.py`
- `src/backend/polaris/cells/factory/pipeline/cell.yaml`
- `src/backend/polaris/cells/factory/pipeline/context.pack.json`
- `src/backend/polaris/cells/factory/pipeline/generated/descriptor.pack.json`
- `src/backend/polaris/infrastructure/llm/instructor_client.py`
- `src/backend/polaris/tests/test_instructor_integration.py`
- `src/backend/polaris/infrastructure/llm/providers/provider_helpers.py`
- `src/backend/polaris/infrastructure/llm/providers/async_provider_helpers.py`
- `src/backend/polaris/infrastructure/llm/providers/async_http_client.py`
- `src/backend/polaris/infrastructure/llm/providers/async_base_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/async_provider_adapter.py`
- `src/backend/polaris/infrastructure/llm/providers/anthropic_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/async_gemini_api_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/async_ollama_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/gemini_api_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/kimi_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/minimax_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/ollama_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/openai_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/codex_cli_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/codex_process.py`
- `src/backend/polaris/infrastructure/llm/providers/codex_sdk_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/gemini_cli_provider.py`
- `src/backend/polaris/infrastructure/llm/providers/provider_registry.py`
- `src/backend/polaris/infrastructure/llm/providers/__init__.py`
- `src/backend/polaris/infrastructure/llm/sdk/openai_sdk.py`
- `src/backend/polaris/infrastructure/llm/sdk/__init__.py`
- `src/backend/polaris/infrastructure/llm/providers/tests/test_provider_helpers_retry.py`
- `src/backend/polaris/infrastructure/llm/providers/tests/test_async_provider_helpers.py`
- `src/backend/polaris/infrastructure/llm/providers/tests/test_async_http_client.py`
- `src/backend/polaris/infrastructure/llm/providers/tests/test_codex_process_physical_dispatch.py`
- `src/backend/polaris/infrastructure/llm/sdk/tests/test_openai_sdk_physical_dispatch.py`
- `src/backend/polaris/tests/integration/llm/providers/test_anthropic_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_codex_cli_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_codex_sdk_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_gemini_api_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_gemini_cli_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_kimi_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_minimax_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_ollama_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_openai_provider.py`
- `src/backend/polaris/tests/integration/llm/providers/test_provider_registry.py`
- `src/backend/polaris/tests/test_anthropic_streaming.py`
- `src/backend/polaris/tests/test_kimi_streaming.py`
- `src/backend/polaris/tests/test_minimax_streaming.py`
- `src/backend/polaris/tests/test_openai_streaming.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_final_provider_attempt_gate.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_final_provider_attempt_lifecycle.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_llm_invoker_decomposition_characterization.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_llm_caller_components.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_final_request_sampling_audit.py`
- `src/backend/polaris/cells/roles/kernel/tests/test_llm_invoker_role_binding_fallback.py`
- `src/backend/polaris/cells/events/fact_stream/public/catalog.py`
- `src/backend/polaris/cells/events/fact_stream/public/contracts.py`
- `src/backend/polaris/cells/events/fact_stream/public/service.py`
- `src/backend/polaris/cells/events/fact_stream/public/__init__.py`
- `src/backend/polaris/cells/events/fact_stream/__init__.py`
- `src/backend/polaris/cells/events/fact_stream/public/tests/test_public_contracts.py`
- `src/backend/polaris/cells/events/fact_stream/public/tests/test_public_service.py`
- `src/backend/polaris/cells/events/fact_stream/public/tests/test_workspace_bootstrap.py`
- `src/backend/polaris/cells/events/fact_stream/cell.yaml`
- `src/backend/polaris/cells/events/fact_stream/generated/context.pack.json`
- `src/backend/polaris/cells/events/fact_stream/generated/descriptor.pack.json`
- `src/backend/polaris/cells/events/fact_stream/generated/verify.pack.json`
- `src/backend/docs/graph/catalog/cells.yaml`

### Phase B — snapshot qualification and Factory hard gate

- `src/backend/polaris/cells/context/engine/public/contracts.py`
- `src/backend/polaris/cells/context/engine/public/service.py`
- `src/backend/polaris/cells/context/engine/public/__init__.py`
- `src/backend/polaris/cells/context/engine/tests/test_public_contracts.py`
- `src/backend/polaris/cells/context/engine/tests/test_public_service.py`
- `src/backend/polaris/cells/factory/pipeline/internal/bench_gates.py`
- `src/backend/polaris/cells/factory/pipeline/internal/final_request_audit.py`
- `src/backend/polaris/cells/factory/pipeline/internal/factory_run_service.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_final_request_audit.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_bench_gates.py`
- `src/backend/polaris/cells/factory/pipeline/tests/test_factory_run_service_pm_contract.py`
- `src/backend/polaris/tests/test_factory_run_service.py`
- `src/backend/scripts/factory_bench/run_factory_bench.py`
- `src/backend/polaris/tests/unit/scripts/test_factory_bench_runner.py`
- `src/backend/polaris/cells/context/engine/cell.yaml`
- `src/backend/polaris/cells/context/engine/generated/context.pack.json`
- `src/backend/polaris/cells/context/engine/generated/descriptor.pack.json`
- `src/backend/polaris/cells/factory/pipeline/cell.yaml`
- `src/backend/polaris/cells/factory/pipeline/context.pack.json`
- `src/backend/polaris/cells/factory/pipeline/generated/descriptor.pack.json`
- `src/backend/docs/graph/catalog/cells.yaml`

Provider adapters and shared HTTP/SDK/CLI transport helpers are in scope only
to carry and enforce the generic physical-dispatch port immediately before
outbound work; no provider-specific business policy is added. Free-form role
prompt prose, target-project, UI, Run Ledger, ReceiptStore, and business project
files remain out of scope. The canonical system marker/evidence anchors are
structured protocol data, not prompt-policy prose.

## RED matrix

1. valid route, missing snapshot ref;
2. invalid/path-like/non-24-hex ref;
3. valid ref, snapshot absent/unreadable/wrong workspace;
4. snapshot lacks provider request or has wrong schemas;
5. wrong role/system identity;
6. messages missing or count drift;
7. tools/tool choice/response format fields missing or count drift;
8. final token/window/utilization fields missing or inconsistent;
9. evidence coverage false/missing;
10. missing required contract/blueprint/target/failure-quality ref;
11. missing required tool, unexpected pruning, missing registry schema or any
    other B3.5 qualification rejection produces zero transport and exactly one
    non-physical `FinalProviderAttemptQualificationRejectionV1` audit fact with
    `schema_version="llm.final_provider_attempt_qualification_rejection.v1"`.
    The roles.kernel qualification gate appends it through
    `events.fact_stream.public` to
    `roles.kernel.final_request_qualification_rejections.<scope-hash>`. It is
    keyed by scope/run/role/turn/call/freeze plus stable rejection code, has no
    provider request id/reservation/start/terminal, consumes no attempt budget
    and cannot satisfy physical inventory;
12. audit/evidence/authority hash missing, malformed, or well-formed but stale;
13. multiple calls where one qualifies and one fails: whole gate fails;
14. start event without terminal event;
15. Architect/PM/CE/Director/QA role required but no qualified call;
16. mirrored events for one call are deduplicated without hiding a failure.
17. only sampled `events_tail` exists, or inventory count differs from audited
    call count;
18. event/snapshot call, run, turn, provider, model, request, audit, evidence,
    authority, or workflow-chain binding drifts;
19. two distinct calls share otherwise identical route/token data and both
    remain independently audited.
20. one logical `call_id` has two fallback/retry provider attempts and only one
    qualifies: the whole gate fails;
21. historical evidence from another run cannot satisfy the current run;
22. metadata role is correct but the system message contains another role;
23. a full tool or response-format schema body changes while summaries remain
    identical: recomputed hash fails;
24. expected evidence exists only in the control-plane sidecar, not in the
    provider-visible payload: invocation is blocked;
25. Architect/PM/CE/Director/QA times
    sync/stream/provider-retry/fallback snapshot storage OSError: outbound
    HTTP/SDK/CLI invocation count remains zero;
26. cache-hit and non-provider lifecycle records are classified without
    creating or satisfying a dispatched-attempt obligation;
27. reached role has zero current-run provider attempts: gate fails;
28. legacy snapshot callback or Instructor/manual retry tries to bypass frozen
    attempt enforcement: invocation is blocked.
29. N same-call provider retries/fallbacks reach outbound transport: exactly N
    unique physical attempt starts and N matching terminals exist; a missing
    pair fails.
30. an early CE attempt precedes later workspace-quality evidence: the later
    fact does not retroactively alter the CE static slot obligation;
31. an early Director attempt precedes a later QA failed gate: the later failure
    does not change that attempt's already-present `failure_feedback` slot;
32. a dynamic slot is omitted instead of explicitly present/absent: dispatch
    and qualification fail;
33. `thinking`, `service_tier`, or a nested `request_overrides` value
    changes while snapshot/hash stays unchanged: dispatch is blocked;
34. non-JSON-safe/unredactable config blocks with
    `provider_config_not_snapshot_safe`; secret values never appear in the
    snapshot while unknown JSON-safe semantic fields remain present and hashed;
35. `complete_run(success=True)` from both PENDING and active histories must
    pass the awaited pre-terminal barrier; neither path can write COMPLETED
    while a role attempt is unaudited;
36. static dependency test rejects any import from
    `final_request_audit.py` to `bench_gates` or `scripts.factory_bench`.
37. a stage finishes concurrently with `complete_run`: phase 1 releases
    `run_lock`, `_mark_stage_finished` clears the stage, and phase 3 completes
    without deadlock; nonce/fencing/high-watermark drift still fails closed.
38. a failed/cancelled run with missing terminal audit evidence records the
    defect and still completes FAILED/CANCELLED cleanup; it can never become
    COMPLETED or satisfy Bench.
39. more than 100 physical attempts are paged through the strict FactStream to
    the captured head without sampling or truncation;
40. JSONL-only attempt evidence does not enter inventory; FactStream-only
    evidence does enter inventory and is audited even when mirrors are absent;
41. two retries under one logical call receive different provider request ids,
    and snapshot, receipt, start, and terminal all carry the same per-attempt
    identity with no `_executor_base` stable-hash fallback.
42. more than 4096 physical attempts in one run roll across native strict
    segments without record/byte exhaustion, truncation, stream-directory
    listing, or loss of one start/terminal pair;
43. concurrent first append/rollover, process restart, and ambiguous failure
    after start fsync are logical-stream-wide CAS/idempotent: replay resolves
    the original start before allocating, terminal recovers it from the ledger,
    and exactly one pair remains across all segments. Restart independently
    fences the recovered run; captures one Factory-stage/cutoff/lifecycle/fence
    vector; builds a detached replay-only view element for every exact
    freeze/cutoff identity; aggregates all elements under one authority hash;
    and rejects missing/duplicate/cross-view facts. Barriers append across each
    capture/read/recheck boundary and prove vector drift discards the candidate;
    the fourth full build and a fake-clock 30-second deadline independently
    exhaust with `factory_physical_attempt_replay_head_unstable`; expected-head
    CAS guards the cancelled terminal. Replay never reconstructs the nonce,
    revives the old grant, accepts lifecycle self-proof, loses a second legal
    freeze, or performs outbound from the recovered `factory_run_id`. A crash
    after durable `START_COMMITTED` but before transport enters the replay-only
    recovery edge. A mixed history with normal terminal pairs plus unmatched
    starts proves total-start budget conservation, cancelled terminals only for
    the unmatched subset, exactly one terminal and one consumed unit per
    distinct started `provider_request_id` despite overlapping state ancestry,
    zero new transport and never the live green equality;
44. a missing/gapped/out-of-order segment, global sequence or hash drift,
    ordinary raw append into the protected namespace, oversized lifecycle
    record, or missing logical-stream enrollment fails closed before COMPLETED;
45. the snapshot and lifecycle facts agree on the envelope authority hash but
    a provider-visible anchor disagrees with the independently reconstructed
    current-run canonical source fact: qualification fails (producer self-proof
    is insufficient).
46. a qualifying failure/quality/receipt fact exists before the Factory causal
    cutoff, while the producer submits an older valid watermark and claims
    `absent_at_request_time`: the Factory-issued cutoff marks it present and
    dispatch remains zero;
47. one adapter invocation performs two actual HTTP retries and mutates
    `max_tokens` between them: two unique physical snapshots/lifecycle pairs
    exist, both share one authorized `request_freeze_id`, and each stored wire
    body/hash equals the exact body sent on that attempt;
48. governed provider/SDK/CLI path omits the physical-dispatch hook, rebuilds
    the payload after freeze, or cannot safely snapshot the complete outbound
    request: the outbound call/subprocess count remains zero.
49. OpenAI SDK chat-completions and Responses API paths both set hidden
    `max_retries=0`, hook the exact `.create` payload, and independently prove
    their version-locked single-request boundary before dispatch. Codex/Gemini
    CLI PTY, non-PTY, winpty and fallback Popen branches remain Factory-disabled
    with outbound process count zero until they expose one governed hook for
    every internal HTTP attempt; a successful outer process launch is not
    qualified physical-request evidence.
50. a valid cutoff is reused for another call/turn/role/semantic hash, after
    lease or stage-claim invalidation, or beyond its physical-attempt budget:
    the physical-dispatch port rejects it and outbound count remains zero.
51. cancellation during async HTTP response, stream iteration, SDK await, CLI
    wait, and blocking-thread bridge yields a shielded fsync cancelled terminal
    before cancellation escapes; a simulated process death leaves an unmatched
    start and Factory completion fails closed.
52. Instructor validation failure that would normally trigger hidden reask has
    `max_reasks=0`; any explicit retry returns through KernelOne and produces a
    separately frozen physical attempt. Direct Instructor SDK invocation or
    post-hook request mutation keeps outbound count zero.
53. two Factory runs plus one ordinary role-chat run concurrently: each
    transport uses its injected run/session `ProviderAttemptInFlightDrainPort`,
    Factory drain waits only its own late `to_thread` terminal, role chat is not
    blocked by missing chain anchors, and its session ledger cannot satisfy or
    impersonate either Factory inventory.
54. with more than 4096 attempts, instrumentation proves healthy append touches
    only cursor, one sharded locator, and tail; restart/mismatch performs one
    full rebuild then returns to the fast path; strict paginated Factory audit
    visits each fact/segment a bounded constant number of times and never
    rescans every prefix or rewrites a global idempotency index.
55. after phase-2 qualification but before phase 3, concurrent retention sweep,
    delete, replacement, truncation, or content mutation targets one referenced
    snapshot: valid pins prevent ordinary sweep/delete/replace; forced
    corruption or missing/malformed pin is detected by phase-3 full re-read and
    qualification, so COMPLETED is never persisted from the stale candidate.
56. canonical semantic context is complete but provider translation drops or
    changes the role system marker, one evidence anchor, full tool schema,
    `tool_choice`, or response format while summary counts remain plausible:
    wire-semantic projection/equivalence fails before outbound dispatch.

## FPR-001..010 closure matrix

| ID | Machine invariant | Producer/consumer location | Required RED | Verification |
|---|---|---|---|---|
| FPR-001 | Snapshot persist/re-read/ref failure leaves outbound HTTP/SDK/CLI call count at zero and emits only a non-physical qualification-rejection audit fact | physical-dispatch port plus roles.kernel gate | Architect/PM/CE/Director/QA x sync/stream/provider-retry/fallback OSError | focused transport, engine, and roles.kernel tests |
| FPR-002 | Every primary, provider-internal retry, fallback and structured attempt consumes one physical frozen gate result | provider transports plus KernelOne executors and roles.kernel wiring | mutate required ref/tool/role in every path; no outbound dispatch | provider matrix, fallback characterization, and engine suites |
| FPR-003 | Only provider-visible evidence anchors count; sidecar is expected input only | versioned KernelOne policy/integrity helper plus context.engine qualifier | sidecar complete, frozen messages missing anchor | evidence plus context.engine suites |
| FPR-004 | Metadata role and first-system canonical marker must agree | roles.kernel policy gate plus context.engine independent qualifier | swap Architect/PM/CE/Director/QA system markers | five-role parameterized tests |
| FPR-005 | One v2 snapshot per physical outbound attempt contains the complete canonical semantic request plus exact post-translation/post-mutation redacted HTTP/SDK/CLI representation | KernelOne semantic freeze, infrastructure physical-dispatch port, ContextOS store | full semantic/wire/config drift with unchanged summary; unsafe config | snapshot schema/hash/redaction and transport tests |
| FPR-006 | Five-role strict policy uses a static required-slot table; Factory issues a fenced causal cutoff and independently reconstructs anchors/states from canonical current-run facts, while producer watermarks/authority hashes are consistency-only | Factory evidence-cutoff authority plus `kernelone.events.final_request_evidence` plus context.engine | producer submits stale valid watermark, omits a slot, lies about state/authority, or supplies a self-consistent anchor disagreeing with cutoff/source fact | cutoff/policy/Factory/context tests |
| FPR-007 | Instructor/manual retry and legacy callback cannot dispatch outside the physical gate | structured/provider path routed through dispatch port; governed legacy callback rejected | bypass attempt and multiple internal retries | structured/legacy regression tests |
| FPR-008 | Every actual provider-helper/stream/SDK/CLI retry crosses the physical-dispatch port, receives a unique provider request id, and snapshots the exact outbound representation | provider transports plus roles.kernel gate/lifecycle | two internal retries with payload mutation; missing hook; post-freeze rebuild | provider matrix and physical-attempt tests |
| FPR-009 | Provider-attempt inventory is one run-scoped native segmented logical FactStream with global idempotency, continuous sequence/hash, strict head pagination, namespace guard, and permanent audit pin | KernelOne segmented store plus events.fact_stream public service plus Factory audit | >4096 attempts, rollover/restart/ambiguous fsync, raw namespace append, gap/hash drift | segmented store/FactStream/Factory tests |
| FPR-010 | Every Factory physical snapshot is pinned before outbound dispatch; retention honors the pin and phase 3 re-reads/requalifies the same storage/content/composite hashes before COMPLETED | Context snapshot store/retention plus context.engine plus Factory three-phase gate | phase-2-to-3 sweep/delete/replace/mutate and malformed pin | retention/context/Factory race tests |

## Verification ladder

1. RED producer tests proving snapshot/evidence/system/hash failures prevent
   every provider path from invoking;
2. focused roles.kernel/KernelOne full-v2-snapshot and freeze tests;
3. focused context.engine per-attempt qualification tests;
4. focused Factory current-run inventory/per-attempt hard-gate tests;
5. factory_bench projection/gate tests (consumer only);
6. full roles.kernel, KernelOne LLM, context.engine, and Factory focused suites;
7. Ruff, format, mypy, compileall, Cell/catalog/context-pack hard-fail, and diff
   check;
8. independent specification review, then independent code-quality review.

No real role LLM call or Bench is authorized by this implementation bucket.
After the verifier is green and governance metadata is synchronized, the next
role-involving verification must audit every generated
Architect/PM/CE/Director/QA snapshot with this gate before its result is
accepted.
