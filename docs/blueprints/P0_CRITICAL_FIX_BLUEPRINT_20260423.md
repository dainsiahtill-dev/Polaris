# Polaris P0 Critical Issues Fix Blueprint

**文档编号**: BLUEPRINT-2026-0423-P0-FIX
**日期**: 2026-04-23
**状态**: Phase 1 蓝图 — 待 Phase 2 工程落地
**优先级**: P0 (修复完成前禁止发布)
**架构师**: Principal Architect (中书令)

---

## 1. 执行摘要

本蓝图定义了 Polaris 项目 **6 个 P0 Critical Issues** 的系统性修复方案。目标是：
1. 消除架构债务，恢复 ACGA 2.0 合规性
2. 修复安全漏洞 (command injection)
3. 消除性能瓶颈 (async blocking)
4. 建立测试覆盖基线 (audit module)

---

## 2. 问题清单与修复策略

### Issue 1: KernelOne → Cells 依赖违规 (Critical)

**问题描述**: 50+ 个 kernelone/ 文件导入 cells/ 模块，违反 ACGA 2.0 Section 6.3 原则

**违规示例**:
```python
# kernelone/multi_agent/bus_port.py:254
from polaris.cells.roles.runtime.internal import KernelOneBusPort

# kernelone/prompts/meta_prompting.py:40
from polaris.cells.roles.kernel.public.role_alias import RoleAlias

# kernelone/cognitive/orchestrator.py:112
from polaris.cells.values.alignment_service import AlignmentService
```

**修复策略**: Port/Adapter 模式
- 在 `kernelone/` 中定义 **Port 接口** (抽象基类)
- 在 `cells/` 中实现 **Adapter** (实现 Port)
- 修改 KernelOne 使用 Port 而非直接依赖 Cell

**文件变更**:
```
src/backend/polaris/kernelone/
├── multi_agent/
│   └── bus_port.py  [MOD] - 使用 IBusPort 接口
├── prompts/
│   └── meta_prompting.py  [MOD] - 使用 IRoleProvider 接口
├── cognitive/
│   └── orchestrator.py  [MOD] - 使用 IAlignmentService 接口
└── ports/  [NEW] - 抽象接口定义
    ├── __init__.py
    ├── bus_port.py    - IBusPort
    ├── role_provider.py - IRoleProvider
    └── alignment.py   - IAlignmentService

src/backend/polaris/cells/
└── adapters/  [NEW] - Port 实现适配器
    ├── kernelone/
    │   ├── bus_adapter.py    - KernelOneBusPortAdapter
    │   ├── role_adapter.py   - RoleProviderAdapter
    │   └── alignment_adapter.py - AlignmentServiceAdapter
    └── __init__.py
```

### Issue 2: Cell Encapsulation 泄漏 (Critical)

**问题描述**: Cells 直接导入其他 Cells 的 internal 模块

**违规示例**:
```python
# cells/roles/session/internal/role_session_service.py
from polaris.cells.roles.kernel.internal import KernelSessionManager
```

**修复策略**: 公共契约 + CI 门禁
1. 定义 Cell 间通信的公共接口
2. 添加 pre-commit hook 拦截 internal 导入
3. 重构为通过 public/contracts.py 通信

**文件变更**:
```
src/backend/polaris/cells/roles/session/internal/
└── role_session_service.py  [MOD] - 通过 public/contracts 通信

# 添加公共契约
src/backend/polaris/cells/roles/kernel/public/
└── session_contracts.py  [NEW] - ISessionManager 接口
```

### Issue 3: ContentStore 重复 (Critical)

**问题描述**: ContentStore 在两处实现

**违规位置**:
1. `polaris/kernelone/context/context_os/content_store.py`
2. `polaris/kernelone/kernelone/context/context_os/content_store.py` (重复)

**修复策略**: 删除重复，保留规范位置

**文件变更**:
```
删除: polaris/kernelone/kernelone/context/context_os/content_store.py
保留: polaris/kernelone/context/context_os/content_store.py
```

### Issue 4: shell=True Command Injection (Critical)

**问题描述**: SafeCommandExecutor 使用 shell=True 允许命令注入

**漏洞位置**: `cells/factory/verification_guard/internal/safe_executor.py:320`

```python
# 当前 (不安全)
result = subprocess.run(command, shell=True, ...)
```

**修复策略**: 移除 shell=True，使用参数列表

**文件变更**:
```
src/backend/polaris/cells/factory/verification_guard/internal/
└── safe_executor.py  [MOD] - shell=False + 参数验证
```

### Issue 5: time.sleep() 阻塞 Async (Critical)

**问题描述**: ReflectionGenerator 在 async 上下文中使用阻塞 sleep

**漏洞位置**: `kernelone/memory/reflection.py:208`

```python
# 当前 (阻塞事件循环)
time.sleep(REFLECTION_RETRY_BACKOFF_SECONDS[attempt])

# 修复后 (非阻塞)
await asyncio.sleep(REFLECTION_RETRY_BACKOFF_SECONDS[attempt])
```

**文件变更**:
```
src/backend/polaris/kernelone/memory/
└── reflection.py  [MOD] - time.sleep → asyncio.sleep
```

### Issue 6: Audit Module 零测试 (Critical)

**问题描述**: 整个 audit 模块无测试覆盖

**缺失覆盖**:
- AuditIndex
- KernelAuditRuntime
- Chain verification
- Event normalization

**修复策略**: 全面测试覆盖

**文件变更**:
```
src/backend/polaris/kernelone/audit/tests/
├── __init__.py
├── test_audit_index.py        [NEW]
├── test_audit_runtime.py      [NEW]
├── test_chain_verification.py [NEW]
└── test_event_normalization.py [NEW]
```

---

## 3. 模块职责划分

### 3.1 Engineer 1: 架构重构 (工部尚书)

**职责**: Issue 1 & 2 修复
- 创建 kernelone/ports/ 抽象接口
- 实现 cells/adapters/ 适配器
- 添加 CI pre-commit hook

### 3.2 Engineer 2: 安全修复 (监察御史)

**职责**: Issue 4 修复
- 修复 SafeCommandExecutor shell=True
- 验证命令参数验证
- 安全回归测试

### 3.3 Engineer 3: 性能修复 (仓部郎中)

**职责**: Issue 5 修复
- 修复 ReflectionGenerator async blocking
- 检查其他 time.sleep() 误用
- 性能回归测试

### 3.4 Engineer 4: 测试覆盖 (都官郎中)

**职责**: Issue 6 修复
- 编写 AuditIndex 测试
- 编写 KernelAuditRuntime 测试
- 编写 Chain verification 测试

### 3.5 Engineer 5: 代码清理 (殿中侍御史)

**职责**: Issue 3 修复
- 删除重复 ContentStore
- 清理无效导入
- 验证无回归

---

## 4. 技术选型

### 4.1 Port/Adapter 模式

```python
# kernelone/ports/bus_port.py
from abc import ABC, abstractmethod

class IBusPort(ABC):
    """Bus port abstraction for KernelOne."""

    @abstractmethod
    async def publish(self, topic: str, message: bytes) -> None: ...

    @abstractmethod
    async def subscribe(self, topic: str) -> AsyncIterator[bytes]: ...
```

### 4.2 CI Pre-commit Hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: local
    hooks:
      - id: cell-internal-imports
        name: Block Cell internal imports
        entry: python scripts/check_cell_imports.py
        language: system
        types: [python]
```

### 4.3 参数化命令执行

```python
# safe_executor.py 修复
import shlex

def _execute_command(self, command: str, args: list[str]) -> subprocess.CompletedProcess:
    """Execute command with proper argument handling."""
    if not self._validator.is_allowed(command):
        raise SecurityError(f"Command not in whitelist: {command}")

    # 确保命令是白名单中的基础命令
    cmd_list = [command] + [shlex.quote(str(a)) for a in args]
    return subprocess.run(
        cmd_list,
        shell=False,  # 安全：无 shell 解析
        capture_output=True,
        text=True,
        timeout=self._timeout,
    )
```

---

## 5. 数据流变更

### 5.1 依赖注入流程 (修复后)

```
┌─────────────────────────────────────────────────────────────┐
│                    KernelOne (Platform)                     │
├─────────────────────────────────────────────────────────────┤
│  multi_agent/bus_port.py                                  │
│         │                                                 │
│         ▼                                                 │
│  from kernelone.ports.bus_port import IBusPort  [抽象]   │
└─────────────────────────────────────────────────────────────┘
                              │
                              │ 依赖注入
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    Cells (Business Logic)                   │
├─────────────────────────────────────────────────────────────┤
│  cells/adapters/kernelone/bus_adapter.py                  │
│         │                                                 │
│         ▼                                                 │
│  class KernelOneBusPortAdapter(IBusPort):  [实现]         │
└─────────────────────────────────────────────────────────────┘
```

### 5.2 命令执行安全流程 (修复后)

```
用户输入命令
      │
      ▼
┌─────────────────┐
│ CommandWhitelist │ ← 白名单验证
└─────────────────┘
      │
      ▼
┌─────────────────┐
│ ArgValidator    │ ← 参数验证
└─────────────────┘
      │
      ▼
┌─────────────────┐
│ subprocess.run() │ ← shell=False
│   (安全模式)     │
└─────────────────┘
```

---

## 6. 验收标准

- [ ] `ruff check kernelone/ports/ --select=E,F` 零错误
- [ ] `ruff check cells/adapters/ --select=E,F` 零错误
- [ ] `mypy kernelone/ports/*.py` Success: no issues
- [ ] `pytest kernelone/audit/tests/ -v` 100% PASS
- [ ] SafeCommandExecutor shell=False 验证通过
- [ ] asyncio.sleep() 替换验证通过
- [ ] ContentStore 重复删除验证
- [ ] CI pre-commit hook 拦截 internal 导入

---

## 7. 风险评估

| 风险 | 可能性 | 影响 | 缓解 |
|------|--------|------|------|
| Port/Adapter 重构破坏现有功能 | 中 | 高 | 保留旧接口作为 alias，逐步迁移 |
| CI hook 阻止合法代码 | 低 | 中 | 细粒度配置白名单 |
| shell=False 导致 Windows npm 失败 | 中 | 中 | 测试 Windows CI 环境 |
| 删除 ContentStore 重复影响下游 | 低 | 高 | 保留导入 alias 确保向后兼容 |

---

## 8. 实施顺序

```
Phase 1: Issue 5 (最低风险) → Issue 3 (代码清理)
    ↓
Phase 2: Issue 4 (安全修复) → Issue 6 (测试覆盖)
    ↓
Phase 3: Issue 1 & 2 (架构重构，高风险)
    ↓
Phase 4: 回归测试 + 验证
```

**建议**: 按风险递增顺序执行，从低风险修复开始建立信心。
