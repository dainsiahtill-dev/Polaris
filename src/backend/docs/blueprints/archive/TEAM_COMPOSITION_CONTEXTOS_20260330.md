# ContextOS 统一上下文架构重构团队

**版本**: 1.0
**日期**: 2026-03-30
**项目**: ContextOS 统一上下文架构重构
**周期**: 10 周

---

## 1. 团队架构

```
┌─────────────────────────────────────────────────────────────────┐
│                     项目负责人 (1)                                │
│                   架构治理实验室主任                              │
└─────────────────────────────────────────────────────────────────┘
                              │
          ┌───────────────────┼───────────────────┐
          │                   │                   │
          ▼                   ▼                   ▼
┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐
│  核心重构组 (4) │ │  测试保障组 (3) │ │  质量门禁组 (3) │
└─────────────────┘ └─────────────────┘ └─────────────────┘
```

---

## 2. 人员分配与职责

### 2.1 核心重构组 (4人)

#### 2.1.1 首席重构工程师 A（1人）

**职责**:
- 负责 P0-1（消除双轨制）和 P0-2（元数据保留）的核心实现
- 主导 `ToolLoopController` 重构
- 设计 `ContextEvent` 数据类

**任务**:
```
Task-001: 实现 ContextEvent 数据类
Task-002: 重构 _extract_snapshot_history() 保留完整元数据
Task-003: 修改 _history 类型从 List[Tuple] 到 List[ContextEvent]
Task-004: 更新 append_tool_cycle() 使用 ContextEvent
Task-005: 消除 request.history 回退路径
Task-006: 添加 SSOT 约束验证
```

**技术要求**:
- 5+ 年 Python 经验
- 深入理解 dataclass/immutability
- 有事件溯源（Event Sourcing）实践经验优先

---

#### 2.1.2 首席重构工程师 B（1人）

**职责**:
- 负责 P1-1（统一压缩策略）和 P1-2（ContextRequest 统一）
- 主导 `RoleContextGateway` 重构
- 实现 `ContextOverflowError`

**任务**:
```
Task-007: 实现 ContextOverflowError 异常类
Task-008: 重构 _apply_compression() 统一压缩管道
Task-009: 合并 L1 语义压缩 + L2 物理截断
Task-010: 迁移 ContextRequest 到 contracts.py
Task-011: 更新 context_gateway.py 导入路径
Task-012: 验证压缩策略在所有模式下生效
```

**技术要求**:
- 5+ 年 Python 经验
- 深入理解 Context/Compression 策略
- 有异常处理/错误恢复设计经验

---

#### 2.1.3 重构工程师 C（1人）

**职责**:
- 负责 P2-1（快照摘要修复）和 P2-2（延迟序列化接口）
- 主导 `ProviderFormatter` 接口设计
- 实现调试模式完整输出

**任务**:
```
Task-013: 实现 _format_context_os_snapshot() verbosity 参数
Task-014: 设计 ProviderFormatter Protocol
Task-015: 实现 NativeProviderFormatter
Task-016: 实现 AnnotatedProviderFormatter
Task-017: 更新 _messages_to_input() 文档
Task-018: 编写 ProviderFormatter 测试
```

**技术要求**:
- 4+ 年 Python 经验
- 深入理解 Protocol/ABC 设计模式
- 有接口设计经验

---

#### 2.1.4 重构工程师 D（1人）

**职责**:
- 负责所有重构的代码整合与端到端协调
- 确保各模块间接口兼容
- 编写集成测试

**任务**:
```
Task-019: 整合所有重构模块
Task-020: 执行端到端集成测试
Task-021: 验证 ContextOS SSOT 约束
Task-022: 协调回归测试套件执行
Task-023: 更新相关模块的 __init__.py 导出
Task-024: 生成重构后的架构图
```

**技术要求**:
- 4+ 年 Python 经验
- 有大型重构项目协调经验
- 理解整体系统架构

---

### 2.2 测试保障组 (3人)

#### 2.2.1 测试工程师 E（1人）

**职责**:
- 编写 P0 级问题的新增测试用例
- 确保 SSOT 约束可验证

**任务**:
```
Task-025: 编写 test_context_os_ssot_constraint.py
Task-026: 编写 test_context_event_metadata.py
Task-027: 验证所有 ContextEvent 字段保留
Task-028: 编写双轨制消除验证测试
```

**技术要求**:
- 3+ 年 Python 测试经验
- 精通 pytest/fixture 设计
- 有 TDD 实践经验优先

---

#### 2.2.2 测试工程师 F（1人）

**职责**:
- 编写 P1/P2 级问题的新增测试用例
- 实现压缩策略验证

**任务**:
```
Task-029: 编写 test_context_overflow_guard.py
Task-030: 编写 test_context_request_unification.py
Task-031: 编写 test_snapshot_verbosity.py
Task-032: 验证 ContextOverflowError 正确抛出
```

**技术要求**:
- 3+ 年 Python 测试经验
- 有异常测试设计经验
- 理解 token 预算概念

---

#### 2.2.3 测试工程师 G（1人）

**职责**:
- 执行现有回归测试套件
- 确保无功能退化

**任务**:
```
Task-033: 执行 test_turn_engine_run_parity.py
Task-034: 执行 test_run_stream_parity.py
Task-035: 执行 test_kernel_stream_tool_loop.py
Task-036: 执行 test_llm_caller.py
Task-037: 生成测试覆盖率报告
Task-038: 识别并报告任何退化
```

**技术要求**:
- 3+ 年 Python 测试经验
- 精通 pytest/pytest-asyncio
- 有大规模回归测试经验优先

---

### 2.3 质量门禁组 (3人)

#### 2.3.1 质量工程师 H（1人）

**职责**:
- 执行 Ruff 代码规范检查
- 确保无 lint 错误

**任务**:
```
Task-039: 对所有改动文件执行 ruff check . --fix
Task-040: 对所有改动文件执行 ruff format .
Task-041: 审查并修复任何 Ruff 警告
Task-042: 更新相关配置（pyproject.toml）如需
```

**技术要求**:
- 熟悉 PEP 8 和 Ruff 规则
- 有代码审查经验
- 理解 UTF-8 编码规范

---

#### 2.3.2 质量工程师 I（1人）

**职责**:
- 执行 Mypy 静态类型检查
- 确保类型安全

**任务**:
```
Task-043: 对所有改动文件执行 mypy
Task-044: 修复任何类型错误
Task-045: 确保无 # type: ignore 滥用
Task-046: 更新类型注解如需
```

**技术要求**:
- 精通 Python 类型系统
- 有 mypy 配置经验
- 理解泛型/Protocol 类型

---

#### 2.3.3 质量工程师 J（1人）

**职责**:
- 执行最终质量验收
- 生成质量报告

**任务**:
```
Task-047: 执行完整 pytest 套件
Task-048: 验证所有门禁通过
Task-049: 生成最终重构报告
Task-050: 更新 CLAUDE.md 如需
Task-051: 提交代码审查 (CR) 申请
```

**技术要求**:
- 5+ 年 Python 经验
- 有质量门禁设计经验
- 理解 fail-closed 原则

---

## 3. 执行时间表

| 周次 | 核心重构组 | 测试保障组 | 质量门禁组 |
|------|------------|------------|------------|
| Week 1-2 | Task-001~006 (P0-1, P0-2) | Task-025~028 (P0 测试) | Task-039~042 (Ruff) |
| Week 3-4 | Task-007~012 (P1-1, P1-2) | Task-029~032 (P1 测试) | Task-043~046 (Mypy) |
| Week 5-6 | Task-013~018 (P2-1, P2-2) | Task-033~036 (回归测试) | Task-047~048 (质量验收) |
| Week 7-8 | Task-019~024 (整合) | Task-037~038 (覆盖率) | Task-049~050 (报告) |
| Week 9-10 | 缓冲期/修复 | 回归修复 | Task-051 (CR) |

---

## 4. 沟通机制

### 4.1 每日站会
- 时间: 每天 09:30 (UTC+8)
- 形式: 异步（Slack/飞书）
- 内容: 昨日完成、今日计划、阻塞问题

### 4.2 周报
- 时间: 每周五 18:00
- 内容: 进度更新、风险评估、下周计划
- 接收人: 架构治理实验室主任

### 4.3 代码审查
- 所有 Task 完成后必须经过至少 1 人 CR
- P0/P1 级别改动需要 2 人 CR
- 审查通过标准: 无 Blocking 评论

---

## 5. 风险管理

| 风险 | 影响 | 概率 | 应对措施 |
|------|------|------|----------|
| P0-2 元数据保留导致向后兼容问题 | 高 | 中 | 保留 `to_tuple()` 方法 |
| P1-1 压缩策略收紧导致测试失败 | 中 | 低 | 使用 feature flag 渐进 |
| P2-2 ProviderFormatter 接口设计分歧 | 中 | 中 | 先实现最小接口再扩展 |

---

## 6. 工具与资源

### 6.1 代码仓库
- 分支: `feature/contextos-unified-architecture`
- 位置: `src/backend/`

### 6.2 文档位置
- 蓝图: `docs/blueprints/CONTEXTOS_UNIFIED_CONTEXT_ARCHITECTURE_20260330.md`
- 审计: `docs/audit/...` (本次审计报告)

### 6.3 测试命令
```bash
# 新增测试
pytest tests/contextos/

# 回归测试
pytest tests/roles/kernel/ -v

# Ruff 检查
ruff check polaris/kernelone/context/
ruff check polaris/cells/roles/kernel/internal/

# Mypy 检查
mypy polaris/kernelone/context/context_os/
mypy polaris/cells/roles/kernel/internal/tool_loop_controller.py
```

---

## 7. 验收清单

### 7.1 功能验收
- [ ] P0-1: ToolLoopController 只接受 context_os_snapshot
- [ ] P0-2: ContextEvent 保留所有元数据字段
- [ ] P1-1: ContextOverflowError 在超限时正确抛出
- [ ] P1-2: ContextRequest 统一到 contracts.py
- [ ] P2-1: verbosity 参数正确控制输出详细度
- [ ] P2-2: ProviderFormatter 接口可用

### 7.2 质量验收
- [ ] Ruff: 无 Error/Warning
- [ ] Mypy: Success: no issues found
- [ ] pytest: 100% 通过

### 7.3 文档验收
- [ ] 蓝图文档已创建
- [ ] 审计报告已更新
- [ ] CLAUDE.md 已更新（如需）
