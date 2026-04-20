# Roles Cell Technical Debt Tracker

**Document Version**: 2.0.0
**Created**: 2026-03-26
**Last Updated**: 2026-04-09
**Owner**: Polaris Architecture Review Board

---

## Overview

This document tracks all technical debt in the `polaris/cells/roles` Cell and its sub-Cells.
It serves as the single source of truth for pending deletions, refactoring items, and migration deadlines.

> **⚠️ ARCHIVED**: This document is superseded by `MIGRATION_COMPLETION_BLUEPRINT_20260409.md`
> and `FULL_CONVERGENCE_AUDIT_20260405.md`. Most items below have been resolved.

---

## Phase 4 Legacy Code - RESOLVED

### H3: Legacy AgentService Freeze - ✅ COMPLETED

| Item | File Path | Status | Resolution Date |
|------|-----------|--------|-----------------|
| StandaloneRunner | `runtime/internal/standalone_runner.py` | ✅ DELETED | 2026-04-05 |
| TUI Console | `runtime/internal/tui_console.py` | ✅ DELETED | 2026-04-05 |
| AgentService | `runtime/internal/role_agent_service.py` | ✅ DEPRECATED | 2026-04-09 |

**Migration Path**:
```python
# OLD (deprecated)
from polaris.cells.roles.runtime.internal.role_agent_service import AgentService
service = AgentService(workspace=".", agents=["PM", "Director"])

# NEW (production path)
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
service = RoleRuntimeService(workspace=".")
result = await service.execute_role_session(...)
```

**Unified Execution Path**:
```
RoleRuntimeService -> RoleExecutionKernel (CHAT | WORKFLOW mode)
```

---

### C1: Phase 4 Dual Execution Path Violations - ✅ RESOLVED

| Item | File Path | Status | Resolution Date |
|------|-----------|--------|-----------------|
| `standalone_runner.py` | `runtime/internal/standalone_runner.py` | ✅ DELETED | 2026-04-05 |
| `tui_console.py` | `runtime/internal/tui_console.py` | ✅ DELETED | 2026-04-05 |

---

## P1 Technical Debt Items - RESOLVED

### H1: Context Compression Not Activated - ✅ COMPLETED

**Resolution**: Implemented per FULL_CONVERGENCE_AUDIT_20260405

### H2: Structured Output Fallback Type Safety - ✅ COMPLETED

**Resolution**: GenericRoleResponse fallback schema implemented per FULL_CONVERGENCE_AUDIT_20260405

---

## P2 Technical Debt Items - RESOLVED

### M1: Tool Gateway Coupling - ✅ COMPLETED

**Resolution**: ToolGatewayPort Protocol defined per FULL_CONVERGENCE_AUDIT_20260405

### M2: Retry Strategy Hardcoded - ✅ COMPLETED

**Resolution**: KernelConfig dataclass implemented per FULL_CONVERGENCE_AUDIT_20260405

### M3: Session Service Incomplete - ✅ COMPLETED

**Resolution**: Session persistence and TTL cleanup implemented per FULL_CONVERGENCE_AUDIT_20260405

---

## Remaining Work

| Item | Status | Notes |
|------|--------|-------|
| `role_agent_service.py` cleanup | ⏳ PENDING | DEPRECATED but still present; requires final deletion |

---

## Verification Commands

```bash
# Check for deprecation warnings
python -W default::DeprecationWarning -c "from polaris.cells.roles.runtime.internal import agent_service"

# Check for frozen markers
grep -r "__frozen__\|DEPRECATED\|deprecated" polaris/cells/roles/runtime/internal/*.py

# Verify no legacy paths in cell.yaml
grep -E "standalone|tui" polaris/cells/roles/cell.yaml
```

---

## Change Log

| Date | Version | Changes |
|------|---------|--------|
| 2026-03-26 | 1.0.0 | Initial version, added H3 deprecation markers |
| 2026-03-26 | 1.0.0 | Created tech-debt-tracker.md |
| 2026-04-09 | 2.0.0 | Major update: marked all Phase 4 items as resolved per FULL_CONVERGENCE_AUDIT_20260405 |

---

**Document Status**: ARCHIVED - Superseded by MIGRATION_COMPLETION_BLUEPRINT_20260409.md
**Next Review**: N/A - Document archived
