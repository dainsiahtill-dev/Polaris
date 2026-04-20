# Polaris 兼容性清单

**生成日期**: 2026-03-06
**目的**: 登记 sys.path 操作，标记兼容层

---

## 1. sys.path 操作清单

### 1.1 生产代码 (非 tests/scripts)

以下文件包含 `sys.path.insert` 或 `sys.path.append`，需评估是否可移除:

| 文件 | 行号 | 类型 | 用途 | 状态 |
|------|------|------|------|------|
| `app/utils.py` | 88 | insert | 动态加载 loop 模块 | 需审查 |
| `app/routers/arsenal.py` | 362 | insert | 动态加载 loop 目录 | 需审查 |
| `app/routers/pm_management.py` | 26 | insert | 加载 PM scripts | 需审查 |
| `app/orchestration/workflows/generic_pipeline_workflow.py` | 30 | insert | 加载 backend root | 需审查 |
| `core/runtime_orchestrator.py` | - | insert | Runtime 启动 | 兼容层 |
| `core/orchestration/runtime_orchestrator.py` | - | insert | 新版 Runtime | 正常 |
| `core/orchestration/process_launcher.py` | - | insert | 进程启动 | 正常 |
| `core/startup/backend_bootstrap.py` | - | insert | 后端引导 | 正常 |
| `core/startup/config_loader.py` | - | insert | 配置加载 | 正常 |
| `application/dto/backend_launch.py` | - | insert | DTO | 正常 |

### 1.2 测试代码 (tests/)

| 文件 | 类型 | 用途 |
|------|------|------|
| `tests/orchestration/test_workflow_runtime.py` | insert | 测试引导 |
| `tests/test_imports.py` | insert | 导入测试 |
| `tests/test_storage_layout.py` | insert | 存储布局测试 |
| `tests/test_pm_state_sync.py` | insert | PM 状态同步测试 |
| `tests/test_pm_task_quality_gate.py` | insert | 任务质量门禁测试 |
| `tests/test_refactored_architecture.py` | insert | 架构重构测试 |
| `tests/test_llm_phase0_regression.py` | insert | LLM 回归测试 |
| `tests/test_workflow_chain.py` | insert | 工作流链测试 |
| `tests/test_director_interface_timeout.py` | insert | Director 超时测试 |
| `tests/conftest.py` | insert | pytest 配置 |
| `tests/test_roles_kernel.py` | insert | 角色内核测试 |
| `tests/test_memory_retrieval.py` | insert | 记忆检索测试 |
| `tests/test_permission_service.py` | insert | 权限服务测试 |
| `tests/test_role_tooling_security_hardening.py` | insert | 工具安全测试 |
| `tests/test_integration_qa_command.py` | insert | QA 集成测试 |
| `tests/test_stream_thinking_fix.py` | insert | 流式思考修复测试 |
| `tests/test_pm_task_limit.py` | insert | PM 任务限制测试 |
| `tests/test_pm_zero_tasks_fallback.py` | insert | PM 零任务回退测试 |
| `tests/test_learn_claude_code_integration.py` | insert | Claude Code 集成测试 |
| `tests/test_loop_pm_backend_resolution.py` | insert | PM 后端解析测试 |
| `tests/test_agents_helpers.py` | insert | Agent 辅助测试 |
| `tests/test_pm_detect_tech_stack.py` | insert | 技术栈检测测试 |
| `tests/test_new_capabilities.py` | insert | 新能力测试 |
| `tests/test_io_utils_logical_paths.py` | insert | IO 逻辑路径测试 |
| `tests/test_plan_template_dynamic.py` | insert | 计划模板动态测试 |
| `tests/test_context_gatherer.py` | insert | 上下文收集测试 |
| `tests/test_existence_gate.py` | insert | 存在门禁测试 |
| `tests/test_failure_hops.py` | insert | 失败hops测试 |
| `tests/test_repo_map_provider.py` | insert | Repo Map Provider 测试 |
| `tests/test_sniper_mode.py` | insert | 狙击模式测试 |
| `tests/test_context_engine.py` | insert | 上下文引擎测试 |

### 1.3 Scripts 代码

| 文件 | 类型 | 用途 |
|------|------|------|
| `scripts/director/director_service.py` | insert | Director 服务 |
| `scripts/director/cli_thin.py` | insert | Director CLI 瘦入口 |
| `scripts/director/director_role.py` | insert | Director 角色 |
| `scripts/pm/pm_service.py` | insert | PM 服务 |
| `scripts/pm/cli_thin.py` | insert | PM CLI 瘦入口 |
| `scripts/pm/pm_role.py` | insert | PM 角色 |
| `scripts/pm/chief_engineer.py` | insert | 首席工程师 |
| `scripts/pm/director_interface_integration.py` | insert | Director 接口集成 |
| `scripts/loop-director.py` | insert | Director 循环 |
| `scripts/loop-pm.py` | insert | PM 循环 |
| `scripts/director_auto_test.py` | insert | Director 自动测试 |
| `scripts/pm/config.py` | insert | PM 配置 |
| `scripts/pm/cli.py` | insert | PM CLI |
| `scripts/run_pulsehud_from_empty.py` | insert | PulseHUD 运行 |
| `scripts/test_integration_verification.py` | insert | 集成验证 |
| `scripts/backfill_memories.py` | insert | 记忆回填 |
| `scripts/polaris_stress.py` | insert | 压力测试 |
| `scripts/director_v2.py` | insert | Director V2 |

---

## 2. 兼容层模块清单

### 2.1 API 兼容层

| 模块 | 状态 | 迁移建议 |
|------|------|----------|
| `app/routers/pm.py` | ⚠️ 兼容 | 迁移到 `/v2/pm/*` |
| `app/routers/director.py` | ⚠️ 兼容 | 迁移到 `/v2/director/*` |

### 2.2 核心兼容层

| 模块 | 状态 | 说明 |
|------|------|------|
| `core/runtime_orchestrator.py` | ⚠️ DEPRECATED | 使用新版 |
| `core/orchestration/runtime_orchestrator.py` | ✅ 主链路 | V2 编排 |

### 2.3 CLI 兼容层

| 模块 | 状态 | 说明 |
|------|------|------|
| `scripts/pm/cli_thin.py` | ✅ 兼容 | 瘦入口 |
| `scripts/director/cli_thin.py` | ✅ 兼容 | 瘦入口 |
| `scripts/pm/cli.py` | ⚠️ 旧版 | 保留但不推荐 |
| `scripts/director/cli.py` | ⚠️ 旧版 | 保留但不推荐 |

---

## 3. 禁止模式

### 3.1 禁止新建 sys.path.insert

在以下目录外的新增代码中，禁止使用 `sys.path.insert` 或 `sys.path.append`:
- ✅ `scripts/` - CLI 入口点
- ✅ `tests/` - 测试文件
- ✅ `core/` - 核心模块 CLI 入口

### 3.2 禁止复制状态 Merge

已有 `_merge_director_status()` 作为唯一 merge 逻辑，禁止在其他位置重复实现。

### 3.3 禁止独立角色对话文件

必须使用 `app/llm/usecases/role_dialogue.py` 统一入口，禁止创建新的 `generate_xxx_response()` 函数。

---

## 4. sys.path 清理优先级

| 优先级 | 文件 | 原因 |
|--------|------|------|
| 高 | `app/utils.py:88` | 生产代码，需评估 |
| 高 | `app/routers/arsenal.py:362` | 生产代码，需评估 |
| 高 | `app/routers/pm_management.py:26` | 生产代码，需评估 |
| 高 | `app/orchestration/workflows/generic_pipeline_workflow.py:30` | 生产代码，需评估 |
| 低 | 测试文件 | 允许 |
| 低 | Scripts | 允许 |

---

*本清单记录当前 sys.path 使用情况，用于后续清理参考。*
