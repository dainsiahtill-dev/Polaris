# Polaris 核心系统地图

本文件是当前工程主线的复用入口。所有跨 Cell 调用走公开边界，所有新能力先复用已有 Cell，再复用 KernelOne；内部文件仅用于定位 owner，不能当作跨 Cell import 示例。

## 工程运行与角色

- 唯一工程公开服务：`src/backend/polaris/cells/roles/runtime/public/engineering_service.py` 的 `EngineeringRunService`。
- 程序类型：`roles.runtime.public.contracts.engineering_run` 的 `RoleRunCommand`、`PermissionScope`、`RunLimits`、`ToolObservation`、`RoleRunResult`；这些不是模型业务合同。
- HTTP：`/v2/engineering/runs`；CLI 共用 `polaris.delivery.cli.engineering` 适配器；启动装配在 `polaris.bootstrap.engineering_runtime`，不拥有第二份状态。
- 默认 Director。显式 collaborative 才运行 PM、Chief Engineer、Director；按需咨询只读且一层返回。普通 discuss 无动作工具、不创建 run；未调用角色为 `not_called`。
- 实现职责：`engineering_loop` 运行 native 多轮；`engineering_tools` 组合安全工具；`engineering_roles` 角色提示/只读咨询；`engineering_verification` 执行真实当前候选检查；`engineering_session` 持久化与 workspace 锁。
- 唯一工程事实库：`<workspace>/.polaris/runtime/engineering/sessions.sqlite3`。不新增 Cell/controller/ledger/task 表，不把新运行镜像到旧 TaskBoard、Task Market、Run Ledger 或 RoleSession。

## 工具与模型技术底座

- `polaris.kernelone.tool_execution.tool_spec_registry`：工具定义、tool alias 和 arg_aliases 的统一归一化；归一化后仍须 host 权限、路径和命令检查，未知参数 fail-closed。
- `polaris.kernelone.llm.engine.client` / `provider_native_request`：现有 provider 调用、预算与 native 消息编解码；不得另造 provider registry 或静默 fallback。
- `polaris.kernelone.fs.runtime` / `storage.layout`：KFS、UTF-8 与 canonical `<workspace>/.polaris/runtime`。
- `polaris.kernelone.process.process_tree` 与现有 sandbox：真实命令隔离、取消、drain；仅 cwd/进程组不能替代 OS containment，不支持时明确拒绝。
- host `PermissionScope` 保留显式空 grant；PM/CE/QA 默认只读，同 workspace 单一源码写入者。模型文本、旧 JobToken/DEO/合同 hash 不能扩大授权。

## 验证、恢复与观测

- 实际检查绑定当前源码；用户已声明检查必须保留，未声明时 Director 从真实工程选择并实际执行。必要独立 QA 审查同一候选；回复 completed 不能替代 accepted。
- 原目标、相关代码、实际建议、近期工具观察与失败结果进入上下文。最终 provider request、token/window、工具 schema 与可读 ContextOS ref 由平台记录，不要求模型填写覆盖 flags/hash。
- runtime.v2/Nats-JetStream 与 `/v2/ws/runtime` 是唯一实时链路；HTTP 仅初始/重连快照、显式刷新、一次 command/query。UI 不得轮询或在断线时伪装实时。
- Launcher/Instance Registry 管实例发现与运维；每实例单 workspace，显式 backend/token/workspace，非主实例不占 `49977/5173`。
- cancel/resume 保留身份、原权限/deadline 和当前 policy 校验；保留 failed/interrupted/UNKNOWN，不自动重放未知命令/写入，不把旧 ID 转为 engineering run。

## 历史实现与可选工具

旧 `RoleRuntimeService`、`RoleExecutionKernel`、`KernelToolExecutor`、Factory 合同编排、TaskBoard/Task Market、RoleSession/Run Ledger 记录只用于原引擎只读解码/审计；旧在途运行留在原冻结 owner。它们不是新工程入口的复用建议，也不表示所有历史源码已删除。

独立 deterministic repair 实用工具仅显式使用，owner 保持 `director.runtime` public；其 coverage、policy、receipt/revalidation 不成为普通修复前置。禁止把 CE portfolio/semantic patch、JobToken/DEO/旧 ledger gate 或自动 repair schedule 接回新循环。

历史 `llm.dialogue.role_dialogue`、`role_integrations` 与 TaskBoard 不再是新工程的唯一实现；禁止照旧例直接 import 其它 Cell internal 或再创建平行工具/任务/角色控制器。

新增能力前先核对本地图、当前 `roles.runtime/cell.yaml`、graph 与真实 public consumers；缺失生成 pack 保持缺失事实，不能冒充新鲜资产或执行证据。真实 HTTP 次数与费用没有物理证据时保持 UNKNOWN。
