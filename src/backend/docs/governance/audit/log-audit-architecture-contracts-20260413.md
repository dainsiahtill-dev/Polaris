# 日志审计任务 #84: 架构契约分析报告

**审计日期**: 2026-04-13
**审计任务**: #84 - 架构契约分析
**审计范围**: `polaris/` 下的 KernelOne 架构契约

---

## 1. 执行摘要

| 审计维度 | 发现问题数 | 严重程度分布 |
|----------|-----------|-------------|
| 跨 Cell 内部导入违规 | 1 | BLOCKER × 1 |
| 重复危险模式定义 | 5 | HIGH × 5 (CELL_KERNELONE_03) |
| 本地路径解析函数重复 | 1 | HIGH × 1 (CELL_KERNELONE_04) |
| 事件流状态所有权模糊 | 1 | BLOCKER × 1 |
| effects_allowed 范围外写入 | 多个 | HIGH × 多处 |

---

## 2. 契约边界违规 (Cross-Cell Internal Import Violations)

### 2.1 BLOCKER: director.tasking 导入 director.execution 内部模块

**违规文件**: `polaris/cells/director/tasking/internal/worker_executor.py` (行 64-78)

```python
# Phase 4 target location (canonical after director.runtime migration)
with contextlib.suppress(ImportError):
    _CGE = _importlib.import_module("polaris.cells.director.tasking.internal.code_generation_engine")

with contextlib.suppress(ImportError):
    _FAS = _importlib.import_module("polaris.cells.director.tasking.internal.file_apply_service")

# Fall back to execution/internal.code_generation_engine directly.
if _CGE is None:
    with contextlib.suppress(ImportError):
        _CGE = _importlib.import_module("polaris.cells.director.execution.internal.code_generation_engine")

if _FAS is None:
    with contextlib.suppress(ImportError):
        _FAS = _importlib.import_module("polaris.cells.director.execution.internal.file_apply_service")
```

**问题分析**:
- `director.tasking` 是独立的 Cell (`polaris/cells/director/tasking/`)
- `director.execution` 也是独立的 Cell (`polaris/cells/director/execution/`)
- 该导入跨越了 Cell 边界，直接引用了 `director.execution.internal` 模块

**违反规则**:
- `no_cross_cell_internal_import` (blocker): Cross-Cell imports may only target a Cell's public surface
- `declared_cell_dependencies_match_imports` (high): Every cross-Cell code dependency must be declared in `depends_on`

**证据**:
- cells.yaml 中 `director.tasking` 声明的 `depends_on` 不包含 `director.execution`
- cells.yaml 中 `director.execution` 未被列为 `director.tasking` 的依赖

**修复建议**:
- 应通过 `director.runtime` 的 public contracts 接口访问
- 或将 `CodeGenerationEngine` 和 `FileApplyService` 迁移到 `director.runtime` Cell

---

## 3. CELL_KERNELONE 集成规则违规

### 3.1 CELL_KERNELONE_03: 重复危险模式定义

**规则要求**: `dangerous_patterns` 必须以 `polaris.kernelone.security.dangerous_patterns` 为唯一 canonical 源头。

**发现的重复定义**:

| 文件位置 | 定义名称 | 行号 | 类型 |
|---------|---------|------|------|
| `polaris/cells/roles/adapters/internal/schemas/director_schema.py` | `dangerous_patterns` | 29 | 列表 |
| `polaris/cells/roles/kernel/internal/output_parser.py` | `DANGEROUS_PATTERNS` | 117 | 类属性 |
| `polaris/cells/roles/kernel/internal/tool_gateway.py` | `dangerous_patterns` | 657-671 | 列表 |
| `polaris/cells/roles/kernel/internal/quality_checker.py` | `dangerous_patterns` | 297 | 列表 |
| `polaris/cells/llm/dialogue/internal/role_dialogue.py` | `dangerous_patterns` | 1244 | 列表 |

**示例 (tool_gateway.py 行 657-671)**:
```python
dangerous_patterns = [
    "../",
    "..\\",
    "..\\/",  # 标准穿越
    "%2e%2e%2f",
    "%252e%252e%252f",  # URL 编码 /
    ...
]
```

**Canonical 源头** (`polaris/kernelone/security/dangerous_patterns.py`):
```python
_DANGEROUS_PATTERNS: Final[list[str]] = [
    "../",
    "..\\",
    "/etc/passwd",
    ...
]
```

**违反规则**: `CELL_KERNELONE_03` (high)

**修复建议**: 所有 Cell 应统一导入 `from polaris.kernelone.security.dangerous_patterns import is_dangerous_command`

---

### 3.2 CELL_KERNELONE_04: 本地路径解析函数重复

**规则要求**: 存储路径解析必须以 `polaris.kernelone.storage.paths` 为唯一 canonical 源头。

**发现的重复定义**:

| 文件位置 | 函数名 | 行号 |
|---------|-------|------|
| `polaris/cells/docs/court_workflow/internal/docs_stage_service.py` | `_resolve_artifact_path` | 189-197 |

**重复代码**:
```python
def _resolve_artifact_path(
    workspace_full: str,
    cache_root_full: str,
    relative: str,
) -> str:
    """Resolve artifact path."""
    if cache_root_full:
        return os.path.join(cache_root_full, relative)
    return os.path.join(workspace_full, relative)
```

**Canonical 源头**: `polaris/kernelone/storage/io_paths.resolve_artifact_path`

**违反规则**: `CELL_KERNELONE_04` (high)

**修复建议**: 移除本地定义，统一使用 `polaris.kernelone.storage.io_paths.resolve_artifact_path`

---

## 4. 状态所有权模糊 (State Ownership Ambiguity)

### 4.1 BLOCKER: 事件流状态所有权冲突

**问题**: `events.fact_stream` 是 `runtime/events/*` 的唯一 owner，但多个 Cell 声称有 `fs.write:runtime/events/*` 权限。

**Owner 声明**:
```yaml
# events.fact_stream/cell.yaml
state_owners:
  - runtime/events/*
effects_allowed:
  - fs.write:runtime/events/*
```

**其他声称写入权限的 Cells** (22 个):
- `roles.runtime`: `fs.write:runtime/events/*`
- `roles.adapters`: `fs.write:runtime/events/*`
- `llm.tool_runtime`: `fs.write:runtime/events/runtime.events.jsonl`
- `audit.verdict`: `fs.write:runtime/events/*`
- `audit.diagnosis`: `fs.write:runtime/events/ws.connection.events.jsonl`
- `director.execution`: `fs.write:runtime/events/director.llm.events.jsonl`
- `factory.pipeline`: `fs.write:runtime/events/*`
- `docs.court_workflow`: `fs.write:runtime/events/runtime.events.jsonl`
- `resident.autonomy`: `fs.write:runtime/events/runtime.events.jsonl`
- `qa.audit_verdict`: `fs.write:runtime/events/runtime.events.jsonl`
- `finops.budget_guard`: `fs.write:runtime/events/runtime.events.jsonl`
- `orchestration.pm_dispatch`: `fs.write:runtime/events/runtime.events.jsonl`
- `orchestration.workflow_runtime`: `fs.write:runtime/events/*`
- `runtime.execution_broker`: `fs.write:runtime/events/*`
- `runtime.task_runtime`: `fs.write:runtime/events/taskboard.terminal.events.jsonl` + `fs.write:runtime/events/task_runtime.execution.jsonl`
- `orchestration.pm_planning`: `fs.write:runtime/events/pm.events.jsonl`
- `architect.design`: `fs.write:runtime/events/runtime.events.jsonl`
- `chief_engineer.blueprint`: `fs.write:runtime/events/runtime.events.jsonl`

**违反规则**: `single_state_owner` (blocker)

**根因分析**: 这是架构迁移期间的设计问题 - `events.fact_stream` 是新引入的 Cell，但其他 Cell 仍在直接写入事件文件。设计意图是让 `events.fact_stream` 提供统一的 fanout 机制，但目前 fanout 尚未完全实现。

**修复建议**:
1. 短期: 在 `events.fact_stream` 中实现 fanout writer，所有事件写入通过 fanout 路由
2. 中期: 其他 Cell 只保留 `fs.read` 权限，写入统一通过 `emit_fact_event()` 接口
3. 长期: 实现事件流的 append-only 契约

---

## 5. effects_allowed 范围外的能力调用

### 5.1 未声明的 fs.write 能力

多个 Cell 在 `effects_allowed` 中未声明其实际执行的写入操作:

| Cell | 实际写入路径 | effects_allowed 声明 | 缺口 |
|-----|------------|-------------------|------|
| `audit.verdict` | `runtime/contracts/*` | 未声明 | 高 |
| `audit.verdict` | `runtime/state/*` | 声明 `fs.write:runtime/state/*` 但过于宽泛 | 中 |
| `runtime.artifact_store` | `runtime/events/runtime.events.jsonl` | 未在 effects_allowed 中明确声明 | 高 |

**示例** (`audit.verdict/cell.yaml`):
```yaml
effects_allowed:
  - fs.write:runtime/state/*
  - fs.write:runtime/events/*  # 过于宽泛
  - fs.write:runtime/contracts/*  # 未在 effects_allowed 中声明!
```

**违反规则**: `undeclared_effects_forbidden` (blocker)

**修复建议**: 每个 Cell 的 `effects_allowed` 必须精确列出其实际写入的每个路径模式

---

## 6. 契约接口审查

### 6.1 Public Contracts 覆盖度

| Cell | 有 public/contracts.py | 符合 catalog 声明 |
|-----|----------------------|-----------------|
| `context.catalog` | 是 | 是 |
| `runtime.state_owner` | 是 | 是 |
| `roles.kernel` | 是 | 是 |
| `roles.adapters` | 是 | 是 |
| `director.execution` | 是 | 是 |
| `director.planning` | 是 (空) | 需验证 |
| `director.tasking` | 是 (空) | 需验证 |
| `director.runtime` | 是 (空) | 需验证 |
| `director.delivery` | 是 (空) | 需验证 |

**观察**: 部分 Cell (director.planning, director.tasking, director.runtime, director.delivery) 的 `public_contracts.modules` 声明存在，但模块内容为空或仅有占位符。这与 cells.yaml 中声明的迁移状态一致 (Phase 2-5 进行中)。

---

## 7. 跨 Cell 调用链分析

### 7.1 合规的跨 Cell 调用 (示例)

```
roles.kernel.internal.tool_gateway
    └── from polaris.kernelone.security.dangerous_patterns import is_dangerous_command  ✅

roles.kernel.internal.policy.layer.budget
    └── from polaris.kernelone.security.dangerous_patterns import is_dangerous_command  ✅
```

### 7.2 违规的跨 Cell 调用 (示例)

```
director.tasking.internal.worker_executor
    └── from polaris.cells.director.execution.internal.code_generation_engine  ❌

director.tasking.internal.worker_executor
    └── from polaris.cells.director.execution.internal.file_apply_service  ❌
```

---

## 8. 依赖链与 Import 分析

### 8.1 从 kernelone 导入 (合规)

Cells 依赖 kernelone 是设计允许的，因为 kernelone 是 Agent-OS 基础设施层。

```
polaris/cells/*/internal/*.py
    └── from polaris.kernelone.* import ...  ✅ 合规
```

### 8.2 从 infrastructure 导入 (合规)

Cells 通过 infrastructure 适配器访问底层能力是允许的。

```
polaris/cells/*/internal/*.py
    └── from polaris.infrastructure.* import ...  ✅ 合规
```

### 8.3 跨 Cell internal 导入 (违规)

```
polaris/cells/director/tasking/internal/*.py
    └── from polaris.cells.director.execution.internal import ...  ❌ 违规
```

---

## 9. 治理门禁建议

### 9.1 自动化检测规则

建议在 `fitness-rules.yaml` 中新增/强化以下规则:

```yaml
- id: cell_internal_import_fence
  severity: blocker
  description: >
    No Cell may import from another Cell's internal namespace.
    All cross-Cell communication must go through public contracts.
  evidence:
    - polaris/cells/*/internal/**
  current_status: draft
  desired_automation:
    - parse Python imports
    - map import paths to owning Cells
    - fail if import crosses Cell boundary without public contract
```

### 9.2 修复优先级

| 优先级 | 问题 | 修复成本 | 影响范围 |
|-------|------|---------|---------|
| P0 | director.tasking → director.execution 内部导入 | 中 | 1 文件 |
| P1 | 重复 dangerous_patterns 定义 | 低 | 5 文件 |
| P1 | _resolve_artifact_path 重复定义 | 低 | 1 文件 |
| P2 | 事件流状态所有权模糊 | 高 | 22 Cells |
| P2 | effects_allowed 精确度不足 | 中 | 多个 Cells |

---

## 10. 结论

当前 `polaris/` 架构契约存在以下主要问题:

1. **BLOCKER**: `director.tasking` 直接导入 `director.execution.internal` 模块，违反跨 Cell 边界规则
2. **HIGH**: 5 处重复的 `dangerous_patterns` 定义，违反 CELL_KERNELONE_03
3. **HIGH**: 1 处重复的 `_resolve_artifact_path` 定义，违反 CELL_KERNELONE_04
4. **BLOCKER**: `events.fact_stream` 是 `runtime/events/*` 的唯一 owner，但 22 个 Cell 声称直接写入权限

**根本原因**: 架构迁移期间，多个 Cell 仍在使用旧路径，public contracts 尚未完全建立。

**建议行动**:
1. 立即修复 director.tasking → director.execution 的内部导入 (P0)
2. 将所有 dangerous_patterns 引用统一到 kernelone.security.dangerous_patterns (P1)
3. 将 _resolve_artifact_path 引用统一到 kernelone.storage.io_paths (P1)
4. 规划 events.fact_stream fanout writer 实现 (P2，长期)

---

**审计员**: 日志审计专家
**报告版本**: v1.0
**下次审计计划**: 2026-04-20
