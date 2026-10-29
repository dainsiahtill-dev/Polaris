# KFS namespace and effect boundary

Current implementation; scoped component evidence, not project delivery.

## Ownership and entry points

`storage.layout` selects roots. `KernelFileSystem` binds a logical runtime,
workspace or config path to that root. The active filesystem adapter owns the
physical operation through its managed namespace capability. Consumers do not
call guarded primitives as an alternative after an adapter refusal.

`workspace_create_text` creates a missing leaf without replacing existing user
content. The adapter authorizes the whole create, including missing parents.
The receipt distinguishes this call's creation from an already existing leaf.

Local adapter policy follows effective method ownership across inheritance
and instance overrides. A managed implementation must be owned at least as
specifically as any legacy policy it replaces. Inherited opt-in remains valid
until a more specific owner changes that policy. No legacy writer runs as an
authorization probe. The existing managed atomic text entry applies the same
ownership rule while retaining its established refusal error.

## Failure and recovery

- Missing adapter capability or adapter policy refusal occurs before that
  managed operation can provision a target. There is no unmanaged fallback.
- Symbolic links and multiply linked file content are refused by retained
  descriptor traversal. Removing a leaf symlink removes that entry only.
- Once a commit was attempted, a failed acknowledgement does not prove no
  effect. `NamespaceOperationEffectUnknownError` retains the binding and phase;
  callers must inspect state, preserve UNKNOWN and avoid blind replay/rollback.
- The reused create primitive preserves its error code and adds
  `details.original_effect=unknown` for uncertain commit or post-check failure.
- A failed required fsync cannot produce a successful durable append receipt.
  An atomic replacement receipt alone is not proof of power-loss durability
  for an entire newly provisioned directory tree.
- Concurrent non-atomic writes can invalidate each other's post-checks. Such
  calls may return UNKNOWN; stronger serialization is the owning consumer's
  responsibility. Distinct leaves remain independently usable.

Skill loading and the executor's read tools do not install files. Builtins
remain available in memory. Explicit installation uses adapter-owned KFS
create-only operations, preserves user content and writes one canonical tree;
the older runtime skill tree is discovery-only.

Scout anchor reads never bypass a refused KFS read through raw open. A refused
initial read prevents an incomplete merge. An uncertain write returns `None`
for the count, rather than a fabricated zero. Missing/corrupt optional cache
data keeps its existing recovery behavior. Its current tool consumer ignores
the optional count; this is not a project completion record.

`apply_patch` moves in the shared legacy toolkit preserve known destination
writes and uncertain source deletion, including when the state observer fails.
Failed partial calls are not replayable. This optional executor is separate
from the current `EngineeringRunService` tool loop.

## Validation and limits

Tests: `polaris/kernelone/fs/tests`, original PM/FinOps/CFO/event-store
consumers, scout anchor tests, Skill installation/integration and move effect
tests. Root qualification and retained failing controls are under the ignored
project `.polaris/refactor-management/full-refactor-wave2-20261027-013741/`.
The frozen combined qualification passed 824 tests, with Ruff, format and
selected mypy checks. Formal release and independent review are separate
checks; see the current batch evidence before claiming integration.

Descriptor operations require the supported POSIX capabilities and refuse an
unsupported host. They do not establish absolute OS confinement against a
hostile same-UID actor reparenting retained directories. Current engineering
command containment has its own host scope and OS sandbox. Host KFS must
still be able to write its own control-plane files. No new controller, ledger
or production Bench fact source is introduced.
