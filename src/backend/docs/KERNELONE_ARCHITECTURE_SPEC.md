# KernelOne 架构规范 v2.3

**生效日期**: 2026-03-20  
**适用范围**: `` 下的所有 Polaris 后端代码  
**强制级别**: MUST  
**状态**: 迁移期目标架构，当前按过渡门禁执行

**变更记录**:
- v2.3 (2026-03-20): 明确 `KernelOne` 不是狭义技术工具箱，而是面向 AI/Agent 的类 Linux 运行时底座；补充 Agent-OS 风格子系统示例与下沉判定口径
- v2.2 (2026-03-16): 全文改为中文，保留 v2.1 的架构语义并统一术语表达
- v2.1 (2026-03-16): 明确 `kernelone/` 准入标准与 `delivery` 直连边界；定义出站端口归属与 `infrastructure` 映射规则；补充边界对象规则、测试放置规则、迁移执行顺序与过渡期验证说明
- v2.0 (2026-03-16): 以 `bootstrap/delivery/application/domain/kernelone/infrastructure` 六个规范根目录替换旧的 `api/app/framework/core/polaris_app/scripts` 分裂结构；定义迁移规则与旧目录退役策略
- v1.1 (2026-03-16): 增加六边形端口分类、异常边界、依赖注入规则、Application / Polaris 边界
- v1.0 (2026-03-15): 初始版本

---

## 1. 目标

本文档定义 Polaris 的目标后端架构。

它是一份架构政策文档，不是通用编码风格文档。它主要约束：

- 规范根目录的职责归属
- 依赖方向
- 边界对象的归属与映射
- 迁移行为
- 最低验证门禁

诸如显式 UTF-8 文本 I/O、类型约束、打包卫生等项目级工程规则，仍通过 `AGENTS.md` 及相关工程规范强制执行。本文只在这些规则会影响架构边界或可执行性时再次引用。

系统收敛到如下单一规范布局：

```text
bootstrap/        组装根、启动、环境绑定
delivery/         HTTP / WebSocket / CLI 传输适配层
application/      用例编排、事务边界、应用流程
domain/           业务规则、实体、值对象、领域端口
kernelone/        Agent/AI 类 Linux 运行时底座、六边形技术子系统
infrastructure/   存储、消息、遥测、插件等具体适配器
```

其他后端顶层根目录一律视为迁移中的旧根目录并处于退役过程中。即使兼容垫片（shim）尚未删除，新功能也必须进入规范根目录。

---

## 2. 规范根目录

### 2.1 根目录映射

| 根目录 | 角色 | 允许依赖 | 禁止依赖 |
|------|------|----------|----------|
| `bootstrap/` | 组装根与进程启动 | 所有规范根目录 | 旧根目录 |
| `delivery/` | HTTP、WebSocket、CLI 传输层 | `application/`；以及本规范允许的极窄 `domain/` 或 `kernelone/` 公共 API | `infrastructure/` 具体适配器、旧根目录 |
| `application/` | 用例与编排层 | `domain/`、`kernelone/` 公共 API、`application/` 内部包 | `delivery/`、具体基础设施细节 |
| `domain/` | 业务策略与领域模型 | `domain/` 内部包、`domain/ports/`、极少数获准的技术契约 | `delivery/`、`application/`、具体基础设施细节、传输/安全实现细节 |
| `kernelone/` | Agent/AI 运行时底座与纯技术能力 | `kernelone/` 内部 | `delivery/`、`application/`、`domain/`、旧根目录 |
| `infrastructure/` | 出站适配器实现 | `kernelone/` 端口/契约、显式声明于 `application/` 或 `domain/` 的出站端口、用于映射的稳定领域模型/值对象、第三方库 | `delivery/`、`application/` 的用例 / 工作流、`domain/` 的策略/服务 |

### 2.2 执行流向

```text
bootstrap
   -> delivery
      -> application
         -> domain
         -> kernelone
bootstrap
   -> infrastructure
      -> kernelone/application/domain ports
```

关键规则：

1. `bootstrap/` 是唯一允许组装对象图的根目录。
2. 默认请求路径是 `delivery -> application -> domain/kernelone`。
3. `delivery/` 必须保持薄且只关注传输语义。
4. `application/` 拥有编排、事务边界、重试与执行顺序控制。
5. `domain/` 拥有业务规则与领域语义。
6. `kernelone/` 是 Agent/AI 的操作系统底座，但绝不能再次膨胀为新的 `core/` 垃圾场。
7. `infrastructure/` 负责边界处的出站适配与映射，不能演化为另一层应用层。

### 2.3 归属判定顺序

当一段代码看起来可以放进多个根目录时，按以下顺序判定：

1. 如果它处理 HTTP、WebSocket、CLI、请求/会话协议或传输错误语义，它属于 `delivery/`。
2. 如果它在多个协作者之间编排一个用户动作或系统动作，它属于 `application/`。
3. 如果它表达业务不变量、业务概念或领域规则，它属于 `domain/`。
4. 如果它能作为 Agent/AI 通用运行时 / 基础设施 / OS 能力复用，并且不带 Polaris 业务语义，它应优先评估属于 `kernelone/`。
5. 如果它绑定具体后端、SDK、数据库、队列、文件系统、插件宿主或遥测后端，它属于 `infrastructure/`。
6. 如果它只是把进程、生命周期与具体实现装配在一起，它属于 `bootstrap/`。

---

## 3. 根目录职责

### 3.1 `bootstrap/`

```text
职责:
  - 进程启动与关闭
  - 环境归一化
  - 配置加载与校验
  - 依赖图装配
  - 生命周期管理
  - 选择具体适配器与运行时策略

禁止:
  - 请求处理
  - 业务规则
  - 长生命周期的领域编排逻辑
  - 传输结构定义归属
```

规范示例：
- `bootstrap/server.py`
- `bootstrap/runtime.py`
- `bootstrap/config/`

### 3.2 `delivery/`

```text
职责:
  - HTTP / WebSocket / CLI 协议处理
  - 请求解析与响应序列化
  - 认证守卫、会话协议、限流
  - 传输层校验
  - 错误翻译为传输响应

禁止:
  - 直接操作文件系统或数据库
  - 子进程编排
  - 事务归属
  - 直接构造具体适配器
  - 业务策略决策
```

规范示例：
- `delivery/http/routers/`
- `delivery/http/middleware/`
- `delivery/ws/`
- `delivery/cli/`

#### `delivery -> domain` 或 `delivery -> kernelone` 直连边界

默认规则：`delivery/` 应优先调用 `application/`，而不是绕过它。

允许的直接使用范围必须非常窄，仅限：

- 把请求原始值映射为领域值对象、标识符等轻量领域输入
- 不引发业务分支的、无副作用的轻量校验
- 将稳定的只读模型 / 投影格式化为传输输出
- 在完全不涉及业务编排时，调用 `kernelone/` 的公共 API 处理传输邻接事务，例如连接/会话辅助、流式扇出、协议无关序列化辅助能力

`delivery/` 中明确禁止：

- 调用多个协作者来拼装业务流程
- 决定业务授权或业务分支
- 在 `application/` 之外开启、绕过或直接执行写事务
- 直接调用 仓储、消息代理或其他出站适配器
- 拥有重试策略、幂等策略、补偿逻辑或最终一致性编排

传输层认证属于 `delivery/`。业务授权属于 `application/` 或 `domain/`，取决于规则真正归属的位置。

### 3.3 `application/`

```text
职责:
  - 用例编排
  - 事务与一致性边界
  - 协调领域服务与内核能力
  - 重试、幂等编排、流程顺序控制等应用级策略
  - 拥有应用层命令 / 查询 / 结果对象，以及用例端口

禁止:
  - HTTP 专属类型
  - 直接访问具体基础设施实现
  - 隐式全局状态
  - 本应属于 `domain/` 的业务规则
```

规范示例：
- `application/usecases/`
- `application/services/`
- `application/workflows/`
- `application/ports/`
- `application/queries/`

#### 事务与工作流归属

`application/` 是业务执行边界。

- 它决定事务何时开始、提交、回滚，以及何时需要补偿。
- 它决定外部副作用在什么时点可以对外可见。
- 后台任务、CLI 命令、WebSocket 处理器、HTTP 路由进入系统后，仍应通过 `application/` 用例或 `application/` 工作流落地。
- 只要涉及多个协作者之间的业务顺序、重试或幂等，默认归 `application/`；只有纯技术运行时语义才归 `kernelone/`。

### 3.4 `domain/`

```text
职责:
  - 业务实体与值对象
  - 业务不变量与策略
  - 领域服务与领域校验规则
  - 为保持领域纯度所需的业务自有出站端口

禁止:
  - Web 框架类型
  - CLI 关注点
  - 持久化驱动细节
  - 基于环境变量的隐式行为
  - 传输、会话、令牌或框架安全实现细节
```

规范示例：
- `domain/entities/`
- `domain/value_objects/`
- `domain/services/`
- `domain/policies/`
- `domain/ports/`

#### `domain/` 的技术依赖策略

默认规则：如果领域层需要外部能力，优先定义一个狭窄、业务自有的 `domain/ports/` 接口。

`domain/` 只有在以下条件同时成立时，才可以依赖共享技术契约：

1. 该契约与业务无关，且本身是副作用中立的
2. 该依赖是为了表达领域规则，而不是为了直接触达基础设施
3. 若改成领域自有 port 只会制造重复，而不会提升隔离性

允许示例：

- 时间提供者 / 时钟契约
- ID 生成器契约
- 不暴露传输或消息代理语义的领域事件收集 / 发布契约

禁止示例：

- 文件系统抽象
- 插件系统
- 工作流引擎与调度器
- 传输 / 会话 / 认证类型
- serializer 实现
- 缓存后端或功能开关 SDK

### 3.5 `kernelone/`

```text
职责:
  - 面向 AI/Agent 的类 Linux 运行时底座
  - 技术运行时能力
  - 平台无关契约与端口
  - 六边形结构下的内核服务
  - 零 Polaris 业务逻辑的技术编排

禁止:
  - 从 `delivery/`、`application/`、`domain/` 导入
  - 从旧根目录导入
  - 直接理解 Polaris 业务策略
  - 直接使用兼容垫片
```

#### `kernelone/` 的准入标准

一个能力只有在以下条件全部满足时，才允许进入 `kernelone/`：

1. 它不包含 Polaris 业务词汇或具体用例语义
2. 把它抽离成独立技术包后，它仍然是有意义的
3. 它能被多个上层场景复用，或它本身就是基础运行时子系统
4. 它可以在不导入 `delivery/`、`application/`、`domain/` 或旧根目录的情况下被独立测试
5. 它暴露的是稳定技术契约，而不是为了方便上层去拿具体基础设施实现

只要有任一条件不满足，它就不属于 `kernelone/`。

务实说明：仅仅“技术上共享”并不足以成为进入 `kernelone/` 的理由。更准确地说，只有当它在剥离 Polaris 业务语义后，仍能作为 Agent/AI 的 OS 级能力成立，它才配进入 `kernelone/`；否则就应该跟随真正的功能归属，通常放在 `application/` 或 `domain/`。

#### `kernelone/` 的强化原则

`KernelOne` 必须强，但只能强在技术底座。

它不是普通工具层，而是面向 AI/Agent 的运行时操作系统底座。凡是对上层 Cell / role / workflow 来说更像“操作系统服务”而不是“业务用例”的能力，应优先评估下沉到 `kernelone/`。

它不应只是几个空目录或薄包装，而应主动承接跨多个 Cell 的纯技术能力，例如：

- `runtime`
- `fs`
- `storage`
- `db`
- `effect`
- `trace`
- `stream`
- `events`
- `message_bus`
- `ws`
- `locks`
- `scheduler`
- `auth_context`
- `agent_runtime`
- `context_runtime`
- `context_compaction`
- `task_graph`
- `tool_runtime`
- `llm`
- `process`
- `telemetry`
- `technical contracts`

如果同一类纯技术逻辑在多个 Cell / workflow / adapter 中重复出现，并且脱离 Polaris 业务语义后仍成立，应优先上收进 `kernelone/`。

#### `kernelone/` 不得吸收的伪技术对象

以下对象即使被多个地方复用，也不得因为“共享”而进入 `kernelone/`：

- archive run / task snapshot / factory archive 的业务 command、result、event
- PM iteration finalize 之类业务 workflow contract
- migration status、runtime snapshot 等 Polaris 应用投影结果
- workspace guard / permission 规则
- 仍然依赖 Polaris 逻辑子树命名的业务 layout 定义

这些对象应放入：

- `application/contracts/`
- `application/queries/`
- `cells/*/public/contracts/`
- 或稳定的 `domain/` 业务对象

#### 当前迁移期的纠偏对象

当前仓内需要优先纠偏的对象包括：

1. `kernelone/contracts/archive.py`
   - 当前混入了 Polaris archive / finalize / migration 语义
   - 这只能作为迁移期兼容落点，不能成为长期归属
2. `kernelone/runtime/storage_layout.py`
   - 当前同时承担技术路径原语和 Polaris 逻辑布局语义
   - 后续应拆成：
     - `kernelone/` 持有技术 path / root / namespace 原语
     - `storage.layout` Cell 持有 Polaris 逻辑布局 contract
3. `kernelone/agent_runtime/`
   - 必须成长为真正的技术运行时子系统
   - 但不得吸收 `roles.session_runtime` 的业务会话语义

#### 推荐的强内核结构

```text
kernelone/
  runtime/
  fs/
  db/
  effect/
  trace/
  locks/
  scheduler/
  auth_context/
  ws/
  stream/
  events/
  tool_runtime/
  llm/
  process/
  telemetry/
  agent_runtime/
  contracts/
    technical/
```

### 3.6 `infrastructure/`

```text
职责:
  - 出站端口的具体实现
  - 持久化、消息、遥测、插件、存储集成
  - 外部记录 / 消息与内部模型之间的映射
  - 适配器生命周期辅助逻辑

禁止:
  - HTTP / CLI 传输行为
  - 用例编排
  - 领域决策逻辑
  - 变成未分类代码的堆放层
```

#### Port 实现与映射规则

`infrastructure/` 可以实现定义在 `kernelone/`、`application/ports/` 或 `domain/ports/` 中的 port。

Port 归属规则：

- `domain/ports/` 拥有从领域视角出发的聚合持久化接口或业务必需能力接口。
- `application/ports/` 拥有用例专属查询、跨聚合读取、外部工作流协作者、应用层网关等接口。
- `kernelone/contracts/technical/**` 拥有可复用的技术运行时契约。

补充规则：

- 当持久化或消息映射需要时，`infrastructure/` 可以依赖稳定的领域模型或值对象。
- ORM 记录、消息包封、SDK DTO 与内部模型之间的转换，必须放在 `infrastructure/` 的适配器边界附近完成。
- `infrastructure/` 不能导入 application 用例、application 工作流或 domain service 来做业务决策。

---

## 4. 边界对象与映射

比目录结构更容易长期腐化的，是边界对象归属不清。对象归属必须明确。

### 4.1 对象归属

| 对象类型 | 归属 | 不能演化成 |
|----------|------|------------|
| HTTP / WebSocket / CLI 请求与响应结构 | `delivery/` | 领域模型或持久化模型 |
| 应用层命令 / 查询 / 结果 / 只读模型 | `application/` | 传输契约或 ORM 记录 |
| Domain entity / value object / policy / domain event | `domain/` | 默认情况下的原始传输 payload |
| 技术运行时契约 / 技术模型 | `kernelone/` | Polaris 业务 DTO |
| ORM 记录、SDK DTO、消息包封、插件载荷包装对象 | `infrastructure/` | 应用层命令 / 结果对象或领域实体 |

### 4.2 映射规则

1. `delivery/` 负责把传输载荷映射为 application 输入，不能把传输结构直接提升为领域模型。
2. `application/` 负责在用例输入/输出与领域概念、技术契约之间做映射。
3. `domain/` 只拥有领域语义校验，不拥有 HTTP 或持久化序列化职责。
4. `infrastructure/` 负责在适配器边界把外部格式映射为内部模型，反之亦然。
5. 当多个 delivery 机制需要复用同一只读数据形状时，应先定义 application 只读模型 / 投影，再映射为各自的传输结构。

### 4.3 默认暴露规则

领域实体与值对象默认都不是传输契约。如果需要稳定的外部形状，应显式定义 delivery 结构或 application 只读模型。禁止把持久化记录向上泄漏，也禁止把传输 DTO 向下泄漏。

---

## 5. 旧根目录退役策略

以下根目录一律视为旧根目录：

```text
api/
app/
core/
framework/
polaris_app/
scripts/
```

### 5.1 旧根目录规则

1. 旧根目录下不得新增功能代码。
2. 旧根目录中只允许存在：
   - 迁移适配器
   - 保持导入兼容的垫片
   - 弃用标记
   - 临时转发入口
3. 旧文件不得继续担当新行为的主实现。
4. 每个兼容垫片都必须有删除责任人与退役计划。
5. `kernelone/` 绝不允许导入旧根目录。
6. 在调用方尚未迁完前，被触达的旧模块仍必须保持可直接导入，但它们必须保持足够薄。

### 5.2 迁移到规范根目录的映射

| 旧路径 | 规范目标 |
|--------|----------|
| `api/**` | `delivery/http/**` 或 `delivery/ws/**` |
| `scripts/**` | `delivery/cli/**` |
| `framework/**` | `bootstrap/**` 或 `delivery/**` |
| `app/routers/**` | `delivery/http/routers/**` |
| `app/usecases/**` | `application/usecases/**` |
| `app/services/**` | `application/services/**`、`application/workflows/**` 或 `domain/services/**` |
| `polaris_app/**` | `application/**` 或 `domain/**` |
| `core/**` | 按归属迁往 `kernelone/**`、`bootstrap/**` 或 `application/**` |

### 5.3 迁移策略

迁移必须采用收敛式迁移，禁止复制式迁移。

允许的迁移顺序：

1. 在目标规范根目录建立主实现。
2. 将调用方改为指向规范实现。
3. 需要兼容时，把旧实现降级为薄垫片。
4. 当调用方全部迁完后删除旧实现。

禁止的迁移模式：

1. 复制一份逻辑到新根目录。
2. 让旧实现与新实现长期并存。
3. 两边都持续打补丁。

---

## 6. KernelOne 子系统结构

### 6.1 默认结构

中小型 KernelOne 子系统通常应从以下结构起步：

```text
kernelone/<subsystem>/
├── __init__.py
├── ports.py
├── service.py
├── contracts.py        # 可选
├── models.py           # 可选
└── adapters/
    ├── __init__.py
    └── <adapter>.py
```

### 6.2 大型子系统的扩展结构

当子系统规模增长到单文件会造成热点文件或虚假耦合时，可以扩展为：

```text
kernelone/<subsystem>/
├── __init__.py
├── ports/
├── services/
├── models/
├── contracts/
├── adapters/
└── internal/
```

扩展结构规则：

1. 这只是结构扩展，不是语义逃逸口。
2. 对外公共 API 仍需通过 `__init__.py` 进行收口。
3. `internal/` 只允许该子系统内部使用，外部禁止导入。
4. `adapters/` 仍然只负责实现声明过的 port；service 仍然禁止实例化 adapter。
5. 只要能提升隔离性、可审查性与可测试性，就鼓励拆文件，而不是强撑单一 `service.py`。

### 6.3 Port 分类

| 分类 | 含义 | 位置 |
|------|------|------|
| 主端口 / driving port | 向上暴露的公共服务方法 | `service.py` 或 `services/` |
| 从端口 / driven port | 向外声明、由外部实现的抽象依赖 | `ports.py` 或 `ports/` |

---

## 7. 组装根与依赖注入

### 7.1 组装根

`bootstrap/` 是唯一允许的组装根。

典型行为包括：

- 构建 FastAPI 应用
- 装配存储适配器
- 装配运行时服务
- 把配置绑定到具体实现
- 启动与关闭长生命周期运行时资源

### 7.2 必须采用的依赖注入模式

```python
from __future__ import annotations

class ArtifactService:
    def __init__(self, store: ArtifactStorePort, telemetry: TelemetryPort) -> None:
        self._store = store
        self._telemetry = telemetry
```

禁止写法：

```python
class ArtifactService:
    def __init__(self) -> None:
        self._store = SqlArtifactStore()
```

### 7.3 状态与配置规则

默认禁止：

- 可变模块级单例
- `sys.path` patching
- 在 `bootstrap/` 之外修改 `os.environ`
- 在未经批准的迁移缝隙外使用 服务定位器式具体实现解析
- 在应用代码中使用 `atexit` 清理

允许例外，但必须显式收束：

- 不可变注册表
- 测试专用 fixture
- 位于 `bootstrap/` 下的短生命周期启动胶水
- 带退役计划的已记录迁移垫片

配置加载与环境变量修改必须发生在 `bootstrap/config/`。上层只消费强类型配置对象或 port，而不是零散的环境变量读取。

---

## 8. 执行、事务与异步边界

1. `delivery/` 可以建立请求、会话或命令上下文，但业务事务边界属于 `application/`。
2. `domain/` 不得自行启动后台任务、管理重试，或持有并发原语。
3. `kernelone/` 可以拥有技术运行时、调度器与事件循环，但前提是它们确实是平台级能力，而不是 Polaris 业务流程语义。
4. `application/` 拥有业务顺序控制、补偿逻辑，以及决定外部副作用何时可见的权力。
5. 禁止随意“发出即不管”；除非其生命周期归属、取消策略与可观测性已经在 `bootstrap/` 或 `application/` 中被明确声明。

---

## 9. 异常边界

### 9.1 分层翻译

```text
kernelone/infrastructure exceptions
  -> application exceptions
  -> delivery transport exceptions / responses
```

规则：

1. 必须通过 `raise ... from exc` 保留异常链。
2. `delivery/` 负责把异常翻译成 HTTP、CLI 或 WebSocket 语义。
3. `application/` 拥有用例级错误消息与用户可感知的失败语义。
4. `domain/` 拥有不变量与业务规则违规异常。
5. `kernelone/` 与 `infrastructure/` 不得抛出框架级传输异常。

### 9.2 禁止模式

- 在 `delivery/` 以下抛出 `HTTPException`
- 吞掉 `Exception` 却不记录、不分类
- 在运行时逻辑中使用 `except Exception: pass`
- 实际失败却返回静默成功

---

## 10. 测试放置与边界

### 10.1 测试放置规则

- 单元测试应与拥有该生产代码的规范根目录对齐。
- 适配器契约测试应放在对应适配器附近，或放在聚焦的 `tests/contracts/` 区域。
- 架构测试统一放在 `tests/architecture/`。
- 集成测试应尽量通过 `delivery/`、`bootstrap/` 或其他真实边界进入系统，而不是先深度导入内部模块。
- 旧垫片可以保留兼容回归测试，但不能继续成为新行为测试的默认落点。

### 10.2 不允许的做法

- 除非明确用于兼容验证，否则不要继续在旧根目录下添加新的业务行为测试。
- 不要把 传输结构当作 domain 测试的主要断言面。
- 不要把基础设施 adapter 测试当成 application / domain 测试的替代品。

---

## 11. 导入规则

### 11.1 允许的高层依赖方向

| 来源 | 允许目标 |
|------|----------|
| `bootstrap` | 所有规范根目录 |
| `delivery` | `application`；以及第 3.2 节允许的极窄 `domain/` 或 `kernelone/` 公共 API |
| `application` | `domain/`、`kernelone/` 公共 API、`application/` 内部包 |
| `domain` | `domain/` 内部包、`domain/ports/`、获准的技术契约 |
| `kernelone` | `kernelone/` 内部 |
| `infrastructure` | `kernelone/`、显式定义在 `application/` 或 `domain/` 中的出站端口、用于映射的稳定领域模型/值对象、第三方库 |

### 11.2 禁止导入示例

```python
# 禁止: kernel 导入上层
from application.services.runtime_state import RuntimeStateService

# 禁止: delivery 导入具体基础设施实现
from infrastructure.persistence.audit_store import AuditStore

# 禁止: infrastructure 导入 application 编排
from application.workflows.runtime import RuntimeWorkflow

# 禁止: domain 导入传输关注点
from fastapi import HTTPException

# 禁止: 修补 sys.path
import sys
sys.path.insert(0, some_path)

# 禁止: 旧模块之间继续新增耦合
from legacy.app.services.foo import Foo
```

### 11.3 公共 API 导入

只要存在稳定公共表面，优先通过包级公共 API 导入：

```python
from kernelone.fs import KernelFS
from application.usecases.audit import AuditUseCase
from delivery.http.app_factory import create_delivery_app
```

一旦规范根目录已存在对应实现，禁止继续通过兼容根目录导入。

---

## 12. 验证门禁与自动化

当前测试通过，并不等于可以豁免本规范。

### 12.1 当前强制门禁

目前后端收敛工作至少必须通过：

```text
lint-imports --config .importlinter
pytest tests/architecture/test_architecture_invariants.py -q
pytest tests/architecture/test_phase0_migration_guard.py -q
pytest tests/architecture/test_kernel_convergence_hotspots.py -q
```

这些门禁仍然是过渡态门禁。它们今天主要表达的是旧架构迁移阶段约束，还不能完整覆盖目标规范根目录架构。

### 12.2 迁移完成前必须补齐的门禁

在宣布迁移结构完成之前，以下检查必须落地：

- 旧根目录下不得新增文件或新增功能逻辑
- bootstrap-only composition 检查
- `delivery/` 以下禁止导入框架级传输异常
- `bootstrap/` 与 `infrastructure/` 之外禁止导入具体 adapter
- 审计所有 `delivery -> domain` 直连，确保符合第 3.2 节窄边界
- 对实现 `application/ports/` 与 `domain/ports/` 的 adapter 做 port 归属校验
- 每个新 KernelOne 子系统都必须经过准入审查
- 架构测试必须逐步从旧 phase 命名收敛到规范根目录语义

### 12.3 本文引用的工程规则

虽然下列规则不是本文主轴，但它们仍然必须持续被测试或 lint 强制执行：

- 所有文本文件读写必须显式使用 UTF-8
- 迁移期间，被触达模块必须保持可直接导入
- 隐式可变全局状态必须有明确理由说明

---

## 13. 迁移执行顺序

目录迁对位置不等于架构完成。只有当依赖方向、对象归属、组装方式都收敛到本规范时，迁移才算完成。

建议迁移顺序：

1. `bootstrap/` 与 `delivery/` 冻结
   - 停止在旧入口继续堆逻辑
   - 将启动、配置、装配逻辑迁入 `bootstrap/`
   - 让旧路由与脚本只保留薄转发垫片
2. `application/` 与 `domain/` 收敛
   - 抽出用例、事务边界、领域策略与 DTO 归属
   - 把 delivery 层中残留的业务流程移走
3. 有资格的 `kernelone/` 提炼
   - 只迁移符合准入标准的代码
   - 不要因为“看起来通用”就把业务代码硬塞进内核
4. `infrastructure/` 隔离与 mapper 清理
   - 按端口正确实现出站适配器
   - 把 ORM、消息代理、SDK 映射收口到适配器边界
   - 清除上层对具体 adapter 的导入
5. 删除旧实现
   - 只有在调用方真正排空、替代门禁到位后，才删除兼容垫片

优先处理高频变更区与高耦合热点。只挪目录、不改依赖形状，不算成功迁移。

---

## 14. 快速放置指南

### 14.1 常见归属

| 需求 | 正确位置 |
|------|----------|
| 新的后端启动逻辑 | `bootstrap/` |
| 新的 HTTP 端点或 WebSocket 协议处理 | `delivery/` |
| 请求 / 响应结构 | `delivery/` |
| 新的 CLI 入口 | `delivery/cli/` |
| 应用层命令 / 查询 / 结果模型 | `application/` |
| 新用例 | `application/usecases/` |
| 新的应用编排器或工作流 | `application/services/` 或 `application/workflows/` |
| 聚合级或业务视角的仓储端口 | `domain/ports/` |
| 用例专属查询或网关端口 | `application/ports/` |
| 新业务规则 | `domain/services/`、`domain/policies/` 或 `domain/entities/` |
| 新技术运行时能力 | `kernelone/`，且必须满足第 3.5 节 |
| 新的存储 / 消息 / 遥测适配器 | `infrastructure/` |
| 持久化记录 / ORM 模型 / SDK DTO 包装对象 | `infrastructure/` |

### 14.2 常见灰区

| 问题 | 规则 |
|------|------|
| 令牌解析或请求守卫放哪？ | `delivery/` |
| 业务授权放哪？ | `application/` 或 `domain/`，不是 `delivery/` |
| 功能开关提供者的装配放哪？ | `bootstrap/` + `infrastructure/` |
| 功能开关驱动的业务分支放哪？ | `application/` 或 `domain/`，并通过显式输入或端口表达 |
| 缓存属于 KernelOne 还是 infrastructure？ | 具体缓存后端在 `infrastructure/`；可复用的缓存运行时契约可在 `kernelone/` |
| 后台任务调度器放哪？ | 调度器绑定在 `bootstrap/` 或 `infrastructure/`；任务处理器与业务工作流在 `application/` |
| 仓储接口放哪？ | 领域中心的持久化端口在 `domain/ports/`；用例专属读模型与查询端口在 `application/ports/` |

---

## 15. 常见问题

### Q: Polaris 业务逻辑现在应该放在哪里？
**A**: 业务规则与模型进 `domain/`，用例编排进 `application/`。`polaris_app/` 不再是目标根目录。

### Q: `delivery/` 可以直接调用 `kernelone/` 吗？
**A**: 可以，但只允许通过稳定公共 API，且只能用于传输邻接、无业务编排的场景。默认路径仍应是 `delivery -> application`。

### Q: `infrastructure/` 可以依赖 `domain/` 吗？
**A**: 可以，但仅限于实现已声明的出站端口，或在适配器边界对稳定领域模型 / 值对象做映射。凡是会把领域决策逻辑或应用编排拉下来的依赖，一律不允许。

### Q: 仓储接口应该放在哪里？
**A**: 聚合级、业务视角的持久化接口放 `domain/ports/`。用例专属查询、跨聚合读取、工作流网关接口放 `application/ports/`。不要把业务仓储藏进 `kernelone/`。

### Q: 某段代码可复用，但仍然强烈绑定某个功能，怎么办？
**A**: 跟随真实功能归属放置。可复用本身并不足以让它进入 `kernelone/`。

### Q: 配置加载应该放在哪里？
**A**: 放在 `bootstrap/config/`。其他层如果持有配置契约，前提也必须是归属清晰，而且契约本身不直接执行环境读取。

---

**文档版本**: 2.2  
**维护者**: Architecture Team  
**政策**: 本文档是后端迁移的唯一目标架构规范。即使当前仍存在过渡门禁与兼容垫片，所有新代码也必须遵守本规范。
