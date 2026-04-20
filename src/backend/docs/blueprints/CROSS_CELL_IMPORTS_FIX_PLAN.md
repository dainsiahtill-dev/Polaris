# P0-008: Cross-Cell Internal Imports Fix Plan

**Created**: 2026-04-05
**Status**: Analysis Complete, Fix Planning
**Violations Found**: 373 total (348 Type A + 25 Type C)

---

## Executive Summary

This document outlines the fix strategy for P0-008 - cross-cell internal imports that violate ACGA 2.0 Rule 2 (Cell boundaries). Cells and KernelOne modules must only import from public contracts, not internal implementations.

---

## Violation Categories

### Type A: Cells Importing KernelOne Internal (348 violations)
```
polaris.cells.X.* → polaris.kernelone.Y.internal.*
```

### Type B: Cells Importing Other Cells' Internal (Multiple violations)
```
polaris.cells.X.* → polaris.cells.Y.internal.*
```

### Type C: KernelOne Importing Cells Internal (25 violations)
```
polaris.kernelone.X.* → polaris.cells.Y.internal.*
```

---

## Detailed Findings

### Type A Violations by KernelOne Module (348 total)

| Rank | Module | Count | Primary Imports |
|------|--------|-------|-----------------|
| 1 | `llm` | 58 | KernelLLM, config_store, toolkit |
| 2 | `fs` | 41 | KernelFileSystem, get_default_adapter |
| 3 | `process` | 23 | CommandExecutionService, command_executor |
| 4 | `events` | 20 | MessageBus, MessageType, emit_event |
| 5 | `workflow` | 19 | engine, contracts, timer_wheel |
| 6 | `runtime` | 19 | shared_types, constants |
| 7 | `context` | 17 | context_os, budget_gate |
| 8 | `storage` | 16 | resolve_runtime_path, resolve_storage_roots |
| 9 | `utils` | 13 | json_utils, time_utils |
| 10 | `memory` | 10 | memory_store, retrieval_ranker |
| 11 | `prompts` | 5 | loader, meta_prompting |
| 12 | `tools` | 4 | contracts, runtime_executor |
| 13 | `telemetry` | 4 | debug_stream |
| 14 | `audit` | 4 | KernelAuditEventType |
| 15 | `security` | 3 | dangerous_patterns |
| 16 | `trace` | 2 | get_tracer |
| 17 | `db` | 2 | KernelDatabase |
| 18 | `agent_runtime` | 2 | bus_port |
| 19 | Other | 13 | Various single-occurrence imports |

### Type C Violations (25 total)

| File | Import | Issue |
|------|--------|-------|
| `neural_syndicate/base_agent.py` | `roles.runtime.internal.bus_port` | Circular: KernelOne → Cells |
| `neural_syndicate/broker.py` | `roles.runtime.internal.bus_port` | Circular: KernelOne → Cells |
| `neural_syndicate/nats_broker.py` | `roles.runtime.internal.kernel_one_bus_port` | Circular: KernelOne → Cells |
| `llm/toolkit/__init__.py` | `llm.tool_runtime.internal.role_integrations` | Tool integrations leak |
| `policy/__init__.py` | `policy.permission.internal` | Policy boundary violation |

---

## Fix Strategy by Violation Type

### Type A Fix: Create Public Contracts in KernelOne

For each `polaris.kernelone.X.internal.*` imported by cells, create a public contract:

```
polaris/kernelone/X/public/
├── contracts.py      # Interfaces, base classes
├── service.py        # Facade implementations
└── __init__.py       # Public exports
```

**Priority 1 (Most Critical - 58+ violations)**
- `polaris/kernelone/llm/public/` - LLM abstractions
- `polaris/kernelone/fs/public/` - FileSystem abstractions
- `polaris/kernelone/process/public/` - Command execution

**Priority 2 (High - 20-40 violations)**
- `polaris/kernelone/events/public/` - Event bus abstractions
- `polaris/kernelone/workflow/public/` - Workflow engine contracts
- `polaris/kernelone/runtime/public/` - Runtime utilities
- `polaris/kernelone/context/public/` - Context contracts

**Priority 3 (Medium - 10-20 violations)**
- `polaris/kernelone/storage/public/` - Storage path resolution
- `polaris/kernelone/memory/public/` - Memory abstractions
- `polaris/kernelone/utils/public/` - Utility contracts

**Priority 4 (Low - <10 violations)**
- `polaris/kernelone/tools/public/`
- `polaris/kernelone/telemetry/public/`
- `polaris/kernelone/audit/public/`
- `polaris/kernelone/security/public/`
- `polaris/kernelone/trace/public/`
- `polaris/kernelone/db/public/`

### Type B Fix: Use Existing Public Contracts

Many cells already have public contracts defined. Cells should migrate to use them.

**Quick Wins**:
- `polaris/cells/roles/runtime/public/` - Already exists, used correctly by public/service.py
- `polaris/cells/director/execution/public/` - Already exists
- `polaris/cells/archive/run_archive/public/` - Already exists

**Files Needing Migration**:
See Appendix A for complete list.

### Type C Fix: Dependency Injection / Lazy Imports

For `polaris.kernelone → polaris.cells` circular dependencies:

1. **Lazy Imports**: Import inside function bodies to avoid circular import at module load
2. **Dependency Injection**: Inject dependencies via constructor/function parameters
3. **Port Interfaces**: Define port interfaces in KernelOne that cells implement

**Neural Syndicate Fix**:
```python
# BEFORE (circular)
from polaris.cells.roles.runtime.internal.bus_port import InMemoryAgentBusPort

# AFTER (lazy import + DI)
class NeuralSyndicateAgent:
    def __init__(self, bus_port: Optional[AgentBusPort] = None):
        self._bus_port = bus_port

    def _get_bus_port(self) -> AgentBusPort:
        if self._bus_port is None:
            from polaris.cells.roles.runtime.public.bus_port import InMemoryAgentBusPort
            self._bus_port = InMemoryAgentBusPort()
        return self._bus_port
```

---

## Implementation Plan

### Phase 1: Quick Wins (Week 1) - 7 Cells

1. **Fix Type C Violations First** (25 files)
   - `polaris/kernelone/agent_runtime/neural_syndicate/` - 3 main files
   - `polaris/kernelone/llm/toolkit/__init__.py` - Remove commented imports
   - `polaris/kernelone/policy/__init__.py` - Remove internal references

2. **Fix `polaris/cells/roles/kernel/`** (most critical - 42 files)
   - Create public contracts for: `fs`, `llm`, `process`, `context`
   - Files: `polaris/cells/roles/kernel/internal/`

### Phase 2: Core Infrastructure (Week 2-3) - 20+ Cells

1. **Create KernelOne Public Contracts**:
   - `fs/public/` - KernelFileSystem abstraction
   - `llm/public/` - KernelLLM abstraction
   - `process/public/` - CommandExecutionService abstraction

2. **Fix High-Impact Cells**:
   - `polaris/cells/director/execution/` - 4 files
   - `polaris/cells/director/planning/` - 3 files
   - `polaris/cells/director/tasking/` - 2 files
   - `polaris/cells/orchestration/workflow_runtime/` - 8 files

### Phase 3: Remaining Cells (Week 4) - 30+ Cells

1. Create remaining KernelOne public contracts
2. Migrate all cells to use public contracts
3. Remove internal imports from cells

### Phase 4: Validation (Week 5)

1. Run linting and type checking
2. Execute test suites
3. Verify no internal imports remain

---

## Files Requiring Modification

### Priority 1 - Immediate Fix Required

| File | Current Import | Required Action |
|------|---------------|-----------------|
| `polaris/kernelone/agent_runtime/neural_syndicate/base_agent.py` | `roles.runtime.internal.bus_port` | DI + lazy import |
| `polaris/kernelone/agent_runtime/neural_syndicate/broker.py` | `roles.runtime.internal.bus_port` | DI + lazy import |
| `polaris/kernelone/agent_runtime/neural_syndicate/nats_broker.py` | `roles.runtime.internal.kernel_one_bus_port` | DI + lazy import |
| `polaris/cells/roles/kernel/internal/turn_engine/engine.py` | `kernelone.fs`, `kernelone.llm`, etc. | Create public contracts + migrate |
| `polaris/cells/roles/kernel/internal/tool_gateway.py` | `kernelone.fs`, `kernelone.llm` | Create public contracts + migrate |
| `polaris/cells/roles/kernel/internal/services/llm_invoker.py` | `kernelone.llm` | Create public contracts + migrate |

### Priority 2 - High Impact (Top 10 cells by violation count)

| Cell | Violation Count | Key Imports |
|------|-----------------|-------------|
| `roles.kernel` | 42 | fs, llm, process, context |
| `llm.provider_runtime` | 15 | llm.config_store |
| `director.execution` | 12 | fs, process, events |
| `director.planning` | 8 | fs, llm |
| `audit.diagnosis` | 7 | audit, fs, process |
| `audit.evidence` | 5 | audit, fs, storage |
| `audit.verdict` | 3 | fs |
| `context.catalog` | 2 | fs |
| `context.engine` | 1 | runtime.shared_types |
| `archive.run_archive` | 5 | fs, events |

### Priority 3 - Medium Impact

| Cell | Violation Count |
|------|-----------------|
| `orchestration.pm_dispatch` | 3 |
| `orchestration.pm_planning` | 3 |
| `orchestration.workflow_runtime` | 8 |
| `orchestration.workflow_activity` | 3 |
| `factory.pipeline` | 5 |
| `llm.control_plane` | 3 |
| `llm.evaluation` | 3 |
| `llm.tool_runtime` | 2 |
| `finops.budget_guard` | 2 |
| `workspace.integrity` | 3 |
| `roles.adapters` | 6 |
| `roles.session` | 7 |
| `roles.profile` | 1 |
| `runtime.artifact_store` | 2 |
| `runtime.execution_broker` | 1 |
| `runtime.projection` | 8 |
| `runtime.state_owner` | 2 |
| `runtime.task_runtime` | 2 |
| `storage.layout` | 2 |
| `docs.court_workflow` | 1 |

---

## Verification Commands

```bash
# Check for Type A violations
grep -r "from polaris\.kernelone\." polaris/cells/ --include="*.py" | grep "internal" | wc -l

# Check for Type C violations
grep -r "from polaris\.cells\." polaris/kernelone/ --include="*.py" | grep "internal" | wc -l

# Ruff check on roles/kernel
python -m ruff check polaris/cells/roles/kernel/internal/ --fix

# Mypy on roles/kernel
python -m mypy polaris/cells/roles/kernel/internal/ --follow-imports=skip --ignore-missing-imports

# Pytest on roles/kernel
python -m pytest polaris/cells/roles/kernel/tests/ -q --tb=no

# Run catalog governance gate
python docs/governance/ci/scripts/run_catalog_governance_gate.py --workspace . --mode audit-only
```

---

## Appendix A: Complete File List

### Type A - Cells Importing KernelOne Internal (348 files)

Full list saved to: `docs/blueprints/CROSS_CELL_IMPORTS_FIX_PLAN_FILES_TYP_A.txt`

### Type C - KernelOne Importing Cells Internal (25 files)

```
polaris/kernelone/agent_runtime/neural_syndicate/base_agent.py
polaris/kernelone/agent_runtime/neural_syndicate/broker.py
polaris/kernelone/agent_runtime/neural_syndicate/nats_broker.py
polaris/kernelone/agent_runtime/neural_syndicate/tests/test_nats_broker.py
polaris/kernelone/events/tests/test_uep_sinks.py (3 instances)
polaris/kernelone/llm/contracts/adapters.py
polaris/kernelone/llm/toolkit/__init__.py (3 instances)
polaris/kernelone/policy/__init__.py (2 instances)
polaris/kernelone/storage/policy.py (3 instances)
polaris/kernelone/storage/tests/test_layout_no_cell_import.py (3 instances)
```

---

## Appendix B: KernelOne Modules Requiring Public Contracts

### Must Create Public Contracts For:
1. `polaris/kernelone/fs/` - FileSystem abstraction (41 violations)
2. `polaris/kernelone/llm/` - LLM abstraction (58 violations)
3. `polaris/kernelone/process/` - Command execution (23 violations)
4. `polaris/kernelone/events/` - Event bus (20 violations)
5. `polaris/kernelone/workflow/` - Workflow engine (19 violations)
6. `polaris/kernelone/runtime/` - Runtime utilities (19 violations)
7. `polaris/kernelone/context/` - Context contracts (17 violations)
8. `polaris/kernelone/storage/` - Storage paths (16 violations)
9. `polaris/kernelone/utils/` - Utilities (13 violations)
10. `polaris/kernelone/memory/` - Memory abstraction (10 violations)
11. `polaris/kernelone/prompts/` - Prompt loading (5 violations)
12. `polaris/kernelone/tools/` - Tool contracts (4 violations)
13. `polaris/kernelone/telemetry/` - Telemetry (4 violations)
14. `polaris/kernelone/audit/` - Audit events (4 violations)
15. `polaris/kernelone/security/` - Security patterns (3 violations)
16. `polaris/kernelone/trace/` - Tracing (2 violations)
17. `polaris/kernelone/db/` - Database (2 violations)
18. `polaris/kernelone/agent_runtime/` - Agent runtime (2 violations)

---

## Appendix C: Existing Public Contracts (Do Not Duplicate)

| Cell | Public Contract Location |
|------|---------------------------|
| `roles.runtime` | `polaris/cells/roles/runtime/public/` |
| `roles.session` | `polaris/cells/roles/session/public/` |
| `director.execution` | `polaris/cells/director/execution/public/` |
| `archive.run_archive` | `polaris/cells/archive/run_archive/public/` |
| `context.catalog` | `polaris/cells/context/catalog/public/` |
| `policy.workspace_guard` | `polaris/cells/policy/workspace_guard/public/` |

---

## Risk Assessment

| Risk | Impact | Mitigation |
|------|--------|------------|
| Breaking changes to existing imports | High | Create public contracts before removing internal access |
| Circular dependency explosion | High | Use lazy imports and DI as immediate fix |
| Test failures | Medium | Run test suite after each phase |
| Performance regression | Low | Monitor benchmark tests |

---

## Success Criteria

1. `grep -r "from polaris\.kernelone\." polaris/cells/ --include="*.py" | grep "internal"` returns 0 results
2. `grep -r "from polaris\.cells\." polaris/kernelone/ --include="*.py" | grep "internal"` returns 0 results (excluding test files)
3. All cells have public contracts for their externally-consumed capabilities
4. All ruff checks pass
5. All mypy checks pass
6. All pytest tests pass
