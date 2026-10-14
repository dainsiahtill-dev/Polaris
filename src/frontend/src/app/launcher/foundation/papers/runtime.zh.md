# 实例、存储与 runtime.v2：让观察对应正确的项目

## 单进程单 workspace

Polaris 的一个后端实例绑定一个 workspace。Launcher 通过 Instance Registry 与 Supervisor 管理多个独立实例，每个实例有自己的 workspace、runtime root 与端口。Registry 是发现与运维视图，不是 TaskRuntime、效果收据或项目结果的替代拥有者。

实例入口需要保持 backend、token、workspace、instance 绑定。打开某个项目后静默改用默认后端，会使页面看起来在线而事实属于另一项目。本文原理页保留这些 URL 参数，内容本身是静态研究文档。

## 文件布局是解析契约

storage.layout 统一解析工作区路径。默认项目 runtime 根为 workspace/.polaris/runtime，显式外部缓存属于配置选择，并继续按 workspace 隔离。旧 namespace 可以用于只读查找，但不应在活跃执行中自动双写或切根。

Context 快照读取还存在按同 workspace 的候选根查找链。返回一个 hash 不代表快照一定可读；读取失败要能说明 workspace、hash 和已搜索位置。内容快照引用、请求 hash、任务 ID 和轮 ID 有不同语义。

## 命令与实时观察分工

HTTP 承担初始快照、显式刷新和一次性命令或查询。运行变化通过 Nats-JetStream 与 /v2/ws/runtime 统一传输，前端 RuntimeTransportProvider 和 runtimeSocketManager 管理连接、订阅与重连。

推送页面必须保留项目范围、序号和事实新鲜度。连接成功只证明传输可用，不证明模型调用、工具写入或任务验收已完成；重连也不能凭空产生新的运行事实。

## 消息恰好一次不等于工具效果恰好一次

JetStream 提供持久化与至少一次投递；去重和双重确认能在规定条件下改善消息交付保证。即使消息只被交付一次，消费者执行了外部命令后、确认前崩溃，仍可能产生效果与消息状态分离的窗口。Polaris 的 DEO、幂等键、当前 owner、CAS 和效果收据处理的是另一层义务，不能把 NATS 的交付保证当作任意外部副作用保证。

## 事件溯源的成本和边界

严格事件存储提供序号、完整性、幂等与预期版本检查。拥有者决定某条事件的工程语义，存储原语不替拥有者认定目标正确。可重放的数据也必须带有当前权限与输入身份，不能重放模型上下文就重新执行已完成的操作。

长时间会话需要审视每帧大小、总历史增长、重放与投影复杂度。当前真实失败样本中，回合结果日志总量达到 8 MiB 附近，追加端按整段历史应用限制，拒绝正常后继回合；该问题在研究快照中仍未修复。

## 核查入口

- src/frontend/src/app/launcher/LauncherWorkspace.tsx：实例查询与 status.instances 订阅。
- src/frontend/src/runtime/transport/RuntimeTransportProvider.tsx：统一前端传输。
- src/backend/polaris/delivery/ws/runtime_endpoint.py：runtime.v2 服务入口。
- src/backend/polaris/cells/storage/layout/public：工作区物理根解析。
- src/backend/polaris/cells/context/engine/public/snapshot_paths.py:24：快照候选根。
- src/backend/polaris/kernelone/events/sourcing/file_store.py:613：严格追加与持久化边界。
- https://docs.nats.io/concepts/jetstream：NATS 官方交付语义。

