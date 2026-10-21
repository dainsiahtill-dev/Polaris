# Gemini Repository Playbook

本文件是 [AGENTS.md](AGENTS.md) 与 [CLAUDE.md](CLAUDE.md) 的镜像入口，不引入独立规则。后端任务先读 [后端规则](src/backend/AGENTS.md) 和 [架构标准](src/backend/docs/AGENT_ARCHITECTURE_STANDARD.md)。

## 工程主线（2026-10-21）

1. 新工程运行的唯一 owner 是现有 `roles.runtime.public.engineering_service.EngineeringRunService`。HTTP、CLI、UI、取消、恢复与错误路径共用该服务，不新增 Cell、任务表、ledger 或第二 controller。
2. 默认 `workflow=direct`，Director 读取目标、调用工具、根据真实结果继续工作；PM/Chief Engineer 按需只读咨询。显式 `workflow=collaborative` 才执行 PM、Chief Engineer、Director 协作；未调用角色显示 `not_called`。咨询一层返回，不递归派生总控；讨论不创建工程 run。
3. PM/CE 使用可读文本提供规划、设计或审阅。新运行不要求 PM 合同、CE portfolio/blueprint、handoff/hash、OBL/covers 图、semantic patch、Task Market、JobToken/DEO/Run Ledger 旧链准入，也不受旧一 turn 一 batch 规则约束。普通编译/测试失败回到同一 Director，不自动 deterministic repair 或重启规划。
4. 工具授权来自 host 持久化且不可由模型扩大的 `PermissionScope`、当前 profile、预算与 deadline；显式空权限保持为空。归一化后仍须检查真实路径/symlink/允许根，经 KFS 读写和 OS/process sandbox 执行命令；不支持 containment 时明确拒绝，禁止裸跑。PM/CE/QA 默认只读，同 workspace 仅一个源码写入者。
5. 唯一工程事实记录是 `<workspace>/.polaris/runtime/engineering/sessions.sqlite3`，保存运行、事件、真实工具观察和验证；新运行不写旧 TaskBoard/TaskMarket/Run Ledger/role-session 镜像。保留 runtime.v2/Nats-JetStream、ContextOS 最终 provider request、UTF-8、实例 workspace 绑定与 storage 单根。
6. 角色回复结束与 `completed` 不等于 `accepted`。实际检查必须绑定当前源码；用户已声明检查必须保留，未声明时由 Director 结合实际工程选择并实际执行验证。空检查、模型自报、快照条数或 QA 文本不得冒充通过；任务要求的独立 QA 必须审查同一当前候选，拒绝后继续同一任务。
7. 恢复使用原身份、workspace、原始权限与 deadline，并重新校验当前 host policy；保留失败、interrupted/UNKNOWN 和未知效果，不自动重放可能已执行的命令或写入。旧 ID 不升级为新 run；历史按 `engine_version` 只读，旧在途运行留在原冻结 owner，不在新进程恢复旧写入口。
8. 旧 Factory/RoleRuntimeService/execute_role_session/RoleExecutionKernel 写准入与自动 fallback 退役；历史解码器、只读查询与显式可选技术工具可保留，禁止据此声称历史源码已全部删除。旧规范仅解释旧记录，不能重新约束新运行。底层 hash 可用于证据/快照完整性，不能要求模型填写业务 hash。真实 HTTP 次数与费用仅用物理证据，未知保持 UNKNOWN。

所有源码直接在主仓 `main`，不创建开发分支/worktree；审计资料放 `~/.polaris/`。文本显式 UTF-8；shell 每段使用 RTK；源码/架构/调用影响先 CodeGraph；使用适用 superpowers 与压缩报告。保留 Cell public/internal 边界、KernelOne 技术复用、真实权限、单 runtime 根、最终请求审计、runtime.v2、实例隔离与证据先于结论。不得修改生成目标掩盖平台缺陷。
