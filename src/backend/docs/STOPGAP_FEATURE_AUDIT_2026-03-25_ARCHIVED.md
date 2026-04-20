# ⚠️ ARCHIVED DOCUMENT - STOPGAP Feature Audit 2026-03-25

**状态**: 已归档 (ARCHIVED) - 2026-04-09
**原因**: 本文档已被 `MIGRATION_DEBT_INVENTORY_20260409.md` 和 `FULL_CONVERGENCE_AUDIT_20260405.md` 取代
**注意**: 本文列出的债务项部分已根据 FULL_CONVERGENCE_AUDIT_20260405 清理完成

---

## 原始文档内容（仅供参考）



## 结论

当前仓库里存在一批明确的“止血版 / 兼容桥 / 冻结旧面 / 占位实现”能力。它们不是普通的容错 fallback，而是会持续拖慢收口、诱导新代码继续挂靠旧路径的架构债务面。

这份清单只纳入高置信度、可举证的对象。判定标准是至少满足以下之一：

- 代码或文档显式声明 `compatibility shim` / `deprecated` / `legacy` / `placeholder` / `frozen`
- graph `verification.gaps` 直接标出兼容/遗留路径尚未收口
- 运行时存在双协议、双入口、双状态路径并存

不纳入本清单的对象：

- 纯平台兼容 fallback（例如 Windows/Unix 进程终止回退）
- 第三方库缺失时的纯韧性 fallback
- 普通错误处理分支

## 优先级原则

- `P0`: 横跨多个 Cell，持续制造错误收敛方向，应该单独立项治理
- `P1`: 已经有 canonical 目标，但外部仍在走旧入口，应该尽快收口
- `P2`: 明确是兼容别名/薄 shim，短期不致命，但会持续污染 import surface

## 审计清单

### P0-1. Workflow Runtime 混合控制路径

- 当前止血形态:
  - `workflow_runtime` 仍同时暴露 embedded 路径、legacy control path、compat wrapper。
  - 这不是单点 shim，而是一个还没完成 Phase 3 拆分的整片兼容带。
- 证据:
  - `docs/graph/catalog/cells.yaml`: `orchestration.workflow_runtime` gaps 明确写着 `mixed embedded and legacy control paths`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_backend_adapter.py`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/runtime/embedded/store_sqlite.py`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/workflows/generic_pipeline_workflow.py`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/pm_activities.py`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/director_activities.py`
  - `polaris/cells/orchestration/workflow_orchestration/public/contracts.py`
- 典型表现:
  - `runtime_backend_adapter.py` 明写“保留兼容层同步 API”
  - `store_sqlite.py` 是显式 compatibility shim
  - `generic_pipeline_workflow.py` 里仍有 `PMWorkflow/DirectorWorkflow/QAWorkflow` 兼容包装器，并返回简化结果
  - workflow activity 仍引用 `legacy orchestrator` / `legacy Director adapter`
- canonical 目标:
  - 彻底完成 `workflow_engine / workflow_activity / workflow_orchestration` 拆分
  - 让 `workflow_runtime` 退成薄 facade 或直接退出主实现链
- 为什么必须单独立项:
  - 这是整条 PM/Director/QA 编排链的收口核心，不处理会持续诱发“新功能挂在兼容层上”

### P0-2. `infrastructure.compat.io_utils` 兼容大杂烩

- 当前止血形态:
  - `io_utils` 保留旧 import surface，成为跨多个 Cell 的兼容工具兜底层。
- 证据:
  - `polaris/infrastructure/compat/io_utils.py`
  - `polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py`
  - `polaris/cells/orchestration/pm_planning/pipeline.py`
  - `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
  - `polaris/cells/orchestration/pm_dispatch/internal/iteration_state.py`
  - `polaris/cells/roles/runtime/internal/worker_pool.py`
  - `polaris/cells/orchestration/workflow_runtime/internal/runtime_engine/activities/qa_activities.py`
- 典型表现:
  - 模块头部直接声明这是 `Compatibility facade for non-KernelOne callers`
  - 旧调用方继续通过一个“宽命名空间工具包”拿事件、JSONL、stop flag、路径等能力
- canonical 目标:
  - 把能力拆回各自归属的 KernelOne contract 或 Cell-local port
  - 删除对 `compat.io_utils` 的新增依赖
- 为什么必须单独立项:
  - 这是典型的兼容负担集散地，不先拆掉，其他收口都会反复回流

### P0-3. 冻结的 Role Runtime Host Surface

- 当前止血形态:
  - `standalone_runner` 和 `tui_console` 仍存在，但已被标记为 frozen/backward-compat only。
- 证据:
  - `polaris/cells/roles/runtime/internal/standalone_runner.py`
  - `polaris/cells/roles/runtime/internal/tui_console.py`
  - `polaris/cells/roles/runtime/__guard__.py`
  - `docs/graph/catalog/cells.yaml`: `director.execution` gaps
- 典型表现:
  - `standalone_runner.py` 顶部直接写明 `DEPRECATED`、`FROZEN`
  - `tui_console.py` 顶部直接写明 `Legacy role-specific Textual test window`
  - `__guard__.py` 里专门维护 frozen importer allowlist
- canonical 目标:
  - 所有 host 只通过 `RoleRuntimeService` + canonical delivery CLI/API
  - 冻结模块最终只剩删除路径，不再承接任何功能演进
- 为什么必须单独立项:
  - 这类“冻结但仍在线”模块最容易继续被偷接新需求

### P1-1. Agent V1 HTTP Surface 仍是兼容包装器

- 当前止血形态:
  - `polaris/delivery/http/routers/agent.py` 现在已经改成 canonical `roles.session + RoleRuntimeService`，但对外仍保留 V1 agent 专属形状。
- 证据:
  - `polaris/delivery/http/routers/agent.py`
  - `polaris/delivery/http/routers/test_agent_router_canonical.py`
- 典型表现:
  - 文件头明确说明这是 `canonical session/runtime wrapper`
  - `/agent/turn` 仍保留 agent 专属 payload
  - `stream=true` 时仍返回 `stream_url`，而不是直接收敛为统一 roles session stream 协议
- canonical 目标:
  - 把它降成纯 transport adapter，或直接由 `/v2/roles/sessions/*` 取代
- 为什么应该单独完善:
  - 这层已经不再持有私有 session，但协议面还是 V1 兼容面，继续留着会让前端和外部集成长期依赖旧 contract

### P1-2. Runtime WebSocket 双协议并存

- 当前止血形态:
  - v2 websocket 已存在，但 endpoint 仍显式承接 legacy v1 fanout 和 legacy protocol handling。
- 证据:
  - `polaris/delivery/ws/runtime_endpoint.py`
- 典型表现:
  - 文件头明确写 `RuntimeEventFanout ... (legacy v1)`
  - 中段仍有 `Legacy v1 Protocol Handling`
  - 还保留 v2 active 后 ACK 无 protocol 的兼容逻辑
- canonical 目标:
  - 单一 `runtime.v2` 协议
  - legacy v1 仅留边缘适配或明确删除
- 为什么应该单独完善:
  - 协议双轨会持续污染前后端事件语义和订阅行为

### P1-3. PM HTTP v2 兼容端点族

- 当前止血形态:
  - `/v2/pm` 下仍混有 deprecated endpoint 和 Phase 6 兼容编排入口。
- 证据:
  - `polaris/delivery/http/v2/pm.py`
  - `docs/graph/catalog/cells.yaml`: `orchestration.pm_planning` gaps
- 典型表现:
  - `/start_loop` 已被显式标记 deprecated
  - 文件头和中段明确写 `统一编排兼容端点`
- canonical 目标:
  - 单一 orchestration command/query surface
  - 清理旧 PM loop 启动别名和兼容路径
- 为什么应该单独完善:
  - PM 是上游入口，兼容端点不收口，所有编排层都会继续同时适配两套语义

### P1-4. Director HTTP v2 兼容端点族

- 当前止血形态:
  - `/v2/director` 仍保留 Phase 6 兼容入口和测试导向 re-export。
- 证据:
  - `polaris/delivery/http/v2/director.py`
  - `docs/graph/catalog/cells.yaml`: `director.execution` gaps
- 典型表现:
  - 文件头明确写 `统一编排兼容端点`
  - 仍保留 `_merge_director_status = merge_director_status` 的兼容 re-export
- canonical 目标:
  - Director HTTP 面只暴露 canonical execution/query contract
  - 测试迁移到真实 public contract，而不是模块内兼容符号
- 为什么应该单独完善:
  - Director 已经是高频改动区，兼容路由会持续让真实 contract 难以固定

### P1-5. PM Planning 兼容桥和占位 State Port

- 当前止血形态:
  - `pm_planning` 里还存在 Noop state port placeholder 和旧任务结构迁移逻辑。
- 证据:
  - `polaris/cells/orchestration/pm_planning/internal/pipeline_ports.py`
  - `polaris/cells/orchestration/pm_planning/pipeline.py`
  - `docs/graph/catalog/cells.yaml`: `orchestration.pm_planning` gaps
- 典型表现:
  - `get_pm_state_port()` 直接返回 `NoopPmStatePort()`
  - `_migrate_tasks_in_place()` 明写 backward compatibility
  - 多处仍依赖 `compat.io_utils`
- canonical 目标:
  - 使用真实、单写的 PM state owner port
  - 删除从旧 CLI 任务形态镜像过来的迁移函数
- 为什么应该单独完善:
  - 这是 PM 任务合同与状态写路径的核心边界，placeholder 不应长期存在

### P1-6. PM Dispatch 遗留 Host Bridge

- 当前止血形态:
  - `pm_dispatch` 仍保留 host-layer 参数桥接、旧错误类型 re-export、兼容 I/O 调用。
- 证据:
  - `polaris/cells/orchestration/pm_dispatch/internal/error_classifier.py`
  - `polaris/cells/orchestration/pm_dispatch/internal/dispatch_pipeline.py`
  - `polaris/cells/orchestration/pm_dispatch/internal/iteration_state.py`
  - `docs/graph/catalog/cells.yaml`: `orchestration.pm_dispatch` gaps
- 典型表现:
  - `error_classifier.py` 文件头明确写 `Backward-compatibility shim`
  - `dispatch_pipeline.py` 仍接受 deprecated `args=` / `engine=`
  - `iteration_state.py` 仍通过 `compat.io_utils` 读写事件和 pause flag
- canonical 目标:
  - 只保留 typed callback / service port
  - 删除 host 参数桥和旧类型导入面
- 为什么应该单独完善:
  - 这一层还在向 dispatch pipeline 注入“宿主时代的调用习惯”

### P1-7. LLM Evaluation 的 Legacy Report Surface

- 当前止血形态:
  - readiness tests 仍以“前端兼容 legacy report 结构”为直接输出目标。
- 证据:
  - `polaris/cells/llm/evaluation/internal/readiness_tests.py`
- canonical 目标:
  - 基于 typed evaluation contract 输出，再由 delivery 层做必要映射
- 为什么应该单独完善:
  - 评估结果长期受 legacy report shape 约束，会影响上游 contract 演进

### P1-8. Role Dialogue 兼容响应形态和 fallback tool round

- 当前止血形态:
  - `role_dialogue` 里仍保留“向后兼容的响应格式”和 fallback tool round 分支。
- 证据:
  - `polaris/cells/llm/dialogue/internal/role_dialogue.py`
  - `docs/graph/catalog/cells.yaml`: `roles.kernel` / `roles.dialogue` 相关 gaps
- 典型表现:
  - 仍存在 `fallback tool round via orchestrator`
  - 响应构建处明确写 `保持向后兼容的格式`
- canonical 目标:
  - 由 role kernel/runtime 对外提供单一 turn/result contract
  - 把 legacy response mapping 留在边缘 delivery 层
- 为什么应该单独完善:
  - 对话层如果继续承载兼容 payload，会反复把历史 contract 污染带回内核

### P2-1. Roles Engine Sequential Compatibility Shim

- 当前止血形态:
  - 顺序执行策略保留为 compatibility shim，而不是纯正的 engine 实现。
- 证据:
  - `polaris/cells/roles/engine/internal/sequential_adapter.py`
  - `docs/graph/catalog/cells.yaml`: `roles.engine` gaps
- canonical 目标:
  - 让 engine selection 和 sequential strategy 只依赖 `roles.engine` 自己的 contract
- 为什么应该单独完善:
  - 现在已经止住了跨 Cell 依赖，但仍处于“保名不保实现”的兼容态

### P2-2. Roles Kernel 旧补丁协议 fallback

- 当前止血形态:
  - output parser 在 unified parser 失败时仍会回退到 legacy 正则提取。
- 证据:
  - `polaris/cells/roles/kernel/internal/output_parser.py`
- 典型表现:
  - 注释明确写 `回退到 legacy 正则`
  - 兼容无 `FILE/PATCH_FILE` 包装的旧输出
- canonical 目标:
  - 统一补丁协议，只接受 canonical patch/search-replace envelope
- 为什么应该单独完善:
  - 只要旧协议还能执行，模型和上层调用方就会持续产出非 canonical 输出

### P2-3. Domain 旧导入路径 re-export shim

- 当前止血形态:
  - domain 里仍保留旧 canonical location 的 tombstone/shim。
- 证据:
  - `polaris/domain/models/task.py`
  - `polaris/domain/services/token_service.py`
- canonical 目标:
  - 全量迁移 import 到 `domain.entities.task` 和 `infrastructure.llm.token_service`
- 为什么应该单独完善:
  - 这类 shim 不难，但非常容易长期留存，持续制造假 canonical surface

### P2-4. KernelOne 迁移别名和旧品牌环境变量 fallback

- 当前止血形态:
  - KernelOne 层仍保留 Polaris 旧品牌 env var fallback、legacy alias、compat gateway。
- 证据:
  - `polaris/kernelone/_runtime_config.py`
  - `polaris/kernelone/trace/context.py`
  - `polaris/kernelone/fs/control_flags.py`
  - `polaris/kernelone/audit/gateway.py`
- 典型表现:
  - `_runtime_config.py` 接受 `POLARIS_*` 作为 backward-compatible fallback
  - `control_flags.py` 里有 `stop_flag_path()/director_stop_flag_path()` 等 legacy alias
  - `audit/gateway.py` 明写 `Compatibility gateway`
- canonical 目标:
  - bootstrap 负责映射，KernelOne 内部逐步只保留 `KERNELONE_*` 和 kernel-native contract
- 为什么应该单独完善:
  - 这些 alias 看似小，但会长期把 Polaris 业务语义粘在 KernelOne 上

### P2-5. LLM Control Plane 的 Compatibility API

- 当前止血形态:
  - public contracts 里仍保留 direct `generate/stream` compatibility API。
- 证据:
  - `polaris/cells/llm/control_plane/public/contracts.py`
- canonical 目标:
  - 所有调用统一走 typed invoke/stream command contract
- 为什么应该单独完善:
  - 兼容 API 会让调用方绕开更稳定的命令模型

## 建议的专项拆分顺序

1. `workflow-runtime-convergence`
2. `compat-io-utils-removal`
3. `role-runtime-host-surface-retirement`
4. `delivery-protocol-unification`
5. `pm-dispatch-and-planning-canonicalization`
6. `llm-contract-surface-cleanup`
7. `domain-kernelone-alias-removal`

## 备注

- 本次不把“所有 fallback”都视为止血功能，只收显式的迁移/兼容/冻结债务。
- `session_continuity` 已经从 ad-hoc 规则提升为 `KernelOne Session Continuity Engine`，因此不列入本清单的“待识别止血版”范围；但它仍有后续智能化空间，不代表治理已经终态完成。
