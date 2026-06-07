# 文档与代码一致性审计报告 (Doc–Code Discrepancy Audit)

**日期 / Date:** 2026-06-07
**审计范围:** 755 个文件，67 路审计员产出去重后整理。

## 执行摘要 (Executive Summary)

本次审计比对了仓库文档（权威入口、AGENTS/CLAUDE/GEMINI、治理 ADR、蓝图、各 Cell 的 README、前端文档及记忆文件）与 `src/backend/polaris` ACGA 2.0 迁移后的真实代码。核心结论：**绝大多数偏差源于一次大规模目录迁移**——旧根 `src/backend/{app,api,core,scripts,domain,infrastructure}` 已被删除并迁移到 `src/backend/polaris/{bootstrap,delivery,application,domain,kernelone,infrastructure,cells}`，但大量文档仍引用旧路径；此外一批 `*.py` 单文件已变为同名包目录（如 `turn_engine.py`→`turn_engine/`、`llm_caller.py`→`llm_caller/`、`context_gateway.py`→`context_gateway/`、`executor.py`/`tool_normalization.py`→目录），文档里的工具契约 SSOT 路径 `polaris/kernelone/tools/contracts.py` 实际应为 `polaris/kernelone/tool_execution/contracts.py`。

原始严重度统计 / Severity tally: **high: 51，medium: 55，low: 70**（共 176 条原始发现）。
原始建议统计 / Recommendation tally: **delete: 2，edit: 141，relabel-as-historical: 31，keep: 2**。

去重合并后的去重计数见各节标题。其中权威入口文档（`CLAUDE.md`、`src/backend/AGENTS.md`、`README*.md`、`CLI_USAGE.md`、`docs/CONSTITUTION.md` 等）与记忆文件（`MEMORY.md`、`swebench-phaseb-status.md`）的偏差最具危害性，因其直接误导 AI 编码代理与开发者执行不存在的命令/路径。

---

## 高优先级（必须修改 / High priority）

权威入口文档、记忆文件、以及全部 high-severity 的 delete/edit 项。

| 文档 | 文档声称 | 实际代码 | 建议 | 证据 |
|---|---|---|---|---|
| `CLAUDE.md` | §1/§2/§3/§5/§7 大量引用 `src/backend/{app,core,api,scripts/pm,scripts/director}`：后端入口 `app/main.py`、PM CLI `scripts/pm/cli.py`、Director CLI `scripts/director/cli_thin.py`、Architect/Chief CLI `core/polaris_loop/role_agent/*`、Loop 核心 `core/polaris_loop`、`core/llm_toolkit/`、`app/llm/usecases/role_dialogue.py`、`app/services/task_board.py`、§7.5 替代项 `api/v2/pm.py`、`app/roles/workflow_adapter.py` | 旧根全部删除。正确：`polaris/delivery/http/app_factory.py`；`polaris.delivery.cli.pm.cli` / `director.cli_thin`（pyproject 控制台脚本 `pm`/`director`）；`polaris.cells.architect.design.internal.architect_cli` 与 `chief_engineer.blueprint.internal.chief_engineer_cli`；`polaris/kernelone/llm/toolkit/`；role chat 经 `routers/role_chat.py`；`/v2/pm` 在 `routers/pm_chat.py`+`pm_management.py` | edit | `test -e src/backend/app/main.py`/`scripts/pm`/`core/polaris_loop` 全部 MISSING；pyproject `[project.scripts]` 仅 polaris/pm/director |
| `src/backend/AGENTS.md` | §8.2/§8.3/§8.4 MCP/Tree-sitter/代码分析在 `src/backend/infrastructure/tools/{mcp_client,treesitter,code_analysis}.py`；§7 推荐 `python -m scripts.director.cli_thin`、role 表 `scripts/pm/cli.py` 与 `role_agent/*_cli.py` | `src/backend/infrastructure` 不存在；唯一 `treesitter.py` 在 `polaris/kernelone/llm/toolkit/executor/handlers/`；`mcp_client.py`/`code_analysis.py` 全仓不存在；CLI 同 CLAUDE.md 迁移路径 | edit | `find src/backend -name mcp_client.py`/`code_analysis.py` 无结果；`infrastructure/` MISSING |
| `src/backend/AGENTS.md` / `src/backend/CLAUDE.md` §6.1 / `src/backend/GEMINI.md` §6.1 | §15.1/§6.1 称 `docs/graph/subgraphs/` 当前**仅有** `execution_governance_pipeline.yaml` 与 `storage_archive_pipeline.yaml` 两个文件 | 实际有 **15** 个 subgraph yaml（pm_pipeline 93 行、director_pipeline 91 行、qa/audit/llm/finops/knowledge/context_plane 等）。三文件声明 `agent_instruction_snapshot_consistent` 一致性不变量，须**同步修正** | edit | `ls docs/graph/subgraphs/*.yaml \| wc -l` => 15 |
| `CLI_USAGE.md` | 方式1：`python polaris.py`、`hp.bat`、`hp.ps1`、`hp.sh`；方式2 安装 `hp`/`hpm` 控制台命令 | 仓根均不存在这些文件；pyproject 仅定义 `polaris`/`pm`/`director` | edit | `test -e polaris.py`/`hp.*` 全 MISSING |
| `CLI_USAGE.md` | 大量子命令 `polaris init/status/pm/director/backend …` | 控制台脚本 `polaris` = `polaris.delivery.server:main`，单一后端启动器，仅 `--host/--port/...` 标志，**无任何子命令** | **delete** | `server.py` main() 无 `add_subparsers` |
| `docs/CONSTITUTION.md` | 导入自 `core.polaris_loop.constitution` / `constitution_integration`；文件位置 `core/polaris_loop/constitution*.py` | 实现在 `polaris/cells/roles/kernel/internal/constitution_rules.py` 与 `constitution_adaptor.py`；bindings 在 `polaris/delivery/cli/pm/nodes/constitution_bindings.py` | edit | `find src/backend -name 'constitution*.py'` 指向 polaris/cells/... |
| `docs/ROLE_FRAMEWORK.md` | `from core.role_framework import RoleBase, RoleCLI, RoleFastAPI`；PYTHONPATH 加 `src/backend/core` | 包在 `polaris/kernelone/single_agent/role_framework/`（base/cli/fastapi.py）；无代码 `from core.role_framework` | edit | `find src/backend -type d -name role_framework` 指向 polaris/... |
| `docs/ROLE_KERNEL_REFACTOR_SUMMARY.md` | 角色内核位于 `src/backend/app/roles/*`，配置 `app/config/core_roles.yaml`，测试 `tests/test_roles_kernel.py`，chat `app/llm/usecases/role_dialogue.py` | `app/roles/` 整体删除；role_dialogue 迁至 `polaris/cells/llm/dialogue/internal/role_dialogue.py` | relabel-as-historical | `test -e src/backend/app/roles` MISSING |
| `docs/TUI_DIRECTOR_CONSOLE.md` | 模块 `polaris/delivery/cli/director/{console_app,console_widgets}.py`（含行号）；测试 `tests/test_director_console_textual_walkthrough.py` | 仅有 `console_{models,render,host}.py`；`console_app.py`/`console_widgets.py` 与该 walkthrough 测试**全仓不存在** | edit | `find src/backend -name console_app.py` 无结果 |
| `docs/agent/architecture.md` | ChiefEngineer 在 `core/polaris_loop/chief_engineer.py`；并列 `auditor.py`/`director_skills.py`/`director_capability_gate.py`/`tools/policy_mcp_server.py`/`core/director_runtime/storage/code_search.py` | `core/polaris_loop`、`core/director_runtime` 不存在；chief_engineer 迁至 `polaris/delivery/cli/pm/chief_engineer.py` 与 `.../http/v2/chief_engineer.py`；其余文件全仓不存在 | edit | 各 `find` 均空 |
| `docs/agent/reference.md` | 目录树假定顶层 `backend/`、`tools/`、`core/polaris_loop/`（io_utils/prompts/director_exec…）；CLI `backend/scripts/loop-{pm,director}.py` | 真实根为 `src/`；`core/polaris_loop` 不存在；loop 入口在 `polaris/delivery/cli/loop-{pm,director}.py` | edit | `ls src` => backend electron frontend |
| `docs/instructor_integration_guide.md` | `from app.roles import RoleExecutionKernel`；`from app.roles.schemas import TaskListOutput` | `app/roles` 不存在；schema 在 `polaris/cells/roles/adapters/internal/schemas/{pm,director}_schema.py` | edit | `test -d src/backend/app/roles` MISSING |
| `docs/instructor_tool_calling_demo.md` | `from app.roles.schemas import TaskListOutput, DirectorOutput, …` | 同上，导入将失败；schema 在 `polaris/cells/roles/adapters/internal/schemas/` | edit | `grep 'class DirectorOutput'` 指向 polaris/... |
| `docs/migration/unified-orchestration-migration-guide.md` | `from core.orchestration import get_orchestration_service`、`from application.dto.orchestration_contracts import …` | 实现在 `polaris/cells/orchestration/workflow_runtime/internal/{unified_orchestration_service,runtime_contracts,runtime_orchestrator}.py`；旧 import 路径全不存在 | edit | `find '*core/orchestration*'`/`'*application/dto*'` 空 |
| `docs/resident/README.md` | 后端入口 `src/backend/app/resident/*.py`（8 个）+ `api/v2/resident.py`；最小验证 `src/backend/tests/test_resident_*.py`（4 个） | `app/resident/`、`api/v2/resident.py` 删除；迁至 `polaris/cells/resident/autonomy/{public/service.py,internal/*}` 与 `polaris/delivery/http/v2/resident.py`；测试在 `polaris/tests/` | edit | 各 `test -e` MISSING；`find` 指向 polaris/... |
| `docs/resident/resident-engineering-rfc.md` | §4.1/§4.2/§11 AGI 内核在 `src/backend/app/resident/*`、控制面 `api/v2/resident.py`、projection `app/services/runtime_projection.py`、hooks `app/orchestration/workflows/*`、测试 `src/backend/tests/test_resident_*` | 全部迁至 `polaris/cells/resident/autonomy/internal/*` 与 `polaris/domain/models/resident.py`；router `polaris/delivery/http/v2/resident.py`；workflows 在 `polaris/cells/orchestration/...`；测试在 `polaris/tests/`；`runtime_projection.py` 不存在 | edit | `test -e src/backend/api/v2/resident.py`/`app/orchestration` MISSING |
| `docs/agent/workspace_persistence.md` | "## 当前实现" 列 `app/routers/system.py`、`app/settings_utils.py`、`api/main.py`、`core/startup/backend_bootstrap.py` 及 `src/backend/tests/test_workspace_*` | 旧根删除；`system.py`→`polaris/delivery/http/routers/system.py`，`settings_utils.py`→`polaris/cells/storage/layout/internal/`，`backend_bootstrap.py`→`polaris/bootstrap/` | edit | 4 路径 + 测试 MISSING |
| `src/backend/docs/API_V2_QUICK_REFERENCE.md` | §3/§5 错误信封为**扁平** `{"code","message","details"}` | 实际处理器统一包裹在 `error` 顶层键 `{"error":{"code","message","details"}}`；兄弟文档 VERSIONING/ONBOARDING §4.1 均为嵌套形，本文档自相矛盾 | edit | `error_handlers.py` 54-58/84-87/110-113/144-147 均为 `{"error":{...}}` |
| `src/backend/docs/cognitive_runtime_architecture.md` | "当前代码事实(2026-03-26)" 列 Phase-1 skeleton 已落在 `polaris/bootstrap/cognitive_runtime/` | 其余 5 个落点存在，唯独 `bootstrap/cognitive_runtime` 不存在 | edit | `grep -rln cognitive_runtime polaris/bootstrap/` 空 |
| `src/backend/docs/audit/contextos/CONTEXTOS_DESIGN_IMPLEMENTATION_GAP_ANALYSIS_REPORT_20260331.md` | 应为 ContextOS 设计-实现差距分析报告 | 全文仅 8 字节 `: false;`，文件损坏/截断，无内容 | **delete** | `wc -c` => 8 bytes |
| `src/backend/docs/governance/CANONICAL_TOOL_SPEC.md` / `AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD.md` / `TOOL_ALIAS_DESIGN_GUIDE.md` / `TOOL_REGISTRATION_STANDARD.md` | 工具契约 SSOT = `polaris/kernelone/tools/contracts.py`（`_TOOL_SPECS`、`canonicalize_tool_name`）；并列 `tool_normalization.py`、`llm_caller.py` 单文件 | `tools/` 仅 `__init__.py`；真实 SSOT 在 `polaris/kernelone/tool_execution/contracts.py:152 _TOOL_SPECS`、`:155 canonicalize_tool_name`；`tool_normalization`/`llm_caller` 均为目录 | edit | gate import `from polaris.kernelone.tool_execution.contracts import …` |
| `src/backend/docs/governance/cli_visual_quality_gate.md` | Status 已生效，铁律：严禁在 `src/backend/polaris/**` 下创建任何测试文件（`❌ cells/**/test_*.py`），测试只能在 `tests/` | 仓库**普遍**在 `polaris/cells/.../tests/` 同址放测试，共 **339** 个，是项目实际规范；该铁律与现状直接冲突 | edit | `find polaris/cells -path '*/tests/test_*.py' \| wc -l` => 339 |
| `docs/testing/FULL_CHAIN_AUDIT_ACCEPTANCE_MATRIX.md` | 验证命令含 `src/backend/polaris/tests/domain/verification/test_director_policy_gate.py` | 该测试不存在（目录仅 business_validators/evidence_collector/progress_delta）；命令会因路径报错 | edit | `ls .../domain/verification/` 无该文件 |
| `docs/testing/HYBRID_UI_AUTOMATION.md` | `npm run test:e2e:hybrid`、`npm run auto:fix:hybrid` | package.json 无这两个脚本（仅 auto:fix:panel/real-flow）；`run-hybrid-panel-task.mjs` 存在但未接入 | edit | package.json scripts 检查 MISSING |
| `src/backend/polaris/cells/director/runtime/README.agent.md` | "MIGRATION COMPLETED (2026-04-09)"，Runtime 拥有 PatchApplyEngine/FileApplyService/ExistenceGate/RepairService | `runtime/internal/` 仅 `__init__.py`（空骨架）；FileApplyService/RepairService 实际在 `director/tasking/internal/`；无 `PatchApplyEngine`/`ExistenceGate` 类；源仍在 `director/execution/internal/` | edit | `find director/runtime -type f` 仅骨架文件 |
| `src/backend/polaris/cells/director/delivery/README.agent.md` | "MIGRATION COMPLETED"，CLI 迁至 `delivery/cli/director/`，源 `execution/internal/director_cli.py` 已迁移 | `delivery/cli/director/` 无 `director_cli.py`；`director_cli.py` 仍同时存在于 `execution/internal/` 与 `tasking/internal/`，迁移未发生 | edit | `test -e execution/internal/director_cli.py` OK |
| `src/backend/polaris/cells/director/execution/README.agent.md` | "All phases complete as of 2026-04-09"，Phase 4/5(runtime/delivery) 完成 | runtime 为空骨架、delivery 无 director_cli；全部实现仍在 `execution/internal/`，Phase 4/5 未完成 | edit | `ls execution/internal/` 完整实现仍在 |
| `src/backend/polaris/cells/roles/runtime/README.agent.md` | 拥有 `polaris/application/services/role_session_{service,artifact_service,audit_service}.py` | `application/services/` 不存在；`role_session_service.py`→`cells/roles/session/internal/`，audit→`cells/audit/evidence/internal/`，artifact_service **无实现**（仅测试） | edit | `ls polaris/application/services/` 无此目录 |
| `src/frontend/src/app/components/llm/ARCHITECTURE_OPTIMIZATION.md` | 7 个 data-manager 类（Optimized/Batch/Lazy/Reactive/ConflictAware/Optimistic/Debuggable）+ 旧 `UnifiedLlmDataManager` | 唯一存在的是 `UnifiedLlmDataManagerV2`；其余 7 类全仓不存在，设计从未实现 | relabel-as-historical | `grep 'class.*DataManager'` 仅 `UnifiedLlmDataManagerV2` |
| `src/backend/polaris/kernelone/llm/RELIABILITY_AUDIT_REPORT.md` | Critical C1：CircuitBreaker 缺少 HALF_OPEN 状态 | `engine/resilience.py` 已实现 `CircuitState.HALF_OPEN`、`half_open_max_calls`、完整 OPEN→HALF_OPEN→CLOSED 转换；核心 Critical 已解决 | relabel-as-historical | `grep HALF_OPEN engine/resilience.py` 命中 209-305 |

---

## 中优先级 (Medium)

| 文档 | 文档声称 | 实际代码 | 建议 | 证据 |
|---|---|---|---|---|
| `CLAUDE.md` §7.5 | 替代项 `app/roles/workflow_adapter.py` 隐含为现役 | `app/roles/` 删除，该替代文件不存在 | edit | `test -e` MISSING |
| `AGENTS.md` §8.6 | 多编辑实现 `precision_editor.py` | 无此文件；`precision_edit` 仅是工具操作名 | edit | `find -name precision_editor.py` 空 |
| `src/backend/AGENTS.md` §5 | `scripts/` 保留 56 个文件 | 实际 86 个 | edit | `find scripts -type f \| wc -l` => 86 |
| `README.md` / `README.en.md` | `pytest -q tests/architecture/test_kernelone_release_gates.py` | 实际在 `src/backend/polaris/tests/architecture/`，命令失败 | edit | repo-root 路径 MISSING |
| `CLI_USAGE.md` | 流程示例 `polaris director --workspace . --iterations 1` | Director 是独立 `director` 控制台脚本，非 `polaris director` 子命令 | edit | pyproject director= cli_thin |
| `src/backend/docs/cognitive_runtime_architecture.md` §6.3 | 复用源 `polaris/kernelone/context/manager.py` | 该文件不存在；唯一 `manager.py` 在 `context/context_os/memory/` | edit | `find` 仅 memory/manager.py |
| `src/backend/docs/KERNELONE_OPENCODE_INTEGRATION_ARCHITECTURE.md` | ToolState FSM 在 `polaris/kernelone/tool/{state_machine,tracker}.py` | 实际在 `polaris/kernelone/tool_state/`；文档 import 全部会 ModuleNotFoundError | edit | `tool/` MISSING；`tool_state/` OK |
| `src/backend/docs/governance/CANONICAL_TOOL_SPEC.md` / `TOOL_CALLING_CANONICAL_GATE_STANDARD.md` 等 | `tool_normalization.py`/`llm_caller.py` 单文件；回归测试 `tests/architecture/test_tool_calling_canonical_gate.py` | 均为目录；测试在 `polaris/tests/architecture/` | edit | 单文件 MISSING，目录 OK |
| `src/backend/docs/governance/decisions/ADR-CONTEXTOS-001/002` | ProviderFormatter/ContextEvent 在 `kernelone/context/contracts.py`+`formatters/`；`context_gateway.py` 单文件 | ProviderFormatter 在 `llm_caller/provider_formatter.py`；ContextEvent 在 `kernelone/events/context_events.py`；`context_gateway/` 为目录；无 `formatters/` 目录 | edit | 各 grep/test 见原始证据 |
| `src/backend/docs/governance/decisions/adr-0055/adr-0065` | ProviderManager 仅存于 `kernelone/llm/providers/registry.py`；RolePolicyEngine 在 `kernelone/policy/role_engine.py`；ToolSpecRegistry 在 `kernelone/tools/tool_spec_registry.py`；删除 core_roles.yaml | 仍有第二份 ProviderManager 在 `infrastructure/llm/providers/provider_registry.py:32`（被多处导入）；无 `role_engine.py`/`RolePolicyEngine`；ToolSpecRegistry 在 `tool_execution/`；core_roles.yaml 仍存在两处——收敛未完成 | edit / relabel | `grep 'class ProviderManager'` 命中两处 |
| `docs/agent/architecture.md` / `docs/agent/README.md` | 链接 `docs/agent/pm-director-flow.md`、`qa_chancellery_v1.md`、`chief_engineer_blueprint.md`、`failure_3hops_implementation.md` | 这些文档均不存在 | edit | `find`/`test -e` MISSING |
| `docs/phase0/audit-system-design.md` | 审计组件在 `application/audit_service.py`、`domain/verification/`、`infrastructure/persistence/` | `audit_service.py` 不存在；IndependentAuditService 在 `polaris/cells/audit/verdict/internal/`；store 在 `polaris/infrastructure/audit/stores/` | edit | 各 grep 见原始证据 |
| `docs/phase0/rbac-technical-design.md` | require_permission/RoleToolGateway/SecurityService 在 `api/dependencies.py`、`app/roles/gateways/tool_gateway.py`、`domain/services/security_service.py` | 迁至 `polaris/cells/roles/kernel/internal/tool_gateway.py`、`polaris/domain/services/security_service.py`、`polaris/delivery/http/dependencies.py` | edit | 旧路径 MISSING |
| `docs/phase0/memory-retrieval-evaluation.md` | MemoryStore 在 `core/polaris_loop/anthropomorphic/memory_store.py`；ContextEngine 在 `core/polaris_loop/context_engine/engine.py` | 迁至 `polaris/kernelone/memory/memory_store.py`、`polaris/kernelone/context/engine/engine.py` | edit | 旧路径 MISSING |
| `docs/product/product_spec.md` | 底部链接 `architecture.md`/`anthropomorphic_design.md`/`reference.md` | 三个相对链接均失效 | edit | 三者 MISSING |
| `docs/architecture/落盘体系重构与分类治理_v2.md` | storage_policy / storage_layout / history_archive / archive_hook / task_board / iteration_state / orchestration_service_impl / migrate_storage_layout_v2 均在旧 `core`/`app` 路径 | 迁至 `polaris/kernelone/storage/{policy,layout}.py` 与 `polaris/cells/...`；后两者全仓不存在 | edit | 各 find 见原始证据 |
| `docs/resident/resident-engineering-rfc.md`(§11) / `resident-rollout.md` / `agi-value-proposition.md` | 验证/实现路径在 `src/backend/tests/test_resident_*`、`app/orchestration/workflows/*`、`app/resident/*` | 迁至 `polaris/tests/` 与 `polaris/cells/...` | edit | 旧路径 MISSING |
| `src/backend/docs/governance/decisions/adr-0066/adr-0067` | Context Benchmark runner `infrastructure/accel/eval/runner.py`；新增 `benchmark/adapters/context_fixture_mapper.py` | runner.py/context_fixture_mapper.py 均不存在（metrics.py 存在） | edit / relabel | `test -e` MISSING |
| `docs/testing/FULL_CHAIN_AUDIT_ACCEPTANCE_MATRIX.md` | `director_policy_gate.py` 校验禁路径/作用域 | 全仓无此文件；逻辑在 `polaris/kernelone/llm/toolkit/write_policy.py` | edit | `grep director_policy_gate` 空 |
| `docs/testing/PLAYWRIGHT_ELECTRON_AUTOMATION.md` | `npm run test:e2e:hybrid -- --dry-run` | 无该脚本（同 HYBRID 文档根因） | edit | package.json MISSING |
| `src/backend/polaris/cells/director/planning/README.agent.md` | "MIGRATION COMPLETED"，实现从 execution 迁移 | 是**复制**而非迁移：`execution/internal/` 仍保留 director_agent/context_gatherer/director_logic_rules，重复 | edit | `ls execution/internal/` 仍列出 |
| `src/backend/polaris/cells/llm/{dialogue,evaluation}/README.agent.md` | Verification 列若干 `tests/test_*.py` 为 cell-relative | 实际在中央 `polaris/tests/`，cell-relative 解析不到 | edit | `ls cells/llm/.../tests/` 不含 |
| `src/backend/polaris/cells/roles/runtime/README.agent.md` | `internal/tui_console.py`、`internal/standalone_entry.py` 为冻结遗留窗口 | 两文件已删除（tech-debt-tracker 2026-04-05），不存在 | edit | `test -e` MISSING |
| `src/backend/docs/governance/blueprints/tool-catalog-unification-blueprint.md` / `ADR-CONTEXTOS-*` | `kernelone/tools/contracts.py`、`tool_normalization.py` 编辑目标 | contracts 在 `tool_execution/`；tool_normalization 为目录 | edit | 见上 |
| `src/backend/polaris/docs/SESSION_STATE_MACHINE.md` | SessionState 7 态枚举 + SessionStateTransitionEvent | 实现仅 4 态(ACTIVE/PAUSED/COMPLETED/ARCHIVED)；无该 Event 类 | relabel-as-historical | `grep class SessionState` 仅 4 态 |
| `src/backend/polaris/kernelone/context/context_os/pipeline_design.md` | `enable_pipeline` 特性开关含向后兼容回退 | 无该属性；`engine.py` project() 无条件走 pipeline，pipeline 是唯一路径 | edit | `grep enable_pipeline` 仅 .md |
| `src/backend/polaris/kernelone/llm/RELIABILITY_AUDIT_REPORT.md` | `toolkit/executor.py`、`tool_normalization.py` 单文件 | 均为目录 | relabel-as-historical | `test -e` MISSING |
| `src/backend/polaris/tests/{CODE_REVIEW_REPORT,FINAL_VERIFICATION_REPORT,TEST_REPORT_JSON_TOOL_PARSING}.md` | `kernelone/tools/{validators,contracts}.py` + `tools/tests/`；`llm_caller.py` 单文件 | `tools/` 仅 `__init__.py`(已弃用)；validators 迁至 `tool_execution/`；llm_caller 为目录 | relabel-as-historical | `ls kernelone/tools/` 仅 __init__ |
| `src/backend/polaris/kernelone/llm/tools/README.md` | 描述为现役工具调用内核运行时 | 该包 `__init__.py` 自标 DEPRECATED，README 无弃用提示 | edit | `head __init__.py` DEPRECATED |
| `src/backend/polaris/tests/agent_stress/AGENT_GUIDE.md` | `app/llm/usecases/role_dialogue.py`(现役)、`application/services/factory_run_service.py` | `app/` 不存在；role_dialogue 在 `polaris/cells/llm/dialogue/internal/`；factory_run_service 在 `polaris/cells/factory/pipeline/internal/` | edit | `find` 指向 polaris/... |
| `src/frontend/README.md` | `utils/performance.tsx` + `useRenderTime` hook | 全仓无此文件/符号 | edit | `find performance*` 空 |
| `src/frontend/src/app/components/ai-dialogue/README.md` | quick-start 用 `role`/`roleName` props | 实际必填 prop 是 `dialogueRole`/`roleDisplayName`（自相矛盾） | edit | `AIDialoguePanel.tsx:48-52` |
| `src/frontend/src/app/components/llm/test-integration.md` | 后端端点带 `/api/llm/providers...` 前缀 | 实际为 `/llm/providers`(DEPRECATED) 与 `/v2/llm/providers/*`，无 `/api` 前缀 | edit | `grep '/api/llm/providers'` 空 |
| `src/backend/docs/governance/README.md`(等) 测试路径前缀 | 多处 `tests/...` cell/backend-relative | 实际在 `polaris/tests/...` | edit | 见各原始证据 |

---

## 低优先级 / 历史归档 (Low / historical)

仅简列；多为带日期的历史 ADR/报告（present-tense 但实为快照）、断链、行号漂移、单文件→目录的表述滞后等。

- **记忆文件漂移**：`swebench-phaseb-status.md` 提到 `repo_map.py`（实际拆分为 facade/ranker/renderer）、`service.py:1161`（实际 `:3517`）；`MEMORY.md` 提到已修改 `worker_service.py`（全仓不存在）——均建议 relabel-as-historical / 更新行号。
- **历史 ADR/迁移记录（建议 relabel-as-historical）**：`docs/architecture/{ADR-001-unified-orchestration-kernel,adr-001-thin-cli-policy,compatibility-inventory,current-baseline,IMPLEMENTATION-SUMMARY,llm-full-link-implementation,migration-guide-phase6,refactoring-implementation-summary}.md`、`docs/migration/cli-adapter-v2.md`、`docs/resident/implementation-roadmap.md`、`docs/agent/AGENTS_FULL.md`、`adr-0065`(provider/role 收敛未完)、`src/backend/polaris/cells/SCHEMA_STANDARDIZATION_PLAN.md`(52→62 cells)、`src/backend/polaris/cells/roles/tech-debt-tracker.md`(role_agent_service 已删)、`src/backend/polaris/tests/llm_stress/{IMPROVEMENT,TOOLS_INTEGRATION}_SUMMARY.md`——内容引用已删旧根，应标注为历史快照。
- **断链 / 文件重命名（建议 edit）**：`docs/blueprints/INDEX.md`(未含 2026-05/06 蓝图)、`src/backend/docs/blueprints/README.md`(双重路径前缀)、`research/README.md`(链接 `CONTEXTOS_TURNENGINE_RESEARCH_INITIATIVE_*` 不存在、adr-0068 误指 dead-loop)、`docs/archive/README.md`(adr-0071/COGNITIVE_LIFEFORM memo 实际在 `src/backend/docs/`)、`adr-0065/0066`+`benchmark-sandbox-...` 蓝图引用未跟随 archive 迁移、`POLARIS_CLI_PRODUCT_MEMO`(KERNELONE_→POLARIS_ 重命名)、`docs/incidents/INCIDENT-2026-03-11-...`(testing/agent-stress-testing.md 缺失)、`docs/migration/cli-adapter-v2.md`(config-snapshot.md/ports/README.md 缺失)、`TUI_DIRECTOR_CONSOLE.md`(KERNELONE_CLI_PRODUCT_MEMO 缺失)、`docs/agent/AGENTS_FULL.md`(backend/tools 顶层)。
- **单文件→目录表述滞后（建议 edit，多为低危）**：`turn_engine.py`→`turn_engine/`（adr-0042/0044、tool-catalog-blueprint、kernel/README）、`context_gateway.py`→目录（FOUR_EXPERT_AUDIT）、`runtime.py`→目录（harborpilot-whitepaper StateFirstContextOS）、`toolkit/{executor,parsers,tool_normalization}.py`→目录（TOOL_CALLING_PROTOCOL）。
- **图谱 catalog 偏差（建议 edit）**：`cells.yaml` 中 `code_intelligence.engine`/`director.runtime`/`director.delivery` 声明的 `public/contracts.py` 不存在（仅 `__init__.py`）；`code_intelligence.engine` 的 "pending creation" gap 已过期（目录已建）。`director.runtime`/`director.delivery` 自述迁移中，可保留(keep)。
- **其它低危 edit**：`API_DEVELOPER_ONBOARDING.md`/`API_STANDARDIZATION_CHANGELOG.md`(UserRole 实际在 `auth/roles.py` 字符串枚举，非 `middleware/rbac.py` 整数枚举)、`akashic/README.md`(docs/ 子目录、IMPLEMENTATION.md 缺失)、`TOOLS_SYSTEM_ENHANCEMENT_PLAN.md`/`adr-0042-canonical-code-exploration`(tools/contracts.py)、`EVENT_BUS_CONVERGENCE_PLAN.md`(neural_syndicate 迁 multi_agent/)、`LLM_TOOL_ADAPTER_BLUEPRINT.md`(标"已实现"但 5/6 文件不存在→relabel)、多个 cell README 的 cell-relative 测试路径(`provider_runtime`/`provider_config`/`tool_runtime`/`pm_dispatch`/`projection`/`artifact_store`/`run_archive`/`task_snapshot_archive`)、`kernelone/{core,traceability}/README.agent.md`("目录待实现" 已过期)、`agent_stress/README.md`(`core.stress_path_policy` 应为 `.stress_path_policy`)、`TEST_COVERAGE.md`(`run_textual_tests.py` 不存在)、`src/electron/assets/README.md`(`Polaris-icon.png` 大小写)、`product_spec.md`/agent_stress 提示词(`tests.agent_stress` 不可导入)、`ai-dialogue/README.md`(PMAIDialoguePanel 在 components/pm/)、`ARCHITECTURE_OPTIMIZATION.md`(EnhancedLLMSettingsTab 不存在)、`docs/CLI` ModuleNotFound 排错引用 hp.bat/hp.sh。

---

## 建议的处置清单 (Actionable disposition)

按建议类型分组，逐条可单独审批。

### delete（删除）
- [ ] **delete** `src/backend/docs/audit/contextos/CONTEXTOS_DESIGN_IMPLEMENTATION_GAP_ANALYSIS_REPORT_20260331.md`：8 字节损坏文件，无内容，删除。
- [ ] **delete** `CLI_USAGE.md` 的整段 `polaris init/status/pm/director/backend` 子命令文档：`polaris` 入口无子命令，删除该误导章节（或重写为 `pm`/`director` 独立脚本说明）。

### edit（修正路径/命令/事实）
- [ ] **edit** `CLAUDE.md`：§1/§2/§3/§5/§7 全部旧根路径改为 `src/backend/polaris/*`（入口 `delivery/http/app_factory.py`、CLI 控制台脚本 `pm`/`director`、toolkit `kernelone/llm/toolkit/`、role_dialogue/task_board 迁移路径、§7.5 替代项更新）。
- [ ] **edit** `src/backend/AGENTS.md`：§8.2-8.4 工具路径改 `polaris/kernelone/llm/toolkit/executor/handlers/treesitter.py` 等；§5 scripts 计数 56→86；§15.1 subgraphs "仅有两个"→列 15 个。
- [ ] **edit** `src/backend/CLAUDE.md` §6.1 与 `src/backend/GEMINI.md` §6.1：与 AGENTS.md §15.1 同步修正 subgraphs 计数（一致性 gate 要求三文件同改）。
- [ ] **edit** `CLI_USAGE.md`：删除 `polaris.py`/`hp.*`/`hpm`，改用 `polaris`/`pm`/`director` 控制台脚本；故障排除去掉 hp.bat/hp.sh。
- [ ] **edit** `README.md` / `README.en.md`：release-gate 测试路径改 `src/backend/polaris/tests/architecture/test_kernelone_release_gates.py`。
- [ ] **edit** `docs/CONSTITUTION.md` / `docs/ROLE_FRAMEWORK.md`：constitution/role_framework 导入与位置改 `polaris/cells/roles/kernel/internal/` 与 `polaris/kernelone/single_agent/role_framework/`。
- [ ] **edit** `docs/TUI_DIRECTOR_CONSOLE.md`：删除 `console_app.py`/`console_widgets.py`/walkthrough 测试引用，改为现存 `console_{models,render,host}.py`。
- [ ] **edit** `docs/agent/{architecture,reference,README}.md`：ChiefEngineer/目录树/loop CLI 路径改 `src/backend/polaris/*`；补建或移除 pm-director-flow/qa_chancellery 等死链。
- [ ] **edit** `docs/instructor_integration_guide.md` / `docs/instructor_tool_calling_demo.md`：`app.roles.schemas` 改 `polaris.cells.roles.adapters.internal.schemas.*`。
- [ ] **edit** `docs/resident/{README,resident-engineering-rfc,resident-rollout,agi-value-proposition}.md`：resident 路径改 `polaris/cells/resident/autonomy/*` 与 `polaris/tests/`、`polaris/delivery/http/v2/resident.py`。
- [ ] **edit** `docs/agent/workspace_persistence.md` / `docs/phase0/{audit-system-design,rbac-technical-design,memory-retrieval-evaluation}.md`：旧 app/api/core 组件路径改 polaris 迁移路径。
- [ ] **edit** `docs/migration/unified-orchestration-migration-guide.md`：import 改 `polaris/cells/orchestration/workflow_runtime/internal/*`；架构守护测试改 `polaris/tests/refactor/`。
- [ ] **edit** `docs/architecture/落盘体系重构与分类治理_v2.md`：storage/archive/task_board 等改 `polaris/kernelone/storage/*` 与 `polaris/cells/*`。
- [ ] **edit** `src/backend/docs/API_V2_QUICK_REFERENCE.md`：错误信封改为嵌套 `{"error":{...}}`（对齐 error_handlers.py）。
- [ ] **edit** `src/backend/docs/cognitive_runtime_architecture.md`：删除/更正 `bootstrap/cognitive_runtime` 落点；§6.3 改 `context/context_os/memory/manager.py`。
- [ ] **edit** `src/backend/docs/governance/{CANONICAL_TOOL_SPEC,AGENTIC_TOOL_CALLING_MATRIX_V2_STANDARD,TOOL_ALIAS_DESIGN_GUIDE,TOOL_REGISTRATION_STANDARD,TOOL_CALLING_CANONICAL_GATE_STANDARD,README}.md` 及 `blueprints/tool-catalog-unification-blueprint.md`：SSOT 改 `polaris/kernelone/tool_execution/contracts.py`；`tool_normalization`/`llm_caller`/`turn_engine` 标为目录；测试路径加 `polaris/` 前缀。
- [ ] **edit** `src/backend/docs/governance/cli_visual_quality_gate.md`：撤销"禁止在 polaris/ 下放测试"铁律，改为承认 `cells/.../tests/` 同址约定（339 个现存）。
- [ ] **edit** `src/backend/docs/governance/decisions/{ADR-CONTEXTOS-001,ADR-CONTEXTOS-002,adr-0055,adr-0065,adr-0066,adr-0067}.md`：ProviderFormatter/ContextEvent/ProviderManager/ToolSpecRegistry/benchmark 路径更正；标注收敛未完成阶段。
- [ ] **edit** `src/backend/docs/KERNELONE_OPENCODE_INTEGRATION_ARCHITECTURE.md`：`kernelone/tool/` → `kernelone/tool_state/`。
- [ ] **edit** `docs/testing/{FULL_CHAIN_AUDIT_ACCEPTANCE_MATRIX,HYBRID_UI_AUTOMATION,PLAYWRIGHT_ELECTRON_AUTOMATION}.md`：修正 director_policy_gate 文件名/测试路径；补/改 `test:e2e:hybrid`、`auto:fix:hybrid` 脚本说明。
- [ ] **edit** director cell README：`director/{runtime,delivery,execution,planning}/README.agent.md` 更正"MIGRATION COMPLETED"为实际状态（runtime/delivery 仍为骨架、planning 为复制非迁移、实现仍在 execution/internal）。
- [ ] **edit** `src/backend/polaris/cells/roles/runtime/README.agent.md`：role_session 服务路径改 `cells/roles/session/internal/` 等；删除已删的 tui_console/standalone_entry 引用。
- [ ] **edit** 前端文档：`src/frontend/README.md`(utils/performance)、`ai-dialogue/README.md`(dialogueRole/roleDisplayName、PMAIDialoguePanel)、`llm/test-integration.md`(去 `/api` 前缀)、`llm/ARCHITECTURE_OPTIMIZATION.md`(EnhancedLLMSettingsTab)、`src/electron/assets/README.md`(polaris-icon.png 小写)。
- [ ] **edit** 图谱 catalog `src/backend/docs/graph/catalog/cells.yaml`：移除过期 "pending creation" gap；修正 `public/contracts.py` 缺失的声明。
- [ ] **edit** 各类断链/重命名/单文件→目录的低危文档（见低优先级清单条目）。

### relabel-as-historical（标注为历史快照）
- [ ] **relabel** `docs/architecture/*`(ADR-001/thin-cli/compatibility/baseline/IMPLEMENTATION-SUMMARY/llm-full-link/migration-guide-phase6/refactoring-summary)、`docs/migration/cli-adapter-v2.md`、`docs/resident/implementation-roadmap.md`、`docs/agent/AGENTS_FULL.md`：在抬头加"历史快照(日期)，路径已被 polaris 迁移取代"。
- [ ] **relabel** `docs/ROLE_KERNEL_REFACTOR_SUMMARY.md`、`SCHEMA_STANDARDIZATION_PLAN.md`(52→62)、`roles/tech-debt-tracker.md`、`llm_stress/*_SUMMARY.md`、`tests/{CODE_REVIEW,FINAL_VERIFICATION,TEST_REPORT_JSON_TOOL_PARSING}.md`、`RELIABILITY_AUDIT_REPORT.md`(HALF_OPEN 已实现)、`SESSION_STATE_MACHINE.md`(4 态)、`LLM_TOOL_ADAPTER_BLUEPRINT.md`(未实现)、`ARCHITECTURE_OPTIMIZATION.md`(7 类不存在)：标注为历史/未实现。
- [ ] **relabel** 记忆文件 `swebench-phaseb-status.md`、`MEMORY.md`：标注已迁移/行号漂移条目为历史。

### keep（保留）
- [ ] **keep** `cells.yaml` 中 `director.runtime` / `director.delivery` 的 `public/contracts.py` 声明（自述"迁移中"，与现状一致，无需改动）。
