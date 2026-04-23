Polaris 架构完善与强收敛实施计划 v2
Summary

目标不是“继续修补旧链路”，而是把 Polaris 收敛成 单一执行写路径 + 单一运行态读模型 + 单一配置真源 + 明确的兼容退役路径。
已锁定决策：两阶段退役旧入口、把 Factory / RoleSession / PermissionService 功能缺口纳入主计划、按阶段可回滚交付。
本计划只覆盖 Polaris 主仓，不涉及任何目标项目代码；所有文本读写必须显式 UTF-8。
Locked Decisions

正式 API 面统一为 /v2/*；旧 /pm/*、/director/*、旧 role_chat 走两阶段退役。
正式执行内核统一为 UnifiedOrchestrationService；旧 runtime_orchestrator.py 仅允许短期 shim。
正式运行态读模型统一为 runtime_projection.py::build_runtime_projection()，见 runtime_projection.py:560。
ConfigSnapshot 是唯一配置真源；请求 DTO 不再携带"伪显式默认值"覆盖配置。
FactoryRun、RoleSession、PermissionService 不是后续"补充项"，而是本轮收敛的正式工作包。

Current Implementation Status (Updated: 2026-03-06)

| Phase | 目标 | 实际状态 | 关键文件 |
|-------|------|----------|----------|
| 0 | 基线止血与冻结 | implemented | backend_launch.py DTO 统一 |
| 1 | 单一运行态读模型 | implemented | RuntimeProjectionService 收口 |
| 2 | API 装配边界重构 | adopted | legacy_tombstone.py 已替换 legacy_bridge |
| 3 | 配置系统治理 | in_progress | sys.path 清理进行中 |
| 4 | 单一执行写路径 | implemented | OrchestrationCommandService 已创建 |
| 5 | Factory 服务化 | implemented | FactoryRunService 21 tests PASS |
| 6 | RoleSession 正式化 | adopted | export-to-workflow 前后端已集成 |

注意：本计划描述目标架构，实际采用状态见 current-baseline.md 中的 Adoption 状态表。
Target Architecture

api/*：只做 HTTP/WS 适配、鉴权、DTO 映射、错误映射，不包含业务状态拼装和流程控制。
app/services/*：只做读模型投影、持久化适配、会话/产物/审计存取、兼容包装。
application/*：只做命令式用例和 orchestration command coordination。
domain/*：只放不可变合同、枚举、校验规则、失败分类。
core/*：只放低层运行时、进程编排、工具执行基础设施；不再依赖 app/* 的业务实现。
frontend/*：只消费 canonical DTO，不再自己推断多套 PM/Director/QA 状态格式。
Phase 0 — 基线止血与冻结

范围：先修当前代表性失败门禁，再冻结真实 W0 基线，禁止带病进入大重构。
文件： backend_launch.py, backend_bootstrap.py, config_loader.py, runtime_lifecycle.py, test_backend_bootstrap.py, test_runtime_lifecycle.py, current-baseline.md。
变更：BackendLaunchRequest.host、cors_origins、ramdisk_root 改为“显式可选输入”，默认值只由 ConfigLoader.DEFAULTS 提供；__post_init__() 只做无副作用规范化，不做 workspace existence hard fail；workspace/ramdisk/port 校验统一放进 validate() 和 bootstrap()。
变更：backend_bootstrap._load_configuration() 仅把用户显式传入字段转成 CLI override；未显式传入的 host/cors/log_level 不得覆盖 config_snapshot。
变更：runtime_lifecycle.py 去掉裸 from io_utils import ...，改为包内导入；test_runtime_lifecycle.py 改为按包路径导入模块，不再用 spec_from_file_location() 破坏包上下文。
变更：用实际代码重新生成 current-baseline.md；当前文档里对 /v2/director/status 的 merge 说明已与代码不一致，必须修正。
验收：test_runtime_projection.py -q 全绿。
回滚点：不进入下一阶段，直到以上门禁和基线文档同步完成。
Phase 1 — 单一运行态读模型收敛

范围：把所有“状态聚合”统一收口到 build_runtime_projection()，禁止路由层、WS 层、snapshot 层各自拼状态。
文件： runtime_projection.py, runtime_ws_status.py, system.py, artifacts.py, api/v2/director.py。
变更：保留 RuntimeProjection 作为唯一聚合容器；新增 build_snapshot_payload_from_projection(projection, state, workspace, cache_root)，由 projection 生成 /state/snapshot 的兼容载荷。
变更：artifacts.py::build_snapshot() 退化为 compatibility wrapper，内部调用 projection-based builder；新的状态逻辑不得继续直接读 PM/Director/engine/workflow 多源。
变更：/state/snapshot、/v2/ws/runtime、任何 dashboard/status hook 的服务端数据都统一从 RuntimeProjection 出来；runtime_ws_status.py 中 legacy merge 入口只保留 wrapper，不再持有第二套业务规则。
变更：/v2/director/status 明确保留“local-only role status”；需要统一态的前端一律走 /state/snapshot 或 /v2/ws/runtime。
验收：仓库 grep 只允许 merge_director_status() 在 runtime_projection.py 存在一份正式实现；test_runtime_projection.py 增加本地运行优先、workflow fallback、task rows 二选一、engine orphan recovery 四类场景。
回滚开关：KERNELONE_USE_RUNTIME_PROJECTION_V2=0 时退回旧 snapshot wrapper，但仅作为阶段性兜底，阶段完成后删除。
Phase 2 — API 装配边界重构与兼容层拆分

范围：拆掉“compat_router 既是入口又是兼容层”的混乱结构，正式路由和遗留桥接分离。
文件： api/main.py, api/routers/compat.py, api/v2/init.py, role_chat.py, pm_management.py。
新增：primary.py，只注册仍然正式支持的非 /v2 路由。
新增：legacy_bridge.py，Phase 2A 用于旧 /pm/*、/director/*、旧 role chat 入口到新接口的桥接，统一加 Deprecation: true、Sunset、Link 响应头，并在 body 中返回 migration_target。
新增：legacy_tombstone.py，Phase 2B 把桥接过的旧入口统一切成 410 Gone。
变更：api.main.create_app() 直接注册 v2_router、system.router、runtime.router、history.router、conversations.router、role_session.router 和仍然正式保留的 legacy routers；不再把整个应用装配都藏在 compat_router 后面。
变更：role_chat.py 明确为 Phase 2A 兼容桥，内部统一基于 RoleSessionService 和 /v2/roles/sessions/* 语义；Phase 2B 切 tombstone。
验收：所有正式前端调用不再依赖旧 /pm/*、/director/*、旧 role chat 入口；桥接日志有调用计数；桥接阶段测试断言响应头和 migration target。
回滚开关：KERNELONE_ENABLE_LEGACY_BRIDGE=1 保留桥接；切 410 前必须观察到旧调用量清零或受控。
Phase 3 — 配置系统与导入系统根因治理

范围：把 sys.path.insert 和“模块必须靠路径注入才可运行”的问题从生产代码中清除。
文件： backend_launch.py, config_loader.py, backend_bootstrap.py, app/utils.py, arsenal.py, pm_management.py, generic_pipeline_workflow.py。
变更：移除生产代码中的 sys.path.insert；测试和 CLI 壳层保留最小白名单引导，但必须在 compatibility-inventory.md 中登记。
变更：删除 backend_bootstrap._ensure_loop_modules()；任何 core/polaris_loop 包内模块一律改为包导入或相对导入。
变更：pm_management.py 不再动态注入 scripts/pm，改为正式应用服务或专用 adapter import。
新增：check_architecture_drift.py 增强规则，硬性校验生产目录不得新增 sys.path.insert/append，不得新增第二份 director status merge，不得从生产代码导入 deprecated orchestrator。
验收：rg "sys\\.path\\.insert|sys\\.path\\.append" src/backend 仅允许白名单文件命中；test_architecture_guard.py -q 全绿。
回滚：单文件可回滚，不影响 Phase 1/2 的运行态和接口合同。
Phase 4 — 单一执行写路径与 Orchestration Command 收口

范围：把 PM、Director、Factory 的“启动/运行/调度/停止”统一到 application command 层，避免 router、service、legacy orchestrator 各自发号施令。
文件： api/v2/pm.py, api/v2/director.py, core/orchestration/orchestration_service_impl.py, core/runtime_orchestrator.py。
新增：orchestration_command_service.py，作为 PM/Director/Factory 的唯一 command facade。
变更：/v2/pm/run、/v2/director/run、Factory 启动链统一调用 OrchestrationCommandService -> UnifiedOrchestrationService；不允许 router 直接启动脚本或直接拼 run_id/metadata 规则。
变更：PMService、DirectorService 保留为本地进程/状态适配器，但不再负责发起新的业务级 orchestration decisions；它们只暴露 local lifecycle 和 status。
变更：runtime_orchestrator.py 只保留 shim；所有实际 spawn_pm/spawn_director 都转到 orchestration command service 或新版 process launcher。
验收：新增“无 direct spawn outside allowlist” 架构守护测试；pm.py 和 director.py 不再内嵌 run_id 生成、metadata 拼装和 adapter registration 细节，统一下放到应用层。
回滚开关：KERNELONE_FACTORY_RUN_SERVICE_V2=0 时 Factory 仍可走旧路径，但 PM/Director V2 API 不回退到旧 orchestrator。
Phase 5 — Factory 正式服务化与审计持久化

范围：把当前 factory.py 中的 V1 内存编排提升为可恢复、可审计、可回放的正式服务。
文件： factory.py, factory.py types。
新增：factory_run_service.py，负责 FactoryRun 状态机、gate 执行、checkpoint、event append、恢复逻辑。
新增：factory_store.py，持久化 run snapshot、events、artifacts、gate results。
持久化策略：耐久数据存 workspace/.polaris/factory/<run_id>/；运行中临时数据可写 runtime/factory/<run_id>/；两者均显式 UTF-8。
变更：router 只做 CRUD、SSE、control action；阶段推进、重试、恢复、docs init、PM planning、Director dispatch 全部从 service 驱动。
变更：补齐当前 TODO：文档生成、PM 任务规划、Director 调用、pause/resume，不再返回空行为。
验收：Factory run 在后端重启后可从耐久 snapshot 恢复；test_factory_e2e_smoke_entry.py、相关 integration/e2e 必须转绿；full-chain audit 中对 factory 的 evidence path 可追溯到 durable run dir。
回滚：保留原有类型定义 FactoryRun 不变，优先保 API 兼容，再替换实现。
Phase 6 — RoleSession 正式化与工作台导出闭环

范围：把 /v2/roles/sessions/* 从“有 CRUD、缺 artifacts/audit/export-to-workflow”的半成品，收敛成正式多宿主会话系统。
文件： role_session.py, role_session_service.py, PMWorkbenchPanel.tsx, DirectorWorkbenchPanel.tsx。
新增：role_session_artifact_service.py，负责 session artifact 索引、路径分配、列表/查询。
新增：role_session_audit_service.py，负责 session audit event 读取和筛选。
持久化策略：每个 session 的耐久根目录固定为 workspace/.polaris/role_sessions/<session_id>/，子目录包含 artifacts/, audit/, exports/。
变更：GET /v2/roles/sessions/{id}/artifacts 返回真实 artifact metadata，不再返回空数组；GET /audit 返回规范化 audit events。
新增：POST /v2/roles/sessions/{id}/actions/export-to-workflow，请求体固定为 { target: "pm" | "director", run_id?: string, task_id?: string, export_kind: "session_bundle" | "patch_bundle" }。
变更：前端 PM/Director workbench 的“导出到流程/导出补丁”改为调用 export-to-workflow，不再 alert + TODO。
变更：role_chat.py 只保留兼容桥职责，新客户端只认 /v2/roles/sessions/*。
验收：workbench 会话可创建、附着、发消息、导出 artifact、查看 audit、导出到 workflow；对应前端 vitest 和 Electron workbench 用例转绿。
Phase 7 — PermissionService 真正完成，不再挂 TODO

范围：把当前权限系统从“声明很多、条件评估/继承解析没实现”的状态补全为可审计 PDP。
文件： permission_service.py, permissions.py。
新增：permission_condition_evaluator.py，实现 policy condition evaluation。
新增：permission_role_graph.py，实现 role inheritance expansion 和 cycle detection。
新增：permission_policy_store.py，负责内置策略与 workspace override 的加载/缓存。
变更：PermissionService 只做 facade 和 decision composition；所有 TODO 的 条件评估、角色继承展开、inherits_from 真实化。
变更：权限决策结果统一输出 { allowed, matched_policies, deny_reason, source, evaluated_conditions } 结构，便于审计和前端展示。
验收：新增 unit/integration 测试覆盖 allow/deny precedence、sensitive file deny、role inheritance、conditional policy、workspace override 五类场景；任何工具调用审计都能落回 matched policy。
Phase 8 — 前端 canonical contract 收敛

范围：让前端只消费一种运行态合同，删除 useRuntime 和 statusService 里的多形态猜测逻辑。
文件： api.ts, services/api.ts, useRuntime.ts, SettingsModal.tsx。
新增：runtimeProjection.ts，定义 canonical RuntimeProjectionPayload, RoleLocalStatusPayload, WorkflowStatusPayload, TaskRowPayload。
新增：runtimeProjectionCompat.ts，作为唯一临时适配器，把旧响应形态映射成 canonical DTO；迁移完成后删除。
变更：statusService.getDirector() 不再做双格式 normalize；/v2/director/status 的 local-only schema 固定后直接返回 typed payload。
变更：useRuntime.ts 拆成 useRuntimeSocket, useRuntimeProjectionState, runtimeProjectionSelectors 三段；移除 PM/Director/QA token 猜测分支。
变更：SettingsModal.tsx 拆为 GeneralSettingsTab, LLMSettingsBridge, WorkflowSettingsTab, SystemServicesTabHost；不再保留 2000+ 行巨型单文件。
验收：frontend vitest 新增 projection reducer/selectors 测试； Electron 面板和 full-chain audit 只使用 canonical runtime contract。
Phase 9 — 大文件拆分与目录稳定化

范围：在主链稳定后，拆解当前维护成本最高的超大文件，但不改变公开行为。
后端优先文件：polaris_engine.py, orchestration_engine.py, worker_executor.py。
前端优先文件：SettingsModal.tsx, useRuntime.ts, DirectorWorkspace.tsx。
规则：先建 facade，再抽 coordinator / serializer / policy / adapter；每次拆分都必须配 guard tests，禁止“先搬文件后补测试”。
验收：任何单文件不超过约 1000 行；拆分后 import 方向符合目标架构，不再新增跨层耦合。
Public APIs / Interfaces / Types

保留并正式化：POST /v2/pm/start, POST /v2/pm/run_once, GET /v2/pm/status, POST /v2/pm/stop, POST /v2/pm/run, GET /v2/director/status, GET /state/snapshot, GET /v2/ws/runtime, POST/GET /v2/factory/runs*, POST/GET /v2/roles/sessions*, GET /v2/permissions/*。
新增：POST /v2/roles/sessions/{id}/actions/export-to-workflow。
新增：RuntimeProjectionPayload 前后端共享合同；FactoryRun 持久化格式固定；PermissionDecisionPayload 固定。
退役 Phase 2A：旧 /pm/*, /director/*, 旧 /v2/role/{role}/chat* 仅桥接并带弃用头。
退役 Phase 2B：以上旧接口全部 410 Gone。
Test Matrix

单元测试：bootstrap/config merge、runtime projection source priority、permission decision、role session artifact/audit/export、factory state transitions。
合同测试：/v2/pm/*, /v2/director/*, /state/snapshot, /v2/ws/runtime, /v2/roles/sessions/*, legacy bridge headers, tombstone 410 payload。
集成测试：Factory run persistence/recovery、RoleSession export-to-workflow、PermissionService with workspace overrides、runtime projection feeding system snapshot。
前端测试：statusService canonical parsing、useRuntime reducers/selectors、PMWorkbenchPanel / DirectorWorkbenchPanel export flow。
E2E：npm run test:e2e -- --list 中的主链用例全跑；重点保 app.spec.ts, realtime-visibility.spec.ts, pm-director-real-flow.spec.ts, full-chain-audit.spec.ts。
守护脚本：check_architecture_drift.py、test_architecture_guard.py、grep guards for sys.path.insert, duplicate merge, deprecated orchestrator import。
Suggested Agent Split

包 A：Phase 0 + Phase 3，负责人需先把配置系统和导入系统门禁打绿。
包 B：Phase 1 + Phase 2，负责人处理 runtime projection、API 装配、legacy bridge/tombstone。
包 C：Phase 4 + Phase 5，负责人处理 orchestration command 收口和 Factory 正式服务化。
包 D：Phase 6 + Phase 7，负责人处理 RoleSession 完整化和 PermissionService 完整化。
包 E：Phase 8 + Phase 9，负责人处理前端 canonical contract 和大文件拆分。
执行顺序：A 先于 B；B 与 C 可并行；D 依赖 B/C 的接口稳定；E 最后进入。
Phase Exit Criteria

Phase 0 退出条件：当前已知 failing pytest 全绿，W0 基线文档重生成。
Phase 1 退出条件：build_runtime_projection() 成为唯一正式状态聚合入口。
Phase 2 退出条件：正式路由和兼容桥接分离，前端无旧入口调用。
Phase 3 退出条件：生产目录 sys.path.insert/append 清零到白名单。
Phase 4 退出条件：无 direct business spawn outside orchestration command path。
Phase 5 退出条件：Factory 可恢复、可审计、可持久化。
Phase 6 退出条件：RoleSession artifacts/audit/export-to-workflow 闭环可用。
Phase 7 退出条件：PermissionService 所有 TODO 清零并有测试覆盖。
Phase 8/9 退出条件：前端 canonical contract 落地、关键超大文件拆分完成、整链回归通过。
Assumptions and Defaults

默认采用“两阶段退役”，不做一次性硬切。
默认把功能缺口与架构收敛一起完成，不留“骨架收敛但能力空洞”的状态。
默认按可回滚阶段交付，每阶段允许单独合并和回退。
默认不修改任何目标项目代码，只改 Polaris 主仓。
默认所有新增 JSON/Markdown/日志/配置文件都显式用 UTF-8 读写。
当前工作树中 runtime_projection 相关未提交改动视为本计划 Phase 1 种子实现，实施者必须先审阅并正式化，不能盲目覆盖或回退。