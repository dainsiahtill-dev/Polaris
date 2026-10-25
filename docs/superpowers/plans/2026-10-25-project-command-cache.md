# CACHE-BOUNDARY-01 implementation

Baseline main 2cdda5a0b; previous freeze released. Main only, no target edits or service restarts.

1. Authority bucket: bootstrap config/resolver, PermissionScope, session, roles and service plus new authority test. Record RED, then strict opt-in/default/cold scope/current-policy ceiling/read-only/browser projections.
2. Storage bucket: new KernelOne command_cache.py, sandbox/process exports, engineering_tools.py and new KernelOne test. Record RED, then exact layout-resolved root, KFS allocation/identity/link validation, opaque env, helper RO. Agree helper signatures before consumption.
3. Root: engineering_verification.py and new integration tests. RED then existing manifest/profile exact cache subtree, verifier always RO even with writable outputs. Reuse existing current/QA/artifact consumers, no second owner.
4. Actual synthetic fixture: two ordinary calls persist executable+dependency; verification executes cache RO and writes declared outputs; executable/dependency/mode/link mutations stale cold evidence. Empty/default, SDK symlink, escape/hardlink and actual stored-scope/current-policy controls. Immediate protocol.json is finite acceptance; expanded protocol is reference.
5. Inspect all diffs; Ruff/format/mypy/targeted and adjacent tests/architecture/KernelOne all-mode gate; independent review. Update graph/Cell/docs/evidence. Freeze new source+explicit host opt-in and run one fresh serial original Bench. Synthetic success is not native delivery or personal quality.

Workers have mutually exclusive files, no commits/push/target changes. Audit artifacts under owning .polaris via KFS. Shared tests/docs owned by Root. Stop actual experiment on protected access, RO cache write, or stale accepted binding.

Execution: all three buckets implemented and independently reviewed; final204joint/K1 ALL/types/style passed. Catalog unchanged FAIL is separately attributed at immutable parent HEAD. Evidence ROOT-acceptance.json; main integration and fresh native pending.
