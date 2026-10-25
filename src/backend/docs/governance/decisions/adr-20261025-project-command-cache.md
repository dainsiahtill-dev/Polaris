# Project command cache through existing execution authority

Status: accepted for implementation; qualification pending, 2026-10-25.

Add one explicit persisted host capability to PermissionScope and one exact KernelOne sandbox mount. Cache data lives under layout-resolved project runtime; readonly consumers cap to RO. Exact cache inputs join the existing candidate manifest. EngineeringRunService and its SQLite remain sole engineering owner/facts.

Reject deriving cache rights from source roots or execute permission: this silently upgrades saved scopes and writable-output verifiers. Reject broad .polaris mounts/model-selected roots. Reject a new cache ledger. Preserve contained SDK symlinks and reject outward aliases or cross-domain hardlinks. Default-disabled shape remains compatible; new current grants never upgrade saved runs. Whole-tree hashing initially favors valid evidence; optimize only with measurements.

See PROJECT_COMMAND_CACHE_BOUNDARY_20261025.md and vc-20261025-command-cache-boundary.yaml for finite acceptance. No native target edits, package rules, model hashes or additional planning prerequisite.
