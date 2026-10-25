# Project command-cache boundary

Status: implemented and component-qualified (2026-10-25). Native adoption, delivery benefit and large SDK cost remain unverified. Parent main2cdda5a0b; prior L216 freeze was released after its terminal integrity check. Generated targets stay read-only.

## Problem, ownership and data flow

L216 lost a JDK download across private /tmp command calls, then persisted352529290 bytes under source .toolchain. The actual final tool schema offered only source-root persistence. Verifier ignored all .polaris, so simply relocating preparation there would lose input provenance.

Host Settings/profile -> original saved PermissionScope -> current host revalidation -> role/tool projection -> KernelOne exact cache mount/process drain -> existing actual input manifest -> current verification/QA/artifact applicability.

Existing EngineeringRunService and engineering SQLite remain sole engineering execution/fact owners. Host owns grants; existing workspace writer owns cache mutations; KernelOne owns containment; existing verifier owns input observations. No new Cell/controller/table/ledger or model-maintained contract. The public method surface is unchanged; no generated pack freshness claim.

## Mechanism and permissions

Add strict host command_cache=disabled|read_only|read_write, default disabled. Enabled modes persist in existing scope JSON; disabled keeps old encoding. Missing saved fields stay disabled. Original authority is a ceiling; current policy may revoke but cannot upgrade it. Cache-only possible writes advance the same candidate revision.

KernelOne storage.layout resolves only canonical <workspace>/.polaris/runtime/command-cache/data. A validated host mount exposes only /polaris-command-cache with POLARIS_COMMAND_CACHE set. The data leaf persists; .polaris control siblings remain hidden. No model root/mode choice, inferred tools/source/network grants, command rewriting, PATH injection or package rules. HOME/XDG/tmp and normal output remain per-call.

Ordinary Director commands may allocate RW after actual deadline/permission/containment admission. Disabled does not inspect/allocate; RO lookup/current queries never mkdir or repair missing cache. PM/CE/QA, browser and read-only helpers cap toRO. Verifier alwaysRO, including checks with writable build output roots.

Pin canonical ancestry and directory identity before mount and revalidate after known process drain, including cancellation/timeout. Reuse existing process owner and retain unknown effects/writer fencing without replay or deletion. Whole-tree no-follow observations validate membership, contained SDK symlink chains and cache-domain hardlink closure; outward/broken/looping links, special files or cross-domain hardlinks fail closed. Host-absolute internal links can be safe but inaccessible in the sandbox; use relative or opaque alias links. No immutable-host-filesystem or arbitrary hostile concurrent-host-writer guarantee is claimed.

## Evidence and invalidation

General .polaris ignore remains. Explicitly observe only exact cache leaf entries through existing KFS manifest: actual regular content hashes, metadata/identity/mode, lexical link text and directory membership. Cache is an input, never a verification output. Before/after/current/QA/artifact consumers share existing bindings, not another cache-valid verdict. Executable/dependency/mode/link changes invalidate prior current results. Historical passed/approved facts remain intact.

Rejected alternatives: derive cache RW from source or execute rights (silently upgrades old scopes and verifiers); broad .polaris/read-root mounts (expose controls); separate ledger (duplicates existing facts). Conservative whole-tree hashing initially favors valid evidence over unmeasured optimization.

## Falsifiable qualification and stop

Root RED6failed/1passed at missing scope field; authority RED28failed. Minimal fixture prepares tiny executable+dependency in one actual command and runs it in another. Verifier executes cache RO with writable build outputs. Four input mutations stale cold verification/review binding. An actual seven-request scripted PM/CE/Director/QA chain produces real command/SVG/verification/QA; dependency mutation makes cold verification/QA/outcome UNKNOWN and saved artifact stale, with no history rewrite. Only model replies/config are substituted; judgment and native delivery are unproved.

Additional actual three-process SDK experiment proves opaque absolute SDK symlinks and cache-internal hardlinks remain executable and bind both hashes. Default/empty/denied/no-allocation, policy ceiling/cold restore, unsafe topology, timeout/cancel and unknown/no-replay controls passed. Protected reads/writes, RO mutation or stale accepted bindings would refute the mechanism and stop integration.

Initial joint204passed. CACHE-007 prelaunch testing found fixed source-reader helpers incorrectly required an unused cache; their cache mount is now disabled. Actual execute_command and verifier/QA/artifact bindings remain unchanged. Final joint270passed (including40architecture); final KernelOne ALL12stages/368passed1skipped; mypy16files/Ruff/format passed. Independent source review found no open blocker, and Root verified final source pins. Optional full catalog audit remainsFAIL: actual immutable parent HEAD produces identical159occurrences/115fingerprints, introduced0, manifest mismatch0; baselines unchanged. Existing catalog governance debt is separate and unresolved.

This finite component topic stops at the above acceptance, then main integration/exact source+host-profile freeze and one fresh serial original Bench with separate Root quality inspection. No all120 rerun, package framework, large SDK optimization or expanded research prerequisite was added. Source/component qualification does not prove unattended delivery, comparative reliability or economics.

Detailed facts, failed fixtures and limits: docs/history/POLARIS_COMMAND_CACHE_BOUNDARY_20261025.md; .polaris/audits/persistent-command-cache-boundary-20261025/ROOT-acceptance.json, ROOT-defects.json and ROOT-harness-failures.json. L216 quality FAIL and original120 counts remain unchanged.
