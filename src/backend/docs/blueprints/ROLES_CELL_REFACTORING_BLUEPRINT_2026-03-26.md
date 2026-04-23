# ROLES CELL 重构蓝图 v1.0
**文档版本**: 1.0.0  
**创建日期**: 2026-03-26  
**审计团队**: Polaris Architecture Review Board  
**目标**: `polaris/cells/roles` - 7个子Cell，185个Python文件，~67,409行代码  
**综合健康度评分**: 75%

---

## 1. 审计摘要

### 1.1 审计范围

| 子Cell | 类型 | 核心职责 | LOC |
|--------|------|----------|-----|
| kernel | Capability | 角色执行内核 (CHAT/WORKFLOW) | ~3000 |
| runtime | Stateful Composite | 运行时编排与Agent生命周期 | ~2500 |
| session | Stateful Capability | 会话生命周期管理 | ~800 |
| profile | Stateful Capability | 角色Profile管理 | ~600 |
| engine | Application | 引擎选择与分类 | ~500 |
| adapters | Composite | 角色适配器工厂 | ~400 |
| host | Capability | 统一主机协议 | ~300 |

### 1.2 架构健康度

| 维度 | 评分 | 状态 |
|------|------|------|
| ACGA 2.0 合规 | 85% | ✅ Graph边界清晰 |
| KernelOne 集成 | 75% | ⚠️ Bus待接入 |
| 异常处理 | 90% | ✅ 防御式规范 |
| 并发安全 | 90% | ✅ RLock正确 |
| 测试覆盖 | 60% | ⚠️ 缺口明显 |
| 技术债清理 | 50% | 🔴 Phase 4遗留 |

---

## 2. 问题清单与修复优先级

### 2.1 CRITICAL - 必须立即处理

#### C1: Phase 4双执行路径违规
**问题**: 遗留冻结表面(`standalone_runner.py`, `tui_console.py`)与统一`RoleExecutionKernel`并存，违反ACGA 2.0单一执行路径原则。

**根因**: Phase 4迁移不完整，遗留代码未清理。

**影响**: 
- 执行行为不一致风险
- 测试覆盖复杂度增加
- 技术债持续累积

**修复方案**:
```python
# 1. 删除遗留文件
polaris/cells/roles/runtime/internal/standalone_runner.py    # ~38KB
polaris/cells/roles/runtime/internal/tui_console.py          # ~38KB

# 2. 更新 cell.yaml 移除 legacy 路径声明
# 3. 更新所有引用点，强制路由到 RoleExecutionKernel
```

**验收标准**:
- [ ] standalone_runner.py 删除
- [ ] tui_console.py 删除
- [ ] 无代码引用遗留路径
- [ ] 现有测试通过

---

#### C2: 跨进程Bus未接入
**问题**: `InMemoryAgentBusPort`仅支持进程内通信，KernelOne NATS路由未接入。

**根因**: Message Bus Port实现缺失，仅有内存回退。

**影响**:
- 分布式部署不可行
- 跨进程事件丢失
- 违反KernelOne集成规范

**修复方案**:
```python
# 1. 实现 KernelOneMessageBusPort
class KernelOneMessageBusPort(AgentBusPort):
    """KernelOne NATS-backed message bus."""
    
    def __init__(self, nats_url: str = "nats://localhost:4222"):
        self._nc = None
        self._nats_url = nats_url
    
    async def publish(self, topic: str, event: AgentEvent) -> None:
        # 实现NATS发布
        
    async def subscribe(self, topic: str) -> AsyncIterator[AgentEvent]:
        # 实现NATS订阅
```

**验收标准**:
- [ ] KernelOneMessageBusPort 实现
- [ ] InMemoryAgentBusPort 保留为测试/回退
- [ ] 单元测试覆盖
- [ ] 环境变量配置支持

---

### 2.2 HIGH - 短期修复 (Sprint 1-2)

#### H1: 上下文压缩未集成
**问题**: `RoleContextCompressor`已实现但未在kernel中激活。

**根因**: Feature flag未启用，集成代码缺失。

**修复方案**:
```python
# kernel/internal/kernel.py
async def run(self, request: RoleExecutionRequest) -> RoleExecutionResponse:
    # 在LLM调用前激活上下文压缩
    if self._context_compressor and request.enable_context_compaction:
        request = await self._context_compressor.compress(request)
    
    # ... 原有逻辑
```

**验收标准**:
- [ ] RoleContextCompressor 在 kernel 中集成
- [ ] 环境变量 `KERNELONE_CONTEXT_COMPACTION=true` 激活
- [ ] 集成测试通过

---

#### H2: 结构化输出回退类型安全缺失
**问题**: Instructor schemas不可用时回退到dict，缺乏类型安全。

**根因**: 回退逻辑直接使用dict，未定义fallback schema。

**修复方案**:
```python
# 定义 fallback response schema
@dataclass(frozen=True)
class GenericRoleResponse:
    content: str
    tool_calls: list[dict] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

# 在 kernel 中使用
def _get_output_parser(self, schema: type | None) -> OutputParser:
    if schema is not None:
        try:
            return InstructorOutputParser(schema=schema)
        except ImportError:
            pass
    return PydanticOutputParser(schema=GenericRoleResponse)
```

**验收标准**:
- [ ] 定义 GenericRoleResponse fallback schema
- [ ] PydanticOutputParser 完整实现
- [ ] 类型安全验证通过

---

#### H3: Legacy AgentService冻结
**问题**: 38KB代码标记为frozen但仍保留在主分支。

**根因**: 冻结注释存在但代码未删除。

**修复方案**:
```python
# runtime/internal/agent_service.py 顶部注释
# ⚠️ DEPRECATED (v1.0.0): Legacy implementation pending removal
# ⚠️ 已在 Phase 3 迁移完成，此文件仅用于向后兼容
# ⚠️ 预计删除版本: v1.2.0
```

**验收标准**:
- [ ] DEPRECATED 注释完善
- [ ] 确定删除版本 v1.2.0
- [ ] 记录到 tech-debt-tracker

---

### 2.3 MEDIUM - 中期优化 (Sprint 3-4)

#### M1: 工具网关耦合
**问题**: `RoleToolGateway`与kernel直接耦合，未通过port注入。

**修复方案**:
```python
# 定义 ToolGatewayPort
@runtime_checkable
class ToolGatewayPort(Protocol):
    async def execute(self, tool_name: str, args: dict) -> Any: ...
    def requires_approval(self, tool_name: str) -> bool: ...

# 在 kernel 中注入
class RoleExecutionKernel:
    def __init__(
        self,
        tool_gateway: ToolGatewayPort | None = None,
    ):
        self._tool_gateway = tool_gateway or RoleToolGateway()
```

**验收标准**:
- [ ] ToolGatewayPort Protocol 定义
- [ ] Kernel 通过 DI 注入
- [ ] Mock 测试覆盖

---

#### M2: 重试策略硬编码
**问题**: max_retries无环境变量配置接口。

**修复方案**:
```python
# kernel/internal/kernel.py
@dataclass(frozen=True)
class KernelConfig:
    max_retries: int = field(
        default_factory=lambda: int(os.getenv("KERNELONE_MAX_RETRIES", "3"))
    )
    retry_delay: float = field(
        default_factory=lambda: float(os.getenv("KERNELONE_RETRY_DELAY", "1.0"))
    )
```

**验收标准**:
- [ ] KernelConfig dataclass 实现
- [ ] 环境变量支持
- [ ] Profile级别覆盖支持

---

#### M3: Session服务完整性
**问题**: `role_session_service.py`可能未完整实现。

**修复方案**:
```python
# session/internal/role_session_service.py
# 检查并补全以下能力:
# 1. 会话持久化 (Storage Port)
# 2. 会话超时管理
# 3. 会话状态事件发布
```

**验收标准**:
- [ ] Session持久化到Storage
- [ ] TTL自动清理
- [ ] 生命周期事件发布

---

### 2.4 LOW - 持续改进

| ID | 问题 | 解决方案 | 验收标准 |
|----|------|----------|----------|
| L1 | 缓存统计未导出 | 添加Prometheus metrics端点 | L1-L3命中率可观测 |
| L2 | 质量阈值硬编码 | 环境变量+Profile覆盖 | `KERNELONE_QUALITY_THRESHOLD`支持 |
| L3 | 日志级别不一致 | 统一使用logger级别规范 | 所有kernel日志符合规范 |

---

## 3. KernelOne 集成路线图

### 3.1 当前状态

| Capability | Status | Implementation |
|------------|--------|---------------|
| fs | ✅ ACTIVE | `polaris.kernelone.fs` |
| storage | ✅ ACTIVE | `polaris.kernelone.storage` |
| events | ✅ ACTIVE | `polaris.kernelone.events` |
| tool_runtime | ✅ ACTIVE | `polaris.kernelone.tools.runtime_executor` |
| message_bus | ⚠️ 25% | 仅InMemory，NATS待接 |
| context_compaction | ⚠️ 50% | 实现但未激活 |

### 3.2 目标状态 (v1.2.0)

| Capability | Target Status | 实现要求 |
|------------|---------------|----------|
| message_bus | 100% | NATS Production Ready |
| context_compaction | 100% | 默认激活 |
| trace | 75% | OpenTelemetry集成 |
| llm_metrics | 80% | Token统计+延迟监控 |

---

## 4. 测试覆盖改进计划

### 4.1 当前缺口

| Cell | 当前覆盖 | 目标覆盖 | 缺口 |
|------|----------|----------|------|
| kernel | PARTIAL | 85% | TurnEngine, ToolLoopController |
| runtime | PARTIAL | 80% | Bus integration |
| adapters | MINIMAL | 70% | 工厂函数集成测试 |
| session | NONE | 75% | 完整覆盖 |

### 4.2 补全策略

```bash
# 新增测试文件
polaris/cells/roles/
├── kernel/tests/
│   ├── test_turn_engine.py          # NEW
│   ├── test_tool_loop_controller.py # NEW
│   └── test_context_compressor.py   # NEW
├── runtime/tests/
│   └── test_kernel_one_bus_port.py  # NEW
├── session/tests/
│   ├── test_session_service.py      # NEW
│   └── test_session_persistence.py   # NEW
└── adapters/tests/
    └── test_factory_integration.py   # NEW
```

---

## 5. 实施路线图

### 5.1 Sprint 0: 准备 (1天)
- [ ] 创建 `polaris/cells/roles/tech-debt-tracker.md`
- [ ] 确认 CI 门禁更新需求
- [ ] 审查治理规则变更需求

### 5.2 Sprint 1: P0 修复 (3天)
- [ ] C1: 删除遗留文件 + 更新引用
- [ ] C2: 实现 KernelOneMessageBusPort

### 5.3 Sprint 2: P1 修复 (5天)
- [ ] H1: 激活上下文压缩
- [ ] H2: 结构化输出fallback类型安全
- [ ] H3: Legacy代码DEPRECATED标注

### 5.4 Sprint 3: P2 优化 (5天)
- [ ] M1: 工具网关解耦 (ToolGatewayPort)
- [ ] M2: 配置可外部化
- [ ] M3: Session服务补全

### 5.5 Sprint 4: 测试补全 (5天)
- [ ] 新增 6 个测试文件
- [ ] 覆盖率从 60% → 75%
- [ ] E2E 烟雾测试更新

### 5.6 Sprint 5: 可观测性 (3天)
- [ ] Prometheus metrics导出
- [ ] OpenTelemetry trace集成
- [ ] 日志规范统一

---

## 6. 风险评估

### 6.1 技术风险

| 风险 | 影响 | 概率 | 缓解措施 |
|------|------|------|----------|
| 删除遗留代码破坏现有功能 | 高 | 中 | 全面回归测试 |
| NATS集成连接失败 | 中 | 中 | InMemory回退 |
| 上下文压缩影响输出质量 | 中 | 低 | 质量阈值监控 |

### 6.2 进度风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| Sprint压缩导致质量下降 | 高 | 严格验收标准 |
| 测试补全耗时超预期 | 中 | 优先kernel+runtime |

---

## 7. 验收门禁

### 7.1 代码质量
- [ ] `ruff check polaris/cells/roles/` 通过
- [ ] `mypy polaris/cells/roles/` 通过
- [ ] 无新增 ruff/mypy 警告

### 7.2 测试覆盖
- [ ] pytest 100% 通过
- [ ] 覆盖率报告生成
- [ ] 覆盖率 ≥ 75%

### 7.3 集成验证
- [ ] Kernel集成测试通过
- [ ] Runtime集成测试通过
- [ ] E2E烟雾测试通过

### 7.4 文档更新
- [ ] `cell.yaml` 更新（如有变更）
- [ ] `README.agent.md` 更新
- [ ] `context.pack.json` 更新（如有变更）

---

## 8. 负责人分配 (10人团队)

| # | 角色 | 专长 | 分配任务 |
|---|------|------|----------|
| 1 | 首席架构师 | ACGA 2.0合规 | 整体架构监督 + C1 |
| 2 | 内核专家 | KernelOne底座 | C2 (Bus Port) |
| 3 | 运行时专家 | Agent生命周期 | C1 (遗留清理) |
| 4 | 集成专家 | 工具系统 | H1 (上下文压缩) |
| 5 | 类型安全专家 | Pydantic/Mypy | H2 (输出回退) |
| 6 | 配置专家 | 环境配置 | H3 + M2 |
| 7 | 测试专家 | pytest/覆盖率 | Sprint 4 (测试补全) |
| 8 | 可观测性专家 | Prometheus/OTel | Sprint 5 (可观测性) |
| 9 | Session专家 | 状态管理 | M3 (Session服务) |
| 10 | 工具网关专家 | Port/DI模式 | M1 (工具网关解耦) |

---

## 9. 附录

### 9.1 相关文档
- `docs/AGENT_ARCHITECTURE_STANDARD.md`
- `docs/graph/catalog/cells.yaml`
- `polaris/kernelone/README.md`

### 9.2 变更日志
| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-03-26 | 1.0.0 | 初始版本，基于架构审计报告 |

---

**文档状态**: 待评审  
**下次评审**: 2026-03-27  
**审批人**: Polaris Architecture Board
