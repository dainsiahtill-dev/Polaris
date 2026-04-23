# ACGA 2.0 Port/Adapter Migration Plan

**Document ID**: MIGRATION-2026-0423-PORT-ADAPTER
**Date**: 2026-04-23
**Status**: Phase 2 Complete - Alignment Service Migrated
**Architect**: Principal Engineer (工部尚书)

---

## 1. Executive Summary

This document outlines the systematic migration to resolve **Issue 1: KernelOne → Cells Layering Violation** and **Issue 2: Cell Encapsulation Leak** from the P0 Critical Fix Blueprint.

### 1.1 What Was Done (Phase 1-2)

- Created `kernelone/ports/` with abstract interfaces (`IRoleProvider`, `IBusPort`, `IAlignmentService`)
- Created `cells/adapters/kernelone/` with concrete implementations
- Refactored `meta_prompting.py` as a working example
- Created CI pre-commit hook for internal import detection
- Migrated `orchestrator.py` to use `IAlignmentService` via adapter
- All quality gates pass: ruff, mypy, ruff format, pytest

---

## 2. Architecture Overview

### 2.1 Before (ACGA 2.0 Violation)

```
kernelone/ → cells/  (VIOLATION)
```

```python
# kernelone/prompts/meta_prompting.py (BEFORE)
from polaris.cells.roles.kernel.public.role_alias import normalize_role_alias  # VIOLATION!
```

### 2.2 After (ACGA 2.0 Compliant)

```
kernelone/ → kernelone/ports/ → cells/adapters/kernelone/ → cells/
```

```python
# kernelone/prompts/meta_prompting.py (AFTER)
from polaris.cells.adapters.kernelone import RoleProviderAdapter  # Via stable adapter
normalize_role_alias = RoleProviderAdapter().normalize_role_alias
```

### 2.3 Dependency Flow Diagram

```
+-------------------+       +-------------------+
|   KernelOne       |       |      Cells        |
|   (Platform)     |       |  (Business Logic) |
+-------------------+       +-------------------+
          |                         |
          v                         v
+-------------------+       +-------------------+
| kernelone/ports/ |       | cells/adapters/  |
| - IRoleProvider  |       | - RoleProviderAdapter
| - IBusPort       |       | - BusAdapter
| - IAlignmentSvc  |       +-------------------+
+-------------------+
          ^
          |
+-------------------+
|  KernelOne        |
|  Components       |
| (meta_prompting, |
|  orchestrator)   |
+-------------------+
```

---

## 3. Files Created

### 3.1 Port Interfaces (kernelone/ports/)

| File | Description |
|------|-------------|
| `__init__.py` | Public exports for port interfaces |
| `role_provider.py` | `IRoleProvider` - Role normalization abstraction |
| `bus_port.py` | `IBusPort`, `IAgentBusPort`, `AgentEnvelope`, `DeadLetterRecord` |
| `alignment.py` | `IAlignmentService` - Value alignment abstraction |

### 3.2 Adapters (cells/adapters/kernelone/)

| File | Description |
|------|-------------|
| `__init__.py` | Public exports for adapters |
| `role_provider_adapter.py` | `RoleProviderAdapter` implementing `IRoleProvider` |
| `bus_adapter.py` | `KernelOneBusPortAdapter` implementing `IBusPort` |
| `alignment_adapter.py` | `AlignmentServiceAdapter` implementing `IAlignmentService` |

### 3.3 CI Pre-commit Hook

| File | Description |
|------|-------------|
| `scripts/check_cell_imports.py` | Pre-commit hook to detect Cell internal imports |

---

## 4. Violation Inventory

### 4.1 KernelOne → Cells Violations (61 total)

| File | Violation Type | Priority |
|------|---------------|----------|
| `kernelone/multi_agent/bus_port.py` | `InMemoryAgentBusPort`, `KernelOneMessageBusPort` | HIGH |
| `kernelone/prompts/meta_prompting.py` | `normalize_role_alias` | **DONE** |
| `kernelone/cognitive/orchestrator.py` | `ValueAlignmentService` | MEDIUM |
| `kernelone/context/context_os/summarizers/slm.py` | `TransactionConfig` | LOW |
| `kernelone/benchmark/unified_runner.py` | `ExecuteRoleSessionCommandV1` | LOW |
| `kernelone/llm/toolkit/__init__.py` | `PMToolIntegration` | LOW |
| 56 other files | Various | LOW |

### 4.2 Cell Encapsulation Violations (859 total)

```
polaris.cells.roles.*/internal/* imports from other polaris.cells.*/internal/*
```

---

## 5. Migration Plan (Remaining Work)

### Sprint 1: High Priority (2 files)

| File | Action | Status |
|------|--------|--------|
| `kernelone/multi_agent/bus_port.py` | Already uses factory pattern with lazy import | DONE |
| `kernelone/cognitive/orchestrator.py` | Add `IAlignmentService` port via adapter | **DONE** |

### Sprint 2: Medium Priority (5 files)

| File | Action |
|------|--------|
| `kernelone/context/context_os/summarizers/slm.py` | Create `ITransactionConfig` port |
| `kernelone/benchmark/unified_runner.py` | Create `ISessionCommand` port |
| `kernelone/llm/toolkit/__init__.py` | Use `IRoleToolIntegration` port |

### Sprint 3: Low Priority (56 files)

Systematic migration using the established pattern:
1. Identify the Cell dependency
2. Create a port interface in `kernelone/ports/`
3. Create an adapter in `cells/adapters/kernelone/`
4. Update the KernelOne file to use the port
5. Run ruff/mypy/pytest

---

## 6. Working Example: meta_prompting.py

### 6.1 Before

```python
# kernelone/prompts/meta_prompting.py
from polaris.cells.roles.kernel.public.role_alias import normalize_role_alias
```

### 6.2 After

```python
# kernelone/prompts/meta_prompting.py

# ACGA 2.0: Import from stable adapter (cells/adapters is part of ACGA 2.0)
from polaris.cells.adapters.kernelone import RoleProviderAdapter

# Re-export for backward compatibility
normalize_role_alias = RoleProviderAdapter().normalize_role_alias
```

### 6.3 Key Insight

The Cells' `role_alias.py` is a **public contract** (not internal), so importing from `cells/adapters/kernelone` is acceptable because:
1. `cells/adapters/` is a new ACGA 2.0 structure
2. It provides stable, versioned implementations
3. It maintains the dependency direction (KernelOne uses Cells' implementation)

---

## 7. Working Example: orchestrator.py (Phase 2)

### 7.1 Before

```python
# kernelone/cognitive/orchestrator.py (BEFORE)
from typing import Any
from polaris.cells.values.alignment_service import ValueAlignmentService

self._value_alignment: Any = None
if self._enable_value_alignment:
    self._value_alignment = ValueAlignmentService()
```

### 7.2 After

```python
# kernelone/cognitive/orchestrator.py (AFTER)
from polaris.kernelone.ports import IAlignmentService
from polaris.cells.adapters.kernelone import AlignmentServiceAdapter

self._value_alignment: IAlignmentService | None = None
if self._enable_value_alignment:
    self._value_alignment = AlignmentServiceAdapter()
```

### 7.3 Key Changes

1. Removed direct import of `ValueAlignmentService` from `polaris.cells.values`
2. Now imports `IAlignmentService` from `polaris.kernelone.ports`
3. Now uses `AlignmentServiceAdapter` from `polaris.cells.adapters.kernelone`
4. Proper type annotation: `IAlignmentService | None` instead of `Any`

---

## 8. CI Pre-commit Hook

### 7.1 Usage

```bash
# Check staged files
python scripts/check_cell_imports.py

# Check specific files
python scripts/check_cell_imports.py file1.py file2.py
```

### 7.2 Integration

Add to `.pre-commit-config.yaml`:

```yaml
repos:
  - repo: local
    hooks:
      - id: cell-internal-imports
        name: Block Cell internal imports
        entry: python scripts/check_cell_imports.py
        language: system
        types: [python]
        pass_filenames: true
```

---

## 8. Quality Gates

| Gate | Command | Status |
|------|---------|--------|
| Ruff check | `ruff check polaris/kernelone/ports/ polaris/cells/adapters/` | PASS |
| Mypy | `mypy polaris/kernelone/ports/ polaris/cells/adapters/` | PASS |
| Ruff format | `ruff format --check polaris/kernelone/ports/ polaris/cells/adapters/` | PASS |

---

## 9. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Breaking existing code | Low | High | Maintain backward compatibility aliases |
| Circular dependencies | Medium | High | Strict port/adapter separation |
| Test failures | Low | Medium | Run pytest after each change |

---

## 10. Next Steps

1. **Sprint 1 Complete**: Migrated `orchestrator.py` to use `IAlignmentService`
2. **Complete Sprint 2**: Migrate high-impact files (slm.py, unified_runner.py, toolkit)
3. **Complete Sprint 3**: Systematic migration of remaining files
4. **Add pre-commit hook** to CI pipeline
5. **Document remaining violations** for future sprints

---

## Appendix A: File Locations

```
polaris/
├── kernelone/
│   └── ports/
│       ├── __init__.py
│       ├── role_provider.py
│       ├── bus_port.py
│       └── alignment.py
├── cells/
│   └── adapters/
│       ├── __init__.py
│       └── kernelone/
│           ├── __init__.py
│           ├── role_provider_adapter.py
│           ├── bus_adapter.py
│           └── alignment_adapter.py
└── backend/
    └── scripts/
        └── check_cell_imports.py
```
