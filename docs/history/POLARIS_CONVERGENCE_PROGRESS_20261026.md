# Polaris 全仓收敛进展（2026-10-26）

当前阶段：技术债务清理与接口收敛，尚未进入全仓验收或冻结。新 Bench 继续暂停。全部源码修改在主仓 main；没有创建新分支、修改原始目标项目或改写历史失败。

## 已有实现与限定验证

- 存储与工具权限：默认项目 `.polaris/runtime`，RAM disk 显式启用；SQLite 创建前检查 workspace／namespace。权限核心消费 host 注入的只读策略，RoleKernel 保留真实 Gateway 适配。独立冷环境406 PASS、1个既有平台 SKIP，另4项 host 反证通过；通用权限 HTTP 仍只提供 RBAC 判断。
- 显式空权限：RoleAgent 的 `[]` 不再被当作 `None` 注册额外工具。原2失败保留，修后相邻27 PASS、独立6 PASS。KernelOne 原有空集合语义没有改变；无效果 handler 不能证明真实工具执行。
- 底层边界：协议解析直接复用 KernelOne；浮点 deadline 经 public 边界传递，不扩大不足0.1秒的剩余租约。Root 相邻335 PASS。效果身份与不可变 payload 依原 owner 拆分，旧类型／序列化仍有独立限定证据。
- 预算与配置：BudgetService／LLMConfigService 提供确定性服务，CFO／HR RoleAgent 留在 roles.runtime，保留工具、消息及生命周期。CFO Root与独审各109 PASS；HR Root311 PASS、独审限定66 PASS，并核对179个原测试节点一一保留。三个旧低层 Agent 导出路径明确迁移，没有留下反向 lazy fallback。此次不是完整 host/provider 验收。
- 配置路径：原逻辑取整个绝对路径中第一次出现的子串，嵌套项目可误归主仓；改为最近的完整物理目录组件，正确处理 `.polaris/runtime`。10项新反例和相邻共212 PASS、128条既有 warning；真实私有 KFS 写入和冷读通过，旧 `workspace/runtime` 写入仍被拒绝。独审15 PASS，真实私有 KFS 冷恢复及有限反例通过。Windows 实机未验证。
- 测试真实性：RunLedger 的假 owner outcome 改为真实 policy／DEO／TaskRuntime／TurnLedger／committer 交接。联合175 PASS、Root复验108 PASS；受控 provider envelope、角色资料与只读 I/O 输入仍是夹具，不算正式 Native 证据。
- TaskRuntime：claim 证据归属回到已有任务 owner，Director completion 与 Factory receipt revalidation 使用不同固定用途。58项 owner／消费者与71项相邻通过；独审37 PASS及独立冷恢复／并发反证通过。终态 receipt 核对不授予新执行权；协作锁可能产生空 lock 文件，不能宣称物理零写入。
- 当前源码发现：退役3份普通导入不会选择的影子文件；SourceLocator 核对实际 package／MRO／定义，原测试定义及断言保留。`.source` 被纳入现有扫描范围，140 PASS与独立41个额外输入通过；只有 `_repository_class.source` 的执行 loader 已确认，另一份实际加载仍为 UNKNOWN。
- 生成资料：正式生成器已在当时冻结输入上更新 FinOps、LLM Control Plane、RoleRuntime 三份 descriptor，并核对真实 receipt、输入 hash 与其他资产不变。实际并集115份资产，其中114份生产 pack、1份 fixture；三份生产 descriptor 变化，其余111份生产 pack未变。随后配置 helper及精确测试声明修改后，FinOps／LLM pack已另起 round2 完成刷新；TR pack仍等待恢复修复冻结。静态生成不证明语义召回或项目交付。

补充：FactStream 全部公开测试及 trace 测试本次183 PASS，19份相关源码／测试前后 hash 不变；覆盖 payload 历史／精确查找／隔离、guarded append 和 workspace bootstrap。它不是新工程任务或 Native 验收。

## 当前正在关闭的真实缺陷

1. **输出丢尾**：真实空行与内部 EOF 同用空字符串。内部 EOF 改为 `None`，保留空行数据；独审旧4 FAIL／3 PASS、修后7 PASS，另5项真实双流实验通过。历史第一次物理丢失缺少原始管道证据，仍为 UNKNOWN。相邻111项中4项旧失败保留，不能说整个进程模块已通过。
2. **超时后进程存活**：真实 `subprocess.TimeoutExpired` 未被正确处理；逻辑 `TIMED_OUT` 又令 terminate／kill 短路。真实私有子进程已复现并回收；修复必须以 poll／wait 确认物理退出，不能用逻辑终态冒充资源释放。正在修改现有 KernelOne process／runtime 边界。
3. **任务旧身份恢复**：旧调用者可能重开被新会话领取的任务。正在现有 owner 锁与 CAS 内加入期望 binding 比较，保护合法恢复。另已复现 binder 从 raw row 读取 projection-only 字段导致重复追加恢复事实，正在同一 owner 修正严格事实读取；不以额外事件或重试掩盖问题。
4. **剩余架构边界**：Factory 对 CE 私有 authority issuer 的2处生产引用、跨 Cell 私有测试依赖、未声明依赖／效果、完整 SCC及占位产物质量语义仍未关闭。不能用公开改名、测试豁免或扩大 cycle allowlist 消掉问题。

5. **队列满后丢尾与缺 EOF**：Root真实私有子进程输出601行、exit0，500项队列满后pump提前退出；stream只收到500行并持续等待，尾部100行（含TAIL_OK）仍在管道。原进程已回收，相关AST前后稳定。仅强制一种合法调度，不证明历史频率；该机制尚未修复，不能调大容量掩盖。

## 全仓状态与停止条件

最新完整严格扫描快照 `Root-after-owner-metadata-correction-hard-fail.json`：756条事件，含276跨私有引用、412依赖声明、67效果声明、1循环事件；schema和测试权限声明错误均为0。此前765条含 Root 写错一条测试效果 glob所引出的声明及相关错误，已改为精确 token。765／759／762旧快照保留。事件并非独立根因，新增 owner／扫描覆盖也会改变数量，净变化不能解释成交付提升。

未完成：已知缺陷关闭、全仓必要静态与行为回归、冻结候选独立审查、新隔离原始 L1-01 零外部代修完整交付／资源闭合、其余119项串行评测与逐项人工质量验收。v28 L1-01 目录仍不存在。主 backend、NATS、frontend 原 PID／birth 保持，健康与 Launcher 均200；没有重启服务。

## 证据入口

私有根 `.polaris/audits/technical-debt-clearance-20261025/`：

- `Root-convergence-current-state.json`：可延续当前状态、文件 owner、开放问题。
- `storage-permission-review/fix-round1/`、`role-agent-empty-allowlist/`：存储／权限首失败与复测。
- `cfo-owner-split-fix/`、`hr-owner-split-fix/`、`Root-config-workspace-inference/`：拆分、旧节点保留、真实 KFS 与推断反例。
- `source-artifact-coverage/`、`canonical-shadow-fix/`、`tr-descriptor-producer/`：源码发现、影子删除与实际类表面。
- `run-ledger-test-boundary-audit/implementation/`、`task-runtime-claim-evidence-owner/`：真实 owner 交接和独审。
- `command-output-tail-audit/independent-review/report.json`、`process-strict-types-fix/`：EOF独审与超时物理反例。
- `factory-wq-bound-owner-fix/`：恢复竞争与重复 binding 首失败。
- `Root-owner-pack-refresh/`、`Root-owner-context-projections/`：生成前后资产、实际 receipt、工作上下文与 inventory 分类纠正。
- `Root-process-output-capacity/counterexample-v2.json`：601行实际进程输出与EOF反例。

各组测试存在重叠，不相加作全仓覆盖数。组件实验不替代实际 LLM 请求、模型费用、Native／Bench 结果。未知保持 UNKNOWN；长期记忆不在本次范围。
