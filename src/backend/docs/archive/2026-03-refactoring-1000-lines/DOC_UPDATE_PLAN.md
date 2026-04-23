# 文档更新计划

**版本**: 2026-03-31
**状态**: Draft
**负责人**: E9: Documentation Lead

---

## 1. 执行摘要

本计划定义重构超过1000行文件后需要更新的文档清单、具体内容和优先级。

---

## 2. 需要更新的文档清单

### 2.1 核心指令文档 (P0 - 最高优先级)

| 文档 | 路径 | 更新类型 | 优先级 |
|------|------|----------|--------|
| AGENTS.md | `src/backend/AGENTS.md` | 架构快照同步 | P0 |
| CLAUDE.md (根目录) | `CLAUDE.md` | 引用路径更新 | P0 |
| CLAUDE.md (backend) | `src/backend/CLAUDE.md` | 架构快照同步 | P0 |

### 2.2 迁移日志文档 (P1)

| 文档 | 路径 | 更新类型 | 优先级 |
|------|------|----------|--------|
| KERNELONE_KERNEL_MIGRATION_LOG.md | `src/backend/docs/KERNELONE_KERNEL_MIGRATION_LOG.md` | 新增重构记录 | P1 |
| TurnEngine Transactional Tool Flow | `src/backend/docs/TurnEngine Transactional Tool Flow - 完整落地蓝图.md` | 导入路径更新 | P1 |

### 2.3 Cell README.agent.md (P1)

| 文档 | 路径 | 更新内容 |
|------|------|----------|
| roles/kernel README.agent.md | `polaris/cells/roles/kernel/README.agent.md` | 模块结构说明 |
| roles/runtime README.agent.md | `polaris/cells/roles/runtime/README.agent.md` | 模块结构说明 |

### 2.4 蓝图文档 (P2)

| 文档 | 路径 | 更新类型 |
|------|------|----------|
| BLUEPRINT.md | `docs/blueprints/refactoring-1000-lines-20260331/BLUEPRINT.md` | 状态更新 |
| INTERFACE_CONTRACT.md | `docs/blueprints/refactoring-1000-lines-20260331/INTERFACE_CONTRACT.md` | 新建 |

### 2.5 测试文档 (P2)

| 文档 | 路径 | 更新内容 |
|------|------|----------|
| 测试报告 | `polaris/tests/*.md` | 新增重构测试说明 |

---

## 3. 各文档详细更新内容

### 3.1 AGENTS.md 更新

**更新位置**: §15 当前架构现实快照

**具体更新内容**:

```markdown
### 15.X 重构后模块结构（2026-03-31）

以下核心文件已重构为模块化结构：

#### turn_engine.py → turn_engine/
| 原文件行数 | 重构后结构 |
|------------|------------|
| 2033行 | `turn_engine/__init__.py` (重导出) |
|          | `turn_engine/engine.py` (~600行) |
|          | `turn_engine/config.py` (~120行) |
|          | `turn_engine/artifacts.py` (~200行) |

**导入路径保持不变**:
```python
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine, TurnEngineConfig
```

#### llm_caller.py → llm_caller/
| 原文件行数 | 重构后结构 |
|------------|------------|
| 2869行 | `llm_caller/__init__.py` (重导出) |
|          | `llm_caller/caller.py` (~600行) |
|          | `llm_caller/retry_policy.py` (~200行) |
|          | `llm_caller/response_parser.py` (~250行) |

**导入路径保持不变**:
```python
from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller
```

#### context_os/runtime.py 拆分
| 原文件行数 | 重构后结构 |
|------------|------------|
| 2013行 | `context_os/runtime.py` (~800行) |
|          | `context_os/classifier.py` (~250行) |
|          | `context_os/patterns.py` (~200行) |
|          | `context_os/helpers.py` (~300行) |

**导入路径保持不变**:
```python
from polaris.kernelone.context.context_os.runtime import StateFirstContextOS
```

#### kernel.py → kernel/
| 原文件行数 | 重构后结构 |
|------------|------------|
| 1761行 | `kernel/__init__.py` (重导出) |
|          | `kernel/kernel.py` (~600行) |
|          | `kernel/retry_handler.py` (~300行) |
|          | `kernel/prompt_adapter.py` (~350行) |

**导入路径保持不变**:
```python
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
```

#### runtime/service.py → service/
| 原文件行数 | 重构后结构 |
|------------|------------|
| 2095行 | `service/__init__.py` (重导出) |
|          | `service/service.py` (~500行) |
|          | `service/persistence.py` (~300行) |
|          | `service/context_adapter.py` (~350行) |

**导入路径保持不变**:
```python
from polaris.cells.roles.runtime.public.service import RoleRuntimeService
```

#### tool_loop_controller.py 拆分
| 原文件行数 | 重构后结构 |
|------------|------------|
| ~800行 | `tool_loop_controller.py` (~500行) |
|          | `context_event.py` (~200行) |
|          | `tool_result_formatter.py` (~150行) |

**导入路径保持不变**:
```python
from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController
```
```

---

### 3.2 CLAUDE.md (根目录) 更新

**更新位置**: §2 维护优先级路径

**具体更新内容**:

添加模块化结构说明：

```markdown
## 2.7 核心模块结构（2026-03-31）

以下核心模块已重构为模块化结构，导入路径保持向后兼容：

### turn_engine
```python
# ✅ 正确用法（向后兼容）
from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine, TurnEngineConfig

# 内部模块结构
polaris/cells/roles/kernel/internal/turn_engine/
├── __init__.py      # 重导出 TurnEngine, TurnEngineConfig
├── engine.py        # TurnEngine 核心类
├── config.py        # TurnEngineConfig, SafetyState
└── artifacts.py     # AssistantTurnArtifacts
```

### llm_caller
```python
# ✅ 正确用法（向后兼容）
from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller

# 内部模块结构
polaris/cells/roles/kernel/internal/llm_caller/
├── __init__.py           # 重导出 LLMCaller
├── caller.py             # LLMCaller 核心类
├── retry_policy.py       # RetryPolicy
└── response_parser.py    # ResponseParser
```

### kernel
```python
# ✅ 正确用法（向后兼容）
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel

# 内部模块结构
polaris/cells/roles/kernel/internal/kernel/
├── __init__.py           # 重导出 RoleExecutionKernel
├── kernel.py             # RoleExecutionKernel 核心类
├── retry_handler.py      # RetryHandler
└── prompt_adapter.py     # PromptAdapter
```

### service
```python
# ✅ 正确用法（向后兼容）
from polaris.cells.roles.runtime.public.service import RoleRuntimeService

# 内部模块结构
polaris/cells/roles/runtime/public/service/
├── __init__.py           # 重导出 RoleRuntimeService
├── service.py            # RoleRuntimeService 协调器
├── persistence.py        # SessionPersistence
└── context_adapter.py    # ContextOSAdapter
```
```

---

### 3.3 CLAUDE.md (backend) 更新

**同步 AGENTS.md §15.X 内容**

---

### 3.4 KERNELONE_KERNEL_MIGRATION_LOG.md 更新

**新增 Phase 8: 模块化重构**

```markdown
## Phase 8: 模块化重构 (2026-03-31)

| 任务 | 负责人 | 状态 | 完成日期 | 备注 |
|------|--------|------|----------|------|
| Task #1: turn_engine 模块化 | E1 | ✅ 完成 | 2026-03-31 | config.py + artifacts.py + engine.py |
| Task #2: llm_caller 模块化 | E3 | ✅ 完成 | 2026-03-31 | retry_policy.py + response_parser.py + caller.py |
| Task #3: context_os 拆分 | E2 | ✅ 完成 | 2026-03-31 | classifier.py + patterns.py + helpers.py |
| Task #4: kernel 模块化 | E5 | ✅ 完成 | 2026-03-31 | retry_handler.py + prompt_adapter.py + kernel.py |
| Task #5: service 模块化 | E4 | ✅ 完成 | 2026-03-31 | persistence.py + context_adapter.py + service.py |
| Task #6: controller 拆分 | E6 | ✅ 完成 | 2026-03-31 | context_event.py + tool_result_formatter.py |

### 阶段门禁状态

| Phase | 状态 | 验证 |
|-------|------|------|
| Phase 8 | ✅ 通过 | `from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine` |
| Phase 8 | ✅ 通过 | `from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller` |
| Phase 8 | ✅ 通过 | `from polaris.kernelone.context.context_os.runtime import StateFirstContextOS` |
| Phase 8 | ✅ 通过 | `from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel` |
| Phase 8 | ✅ 通过 | `from polaris.cells.roles.runtime.public.service import RoleRuntimeService` |

### 新增文件清单

```
polaris/cells/roles/kernel/internal/turn_engine/__init__.py
polaris/cells/roles/kernel/internal/turn_engine/engine.py
polaris/cells/roles/kernel/internal/turn_engine/config.py
polaris/cells/roles/kernel/internal/turn_engine/artifacts.py

polaris/cells/roles/kernel/internal/llm_caller/__init__.py
polaris/cells/roles/kernel/internal/llm_caller/caller.py
polaris/cells/roles/kernel/internal/llm_caller/retry_policy.py
polaris/cells/roles/kernel/internal/llm_caller/response_parser.py

polaris/kernelone/context/context_os/classifier.py
polaris/kernelone/context/context_os/patterns.py
polaris/kernelone/context/context_os/helpers.py

polaris/cells/roles/kernel/internal/kernel/__init__.py
polaris/cells/roles/kernel/internal/kernel/kernel.py
polaris/cells/roles/kernel/internal/kernel/retry_handler.py
polaris/cells/roles/kernel/internal/kernel/prompt_adapter.py

polaris/cells/roles/runtime/public/service/__init__.py
polaris/cells/roles/runtime/public/service/service.py
polaris/cells/roles/runtime/public/service/persistence.py
polaris/cells/roles/runtime/public/service/context_adapter.py

polaris/cells/roles/kernel/internal/context_event.py
polaris/cells/roles/kernel/internal/tool_result_formatter.py
```
```

---

### 3.5 README.agent.md 更新

**roles/kernel README.agent.md**:

添加模块结构说明：

```markdown
## 内部模块结构

### turn_engine/
- `engine.py` - TurnEngine 核心循环
- `config.py` - 配置类和状态
- `artifacts.py` - 数据类和辅助类

### llm_caller/
- `caller.py` - LLMCaller 核心
- `retry_policy.py` - 重试策略
- `response_parser.py` - 响应解析

### kernel/
- `kernel.py` - RoleExecutionKernel 核心
- `retry_handler.py` - 重试处理
- `prompt_adapter.py` - 提示词构建
```

---

### 3.6 INTERFACE_CONTRACT.md 新建

**创建接口契约文档**:

```markdown
# 接口契约

**版本**: 2026-03-31

## 1. 公共接口保留

所有重构必须保持以下公共接口不变：

### 1.1 turn_engine

```python
# 公共接口
from polaris.cells.roles.kernel.internal.turn_engine import (
    TurnEngine,
    TurnEngineConfig,
    SafetyState,
    AssistantTurnArtifacts,
)

# 内部接口（仅限同模块使用）
from polaris.cells.roles.kernel.internal.turn_engine.engine import TurnEngine
from polaris.cells.roles.kernel.internal.turn_engine.config import TurnEngineConfig
```

### 1.2 llm_caller

```python
# 公共接口
from polaris.cells.roles.kernel.internal.llm_caller import (
    LLMCaller,
    LLMResponse,
)

# 内部接口（仅限同模块使用）
from polaris.cells.roles.kernel.internal.llm_caller.caller import LLMCaller
from polaris.cells.roles.kernel.internal.llm_caller.retry_policy import RetryPolicy
```

### 1.3 kernel

```python
# 公共接口
from polaris.cells.roles.kernel.internal.kernel import (
    RoleExecutionKernel,
)

# 内部接口（仅限同模块使用）
from polaris.cells.roles.kernel.internal.kernel.kernel import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.retry_handler import RetryHandler
```

### 1.4 service

```python
# 公共接口
from polaris.cells.roles.runtime.public.service import (
    RoleRuntimeService,
)

# 内部接口（仅限同模块使用）
from polaris.cells.roles.runtime.public.service.service import RoleRuntimeService
from polaris.cells.roles.runtime.public.service.persistence import SessionPersistence
```

## 2. 模块间依赖

```
turn_engine ─────► llm_caller
      │               │
      │               ▼
      │           kernel
      │               │
      ▼               ▼
tool_loop_controller
      │
      ▼
   service ─────► context_os
```

## 3. 禁止行为

1. ❌ 从 `internal/` 子模块直接导入（应使用顶层导入）
2. ❌ 绕过 `__init__.py` 重导出
3. ❌ 跨 Cell 直接导入内部实现
```

---

## 4. 更新优先级

| 优先级 | 文档 | 原因 |
|--------|------|------|
| P0 | AGENTS.md, CLAUDE.md | 架构真相，必须同步 |
| P1 | KERNELONE_KERNEL_MIGRATION_LOG.md, README.agent.md | 迁移记录，Cell 文档 |
| P2 | INTERFACE_CONTRACT.md, 测试文档 | 契约定义，测试说明 |

---

## 5. 执行检查清单

### Wave 1 完成后

- [ ] 读取重构后的模块结构
- [ ] 记录新增文件清单
- [ ] 确认导入路径向后兼容

### Wave 2 完成后

- [ ] 更新导入路径测试
- [ ] 验证 `__init__.py` 重导出

### Wave 3 完成后

- [ ] 更新 AGENTS.md §15.X
- [ ] 更新 CLAUDE.md §2.7
- [ ] 更新 KERNELONE_KERNEL_MIGRATION_LOG.md

### Wave 4 完成后

- [ ] 创建 INTERFACE_CONTRACT.md
- [ ] 更新 README.agent.md
- [ ] 验证所有文档一致性

---

## 6. 验证命令

```bash
# 验证导入路径向后兼容
python -c "from polaris.cells.roles.kernel.internal.turn_engine import TurnEngine"
python -c "from polaris.cells.roles.kernel.internal.llm_caller import LLMCaller"
python -c "from polaris.kernelone.context.context_os.runtime import StateFirstContextOS"
python -c "from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel"
python -c "from polaris.cells.roles.runtime.public.service import RoleRuntimeService"
python -c "from polaris.cells.roles.kernel.internal.tool_loop_controller import ToolLoopController"

# 验证文档一致性
grep -r "turn_engine.py" docs/ --include="*.md"
grep -r "llm_caller.py" docs/ --include="*.md"
```

---

## 7. 交付物清单

| 交付物 | 状态 |
|--------|------|
| AGENTS.md 更新 | Pending |
| CLAUDE.md (根目录) 更新 | Pending |
| CLAUDE.md (backend) 更新 | Pending |
| KERNELONE_KERNEL_MIGRATION_LOG.md 更新 | Pending |
| INTERFACE_CONTRACT.md 新建 | Pending |
| README.agent.md 更新 | Pending |
| CHANGELOG 条目 | Pending |

---

## 8. 附录

### A. 参考文档

- `src/backend/AGENTS.md` - 后端权威入口
- `src/backend/docs/KERNELONE_KERNEL_MIGRATION_LOG.md` - 迁移日志
- `docs/blueprints/refactoring-1000-lines-20260331/BLUEPRINT.md` - 重构蓝图

### B. 文档同步协议

每次更新文档后，必须：
1. 更新本文档状态
2. 验证三个核心文档一致性（AGENTS.md + CLAUDE.md + CLAUDE.md backend）
3. 验证导入路径向后兼容