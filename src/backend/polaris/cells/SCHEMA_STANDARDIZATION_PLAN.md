# Cell Schema Standardization Report

**Generated:** 2026-04-06
**Total Cells Analyzed:** 52

---

## 1. Current Schema Consistency Statistics

| Issue Category | Count | % of Cells |
|----------------|-------|------------|
| Missing `generated_artifacts` | 41 | 79% |
| Missing `verification.smoke_commands` | 52 | 100% |
| `current_modules` vs `public_contracts.modules` inconsistency | 34 | 65% |
| Empty `subgraphs` | 10 | 19% |
| Missing `tags` | ~35 | 67% |
| Missing `verification.tests` | 5 | 10% |

---

## 2. Problem Distribution by Category

### 2.1 Missing Fields (Should Be Added)

| Field | Cells Missing | Severity |
|-------|---------------|----------|
| `verification.smoke_commands` | 52 (ALL) | HIGH |
| `generated_artifacts` | 41 | MEDIUM |
| `tags` | ~35 | LOW |
| `current_modules` (when `public_contracts.modules` exists) | 34 | MEDIUM |

### 2.2 Empty Arrays (Should Be Normalized)

| Field | Cells with Empty Arrays | Severity |
|-------|-------------------------|----------|
| `public_contracts.commands` | 10 | LOW |
| `public_contracts.queries` | 9 | LOW |
| `public_contracts.events` | 10 | LOW |
| `public_contracts.results` | 7 | LOW |
| `public_contracts.errors` | 8 | LOW |
| `subgraphs` | 10 | MEDIUM |

### 2.3 Inconsistent Structure

**Cells with `public_contracts.modules` but no top-level `current_modules`:**
- 34 cells affected
- These cells declare their contract modules but don't list current implementation modules

---

## 3. Most Problematic Cells (by warning count)

| Cell | Warnings |
|------|----------|
| `context.catalog` | 10 |
| `director.planning` | 14 |
| `director.runtime` | 14 |
| `director.tasking` | 14 |
| `director.delivery` | 14 |
| `orchestration.workflow_engine` | 13 |
| `orchestration.workflow_activity` | 13 |
| `roles.host` | 12 |
| `architect.design` | 8 |

---

## 4. Standard Schema Definition

### 4.1 Required Fields (MUST have)

```yaml
id: <cell-id>                    # Unique identifier
title: <string>                 # Human-readable title
kind: <capability|workflow|policy|projection>  # Cell type
visibility: <public|private>    # Visibility
stateful: <boolean>             # State ownership flag
owner: <string>                # Owner team
purpose: <string>              # Description (>50 chars)
owned_paths:                   # Paths owned by this cell
  - <path>
public_contracts:              # Public API surface
  modules:                     # Contract module paths (REQUIRED)
    - <module>
  commands: []                 # Commands (can be empty)
  queries: []                  # Queries (can be empty)
  events: []                  # Events (can be empty)
  results: []                 # Results (can be empty)
  errors: []                  # Errors (can be empty)
depends_on: []                # Dependencies (can be empty)
subgraphs: []                 # Pipeline connections (can be empty)
state_owners: []              # State paths (can be empty)
effects_allowed: []           # Effect permissions (can be empty)
verification:                 # Verification info
  tests: []                   # Test files (can be empty)
  smoke_commands: []          # Smoke test commands (MUST be defined)
  gaps: []                    # Migration gaps (can be empty)
```

### 4.2 Optional Fields (SHOULD have)

```yaml
current_modules:              # Current implementation modules
  - <module>                   # (Required if public_contracts.modules exists)
tags:                         # Cell categorization
  - <tag>
generated_artifacts:          # Generated artifact paths
  - <path>
```

### 4.3 Standard Values for Empty Arrays

| Field | Standard Empty Value | Rationale |
|-------|---------------------|-----------|
| `commands` | `[]` | No commands defined |
| `queries` | `[]` | No queries defined |
| `events` | `[]` | No events defined |
| `results` | `[]` | No results defined |
| `errors` | `[]` | No errors defined |
| `subgraphs` | `[]` | Not connected to pipeline |
| `state_owners` | `[]` | No state ownership |
| `effects_allowed` | `[]` | No effects allowed |
| `verification.tests` | `[]` | No tests yet |
| `verification.smoke_commands` | `[]` | **MUST be defined** (even if empty, to indicate intentional absence) |

---

## 5. Normalization Rules

### 5.1 `current_modules` vs `public_contracts.modules`

**Rule:** Both fields serve different purposes:
- `current_modules`: Implementation modules (what the cell currently has)
- `public_contracts.modules`: Public contract modules (what the cell exposes)

**Action:** If a cell has `public_contracts.modules`, it SHOULD also have `current_modules` listing the implementation modules. If they are the same, document this explicitly.

### 5.2 Empty Arrays

**Rule:** All public_contracts sub-fields should be explicitly present, even if empty.

**Action:** Ensure all cells have:
```yaml
public_contracts:
  modules:
    - <at least one module>
  commands: []
  queries: []
  events: []
  results: []
  errors: []
```

### 5.3 `subgraphs` Normalization

**Rule:** Every cell should be connected to at least one subgraph or explicitly declare `subgraphs: []`.

**Action:** For cells with empty `subgraphs`:
- If genuinely not connected: keep as `[]`
- If should be connected but isn't: add to appropriate pipeline

### 5.4 `verification.smoke_commands`

**Rule:** ALL cells MUST have `verification.smoke_commands` defined.

**Action:** Add empty array `smoke_commands: []` to cells that don't have it, to indicate intentional absence of smoke tests.

---

## 6. Fix Priority

### P0 (Critical - Must Fix)
1. Add `verification.smoke_commands: []` to ALL 52 cells

### P1 (High - Should Fix)
2. Add `generated_artifacts: []` to 41 cells missing it
3. Add `current_modules` to 34 cells that have `public_contracts.modules` but no `current_modules`
4. Normalize empty `subgraphs` to `[]` for 10 cells

### P2 (Medium - Nice to Have)
5. Add `tags: []` or actual tags to cells missing them
6. Normalize `public_contracts` sub-fields to always be present

---

## 7. Batch Fix Script

A fix script (`schema_normalizer.py`) can be created to automatically:
1. Add missing `verification.smoke_commands` field
2. Add missing `generated_artifacts` field
3. Normalize empty arrays
4. Add missing `current_modules` based on `public_contracts.modules`

---

## 8. Validation Checklist

After normalization, each cell.yaml should:

- [ ] Have all required fields present
- [ ] Have `verification.smoke_commands` defined (even if empty)
- [ ] Have `public_contracts.modules` with at least one module
- [ ] Have all `public_contracts` sub-fields explicitly present
- [ ] Have consistent `current_modules` vs `public_contracts.modules` usage
- [ ] Have `subgraphs` explicitly defined
- [ ] Have `state_owners` and `effects_allowed` explicitly defined
