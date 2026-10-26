# TaskRuntime claim evidence 的单一解释边界

状态：事实与设计已收敛，具体实施桶待当前 SourceLocator 消费者收尾后确认。不是新工程循环的准入协议，不给模型增加字段要求。

用户可观察问题：源码重构后，部分边界检查只读失效路径；恢复真实调用后发现 Director adapter 与 Factory materialization 对同一执行任务各自读取底层事件、折叠 latest task_row_snapshot、判断 active/status/session，并据此准许 provider-less 完成。严格完整性与只读并不能消除第二处状态解释。

首偏差：`roles/adapters/internal/director/execute_method/_claim.py` 的 capture_claims 之后逐行折叠 latest；`factory/pipeline/internal/factory_materialization_impl.py` 存在相邻同类读取。旧 SourceLocator 失败曾掩盖它们。相关原失败保留在 canonical-shadow-fix/consumer-source-closure；不得添加路径白名单放行。

事实 owner：现有 runtime.task_runtime，继续复用既有 FactStream、TaskBoard 持久读取和 `_validate_execution_attempt_locked`。现有 Authority.snapshot 是 process-local identity/closed；当前 public validation 已核对 workspace/session/attempt/role/worker/run/external identity、active/expiry，但未返回原 claimed event 的可耐久版本／来源。不得创建第二账本、controller、目录或恢复入口。

假设：一个同 owner 的只读 claim evidence 查询能够让两个消费者复用同一身份／状态／原 claim 解释；其余 CE 合同、artifact/verifier receipt 与当前输入校验仍由现有负责模块完成。具体最小结果字段须由真实调用需求决定，不能先生成大量 schema。

接口要求：输入复用真实 TaskRuntimeExecutionAttemptIdentityV1 和既有 workspace 绑定；状态有效性由 owner 判断。输出绑定原 claim 身份、原 immutable claim 元数据、真实持久版本／ref、当前 owner validation。不能让 caller 再拿全部事件自建 current task projection；也不能只返回一个无法定位的 allowed boolean。权限、期限不扩大，不写状态。

错误语义：缺少原 claim、元数据／身份不符、已过期／终态、完整性错误、效果未知与读取期间漂移须明确区分并保留。不能将 current row 存在冒充 original claim 证据，也不能由历史claim推断当前仍 active。未知不记成功。

最小实验：真实 public TaskRuntime 建立 claim；通过 owner query 读取后让两调用方验证原 receipt-bound 路径。反例至少包含旧 claim 丢失、身份串线、cold reload、更新 metadata／session／attempt、终态／取消／expiry，以及查询和后续决策之间的并发变化。受控输入与时钟可作为标注夹具；状态 owner、持久读取和相邻真实消费者不得替身。

推翻条件：如果新查询能把丢失原claim的 current row、旧session或并发替换后的身份当作有效，或合法未变化claim无法推进，则解释／接口不充分。无关task更新是否应失效须按既有物理版本机制实测，不能靠扩大重试／超时掩盖。

机制与集成：优先复用 public validation 和同 owner reader 的实际能力；必要的新只读 seam 留在现有 TR Cell。两个真实生产者同时迁移，旧 raw-query 路径消失；公开读取、冷恢复与生成的精确 Source/descriptor 投影一并闭合。新的 TR producer 与正在进行的 descriptor 生成批必须协调 Source freeze，不能把源码中途变化算同一包证据。

接受／停止：原失败边界得到真实最小反例、两消费路径通过既有 artifact/verifier/CE 不变量、非法状态拒绝且合法claim可继续、cold与必要并发反例通过、源码无跨owner raw task-state fold；独立审查通过即可停止此主题并集成。大SCC、HR配置分离和后续Native项目独立记录；本模块实验不证明完整交付。

## 实际边界补充（2026-10-26）

两个既有consumer目的不同：Director active_completion只接受当前active未expiry；Factory receipt_revalidation保留同身份active未expiry或completed的当前收据重验。completed不产生新执行／lease／settlement授权，目的由两host调用点代码固定。新查询没有改变既有类／reader签名；live owner同步可沿现有validation锁创建一个空session.lock，故是无任务／事实状态写，不能宣称任意情况下物理零写。historical read_only=True不被升级，缺owner/session不创建命名空间。owner／consumer矩阵最终按原始报告计，不能手写数量作事实。源码与新TR投影仍需独立绑定后验收。
