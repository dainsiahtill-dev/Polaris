# Roles Runtime Cell

## Current engineering owner

`EngineeringRunService` in `public/engineering_service.py` owns all new engineering execution, observation, cancellation and recovery. HTTP/CLI/UI adapt to that public owner. Direct Director is default; explicit collaborative work runs PM, Chief Engineer and Director through the same service. Read-only consultation returns one level; discussion has no action tools and creates no run. Uncalled roles remain `not_called`.

## State and boundaries

The single new-run fact store is `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`; `engineering_session.py` persists runs, events, tool observations and verification, with `engineering/workspace.lock` enforcing one workspace writer. `runtime/engineering/*` in the manifest is relative to the canonical workspace runtime root, not a second physical runtime directory.

`public/contracts/engineering_run.py` exposes `RoleRunCommand`, `PermissionScope`, `RunLimits`, `ToolObservation`, `RoleRunResult`. These are program interfaces, not model-authored business contracts. Cross-Cell consumers use public boundaries; internal modules below belong to this Cell.

- `engineering_loop.py`: ordinary native-tool/model loop.
- `engineering_tools.py`: normalized technical tools with host permission and sandbox enforcement.
- `engineering_roles.py`: role identity, ordinary-text advice and read-only consultation.
- `engineering_verification.py`: real checks against the current source candidate.
- `engineering_session.py`: durable observations, committed events and workspace execution lock.
- `engineering_visual.py`: derives visual model input from the same journal. A complete same-role host capture binding can omit obsolete browser pixels while preserving their metadata, tool results and original stored images. All current capture sets, original user references and unattributed inputs remain; verification receipt sequence alone does not make an image stale. Explicit private host pins are supported, but a public historical-image comparison/retrieval workflow is not wired.

No second controller, task table, ledger or role-session mirror is created for new runs. Legacy `runtime/roles/*` remains declared only to explain old owned history; it is not a writable namespace for engineering.

## Permissions and effects

Host `PermissionScope`, saved limits/deadline and current permission policy govern effects. Explicit empty roots/tools remain empty. Text advice cannot expand grants. File tools use KFS and real-path/symlink/root checks; command tools require actual OS containment and process-tree drain. PM/CE/QA are read-only by default. Unsupported containment fails visibly rather than executing unsandboxed.

When the host grants `execute_command`, QA may use it for independent checks with no workspace write roots and no network. Each command gets K1's existing private temporary space; it cannot persist checker code or change candidate/dependency inputs. PM/CE still do not acquire this execution capability. Availability does not prove that QA selects useful checks or judges their results correctly.

For service-owned native tools and final verification, the existing owner rechecks saved grants against current host policy after normalization and before each effect. A revocation denies the next effect with a real failed observation; it does not retroactively cancel an already running command. Explicit standalone components without a policy resolver have their static supplied scope, not a dynamic revocation promise. Offline commands with no effective write roots preserve the candidate revision; commands with possible writes or network retain conservative verification invalidation. Unknown effects still block recovery and acceptance even when the supplied scope was read-only.

`fs.write:workspace/**` in the graph declares effect kind, not ambient authorization: each operation still needs the saved host grant. Engineering state writes stay under `runtime/engineering/*`; provider requests, tools and durable observations are auditable. KernelOne provides provider/native codecs, ToolSpec aliases/arg aliases, KFS/storage, sandbox/process and budget primitives, without importing the old business execution protocol.

## Verification, QA and recovery

Director consumes ordinary build/test/tool failures within the same run. Declared checks remain required; when omitted, Director selects checks from the actual project and runs them. Model completion, empty checks, ordinary successful command output and QA prose cannot independently produce accepted. Final verification binds current source; required independent QA reviews that same candidate and rejection returns to the same task.

Recovery preserves run/workspace identity, original permissions/checks/deadline and revalidates current host policy. Unknown write or command effects remain interrupted/UNKNOWN until reconciled; never blindly replay. Cancellation is not complete until process containment and drain are known. Old run IDs are never imported into engineering identities.

## Realtime and final request audit

Committed engineering facts publish as `event.engineering` through the existing Nats-JetStream runtime.v2 `/v2/ws/runtime` rail; HTTP serves initial/reconnect snapshots, explicit refresh and one-shot commands. No polling/SSE fallback or second WebSocket. ContextOS exposes actual final provider requests with role/tool schema/tool_choice/response_format/token/window evidence and readable same-workspace snapshot refs. Persisted request/snapshot counts are not physical provider HTTP totals or billing evidence.

The existing `request_prepared` event records derived visual selection counts and attribution reasons. It is auxiliary input-production evidence, not a sent provider body. KernelOne budget and native protocol guards still decide admission; required/current/reference content that exceeds the budget remains an explicit failure. Serialized estimates are not actual visual token costs. This projection does not restore the retired StateFirst/TurnEngine business chain.

## Historical compatibility and optional utilities

`public/service.py`, old DTO exports and declared dependencies on role/kernel/session/task-market/Factory cells describe retained historical readers, serialization and explicitly optional utilities. Their presence is not new-run admission. Legacy writable `RoleRuntimeService`, `execute_role_session`, `RoleExecutionKernel` and Factory protocol paths are retired for new execution; old in-flight runs remain on their original frozen owner.

The new loop never falls back to PM contracts, CE portfolio/hash/semantic patches, JobToken/DEO/Run Ledger gates or automatic deterministic repair schedules. Explicit independent repair utilities may remain in `director.runtime` public, with coverage/policy/receipt/revalidation; they are not prerequisites for ordinary compiler-error repair. Historical code has not all been deleted, and graph metadata must not claim otherwise.

## Verification and generated assets

`cell.yaml` and graph catalog list current engineering tests and the no-legacy-admission architecture proof. Existing historical tests remain historical evidence; declarations do not mean commands passed. Root integration must run relevant tests, KernelOne ALL and catalog audit from the final candidate.

`descriptor_surface` limits generated Agent capabilities to audited `EngineeringRunService` operations; legacy DTOs remain in `public_contracts` for decoding. The canonical descriptor generator is `polaris.cells.context.catalog.internal.descriptor_pack_generator`; its CLI traverses all Cells and writes descriptor packs plus receipts, so do not run it broadly for this Cell. `docs/scripts/generate_polaris_cell_assets.py` is a scaffold generator that may overwrite contracts/README/cell assets, not a safe refresh command. Missing context/verify/descriptor packs are reported as absent; none are fabricated by this document.
