# Guarded FactStream Append v1 Blueprint

**Task:** `DEO-1A-GUARDED-APPEND-BLUEPRINT`
**Revision:** `DEO-1A-BLUEPRINT-SECOND-ROUND-AUDIT-REVISION`
**Status:** `closed` for DEO-1A substrate/bootstrap/enrollment scope by the
2026-07-15 closure amendment in Section 11.4. DEO-1B guarded-append consumption
remains pending; DEO-1C remains blocked by DEO-1B, and bench remains
`not_schedulable`.
**Scope:** a generic KernelOne optimistic two-call protocol that appends exactly
one target fact only when a prepared target-and-guard snapshot is unchanged. It
does not create a reservation journal, a long-held lock, a multi-stream
transaction, or a second source of truth.

## 1. Decision

Independent review found a High TOCTOU in the current child-operation admission
shape: a child can observe its parent as OPEN, the parent can close, then the
child can append durably. A caller could then receive `parent_closed` even though
the child mutation fact already exists.

The selected repair is **generic guarded single-target FactStream append**:

- One immutable parent binding owns exactly one shared `operation_stream_token`.
- Child transition: target the operation stream and guard the parent registry
  stream head.
- DEO-3 parent close: target the registry stream and guard the operation stream
  head.
- `read_guarded_fact_snapshot` locks both streams only to produce a strict,
  deeply immutable snapshot and proof, then releases locks.
- TaskRuntime reduces and authorizes that snapshot outside FactStream locks.
- `append_if_guarded_snapshot` reacquires both locks, repeats strict reads, and
  appends exactly one target fact with `fsync` only when both proofs still match.
- Every guarded and legacy `JsonlEventStore` FactStream writer uses the same
  reusable KernelOne `LockedRegularFileSetV1` / `StreamLeaseSet` capability and
  one platform-owned persistent lock authority in a single cutover.
- The authority is isolated by storage identity. `anchor.lock` is separate from
  its realm and persistently binds storage identity, realm device/inode, and
  format revision. A per-runtime auto-created realm is forbidden in v1.

This makes one successful target append the only linearization point. It does
not claim cross-stream atomic write: the guard stream is read-only in the
transaction and receives no companion record.

Two architecture reviewers agreed on 2026-07-15 that per-runtime auto-created
realms retain a split-lock P0; pre/post realm checks do not establish authority.
The current implementation also has one close-versus-lease-I/O blocker and a
failure-taxonomy/public-projection drift. This revision records the controlling
design only. That former pending status is historical and superseded by the
Section 11.4 closure record.

## 2. Authority Topology

```text
TaskRuntime
  reads immutable snapshot -> reduces and authorizes outside locks -> requests commit
       |                                                       ^
       | snapshot proof + semantic event                         | receipt or typed drift
       v                                                       |
FactStream public boundary ------------------------------------+
  owns continuity proof, strict scans, replay, and StreamLeaseSet use
       |
       v
KernelOne platform lock authority
  explicit provision/maintenance: anchor LOCK_EX
  normal acquire: existing anchor LOCK_SH -> canonical stream LOCK_EX
       |
       v
LockedRegularFileSetV1 / StreamLeaseSet
  anchor-held descriptor-only traversal/read/append/fsync/verdict
       |
       v
JSONL streams
  registry stream (parent state)       operation stream (shared token facts)
```

KernelOne does not import TaskRuntime and does not know OPEN/CLOSED semantics.
TaskRuntime does not own files, locks, physical sequence allocation, or durable
append mechanics. A proof is a physical snapshot witness, not a domain
authorization verdict; heads alone are insufficient, so TaskRuntime must reduce
the complete immutable facts returned by prepare.

The platform lock authority, anchor binding, and enrollment of workspace/runtime
physical identities are projections/capabilities derived from existing storage
identity. They are not a second SSoT, stream registry, domain authorization
record, or durable DEO state.

### 2.1 Bootstrap and Enrollment Invariants

`provision_fact_stream_lock_authority` is the one platform bootstrap authority
service for this capability. It is not an HTTP-only service. Every formal
production process entrypoint must delegate to that one service before its first
FactStream I/O. HTTP application lifespan, Director and role CLI startup, and
Factory direct-runtime startup are adapters only: they may select their
workspace and declared stream set, but must not reconstruct provision, binding,
lock-root, or enrollment logic locally. A production caller count greater than
zero does not close this invariant; the entrypoint inventory must prove that
every formal entrypoint delegates before its first query or append.

Authority provision and stream enrollment are separate maintenance operations:

1. Provision creates the authority only when absent and records its durable
   binding, including the canonical runtime-root device/inode identity. It is
   serialized by `anchor.lock` `LOCK_EX`. A repeat against the
   exact valid binding is idempotent and returns the same typed authority
   receipt; a partial, unsafe, or different binding returns
   `lock_authority_provision_conflict` and is never rebound, repaired, or
   rotated by the data path.
2. Enrollment adds a canonical stream-lock key to an already valid authority
   under the same exclusive anchor. Repeating the exact canonical key set is
   idempotent. Concurrent provision/enrollment requests serialize at the
   exclusive anchor and return evidence identifying the storage identity,
   authority binding, canonical sorted enrolled keys, and whether each key was
   already present or created. After the enrollment durability boundary, its
   receipt must include `created`/`already_present`, format revision, runtime
   root, anchor, and realm identities, canonical stream-lock-key evidence, and
   `final_validation=true`. A missing authority returns
   `lock_authority_missing`; an unsafe key returns `stream_lock_invalid`; an
   incompatible authority binding returns its exact anchor or realm code.
3. Ordinary acquire is never a maintenance operation. It cannot create,
   register, enroll, repair, rebind, rotate, or substitute authority state or a
   stream key. An absent dynamic key therefore fails as `stream_lock_missing`.
4. Static platform streams are enrolled by the platform startup adapter before
   its first FactStream I/O. Dynamic DEO streams are enrolled explicitly by the
   TaskRuntime/DEO aggregate owner before first business I/O through this public
   maintenance port. DEO-1A supplies that port only. DEO-1B is the first
   guarded-append consumer and must not recreate locks or a parallel enrollment
   implementation in TaskRuntime.

Provision must validate the authority before and after its durable work: anchor
regular-file type, `st_nlink == 1`, descriptor identity, and the canonical
storage-identity/runtime-root-device-inode/realm-device-inode/format-revision
binding. An existing authority for the same logical runtime-root path but a
different root device/inode is `lock_anchor_binding_mismatch`; provision and
ordinary acquire must fail rather than rebind it. The same final
validation covers root, each traversed ancestor, parent, leaf, anchor, and
realm. `ELOOP` maps precisely to `lock_anchor_invalid` for the anchor,
`lock_realm_binding_mismatch` for the bound realm, `stream_identity_drift` for
root or ancestor traversal, and `unsafe_stream_object` for a leaf. After a file
fsync, any final root, ancestor, parent, leaf, anchor, or realm drift returns
`post_fsync_authority_reconciliation_required`; it emits no success receipt and
does not roll back a possible durable append.

### 2.2 Stateless Bootstrap and Enrollment Invariants

**Supersession of prior singleton-publication wording:** the prior requirement
for an in-process FactStream workspace registry, initialization mutex, and
published workspace singleton is superseded. It must not be implemented merely
to satisfy that stale wording. `bootstrap_fact_stream_workspace` is deliberately
stateless: it retains no process-local completion cache, authority cache,
registry, or singleton.

The persistent anchor and bound realm are the only bootstrap coordination state.
Every explicit bootstrap call re-provisions or revalidates that persistent
physical authority, then enrolls the declared stream set under the applicable
physical locks. Exact concurrent bootstrap requests serialize at the anchor
`LOCK_EX` and are idempotent across threads and independent processes; their
verdicts derive from current durable authority validation, never from a
process-local published object.

An injected bootstrap failure publishes no process state because no completion
or authority state is retained in memory. It may leave only the durable state
whose physical operation reached its durability boundary; a later explicit retry
must re-enter provision/enrollment, revalidate that state under the anchor lock,
and succeed when the persistent binding is valid. A maintenance receipt remains
non-authoritative observational evidence: it neither caches authority nor grants
bootstrap, enrollment, acquisition, write, or TaskRuntime admission capability.

Every formal process adapter delegates to the one bootstrap service before its
first FactStream I/O. This includes HTTP lifespan and formal Director/role CLI
startup, as well as Factory direct-runtime startup. Ordinary TaskRuntime and
FactStream adapter query/append paths remain non-maintenance paths: they must
never lazily bootstrap, provision, enroll, repair, or rebind. Dynamic DEO stream
enrollment is the aggregate owner's explicit maintenance call before first
business I/O; it is a DEO-1A port, while the production guarded-append consumer
belongs to DEO-1B and is not a DEO-1A completion item.

After enrollment has crossed an `fsync` durability boundary, final validation
rechecks the authority binding and runtime root, realm, anchor, and stream
identity evidence while the authority lock remains held. Any such drift returns
`post_fsync_authority_reconciliation_required`, emits no success receipt, and
requires strict reconciliation. A receipt is valid only with the complete
physical evidence described in Section 2.1; a boolean success without that
evidence is not a maintenance verdict.

## 3. Typed Boundary

The public FactStream surface adds these conceptual contracts. Names are
normative for the design, not a claim that they exist today. Both public calls
reject `target_stream == guard_stream` in v1 with
`same_target_and_guard_stream` before lock acquisition.

```text
ReadGuardedFactSnapshotCommandV1
  workspace: canonical absolute workspace identity
  target_stream: StreamRefV1
  guard_stream: StreamRefV1
  strict_integrity: Literal[True]

GuardedFactSnapshotV1
  workspace: canonical absolute workspace identity
  target_stream: StreamRefV1
  guard_stream: StreamRefV1
  strict_format_revision: str
  target_head_seq: int
  guard_head_seq: int
  target_facts: deeply immutable tuple of deeply immutable facts
  guard_facts: deeply immutable tuple of deeply immutable facts
  target_facts_digest: sha256 canonical exact target facts
  guard_facts_digest: sha256 canonical exact guard facts
  proof: GuardedFactSnapshotProofV1

GuardedFactSnapshotProofV1
  workspace, target_stream, guard_stream
  storage_identity
  strict_format_revision
  target_head_seq, guard_head_seq
  target_presence, guard_presence
  root_identity, target_parent_identity, guard_parent_identity
  target_facts_digest, guard_facts_digest
  continuity_digest: sha256 canonical bound proof fields

AppendIfGuardedSnapshotCommandV1
  snapshot_proof: GuardedFactSnapshotProofV1
  event: CanonicalFactEventV1
  idempotency_key: str
  semantic_digest: sha256 canonical intended event semantics
  strict_integrity: Literal[True]
  durability: Literal["fsync"]

LockAuthorityBindingV1
  storage_identity
  realm_device, realm_inode
  format_revision

ProvisionLockAuthorityCommandV1
  platform_lock_root
  storage_identity
  format_revision
  maintenance_reason

EnrollStreamLockKeysCommandV1
  platform_lock_root
  storage_identity
  logical_paths
  maintenance_reason

AcquireStreamLeaseSetCommandV1
  platform_lock_root
  storage_identity
  logical_paths
  monotonic_timeout_seconds

LockedRegularFileSetV1
  storage_identity
  anchor_fd: existing anchor.lock held LOCK_SH
  bound_realm_fd
  ordered_logical_paths
  root_dirfd, parent_dirfds, leaf_fds
  acquired_lock_keys
  monotonic_deadline
  lifecycle: ACTIVE | CLOSING | CLOSED

StreamLeaseSet
  FactStream-scoped facade over LockedRegularFileSetV1
  strict_snapshot(), append_existing(), create_and_append(), fsync_parents()
```

FactStream computes `continuity_digest` over canonical UTF-8 serialization of the
bound proof fields. It is deliberately non-authenticating: it detects malformed
or accidentally modified proof content but is not a MAC, signature, capability,
or security boundary. The digest must validate before any proof-controlled path
or stream resolution. The proof binds canonical workspace, both stream refs,
strict format/schema revision, both exact head sequences, and full canonical
digests of the exact immutable facts. Commit derives expected heads solely from
the validated proof; it accepts no independent caller-provided head values.

Strict recomputation from current descriptor-read facts is the physical
authority. A caller that rewrites proof content and recomputes the non-secret
digest gains no continuity: commit compares the current strict snapshots against
every bound field. DEO-4's TaskRuntime-only consumer fence remains architecture
control, not a hostile-caller security boundary.

`semantic_digest` is separately canonicalized for idempotency comparison and
excludes generated volatility: `recorded_at`, `event_id`, `seq`, append
timestamp, and occurrence time. It includes all domain-significant event fields
and identity. Full snapshot digests remain exact physical-fact digests.

## 4. Locking and Algorithm

The v1 authority lives under one platform-owned persistent lock root, outside any
mutable workspace or runtime stream directory and isolated by storage identity:

```text
<platform-lock-root>/<storage-identity-key>/
  anchor.lock          # authority lock and immutable binding record
  realm/               # persistent per-stream lock files
```

`anchor.lock` is not inside `realm/`. Its canonical UTF-8 binding records storage
identity, realm device/inode, and authority format revision. Anchor, realm, and
per-stream lock files are persistent and are never removed by normal release.
The platform lock authority and physical-identity enrollment remain capabilities/
projections, not another state SSoT.

Provisioning and acquisition are separate APIs:

1. Explicit platform bootstrap/provision or offline maintenance is the only path
   allowed to create authority objects or enroll canonical stream-lock keys. It
   holds `anchor.lock` with `LOCK_EX`, writes a new binding only for initial
   provision, and fsyncs created files/directories. Repeating provision against
   an exact valid binding is idempotent; any different or partial existing
   binding fails closed and cannot be rebound by v1.
2. Ordinary acquire opens an already provisioned anchor with no-follow regular-
   file checks, takes `LOCK_SH`, validates the full binding, opens the existing
   realm, requires its device/inode to equal the anchor binding, and opens only
   pre-enrolled canonical stream-lock files. It creates no authority, realm, or
   stream-lock object.
3. Missing anchor/realm, symlink, unsafe type, storage mismatch, format mismatch,
   realm identity mismatch, or missing/unsafe stream-lock key fails closed.
   Acquire never creates, rebinds, repairs, rotates, or substitutes authority
   state.
4. v1 has no online realm rotation and no anchor lock upgrade/downgrade. Any
   future rotation requires a new versioned offline protocol.
5. While the anchor remains `LOCK_SH`, canonical per-stream lock keys derived
   from storage identity plus NFC/casefold logical path are taken `LOCK_EX` in
   byte order under a monotonic bounded deadline and released in reverse order.

Pre/post realm checks are defense-in-depth only; without the continuously held
anchor they are not lock authority. The anchor remains held through descriptor
traversal, strict scans, append/create, file fsync, parent-directory fsync, final
authority/identity validation, and the success-or-failure receipt verdict. It is
released before TaskRuntime performs domain reduction or tool work.

In one cutover, every `JsonlEventStore` FactStream writer, including legacy API
callers, must acquire this authority. There is no permanent dual-lock split and
no runtime bridge in the target design. If deployment temporarily requires a
bridge, it is bounded to migration, acquires anchor and both old/new stream locks
in one documented global order, and is deleted before DEO-1A closure.

After lock acquisition, the capability starts from a trusted runtime/root
directory descriptor whose enrolled physical identity is revalidated. It walks
each root-relative component with `openat`-equivalent descriptor operations using
`O_DIRECTORY|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`. A leaf is opened relative to its
verified parent with `O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC` plus read or `O_APPEND`
mode. Every opened object is checked with descriptor metadata: required regular
file type, `st_nlink == 1`, expected device/inode continuity, and storage/root/
parent identity. Special files, symlinks, hard links, or identity drift fail
closed. Reads, writes, and `fsync` use only held descriptors; no validated path is
later reopened by name.

### Prepare: `read_guarded_fact_snapshot`

1. Open the provisioned authority, take anchor `LOCK_SH`, validate its binding,
   then acquire target and guard `LOCK_EX` locks in canonical key order within
   the monotonic deadline.
2. Traverse from the trusted root descriptor and strictly scan both streams
   through held descriptors. Torn tail, middle corruption,
   unknown strict schema, non-adjacent sequence, or invalid head fails closed.
3. Freeze complete facts into deeply immutable values, compute exact canonical
   fact digests and the bound proof, perform final authority/descriptor identity
   validation, decide the snapshot verdict, then release descriptors, stream
   locks, and anchor.
4. Return the snapshot verdict. No domain reducer, caller-supplied execution,
   external call, tool, network request, or process runs while locks are held.

Prepare represents an absent target as an immutable empty strict snapshot. It
requires its canonical authority lock key to have been explicitly enrolled
before prepare. Prepare must not create the target data file, sibling data lock,
cursor, stream directory, or any other data-target artifact before both target
and guard snapshots are accepted at commit.

TaskRuntime performs parent/operation reduction, identity checks, state
transition validation, and authorization only after prepare returns. A denied
snapshot is discarded without a commit call.

### Commit: `append_if_guarded_snapshot`

1. Recompute and validate the non-authenticating `continuity_digest` before using
   any proof field for path, stream, storage, or lock resolution. Malformed or
   inconsistent proof content fails with `snapshot_proof_invalid`.
2. Resolve the validated logical refs through storage identity, open the existing
   authority, take anchor `LOCK_SH`, validate its binding, acquire canonical
   stream `LOCK_EX` locks, traverse from the trusted root descriptor, and
   strictly rescan both through held descriptors.
3. Locate the idempotency key in target facts. An exact semantic match selects
   its original receipt as the replay verdict before proof drift checks. Perform
   final authority/descriptor validation while the anchor remains held, then
   return that original receipt without comparing prepared snapshot drift. The
   same key with different semantics returns `idempotency_semantic_conflict`.
4. Recompute target and guard proofs from the current exact facts and descriptor
   identities. Compare every bound field and both fact digests. Expected heads
   come only from the proof.
5. Only after both snapshots/proofs match may storage mutate. For an existing
   target, append through the already-held `O_APPEND` regular-file descriptor,
   then `fsync` it. For an absent target, create the leaf relative to the verified
   parent with `O_CREAT|O_EXCL|O_NOFOLLOW|O_NONBLOCK|O_CLOEXEC`, validate regular
   file/link/device/inode properties, write and `fsync` the file, then `fsync` the
   parent directory before returning a receipt.
6. While anchor and stream locks remain held, revalidate authority, root, parent,
   and leaf identities after durability and before the receipt verdict. Only then
   construct success and release descriptors/locks.

If file or parent fsync completed but final authority validation drifts, commit
must not emit a success receipt and must not roll back or compensate. It returns
`post_fsync_authority_reconciliation_required` with stage and identity evidence;
the only next step is strict replay/reconciliation under a newly acquired valid
authority.

There is no guard-stream write or external call under lock, including runtime.v2
publication. No internal retry is permitted. On drift, TaskRuntime performs
bounded re-prepare and re-authorization and never reuses stale authorization.

The supported guarantee assumes an owned local filesystem and cooperating
Polaris writers that honor the platform lock authority. Portable POSIX APIs
cannot make ancestor-name stability absolute against a privileged or same-UID
actor concurrently renaming/replacing ancestors while deliberately ignoring
locks. Detected root/parent identity drift fails closed; environments that cannot
meet the ownership/cooperation assumptions are unsupported and must fail closed.
Windows v1 guarded append is unavailable unless a reparse-safe handle-relative
backend provides equivalent traversal, identity, lock, append, and directory
durability guarantees.

### Lease lifecycle and close

`LockedRegularFileSetV1` owns one lifecycle mutex and the state machine
`ACTIVE -> CLOSING -> CLOSED`. Every `StreamLeaseSet`/lease descriptor operation,
including strict read, append, create, fsync, and identity validation, holds that
same owner mutex for the complete descriptor-I/O operation and requires `ACTIVE`.

`close()` acquires the lifecycle mutex, returns unchanged when already `CLOSED`,
sets `CLOSING`, atomically detaches every owned descriptor/lock handle from the
owner into one local close batch, unlocks/closes exactly that detached batch in
reverse ownership order, then records `CLOSED`. No lease operation can overlap
detach or close. Repeated or concurrent close is idempotent, and because the
owner no longer retains detached integer descriptors, it cannot later close an
unrelated descriptor that reused the same OS fd number. Close errors are typed
cleanup evidence; they never restore `ACTIVE` or authorize further I/O.

The anchor is included in the detached close batch and is released last. The
lifecycle mutex and anchor never span TaskRuntime domain reduction, tool work, or
other external execution.

## 5. State and Linearization

| Operation | Target | Guard | Prepare then outside-lock decision | Commit result | Losing race result |
| --- | --- | --- | --- | --- | --- |
| Child `ABSENT -> INTENT_COMMITTED` or later operation transition | shared operation stream | parent registry stream | Parent OPEN; binding/token/operation/state/fence authorize prepared facts | `fsync` of one operation fact if both proofs match | `target_snapshot_drift`, `guard_snapshot_drift`, or typed denial; no append |
| Child exact retry | shared operation stream | parent registry stream | None | Original append's `fsync` | Original receipt even after proof drift or parent close |
| Parent close in DEO-3 | parent registry stream | shared operation stream | Parent may close and operation facts are terminal/eligible | `fsync` of one registry close fact if both proofs match | drift or typed `open_operations`; no registry append |
| Same idempotency key, different event semantics | target stream | any | Canonical semantic digest differs | none | `idempotency_semantic_conflict`; no append |

If parent close commits first, the child's registry proof drifts and its commit
writes nothing. If a child commits first, the parent operation proof drifts and
must re-prepare, observe the exact child fact, then authorize or deny close from
that state. A sibling operation transition similarly invalidates target proof for
a competing child. This is proof revalidation, not TaskRuntime code under lock.

## 6. Failure and Crash Semantics

| Code | Meaning | Durable effect |
| --- | --- | --- |
| `guarded_fs_capability_unavailable` | Platform lacks the required descriptor/reparse-safe backend | None |
| `lock_authority_missing` | Storage identity has no explicitly provisioned platform authority | None |
| `lock_authority_provision_conflict` | Provision found a different, partial, or unsafe existing authority and will not rebind it | None; offline reconciliation required |
| `lock_anchor_invalid` | Anchor is missing required format, unsafe, linked, or not a regular file | None |
| `lock_anchor_binding_mismatch` | Anchor storage identity or format revision differs from acquire request | None |
| `lock_realm_missing` | Bound persistent realm is absent | None |
| `lock_realm_binding_mismatch` | Realm device/inode differs from the anchor binding | None |
| `stream_lock_missing` | A canonical stream-lock key was not explicitly enrolled | None |
| `stream_lock_invalid` | A canonical stream-lock object is linked, non-regular, wrongly named, or otherwise unsafe | None |
| `lock_acquisition_timeout` | Anchor or stream lock exceeded the monotonic deadline | None |
| `lock_acquisition_failed` | Advisory lock acquisition failed for a non-timeout reason | None |
| `stream_lease_closing` | Lease-set lifecycle is `CLOSING`; new I/O is rejected | None |
| `stream_lease_closed` | Lease-set lifecycle is `CLOSED`; new I/O is rejected | None |
| `stream_lease_close_failed` | Detached descriptor/lock cleanup failed; lifecycle remains `CLOSED` | None |
| `stream_parent_missing` | Required root-relative parent component is absent | None |
| `unsafe_stream_object` | Leaf is a symlink, FIFO/device/socket, non-regular file, or otherwise unsafe | None |
| `hard_link_rejected` | A stream or authority file has `st_nlink != 1` | None |
| `stream_identity_drift` | Trusted root, parent, leaf device/inode, or enrolled identity changed before mutation | None |
| `strict_stream_corruption` | Public category for a strict parser denial | None; typed evidence retains the underlying strict reason |
| `snapshot_proof_invalid` | Proof is malformed, unbound, wrong revision, or its continuity digest does not match its canonical fields | None |
| `snapshot_proof_tampered` | Bound proof content changed without a matching continuity digest | None |
| `target_snapshot_drift` | Locked target proof differs from prepared proof | None |
| `guard_snapshot_drift` | Locked guard proof differs from prepared proof | None |
| `idempotency_semantic_conflict` | Existing target key has a different canonical semantic digest | None |
| `same_target_and_guard_stream` | v1 command uses identical target and guard streams | None |
| `append_write_failed` | Write failed before a completed durability boundary | Strict replay determines whether any torn tail exists |
| `file_fsync_reconciliation_required` | File fsync outcome is ambiguous | No success; no rollback; strict replay required |
| `parent_fsync_reconciliation_required` | First-create file fsync completed but parent-directory fsync did not produce a trusted verdict | No success; no rollback; strict replay required |
| `post_fsync_authority_reconciliation_required` | File/parent durability completed, then final anchor/realm/lease validation drifted | No success; no rollback; strict replay required |

This table is the single normative v1 taxonomy for KernelOne sourcing and the
FactStream public boundary. Strict parser failures project public
`strict_stream_corruption` together with typed evidence
`strict_reason=torn_tail|sequence_violation|middle_corruption|unknown_schema`.
The public category is stable for callers; the evidence reason is mandatory and
must never be discarded or converted to a generic string. Recognized failures
preserve their exact code through `FactStreamError`; generic
`locked_regular_file_failed`, `guarded_snapshot_prepare_failed`, or
`guarded_append_failed` fallback must not replace a known code. The current
implementation/public mismatch remains an open High item until code, public
contracts, tests, and this table agree.

Only recognized, typed operational failures may be translated at the public
boundary. An unexpected programming or invariant exception is not compressed
into a generic public error code: it propagates with its original exception
chain, produces no success receipt, and is separately observable for correction.
This prevents an unknown internal failure from being misrepresented as a stable
guarded-operation verdict.

A completed fsync is necessary but not sufficient for a success receipt. Success
requires final authority/identity validation and a receipt verdict while anchor
and stream locks remain held. Crash or any ambiguity after mutation is recovered
through exact idempotent strict replay. A partial/torn final record is never
treated as committed and blocks authorization pending explicit repair/quarantine.
The design never infers rollback or writes a compensating guard fact.

## 7. Complexity and Performance

Prepare and commit each perform strict `O(T + G)` scan work for target and guard
facts and use `O(T + G)` transient immutable memory. Commit is two scans across
the whole optimistic operation; lock count is at most two central keys and
descriptor traversal is `O(P)` in path components. Exact idempotency lookup is
scan-bounded; an optional index cannot become authorization truth. The existing
configurable evidence target remains p95 `<100 ms`, p99
`<500 ms` with four processes, and `<8 MiB` incremental memory; these are
non-hard-CI observations, not timing gates.

## 8. Migration and Cutover

1. Keep DEO-1A `pending`. Historical 121-pass evidence applies only to the
   pre-anchor substrate and cannot close this revision.
2. Add one explicit platform bootstrap/maintenance service for the persistent
   anchor/realm binding and canonical stream-lock enrollment. Every production
   entrypoint delegates before its first FactStream I/O; normal acquire must
   never provision, enroll, or mutate authority.
3. Complete reusable KernelOne `LockedRegularFileSetV1` / `StreamLeaseSet` with
   anchor-held acquisition, lifecycle mutex/state, atomic fd detach, descriptor
   safety, and the single public failure taxonomy.
4. In one cutover, migrate every `JsonlEventStore` FactStream writer, including
   legacy FactStream API callers, to the platform authority. Mixed legacy/guarded
   calls must serialize there; no permanent dual-lock split is accepted.
5. Expose the dynamic stream enrollment port in DEO-1A, then add the TaskRuntime
   public guarded-append consumer in DEO-1B. TaskRuntime/DEO aggregate ownership
   performs explicit maintenance enrollment before first dynamic business I/O,
   prepares, reduces/authorizes outside locks, and boundedly re-prepares after
   drift. It must not rebuild KernelOne locking. DEO-1B remains blocked until
   this step and the multi-process race suite pass; `91/464` and hygiene-green
   status do not close it.
6. DEO-1C remains read-only and `enforcement="not_enabled"`; it must not use
   this port to settle, close, or authorize effects.
7. DEO-3 alone adds parent-close use of the port, receipt closure, recovery, and
   terminal admission. It targets registry and guards the operation head.
8. DEO-4 removes/fences direct FactStream append/CAS paths that can bypass the
   guarded port and allowlists only TaskRuntime as the DEO guarded-commit
   consumer. This is architecture control, not a security boundary.

Graph-facing synchronization for
`cells/events/fact_stream/{cell.yaml,README.agent.md,generated/context.pack.json}`
is separate governance work after code and public contracts stabilize. It is not
implementation evidence by itself and must not be done speculatively, but fresh
metadata/context-pack evidence is required before DEO-1A exit. Bench remains
`not_schedulable`; no calendar or success claim is permitted before DEO-1A
through DEO-4 exit evidence exists.

### Scoped writer inventory

This bucket covers only `JsonlEventStore` and the FactStream sourcing/public
paths that read or write its event streams. Inventory and mixed-writer tests must
enumerate those constructors and call paths explicitly.

`polaris/kernelone/events/io_events.py`, `polaris/kernelone/fs/jsonl/*`, their
generic `.seq`, `.seq.lock`, and `.lock` mechanisms, and unrelated repository
JSONL writers are outside this FactStream/`JsonlEventStore` bucket. Their presence
does not prove a FactStream split-lock defect, and this blueprint does not order a
repository-wide JSONL migration.

### Pending second-round exit plan and current evidence

ETA is `pending`; there is no calendar estimate. The remaining bounded items
are:

1. Inventory every formal production entrypoint and make each delegate to the
   single bootstrap authority service before first FactStream I/O.
2. Stabilize static platform-stream catalog enrollment receipts and evidence.
3. Expose and test explicit dynamic DEO stream maintenance enrollment without
   allowing ordinary acquire to mutate authority.
4. Complete pre/post authority validation, exact `ELOOP` mapping, and the
   no-success post-fsync reconciliation verdict.
5. Remove generic public guarded-operation fallbacks and preserve strict parser
   evidence below `strict_stream_corruption`.
6. Close startup-boundary evidence: optional NATS `OSError` handling, cleanup
   failure boundaries, and FactStream Cell metadata/context-pack freshness.
7. Prove the stateless bootstrap replacement invariant: no process-local
   completion/authority cache, registry, or singleton exists; persistent
   anchor/realm state is the only coordination state; exact concurrent bootstrap
   serializes idempotently under physical locks; and injected failure leaves no
   process state while a later explicit retry revalidates and succeeds.
8. Bind existing authority to runtime-root device/inode, complete enrollment
   receipts with final physical proof, and reject same-path inode replacement.
9. Preserve application-body exceptions during shutdown while settlement
   `OSError` and every remaining cleanup action are recorded and completed.
10. Add a governance gate that compares FactStream root exports, `cell.yaml`,
    `README.agent.md`, generated context pack, and global graph catalog; close
    the `directed_effect_operation` mypy contract deficit separately from the
    DEO-1B guarded-append consumer.

### Historical evidence, superseded by Section 11.4

- Historical focused guarded/FactStream/KernelOne evidence: **328 passed**.
- Historical broad evidence: **611 passed, 3 external baseline failures**.
- Open blocker: lease I/O can race `close()` without one owner lifecycle mutex,
  state machine, and atomic descriptor detach.
- Superseded wording: the former registry-singleton publication blocker does
  not authorize a new process-local registry. The open proof obligation is the
  stateless replacement invariant: anchor/realm-only coordination, idempotent
  physical-lock serialization, and injected-failure retry with no process state.
- Open High: runtime-root identity binding, enrollment final-proof receipts,
  formal CLI delegation, shutdown exception precedence, exact public error
  projection, Cell-surface governance, and the 35-error
  `directed_effect_operation` mypy deficit remain independently audited gaps.
- Open residuals: TaskRuntime snapshot KFS bypass and three external filesystem
  broad-baseline failures. These are tracked outside DEO-1A and are not
  guarded-substrate closure evidence.

Separate Medium item: TaskRuntime snapshot persistence currently bypasses KFS,
leaving the full KernelOne release gate at **393 passed, 1 skipped, 1 failed**.
That failure is not fixed by this documentation task, is not guarded-append
substrate evidence, and cannot be used to close DEO-1A or DEO-1B.

## 9. Required Test Gates

The implementation cannot cut over on unit happy paths alone. Required evidence:

1. Prepared facts are deeply immutable, including nested maps/lists; mutation
   attempts cannot alter their canonical proof.
2. Malformed, rebound, and bit-tampered continuity proofs fail before any
   proof-controlled resolution; tests do not claim authentication.
3. Target and guard snapshot drift each fail closed with no target append.
4. An intervening sibling operation transition invalidates target proof and
   requires TaskRuntime re-prepare/re-authorization.
5. Exact replay after target/guard drift returns the original receipt; same key
   with different semantic content returns typed conflict.
6. Strict corruption cases deny prepare and commit and preserve evidence.
7. Explicit provision creates/binds anchor and realm under `LOCK_EX`; ordinary
   maintenance enrolls stream-lock keys under the same exclusive anchor. Ordinary
   acquire opens only existing authority and enrolled keys under anchor `LOCK_SH`
   and cannot create, rebind, rotate, upgrade, or downgrade them.
8. Deterministic and cross-process close-versus-child races prove neither side
   ignores the other; child-after-winning-close never commits.
9. Injected write/flush/fsync failures prove no emitted success receipt and no
   partial record accepted by a later strict scan.
10. Root/parent rename and replacement, symlink, FIFO/device/socket, hard-link,
    inode/device drift, and special-file cases fail closed through descriptors.
11. Drift and proof failure create no absent target; first creation proves
    `O_CREAT|O_EXCL`, file `fsync`, and parent-directory `fsync` ordering.
12. Provision pre/post validation proves anchor regular/nlink/identity and realm
    binding; exact `ELOOP` mapping, anchor/realm displacement, and final root,
    ancestor, parent, leaf, anchor, or realm drift fail closed while the anchor
    remains held through the final verdict.
13. Lifecycle tests cover lease-I/O-versus-close, concurrent/repeated close,
    `ACTIVE/CLOSING/CLOSED`, atomic fd detach, and forced fd-number reuse.
14. Post-fsync authority drift emits no success, performs no rollback, returns
    `post_fsync_authority_reconciliation_required`, and strict-replays.
15. Mixed legacy/guarded `JsonlEventStore` writers serialize through the same
    authority; scoped inventory excludes `io_events.py` and `fs/jsonl/*`.
16. KernelOne and FactStream public tests assert exact parity with the normative
    failure taxonomy, project strict `torn_tail` and `sequence_violation`
    evidence under `strict_stream_corruption`, and reject generic-code
    replacement for known failures.
17. Windows returns `guarded_fs_capability_unavailable` unless the equivalent
    reparse-safe backend is active.
18. Architecture tests prove KernelOne has no TaskRuntime import, no lock-held
    domain execution path exists, and TaskRuntime is the DEO commit allowlist.
19. Bootstrap has no process-local completion/authority cache, registry, or
    singleton. Exactly 64 concurrent threads and 64 independent processes issue
    the same explicit bootstrap and prove idempotent serialization under the
    persistent anchor `LOCK_EX`. An injected bootstrap failure publishes no
    process state; a later explicit retry re-provisions/revalidates persistent
    authority and succeeds. These tests also prove that a maintenance receipt is
    non-authoritative and ordinary FactStream I/O never lazy-bootstraps.
20. Existing authority with an unchanged logical runtime-root path but changed
    root device/inode fails `lock_anchor_binding_mismatch`; enrollment receipts
    prove `created`/`already_present`, format revision, root/anchor/realm
    identities, canonical keys, and final validation after `fsync`.
21. Formal Director and role CLI startup delegate to the single bootstrap
    service before I/O, while ordinary TaskRuntime and adapter I/O prove they do
    not lazily bootstrap. Dynamic enrollment remains explicit maintenance.
22. Lifespan shutdown tests inject settlement `OSError` and an application-body
    error, prove every cleanup action is attempted, and prove cleanup does not
    replace the application-body exception. Surface-governance tests reject
    drift among exports, manifest, README, context pack, and graph catalog.

## 10. Rejected Alternatives

| Alternative | Rejection reason |
| --- | --- |
| Lock-held domain execution | Couples KernelOne physical locks to TaskRuntime execution and makes bounded lock ownership unenforceable. |
| Per-runtime auto-created realm | Different runtimes can provision distinct authority for one storage identity and split cooperating writers. |
| Pre/post realm checks without held anchor | Checks detect some drift but do not prevent authority replacement between checks. |
| Ordinary acquire create/rebind/rotation | Lets a data-path caller silently replace authority; v1 requires explicit exclusive maintenance and no online rotation. |
| Ordinary acquire creates a missing stream-lock key | Mutates the realm under shared acquisition and permits different writers to observe different lock objects; keys require explicit exclusive enrollment. |
| Process-local bootstrap registry or singleton | Reintroduces cache-publication state without adding physical authority; the stateless service must revalidate persistent anchor/realm state on every explicit call. |
| Path-check then reopen | Reintroduces symlink, special-file, and parent-replacement races after validation. |
| Reservation journal | Introduces a second durable protocol and cleanup/recovery authority without solving the guard-to-append atomicity directly. |
| Long-held lock around tool work | A local lock is not cross-process; a file lock must not span a command that can run for 300 seconds. |
| Multi-stream transaction | Adds a distributed commit/recovery problem when one target fact plus a locked guard supplies the required serialization. |
| Heads-only guard | Cannot establish domain state, identity, authorization, or semantic transition correctness; exact locked facts must be reduced by TaskRuntime. |
| TaskRuntime implementation inside KernelOne | Violates KernelOne's platform-neutral dependency direction and collapses physical and domain ownership. |

## 11. Exit Criteria

DEO-1A guarded append closes only when the two-call continuity contracts,
platform anchor authority, provision/acquire separation, descriptor-only physical
revalidation, lifecycle-safe close, scoped all-`JsonlEventStore` cutover, exact
public taxonomy, replay semantics, formal-entrypoint delegation, static and
dynamic enrollment evidence, startup cleanup/NATS boundary handling, Cell
metadata/context-pack/global-graph freshness, stateless bootstrap proof,
runtime-root binding, enrollment receipt proof, shutdown exception precedence,
and tamper/path/drift/race/crash evidence are complete. Stateless bootstrap
proof requires no process-local completion/authority cache or singleton,
persistent anchor/realm-only coordination, 64-thread and 64-independent-process
idempotent serialization under physical locks, injected-failure/no-process-state
retry, non-authoritative maintenance receipts, and no lazy bootstrap from
ordinary I/O. The historical 328-focused and 611/3 broad snapshots did not
close it; Section 11.4 records the current closure.
It does not close DEO-1B, DEO-3, or DEO-4.
Until their respective consumer, close/recovery, and legacy-removal gates are
complete, the truthful system status is an open P0 path and `not_schedulable`
bench.

### 11.1 2026-07-15 Closure Amendment

This amendment is a DEO-1A closure condition. It tightens the FactStream public
boundary and its governance evidence; it does not authorize a DEO-1B guarded
append consumer or a DEO-1C read-only enforcement change.

1. `FactStreamMaintenanceReceiptV1` is non-authoritative observational
   evidence. A caller can construct a DTO, but construction grants no
   capability: no write path, bootstrap path, enrollment path, or TaskRuntime
   admission may consume a receipt as authorization. Any authoritative decision
   must be revalidated by the platform bootstrap/maintenance service against
   current physical state while it holds the applicable anchor/realm lock. This
   boundary must not rely on forged HMACs, private constructors, or equivalent
   representational secrecy.
2. `FactStreamError.details` is recursively detached at the public boundary.
   Nested mappings, sequences, and supported scalar containers must not share
   mutable state with an internal exception or with any caller-supplied nested
   value; a shallow top-level `dict()` copy is insufficient.
3. A durable fact append that succeeds while realtime publish returns `false`
   remains an append success. The result must carry typed, non-`FactStream`
   publish-projection evidence that records the failed realtime projection.
   It must neither become a `FactStreamError` nor discard the durable append and
   publish-failure evidence.
4. The FactStream public facade/root has exactly 37 exports. The complete set
   must be projected explicitly as `public_surface.exports` in the manifest,
   global catalog, generated context pack, and `README.agent.md`.
   `public_contracts` remains a classification of contract symbols only and is
   not a substitute for the root export list. The mechanical governance gate
   validates both dimensions independently: exact root-export parity and the
   contract-symbol classification.
5. A cycle allowlist records the baseline internal edges for every allowed SCC.
   An observed SCC is converged only when both its members and its internal
   edges are subsets of that baseline; any new internal edge fails the gate,
   even when the SCC member set is unchanged. A members-only subset exemption is
   prohibited.
6. Tests must not monkeypatch the stdlib process-global `os.name`. Platform
   capability behavior is tested through a module-owned helper or constant seam
   so concurrent tests and unrelated runtime code retain their real platform
   identity.
7. This amendment explicitly supersedes every older DEO-1A
   singleton-publication or registry-initialization closure phrase. The
   normative replacement is stateless bootstrap: no process-local
   completion/authority cache or singleton; the persistent anchor/realm is the
   only coordination state; exact bootstrap serializes idempotently under
   physical locks; injected failure publishes no process state and a later
   explicit retry succeeds by revalidating persistent authority. Maintenance
   receipts remain non-authoritative, and ordinary FactStream I/O must never
   lazy-bootstrap.

Closure evidence must include tests for nested mutation detachment, publish
returning `false`, paired public-export removal, and a new edge inside an old
allowlisted SCC. It must also include the full physical test suite, 64-thread
and 64-independent-process concurrency evidence, the injected bootstrap
failure/retry proof, and the hard governance gate. Those bootstrap tests must
prove that no process-local completion/authority state is published, receipts
remain non-authoritative, and ordinary I/O does not lazy-bootstrap. These are
required DEO-1A evidence, not a replacement for the separate DEO-1B consumer
or DEO-1C enforcement gates.

### 11.2 2026-07-15 Final Blockers Amendment

This amendment records final pre-implementation closure conditions from the
audited facts. It does not mark DEO-1A closed, does not change historical test
counts, and does not authorize a guarded-append consumer, receipt authority, or
lazy bootstrap.

1. **Directory durability is an explicit authority-provision proof.** Provision
   must record every directory it newly creates in the platform-lock-root,
   storage-identity, authority, and realm chain. Before it can emit a success
   receipt, it must fsync every recorded created directory and that directory's
   containing parent, deduplicated only after preserving descendant-to-ancestor
   durability order. The proof must identify the created-directory set and the
   completed fsync order. A pre-existing platform root, storage-identity root,
   authority directory, or realm is opened and physically validated; the
   provision path must not silently recreate it. Once any file or directory
   fsync durability boundary has been crossed, every later failure returns typed
   reconciliation-required evidence, emits no success receipt, and leaves the
   durable state for strict reconciliation rather than rollback claims.
2. **Dynamic DEO enrollment has one explicit owner.** Ordinary TaskRuntime and
   FactStream reads and writes are non-maintenance paths and must never call
   enrollment. The DEO aggregate/application owner performs a distinct,
   idempotent maintenance enrollment before the first dynamic DEO business I/O.
   Until DEO-1B connects that owner flow, an unenrolled DEO stream fails closed
   with the applicable missing-stream-lock evidence. In particular,
   `_read_stream -> _enroll_dynamic_stream` is prohibited; constructor-time,
   property-time, lazy, retry, or equivalent implicit enrollment is also
   prohibited. A maintenance receipt remains observational only and cannot
   authorize later I/O.
3. **Concurrency evidence requires a genuine simultaneous release.** The
   64-thread proof and the 64-independent-process proof each require all workers
   to publish an armed acknowledgement before one shared release barrier opens.
   The process harness must use an OS-supported shared release-generation or
   broadcast primitive that wakes the armed participants from one coordinated
   release; sequential stdin writes to children are not a barrier. Each verdict
   set must preserve the stable storage identity, root/anchor/realm identity,
   canonical lock-key, and format-revision proofs. From a clean, physically
   contested absence, the exact evidence is one `created` verdict and the
   remaining `already_present` verdicts for each object whose creation is
   physically meaningful; pre-existing-object cases instead prove the exact
   all-`already_present` result. Scheduler timing, aggregate counts without
   per-worker proofs, or sequential launch evidence cannot satisfy this gate.
4. **Process-spawn evidence belongs outside the FactStream Cell effect surface.**
   Production Cell effect manifests describe runtime effects only. The
   independent-process barrier test and its subprocess-spawn evidence belong in
   a cross-Cell integration or governance test outside the FactStream Cell-owned
   test path. That placement must not widen production `effects_allowed` solely
   for test harness subprocesses, and the production effect gate remains
   unchanged.
5. **Public-service tests bootstrap explicitly.** Every FactStream public
   service test establishes its workspace through the explicit bootstrap or
   maintenance command before ordinary I/O. Tests must not use an autouse
   monkeypatch, fixture side effect, or `_locked_streams` interception that
   provisions authority or enrolls streams from an ordinary read/write path.
   Tests must separately prove that an unbootstrapped workspace and an
   unenrolled dynamic DEO stream fail closed without mutating authority state.

The required test evidence for this amendment includes directory-creation
failure injection after each fsync boundary, complete created-directory fsync
ordering proof, explicit dynamic-owner enrollment and unenrolled DEO rejection,
the two coordinated 64-worker verdict sets, a production-effect-manifest
non-expansion assertion, and per-test explicit workspace bootstrap. These
remain pending closure evidence and do not alter the status stated in Section
11.

### 11.3 2026-07-15 Last Boundaries Amendment

This amendment adds remaining implementation and proof boundaries to the latest
non-closing DEO-1A decision. It does not close DEO-1A, does not update any test
count, and does not authorize an implicit maintenance path, a guarded-append
consumer, or a change to the stated status.

1. **Stream first-create durability retains the full ancestor proof.** When a
   stream append creates one or more previously absent logical-path ancestor
   directories, the lease must retain descriptor-backed records for every
   created directory and its containing parent while the lease owns the
   descriptors. After the new stream file has been written and file-fsynced, it
   must fsync every recorded created directory and every containing parent in
   descendant-to-ancestor order, deduplicating physical directories only after
   preserving that order. File fsync plus the immediate stream parent fsync is
   insufficient. The reconciliation evidence must contain the created-directory
   set, descriptor-validated identities, and the exact completed fsync order.
   Once the file or any directory fsync boundary is entered, every subsequent
   write, directory fsync, identity validation, or release failure returns
   reconciliation-required evidence and emits no append success receipt. It
   makes no rollback claim; strict replay is required to reconcile a possibly
   durable record.
2. **Create-race evidence is controlled, not scheduler-dependent.** The
   `O_CREAT | O_EXCL` `FileExistsError` fallback must be exercised through a
   module-owned controlled race seam that pauses the elected creator after the
   exclusive-create attempt and releases a competing opener deterministically.
   The test must prove that the fallback opens and revalidates the existing
   regular file through the held parent descriptor, preserves one serialized
   stream, and never reports two creators. It must also cover multi-process
   same-stream append serialization through the public ordinary-I/O path. Sleep
   loops, probabilistic collision timing, or a test that merely observes a
   `FileExistsError` without controlling the interleaving are not proof.
3. **Cross-process release is kernel-barrier proof.** The 64-child
   cross-process proof must use an OS lock barrier or an equivalent kernel
   primitive. The parent holds an exclusive lock; all 64 children signal ready
   and then block while requesting a shared lock on that same barrier; the
   parent releases the exclusive lock exactly once only after receiving every
   ready acknowledgement. Children may then proceed to the contested operation.
   The assertion records all per-child readiness and verdict evidence and
   proves that no child passed the release point early. Polling-only release,
   elapsed-time assumptions, sequential child input, or an application-level
   flag without a kernel synchronization primitive is insufficient.
4. **KernelOne sourcing fixtures have no implicit maintenance.** Every ordinary
   KernelOne sourcing test must explicitly create its workspace and explicitly
   provision the lock authority and enroll each stream before its first ordinary
   read or append. Autouse fixtures, monkeypatches, wrappers around
   `_locked_streams`, and helper interception may set non-maintenance test
   dependencies, but must not provision, enroll, repair, or rebind as a side
   effect of ordinary I/O. Missing-authority and missing-enrollment tests remain
   deliberately unbootstrapped and assert both fail-closed evidence and zero
   authority mutation. The fixture contract must make the bootstrap state
   visible at each test call site rather than synthesizing it implicitly.
5. **HTTP lifespan cleanup has typed sync/async separation.** Lifespan cleanup
   must use distinct typed contracts or a typed helper for synchronous callbacks
   and awaitable callbacks; a heterogeneous callback tuple that relies on an
   untyped call followed conditionally by `await` is not acceptable. The helper
   must run every cleanup callback after startup or application failure, log
   cleanup failures without replacing an active application exception, and
   preserve existing cleanup-all ordering and application-error precedence.
   Targeted mypy must type-check the lifespan module and its cleanup helper or
   contract boundary, and targeted lifespan tests must cover synchronous and
   asynchronous cleanup failures both when the application body succeeds and
   when it raises.

Required closure evidence for this amendment is: full stream-ancestor durability
ordering and post-boundary failure injection; deterministic creator/fallback and
multi-process same-stream serialization proof; the explicit 64-child kernel
release barrier proof; sourcing tests with visible explicit provisioning and
unbootstrapped negative cases; and targeted lifespan mypy plus cleanup-all and
application-error-precedence tests. At the time this amendment was written, the
evidence remained pending and additive to Sections 11.1 and 11.2; it is now
satisfied or superseded by the Section 11.4 closure evidence.

### 11.4 2026-07-15 Current Closure Amendment

This is the authoritative current record for DEO-1A. Earlier statements in this
blueprint that mark DEO-1A pending, require singleton publication, or cite
`121`, `328`, or `611` as current evidence are historical and superseded; they
remain only to preserve the review trail.

DEO-1A is closed. Stateless `bootstrap_fact_stream_workspace` is the sole
bootstrap model: it has no process singleton or completion cache, revalidates
the persistent authority on every explicit call, and leaves ordinary I/O without
implicit bootstrap or enrollment. The withdrawn report that 64-process bootstrap
evidence was missing is not an open finding. The Cell-external integration test
holds the parent `LOCK_EX`, releases it exactly once for 64 independent child
processes, and has each child call `bootstrap_fact_stream_workspace`. It proves
one `created` and 63 `already_present` receipts for both provision and enrollment,
with a common storage identity and per-lock-key evidence.

Closure evidence: three final reviews found no Blocker or High issue; the focused
unified gate passed `342` tests in `21.77s`; the broad gate recorded `1533
passed, 3 failed, 1 skipped, 1 xfailed, 13 warnings` in `67.29s`; independent
reviews recorded `109 passed`, `95 passed + 5 passed`, and governance review
`185 passed`. Ruff, compileall, and `git diff --check` are green. Catalog
hard-fail exited 0 with `issue_count=0`, `blocker_count=0`, `high_count=0`,
`new_issue_count=0`, and `mismatch_count=0`. Targeted mypy over 21 production files, including `app_factory.py` and
`directed_effect_operation.py`, reports `0 issues`.

The broad failures are only `FS-BASELINE-001..003`, retained as external/open
filesystem baselines in the ledger. This is not a claim that the whole repository
is green. DEO-1B is the next pending bucket; DEO-1C remains blocked by DEO-1B;
DEO-1 remains pending; DEO-2/3/4 remain unfinished; no bench is admitted.
