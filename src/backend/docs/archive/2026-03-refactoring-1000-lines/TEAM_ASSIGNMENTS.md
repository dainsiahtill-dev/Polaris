# 团队分配

**版本**: 2026-03-31
**项目**: 超过1000行文件重构

---

## 1. 团队概览

| ID | 角色 | 专家背景 | 主要文件 |
|----|------|----------|----------|
| E1 | Core Engine Lead | Python核心引擎专家 | turn_engine.py |
| E2 | Context OS Lead | 上下文系统专家 | context_os/runtime.py |
| E3 | LLM Caller Lead | LLM调用层专家 | llm_caller.py |
| E4 | Service Layer Lead | 服务架构专家 | runtime/service.py |
| E5 | Kernel Lead | 内核架构专家 | kernel.py |
| E6 | Controller Lead | 控制器模式专家 | tool_loop_controller.py |
| E7 | Integration Architect | 系统集成专家 | Cross-cutting |
| E8 | Test Engineer | 测试自动化专家 | All |
| E9 | Documentation Lead | 技术文档专家 | All |
| E10 | Quality Gate | 代码质量专家 | All |

---

## 2. 各专家详细任务

### 2.1 E1: Core Engine Lead

**目标**: 重构 `turn_engine.py` (2033行)

**Wave 1 任务**:
```bash
# 创建目录
mkdir -p polaris/cells/roles/kernel/internal/turn_engine

# 创建 config.py
提取 TurnEngineConfig, SafetyState 到独立文件
```

**Wave 2 任务**:
```bash
# 创建 artifacts.py
提取 AssistantTurnArtifacts, _BracketToolWrapperFilter 到独立文件
```

**Wave 3 任务**:
```bash
# 精简 engine.py
保留 TurnEngine 核心类，导入 config 和 artifacts
创建 __init__.py 重导出
```

**验证命令**:
```bash
pytest polaris/cells/roles/kernel/tests/test_turn_engine*.py -v
```

---

### 2.2 E2: Context OS Lead

**目标**: 重构 `context_os/runtime.py` (2013行)

**Wave 1 任务**:
```bash
# 创建 patterns.py
提取所有正则表达式常量

# 创建 helpers.py
提取辅助函数和 _StateAccumulator
```

**Wave 2 任务**:
```bash
# 创建 classifier.py
提取 DialogActClassifier
```

**Wave 3 任务**:
```bash
# 精简 runtime.py
保留 StateFirstContextOS，导入其他模块
```

**验证命令**:
```bash
pytest polaris/kernelone/context/tests/ -v
```

---

### 2.3 E3: LLM Caller Lead

**目标**: 重构 `llm_caller.py` (2869行)

**Wave 1 任务**:
```bash
# 创建目录
mkdir -p polaris/cells/roles/kernel/internal/llm_caller

# 创建 retry_policy.py
提取重试策略逻辑
```

**Wave 2 任务**:
```bash
# 创建 response_parser.py
提取响应解析逻辑
```

**Wave 3 任务**:
```bash
# 精简 caller.py
保留 LLMCaller 核心
创建 __init__.py
```

**验证命令**:
```bash
pytest polaris/cells/roles/kernel/tests/test_llm_caller*.py -v
```

---

### 2.4 E4: Service Layer Lead

**目标**: 重构 `runtime/service.py` (2095行)

**Wave 1 任务**:
```bash
# 创建目录
mkdir -p polaris/cells/roles/runtime/public/service

# 创建 persistence.py
提取持久化逻辑
```

**Wave 2 任务**:
```bash
# 创建 context_adapter.py
提取 Context OS 集成逻辑
```

**Wave 3 任务**:
```bash
# 精简 service.py
保留 RoleRuntimeService 协调器
创建 __init__.py
```

**验证命令**:
```bash
pytest polaris/cells/roles/runtime/tests/ -v
```

---

### 2.5 E5: Kernel Lead

**目标**: 重构 `kernel.py` (1761行)

**Wave 1 任务**:
```bash
# 创建目录
mkdir -p polaris/cells/roles/kernel/internal/kernel

# 创建 retry_handler.py
提取重试处理逻辑
```

**Wave 2 任务**:
```bash
# 创建 prompt_adapter.py
提取提示词构建逻辑
```

**Wave 3 任务**:
```bash
# 精简 kernel.py
保留 RoleExecutionKernel 核心
创建 __init__.py
```

**验证命令**:
```bash
pytest polaris/cells/roles/kernel/tests/test_kernel*.py -v
```

---

### 2.6 E6: Controller Lead

**目标**: 重构 `tool_loop_controller.py` (~800行)

**Wave 1 任务**:
```bash
# 创建 context_event.py (同级目录)
提取 ContextEvent, ToolLoopSafetyPolicy
```

**Wave 2 任务**:
```bash
# 创建 tool_result_formatter.py (同级目录)
提取工具结果格式化逻辑
```

**Wave 3 任务**:
```bash
# 精简 tool_loop_controller.py
保留 ToolLoopController 核心
更新导入
```

**验证命令**:
```bash
pytest polaris/cells/roles/kernel/tests/test_tool_loop*.py -v
```

---

### 2.7 E7: Integration Architect

**目标**: 确保模块间正确集成

**任务**:
1. 定义接口契约
2. 检查循环依赖
3. 验证依赖图

**验证命令**:
```bash
# 检查循环导入
python -c "import polaris.cells.roles.kernel.internal.turn_engine"
python -c "import polaris.kernelone.context.context_os.runtime"
python -c "import polaris.cells.roles.kernel.internal.llm_caller"
```

---

### 2.8 E8: Test Engineer

**目标**: 确保测试覆盖

**任务**:
1. 运行现有测试
2. 创建新模块测试
3. 生成覆盖率报告

**验证命令**:
```bash
pytest --collect-only -q
pytest --cov=polaris --cov-report=term-missing
```

---

### 2.9 E9: Documentation Lead

**目标**: 更新文档

**任务**:
1. 更新模块 docstring
2. 更新 CLAUDE.md 引用路径
3. 生成 CHANGELOG

---

### 2.10 E10: Quality Gate

**目标**: 确保代码质量

**任务**:
1. 执行 Ruff 检查
2. 执行 Mypy 类型检查
3. 验证代码格式

**验证命令**:
```bash
ruff check polaris/ --fix
ruff format polaris/
mypy polaris/cells/roles/kernel/internal/turn_engine/
```

---

## 3. 协作协议

### 3.1 依赖声明

各专家在开始工作前，必须确认依赖已就绪：

| 专家 | 依赖者 |
|------|--------|
| E1 | 无 |
| E2 | 无 |
| E3 | E1 (TurnEngine) |
| E4 | E2 (ContextOS) |
| E5 | E3 (LLMCaller) |
| E6 | E1 (TurnEngine) |
| E7 | E1-E6 |
| E8 | E1-E6 |
| E9 | E1-E6 |
| E10 | E8 |

### 3.2 进度同步

每个 Wave 完成后：
1. 更新本文档状态
2. 通知依赖方
3. 等待 E7 集成确认

---

## 4. 交付物清单

| 专家 | 交付物 | 状态 |
|------|--------|------|
| E1 | turn_engine/ 模块 | Pending |
| E2 | context_os/ 新模块 | Pending |
| E3 | llm_caller/ 模块 | Pending |
| E4 | service/ 模块 | Pending |
| E5 | kernel/ 模块 | Pending |
| E6 | context_event.py, tool_result_formatter.py | Pending |
| E7 | INTERFACE_CONTRACT.md | Pending |
| E8 | 测试报告 | Pending |
| E9 | 文档更新 | Pending |
| E10 | 质量报告 | Pending |