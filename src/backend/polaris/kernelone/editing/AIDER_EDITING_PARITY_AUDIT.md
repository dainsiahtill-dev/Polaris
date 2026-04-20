# Aider Editing Parity Audit (KernelOne)

Date: 2026-03-25  
Scope: `polaris/kernelone/editing/*` + `polaris/kernelone/llm/toolkit/protocol_kernel.py`
Reference source root: `C:/Users/dains/Downloads/aider-main/aider`

## Source Files Audited

- `aider/coders/search_replace.py`
- `aider/coders/editblock_coder.py`
- `aider/coders/udiff_coder.py`
- `aider/coders/patch_coder.py`
- `aider/coders/wholefile_coder.py`
- `aider/diffs.py`

## Capability Matrix

### Search/Replace Strategies

1. Exact window match: **DONE**
2. Whitespace-tolerant window match: **DONE**
3. Leading-whitespace offset matching: **DONE**
4. Dotdot ellipsis handling (`...`): **DONE**
5. Relative indentation transform: **DONE**
6. diff-match-patch strategy: **DONE**
7. dmp line-level strategy: **DONE**
8. Reverse-lines preproc strategy: **DONE**
9. Strip-blank preproc strategy: **DONE**
10. SequenceMatcher fallback: **DONE**

Implemented in: `search_replace_engine.py`

### EditBlock Parsing

1. SEARCH/REPLACE block extraction: **DONE**
2. Filename resolution (exact/basename/fuzzy): **DONE**
3. Reuse previous filename in consecutive blocks: **DONE**
4. Fence/marker cleaning: **DONE**

Implemented in: `editblock_engine.py`

### Unified Diff

1. Fenced diff extraction: **DONE**
2. Git-style path normalization (`a/` -> `b/`): **DONE**
3. Hunk before/after reconstruction: **DONE**

Implemented in: `unified_diff_engine.py`

### apply_patch Format

1. `*** Begin/End Patch`: **DONE**
2. `*** Add File`: **DONE**
3. `*** Delete File`: **DONE**
4. `*** Update File`: **DONE**
5. `*** Move to`: **DONE**
6. Update hunks -> normalized search/replace operations: **DONE**

Implemented in: `patch_engine.py` + `protocol_kernel.py`

### Whole File Fenced Blocks

1. Fenced whole-file extraction: **DONE**
2. Filename inference priority (`block > saw > chat`): **DONE**

Implemented in: `wholefile_engine.py`

## Runtime Integration

Canonical runtime path:

1. `apply_protocol_output` -> parse canonical protocol
2. If empty, route rich edit formats via `operation_router`
3. Execute by `StrictOperationApplier`

This is active in `protocol_kernel.py`.

## Safety / Governance Guards

1. Runtime vendor import quarantine test: **DONE** (`tests/test_editing_vendor_quarantine.py`)
2. Legacy downgrade matrix documented: **DONE** (`LEGACY_DOWNGRADE_MATRIX.md`)
3. Vendor directory cleanup completed: **DONE** (`polaris/kernelone/editing/vendor` removed)

## Deliberate Exclusions (Not Runtime Core)

Not migrated as runtime editing features:

- Git cherry-pick-based experimental strategies from Aider
- UI/stream rendering logic (`io.py`, `mdstream.py`)
- model/provider/product shell logic

Reason: these are not required for deterministic in-process file edit apply path in KernelOne.
