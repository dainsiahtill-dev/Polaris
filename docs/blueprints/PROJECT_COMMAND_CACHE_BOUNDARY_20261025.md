# Project command-cache boundary

Status: approved for bounded implementation; qualification pending.
Task: CACHE-BOUNDARY-01. Source baseline: f6a7428eaa66d8dc2c879096c8d7e13ee9b9ea40.
Source freeze was released after L216 closeout; authorized implementation is on main in the three disjoint buckets. Generated targets remain read-only.

## Decision proposed

Add one explicit host capability to the existing engineering flow:
`PermissionScope.command_cache: Literal["disabled", "read_only", "read_write"] = "disabled"`.
The matching host permission-profile field has the same strict values and default.
This design is approved for implementation; APIs and benefits remain unverified.

Resolve one workspace-owned cache data directory through existing KernelOne storage.layout:
`<canonical workspace>/.polaris/runtime/command-cache/data`.
Expose only that data directory at an opaque sandbox alias, proposed
`/polaris-command-cache`, with `POLARIS_COMMAND_CACHE` set to the alias.
The host path and all sibling control directories remain hidden.
No model argument can select a root, mode, executable, PATH addition, or permission.

Keep HOME, XDG_CACHE_HOME, TMPDIR and ordinary command output per-call/private.
Do not modify raw commands or automatically change PATH, JAVA_HOME, package-manager
configuration, or existing `toolchain_bin_dirs` policy. The model can use the
advertised environment variable in ordinary commands. Durable prepared inputs belong
there; product source/build deliverables retain their existing authorized locations.

## Single invariant

A host-authorized persistent command cache belongs to the owning workspace's
canonical runtime; it survives ordinary command teardown, exposes no control sibling,
and is read-only for every verifier, QA and other read-only operation. The exact
cache tree available to those operations participates in the existing actual KFS
candidate input binding. Changed executable/dependency bytes, topology, mode or
identity invalidate current verification, QA and candidate-bound artifact evidence.

## Current facts

The supplied audit report records a successful private /tmp download that vanished
on the next command. The final provider tool description advertised persistence
only in source workspace write roots and explicitly hid .polaris. The model then
placed a 352,529,290-byte JDK under source .toolchain. This is an authorized workspace
effect that violates storage placement policy, not evidence of a permission escape.
Do not move, edit, install into, run or repair that live target in this task.

Current sandbox code masks all .polaris, validates external read-only toolchain
directories, clears host environment, and uses private cache/tmp/home/output mounts.
Current verifier `_manifest` ignores all .polaris. Thus a path-only relocation would
remove toolchain provenance from current-source acceptance.

The existing owner chain is:
host Settings/profile -> bootstrap resolver -> persisted PermissionScope in the
existing engineering SQLite row -> role_scope/EngineeringTools -> KernelOne sandbox
and process drain -> existing verification/input binding -> existing QA/current reads.
No new Cell, controller, task table, ledger, readiness authority or package framework.

## Alternatives and tradeoffs

1. **Explicit three-state host capability — recommended.** A disabled default and
   saved ceiling prevent new policy from granting historical runs new authority.
   Read-only is explicit, so verification's writable output roots cannot imply cache RW.
   Cost: one field must propagate across every scope producer/consumer.
2. **Derive cache RW from source write_roots or execute_command.** Smaller apparent
   API change, but conflates source writes, build outputs and cache authority; would
   silently expand old scopes and can give verification RW. Reject.
3. **Reuse general read/write roots or toolchain_bin_dirs under .polaris.** Would
   weaken control hiding or current toolchain validation and still needs fingerprint
   work. Reject. Output capture is a bounded temporary export, not this capability.

A single fixed project cache keeps across-command and cold-resume reuse without
per-run cache IDs, allocation records or another durable authority. Existing one
workspace writer protects platform-managed writers through verification/QA. A later
run changing this shared cache makes earlier cache-bound evidence stale. Hash the
entire validated data subtree conservatively; no dynamic dependency tracing, package
index, compiler-specific rules or model-provided hashes.

## Permission and lifecycle rules

- Disabled means no mount, no variable, no allocation, no cache read on current queries.
- New/missing saved field decodes disabled. Serialize enabled modes in existing scope
  JSON only; omit disabled to preserve historical idempotent identity shape.
- Host policy order is disabled < read_only < read_write. Revalidation returns the
  original scope only when within current policy; saved RW versus current RO denies.
  Current RW must not upgrade an old disabled saved scope.
- Director retains only explicit host access. PM/Chief Engineer/QA cap access at RO;
  PM/CE still have no command tool. Existing empty roots/tools remain empty.
  An explicit new cache grant never adds workspace roots, tools, network or deadlines.
- Verifier always caps cache at RO even though declared build outputs are writable.
  Browser capture's separate read-only `replace(...)` projection also caps cache.
  Read-only helper subprocesses must never inherit RW from Director scope.
- RW cache allocation occurs only after current authorization and ordinary
  execute_command admission under the existing workspace writer. Read-only and cold
  observation paths do not mkdir or repair anything. Missing enabled cache is
  unavailable/UNKNOWN, never an invented empty successful input.
- Open/validate a small host-owned mount object before spawning; pin canonical real
  ancestry and directory identity, recheck before mount and after known drain.
  Keep cache data on normal exit, failure and timeout. Unproved drain or journal
  commit retains existing unknown effects/writer ownership; no deletion or replay.
  Reuse output_capture's KFS identity and process-drain patterns, not its export limits
  or temporary cleanup lifecycle.

## Containment and input binding

Only the exact data leaf is bind-mounted, never .polaris, runtime or command-cache's
parent. Treat alias as reserved alongside existing private mounts. Do not permit
model-chosen host paths or broad generic control-root grants. Reject noncanonical or
symlinked ancestry, special files and cross-domain hardlinks; enforce cache-domain
hardlink closure separately from workspace-write closure.

Internal SDK symlinks are valid: record lexical link text and metadata; resolve chains
under the exact cache root; bind every regular target through the whole-tree manifest.
Do not blanket-reject symlinks. Outward, broken, looping or protected-control
targets fail closed for evidence/mount admission. Existing masking independently
prevents a newly created link in a running command from exposing host control files.
A command leaving unsafe cache topology yields explicit failed/unknown observation;
do not silently delete its links or claim prepared/accepted. Root reconciliation can
be required; no model authority expansion is a repair.

Extend existing `EngineeringVerification._manifest` to inspect the cache leaf
explicitly, separately from the normal workspace traversal. Do not relax the general
.polaris ignore rule. Cache entries use the existing absolute-path -> actual
metadata/hash/symlink/children representation, so current-result, source-before/after,
review_binding and artifact-binding checks consume the same observation. Cache is
never an output root. Cache control ancestry is validated but control siblings'
bytes never enter manifest or LLM context.

Add enabled cache provenance/effective RO mode to the existing verifier profile
binding. Disabled historical shape stays unchanged. Before/after capture and each
fresh query hash actual cache content; metadata-only trust or process-local cached
digests cannot establish freshness. No new fact store or cache approval verdict.

## Bounded implementation boundary for Root

Expected production paths: host config/resolver; PermissionScope; engineering
session serializer; role projection; tools schemas/process handoff; verifier manifest
and RO projection; service scope decode/current-policy comparison/browser projection;
one focused KernelOne command_cache module; sandbox; KernelOne process exports.
Exact proposed files and observed lines are in interface-owner-map.json.

Graph/Cell docs must declare cache namespace effects, existing ownership and tests.
Before implementation, promote the approved blueprint/verification card/structural
decision to the normal docs locations after Root releases source freeze. Do not
refresh all descriptors or write generated packs broadly.

Default-disabled code alone does not close the next fresh Bench environment.
Root must explicitly select read_write in the frozen host profile consumed by
existing host-config-file plumbing, after review. Do not add a Bench-specific
runtime flag, model hint or config auto-enable. Source/component checks, final gates,
fresh native execution, ROOT business QA and safe resource closure remain separate.

## Evidence limits and stop criterion

This subtask read source/evidence and wrote only private proposal artifacts.
No cache experiments, native tool dispatch, installs, builds, Bench retries or target
changes occurred. Root's current L2-16 quality closeout remains FAIL; this proposal
does not upgrade it.

Root reviewed and approved this bounded design after source-freeze release. Record RED before production code; execute the immediate finite protocol. Successful
synthetic controls prove component boundaries, not fresh unattended delivery.
Hashing a whole large toolchain can remain expensive; preserve correctness and measure
actual current-query cost before considering any separate optimization project.
