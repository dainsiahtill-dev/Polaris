# Storage Layout Cell

## Purpose

Resolve global, workspace and runtime persistence layout and path policies as a canonical storage contract for all cells. Provides Polaris-specific path anchoring (config_root under `polaris_home()`, metadata dir `.polaris`) on top of the KernelOne generic storage layout.

## Kind

`capability`

## Public Contracts

### Commands
- **`RefreshStorageLayoutCommandV1`** — evict the in-process cache and re-resolve storage layout
  - `workspace: str` — workspace path (non-empty)
  - `force: bool` — if `True`, clears the storage-roots cache before resolving; if `False`, equivalent to `resolve_storage_layout`
  - Handler: `refresh_storage_layout(command) -> StorageLayoutResultV1`

### Queries
- **`ResolveStorageLayoutQueryV1`** — resolve the full storage layout for a workspace
  - `workspace: str` — workspace path (non-empty)
  - Handler: `resolve_storage_layout(query) -> StorageLayoutResultV1`
- **`ResolveRuntimePathQueryV1`** — resolve a runtime-relative path
  - `workspace: str`, `relative_path: str` — both non-empty
- **`ResolveWorkspacePathQueryV1`** — resolve a workspace-relative path
  - `workspace: str`, `relative_path: str` — both non-empty

### Events
- **`StorageLayoutResolvedEventV1`** — emitted when a layout is resolved (event-driven path)
  - `event_id`, `workspace`, `runtime_root`, `resolved_at` — all non-empty strings

### Results
- **`StorageLayoutResultV1`** — returned by all query/command handlers
  - `workspace: str` — absolute workspace path
  - `runtime_root: str` — runtime data root
  - `history_root: str` — workspace-anchored history directory (always on same drive as workspace)
  - `meta_root: str` — workspace metadata root (= `project_root`)
  - `extras: dict` — contains `config_root`, `workspace_key`, `runtime_mode`, `runtime_base`, `workspace_persistent_root`

### Errors
- **`StorageLayoutErrorV1`** — raised on resolution failure
  - `message: str` (required)
  - `code: str` — error code (default: `"storage_layout_error"`)
  - `details: dict` — additional context
  - `StorageLayoutError` — backward-compat alias (prefer `StorageLayoutErrorV1`)

## Public API (Business Layer)

```python
from polaris.cells.storage.layout import (
    PolarisStorageLayout,     # Path resolver class
    PolarisStorageRoots,      # HP-specific storage roots (config_root anchored at polaris_home())
    polaris_home,             # HP home dir: KERNELONE_HOME > KERNELONE_HOME/.polaris > ~/.polaris
    default_polaris_cache_base,  # Cross-platform default cache base
    resolve_polaris_roots,   # Core roots resolver (HP-specific config_root)
    resolve_storage_layout,       # ResolveStorageLayoutQueryV1 handler
    refresh_storage_layout,       # RefreshStorageLayoutCommandV1 handler
    StorageLayoutErrorV1,
    StorageLayoutResultV1,
)
```

### Key Behaviours

**`polaris_home()` resolution order**
1. `KERNELONE_HOME` env var (if set and non-empty)
2. `KERNELONE_HOME/.polaris` (if `KERNELONE_HOME` is set)
3. `~/.polaris` (default fallback)

**`config_root` anchoring**
`config_root` is always `<polaris_home()>/config`, not `<kernelone_home()>/config`. This is the HP-specific behaviour that distinguishes `PolarisStorageLayout` from the base `StorageLayout`.

**`history_root` workspace-anchoring**
`history_root` is always `<workspace_abs>/.polaris/history`, never under `runtime_base`. This prevents Windows cross-drive `os.path.join()` from silently discarding the workspace path when `runtime_base` is on a different drive.

**Path escape guards**
`normalize_logical_rel_path()` (KernelOne level) rejects:
- `..` path traversal segments
- Metadata dir prefixes (`.polaris/`, `.kernelone/`)
- Non-allowlisted prefixes (only `runtime`, `workspace`, `config` permitted)

**Cache invalidation**
`refresh_storage_layout(force=True)` calls `clear_storage_roots_cache()` before resolving, guaranteeing fresh filesystem probes on every call. `force=False` (the default) is equivalent to `resolve_storage_layout` — no cache is touched.

**Performance targets**
- Hot-path (cache hit, same workspace): < 1ms average
- Cold-path (cache miss, filesystem probe): P99 < 50ms on local storage
- config_root resolution (no stat calls): P99 < 5ms

## Depends On

- `policy.workspace_guard`
- `audit.evidence`

## State Ownership

- `workspace/.polaris/storage_layout/*`

## Effects Allowed

- `fs.read:workspace/**`
- `fs.read:runtime/**`
- `fs.write:workspace/.polaris/storage_layout/*`

## Verification

- `polaris/kernelone/storage/tests/test_storage_layout.py` — KernelOne base layer tests
- `tests/test_storage_layout_v4.py` — integration tests (requires bootstrap)
- `polaris/cells/storage/layout/tests/test_storage_layout_cell.py` — Cell public API tests (59 passed, 2 skipped)
  - Contract validation (query/command/result/error dataclasses)
  - `polaris_home()` env priority chain
  - `PolarisStorageLayout` path resolution
  - `resolve_polaris_roots()` HP-specific roots
  - `resolve_storage_layout()` handler + audit logging
  - `refresh_storage_layout()` cache invalidation
  - Path escape guards (`normalize_logical_rel_path`)
  - Performance benchmarks (hot/cold path, workspace_key determinism, config_root P99)
