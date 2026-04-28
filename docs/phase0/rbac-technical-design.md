# 权限策略中心技术设计文档

## 文档信息

| 项目 | 内容 |
|------|------|
| 文档名称 | 权限策略中心技术设计 |
| 版本 | v1.0 |
| 状态 | 技术评审稿 |
| 创建日期 | 2026-03-04 |

---

## 一、当前架构分析

### 1.1 现有组件关系图

Polaris 现有的权限控制体系由以下核心组件构成，这些组件形成了分层的安全防护架构：

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                              API 请求入口                                        │
│                    require_permission (api/dependencies.py)                    │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                           RoleToolGateway                                       │
│                  (app/roles/gateways/tool_gateway.py)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐    │
│  │  黑名单检查   │→│  白名单检查   │→│  代码写权限   │→│  命令执行权  │    │
│  └──────────────┘  └──────────────┘  └──────────────┘  └──────────────┘    │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │ 文件删除权限  │→│ 调用次数限制  │→│  路径穿越检查 │                       │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                          SecurityService                                        │
│                  (domain/services/security_service.py)                          │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                       │
│  │   路径沙箱    │  │  危险命令检测  │  │  文件操作验证 │                       │
│  └──────────────┘  └──────────────┘  └──────────────┘                       │
└────────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌────────────────────────────────────────────────────────────────────────────────┐
│                        Workspace 文件系统                                       │
│                     (受限的资源操作范围)                                         │
└────────────────────────────────────────────────────────────────────────────────┘
```

### 1.2 权限决策流程

现有的权限决策采用七层过滤机制，每一层都独立进行判断，任一层拒绝即终止请求。以下是完整的决策流程：

```
请求进入 ──→ 黑名单检查 ──→ 白名单检查 ──→ 代码写权限 ──→ 命令执行权 ──→ 文件删除权
                          │              │              │              │
                          ▼              ▼              ▼              ▼
                       通过/拒绝    通过/拒绝     通过/拒绝     通过/拒绝
                                                    │
                                                    ▼
                                         调用次数限制检查
                                                    │
                                                    ▼
                                         路径穿越检查（最后防线）
                                                    │
                                                    ▼
                                         执行工具或返回拒绝
```

**关键决策点说明**：

第一层黑名单检查具有最高优先级，一旦工具名称出现在黑名单中，后续所有检查将被跳过，直接返回拒绝结果。第二层白名单检查采用白名单优先原则，若白名单非空则仅允许白名单中的工具，若白名单为空则默认禁止所有工具。第三层至第五层分别针对不同类型的危险操作进行细粒度控制，包括代码写入、命令执行和文件删除，这些权限默认均为关闭状态。第六层调用次数限制用于防止资源耗尽攻击，默认值为每轮最多 10 次调用。第七层路径穿越检查是最后的安全防线，确保所有文件操作都限制在 workspace 沙箱内。

### 1.3 现有组件职责边界

经过代码分析，各组件的职责边界清晰明确。RoleToolGateway 负责工具级别的权限控制，是权限决策的核心引擎，它整合了白名单、黑名单、权限标志位检查和路径验证等多个安全检查点。RoleToolPolicy 则是策略的数据模型定义，采用不可变数据结构确保策略在运行期间不会被意外修改。SecurityService 提供底层的安全能力，包括路径规范化、符号链接解析和危险命令模式匹配。require_permission 作为 API 层的权限装饰器，支持基于 HTTP header 的声明式权限声明，但目前在代码中应用较少。

---

## 二、增强设计

### 2.1 RBAC 模型定义

为了实现更细粒度的权限控制，本文提出基于 RBAC（Role-Based Access Control）的增强模型。该模型在现有 RoleToolGateway 的基础上引入资源维度和策略维度，形成三维权限控制体系。

#### 2.1.1 核心实体定义

**角色（Role）** 是权限的集合载体，每个角色定义了一组操作能力。在 Polaris 中，角色与内置 Profile 概念对应，including PM, Director, QA, Architect, and Chief Engineer。每个角色拥有特定的任务域和工具集。

**权限（Permission）** 是对资源进行操作的能力标识，采用 `resource_type:action:resource_pattern` 的三元组格式。例如 `file:read:*.py` 表示读取所有 Python 文件的权限，`tool:execute:write_file` 表示执行 write_file 工具的权限。

**资源（Resource）** 是权限控制的目标对象，可以是文件、目录、工具或 API 端点。资源支持通配符匹配和正则表达式，以便进行批量授权。

**策略（Policy）** 是权限的逻辑表达式，由条件规则组成，支持基于上下文（时间、来源、任务状态）的动态决策。

#### 2.1.2 数据模型

```python
# 资源定义
@dataclass
class Resource:
    id: str
    type: ResourceType  # file, directory, tool, api, workspace
    pattern: str  # 支持通配符和正则表达式
    metadata: Dict[str, Any] = field(default_factory=dict)

# 权限定义
@dataclass
class Permission:
    id: str
    resource_type: ResourceType
    action: Action  # read, write, execute, delete, admin
    resource_pattern: str
    conditions: List[Condition] = field(default_factory=list)

# 角色定义
@dataclass
class Role:
    id: str
    name: str
    description: str
    permissions: Set[str]  # 权限 ID 集合
    inherits_from: List[str] = field(default_factory=list)  # 角色继承
    priority: int = 0  # 角色优先级，用于冲突解决

# 策略定义
@dataclass
class Policy:
    id: str
    name: str
    effect: PolicyEffect  # allow, deny
    subjects: List[Subject]  # 谁
    resources: List[str]  # 作用于什么资源
    actions: List[Action]  # 执行什么操作
    conditions: List[Condition] = field(default_factory=list)
    priority: int = 0
```

#### 2.1.3 角色继承机制

为了提高权限管理的灵活性，模型支持角色继承。一个角色可以继承其他角色的权限，继承关系支持多层级联。例如：

```
SuperAdmin
    ├── Admin
    │   ├── Developer
    │   │   ├── PM
    │   │   └── QA
    │   └── Operator
    └── Auditor
```

在权限计算时，系统首先收集当前角色的所有直接权限，然后递归展开继承链，最终合并为完整的权限集合。继承链中的冲突通过角色优先级解决，高优先级角色的权限覆盖低优先级。

---

### 2.2 策略引擎设计

#### 2.2.1 策略引擎架构

策略引擎是权限决策的核心组件，负责评估所有适用的策略并产出最终决策。引擎采用流水线架构，包含策略匹配、条件评估和决策合并三个阶段：

```
请求上下文 ──→ 策略匹配 ──→ 条件评估 ──→ 决策合并 ──→ 最终决策
               │              │              │
               ▼              ▼              ▼
           候选策略集      评估结果        合并规则
```

**策略匹配阶段** 根据请求的主体（角色、用户）、资源类型和操作类型筛选出候选策略集合。这一阶段采用索引优化，将策略按主体、资源类型和操作类型分别建立索引，确保匹配效率。

**条件评估阶段** 对候选策略中的每一条条件进行评估。条件可以是时间范围（允许在工作时间操作）、来源限制（仅限特定 IP 段）、任务状态（仅在任务处于特定状态时允许）或自定义表达式。条件评估支持短路逻辑，一旦某条 deny 策略的条件满足，立即终止评估。

**决策合并阶段** 将所有评估通过的策略结果合并为最终决策。合并规则遵循以下优先级：显式拒绝（Deny）优先于显式允许（Allow），高优先级策略优先于低优先级策略，若无匹配策略则默认拒绝。

#### 2.2.2 支持的策略类型

**静态策略** 在策略定义时确定，适用于基础权限配置。例如：

```json
{
  "id": "pm-read-only",
  "effect": "allow",
  "subjects": [{"type": "role", "id": "pm"}],
  "resources": [{"type": "file", "pattern": "**/*"}],
  "actions": ["read"],
  "priority": 10
}
```

**动态策略** 包含运行时条件，适用于需要根据上下文调整权限的场景。例如：

```json
{
  "id": "director-tool-during-execution",
  "effect": "allow",
  "subjects": [{"type": "role", "id": "director"}],
  "resources": [{"type": "tool", "pattern": "*"}],
  "actions": ["execute"],
  "conditions": [
    {"type": "task_state", "operator": "eq", "value": "running"},
    {"type": "time_window", "start": "00:00", "end": "23:59"}
  ],
  "priority": 50
}
```

**委托策略** 允许角色在特定条件下将权限临时授予其他主体。例如，当 Director 需要 PM 的某些只读权限时，可以通过委托策略获得。

#### 2.2.3 策略存储与加载

策略数据采用分层存储设计：内置策略存储在代码仓库中，作为系统的默认安全基线；运行时策略存储在 workspace 的配置目录中，支持动态更新；会话策略存储在内存中，用于请求级别的临时权限调整。三层策略的加载顺序为：先加载内置策略，再加载运行时策略，最后加载会话策略，后加载的策略可以覆盖先加载的策略。

---

### 2.3 权限决策点设计

#### 2.3.1 统一权限决策点架构

为了将现有的分散权限检查统一起来，本文设计 Permission Decision Point（PDP）作为唯一的权限决策入口。PDP 位于 API 层和业务逻辑层之间，所有需要权限验证的请求都必须经过 PDP：

```
┌─────────────┐    ┌─────────────┐    ┌─────────────┐    ┌─────────────┐
│   API 层    │───→│     PDP     │───→│  业务逻辑   │───→│  响应返回   │
└─────────────┘    └─────────────┘    └─────────────┘    └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  策略引擎    │
                   └─────────────┘
                          │
                          ▼
                   ┌─────────────┐
                   │  审计日志    │
                   └─────────────┘
```

#### 2.3.2 PDP 决策流程

当请求到达 PDP 时，决策流程如下：

第一步是构建请求上下文。PDP 从请求中提取主体信息（包括角色、用户 ID、来源）、资源信息（包括资源类型、路径、标识符）、操作信息（包括操作类型、参数）和环境信息（包括时间、IP、任务状态）。这些信息构成完整的请求上下文，用于后续的条件评估。

第二步是策略查询。PDP 根据上下文查询所有适用的策略，包括直接匹配主体角色的策略、匹配资源类型的策略和匹配操作类型的策略。查询结果按照优先级排序。

第三步是策略评估。对候选策略逐一进行条件评估，计算每条策略的匹配结果。若存在任何 deny 策略匹配且条件满足，则决策为拒绝。

第四步是决策输出。PDP 返回包含决策结果、匹配策略列表和建议信息的结构化响应。

第五步是审计记录。每次决策都会生成审计日志，记录完整的请求上下文、决策结果和匹配策略。

#### 2.3.3 PDP 接口定义

```python
class PermissionDecisionPoint:
    """统一权限决策点"""
    
    async def evaluate(
        self,
        subject: Subject,
        resource: Resource,
        action: Action,
        context: DecisionContext
    ) -> DecisionResult:
        """
        评估权限请求
        
        Args:
            subject: 请求主体（角色、用户）
            resource: 目标资源
            action: 请求的操作
            context: 决策上下文（时间、任务状态等）
            
        Returns:
            DecisionResult: 包含 allow/deny 决策及详细信息
        """
        pass
    
    async def batch_evaluate(
        self,
        requests: List[PermissionRequest]
    ) -> List[DecisionResult]:
        """批量评估权限请求"""
        pass
    
    async def get_effective_permissions(
        self,
        subject: Subject
    ) -> List[Permission]:
        """获取主体的有效权限列表"""
        pass
```

---

### 2.4 权限实施点设计

#### 2.4.1 PEP 分层架构

Permission Enforcement Point（PEP）是策略的执行层，分布在系统的各个入口点。Polaris 的 PEP 分为三个层次：

**API 层 PEP** 位于 API 路由层面，负责验证 API 请求的权限。现有实现中的 require_permission 将迁移为 API 层 PEP 的一部分。新增的 API 层 PEP 支持基于路径的权限配置和基于 HTTP 方法的权限控制。

**工具层 PEP** 位于 RoleToolGateway 内部，负责验证工具调用的权限。现有实现的七层检查机制将被整合为工具层 PEP，同时新增基于策略的动态权限评估。

**资源层 PEP** 位于文件系统和命令执行层面，负责验证具体资源操作的权限。SecurityService 将扩展为资源层 PEP，支持更多的资源类型和更细粒度的操作控制。

#### 2.4.2 PEP 协调机制

三个层次的 PEP 通过统一的权限上下文进行协调。API 层 PEP 负责建立权限上下文，将其附加到请求对象上；工具层 PEP 和资源层 PEP 继承并扩展该上下文，确保权限决策的一致性。当不同层次的 PEP 做出冲突决策时，以最严格的决策为准。

```python
@dataclass
class EnforcementContext:
    """权限实施上下文，在各 PEP 层之间传递"""
    request_id: str
    subject: Subject
    resource_path: str
    action: Action
    task_id: Optional[str] = None
    session_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

class PermissionEnforcer:
    """权限实施协调器"""
    
    def __init__(self, pdp: PermissionDecisionPoint):
        self.pdp = pdp
        self.api_pep = ApiPEP(pdp)
        self.tool_pep = ToolPEP(pdp)
        self.resource_pep = ResourcePEP(pdp)
    
    async def enforce_api(self, request: Request) -> DecisionResult:
        return await self.api_pep.check(request)
    
    async def enforce_tool(self, tool_call: ToolCall, context: EnforcementContext) -> DecisionResult:
        return await self.tool_pep.check(tool_call, context)
    
    async def enforce_resource(self, operation: ResourceOperation, context: EnforcementContext) -> DecisionResult:
        return await self.resource_pep.check(operation, context)
```

---

## 三、实施路线

### 3.1 整体规划

权限策略中心的实施分为三个阶段，每个阶段聚焦于特定目标，逐步构建完整的权限控制体系：

| 阶段 | 目标 | 预计周期 | 关键交付物 |
|------|------|----------|------------|
| Phase 1 | 统一权限决策层 | 2 周 | PDP、策略引擎、API 层 PEP |
| Phase 2 | 细粒度资源权限 | 3 周 | 资源权限模型、工具层 PEP 增强 |
| Phase 3 | 策略动态化 | 2 周 | 运行时策略管理、审计系统 |

### 3.2 Phase 1：统一权限决策层

#### 3.2.1 目标

建立统一的权限决策中心，将现有的分散权限检查整合到 PDP 架构中，实现策略驱动的权限决策。

#### 3.2.2 任务清单

第一项任务是实现核心数据模型。在 domain/entities 目录下创建 Permission、Role、Resource、Policy 等实体类，定义完整的数据结构和验证规则。

第二项任务是实现策略引擎。构建 PolicyEngine 类，支持策略的加载、匹配和评估，实现静态策略和基础条件评估能力。

第三项任务是实现 PDP。创建 PermissionDecisionPoint 类，作为所有权限请求的统一入口，整合现有的 RoleToolGateway 检查逻辑。

第四项任务是实现 API 层 PEP。扩展 api/dependencies.py 中的 require_permission，支持基于策略的动态权限检查，并添加策略管理 API 端点。

第五项任务是迁移现有配置。将 builtin_profiles.py 中的角色定义迁移为 RBAC 格式的策略配置，确保功能等价。

#### 3.2.3 验收标准

验收标准包括：所有现有的权限检查逻辑必须继续正常工作；新策略系统必须支持现有的白名单、黑名单机制；API 响应时间增加不超过 20 毫秒；策略更新后无需重启服务即可生效。

### 3.3 Phase 2：细粒度资源权限

#### 3.3.1 目标

在统一决策层的基础上，实现资源级别的细粒度权限控制，支持基于路径、文件类型、操作类型的精细配置。

#### 3.3.2 任务清单

第一项任务是扩展资源模型。增加对文件、目录、工具、API 端点等资源类型的支持，实现资源模式匹配（通配符、正则表达式）。

第二项任务是实现资源层 PEP。扩展 SecurityService 为资源层 PEP，支持基于资源策略的访问控制。

第三项任务是实现工具层 PEP 增强。在 RoleToolGateway 中集成策略引擎，支持基于策略的工具权限动态调整。

第四项任务是实现委托机制。允许角色在特定条件下临时获取其他角色的权限。

#### 3.3.3 验收标准

验收标准包括：支持基于文件路径模式的权限配置（如禁止删除 .md 文件）；支持基于工具参数的权限检查（如限制写入文件的大小）；委托权限必须在指定时间后自动失效。

### 3.4 Phase 3：策略动态化

#### 3.4.1 目标

实现策略的运行时管理和动态调整能力，支持基于审计数据的策略优化，建立完整的权限审计体系。

#### 3.4.2 任务清单

第一项任务是实现策略管理 UI。在前端添加策略配置界面，支持策略的增删改查和版本管理。

第二项任务是实现审计日志系统。记录所有权限决策事件，支持按角色、资源、时间等维度查询审计日志。

第三项任务是实现策略分析。基于审计数据生成权限使用报告，识别异常访问模式，提供策略优化建议。

第四项任务是实现策略模拟。允许管理员模拟特定场景下的权限决策，预验证策略变更的影响。

#### 3.4.3 验收标准

验收标准包括：策略变更在 5 秒内生效；审计日志查询响应时间不超过 1 秒；策略模拟结果与实际执行结果一致。

---

## 四、API 设计

### 4.1 权限查询接口

#### 4.1.1 检查权限

```
POST /v2/permissions/check
```

请求体：

```json
{
  "subject": {
    "type": "role",
    "id": "director"
  },
  "resource": {
    "type": "file",
    "path": "/workspace/src/main.py"
  },
  "action": "write",
  "context": {
    "task_id": "task-123"
  }
}
```

响应：

```json
{
  "allowed": true,
  "decision": "allow",
  "matched_policies": ["director-write-all"],
  "reason": "policy matched: director-write-all"
}
```

#### 4.1.2 获取有效权限

```
GET /v2/permissions/effective?subject_type=role&subject_id=director
```

响应：

```json
{
  "subject": {
    "type": "role",
    "id": "director"
  },
  "permissions": [
    {
      "id": "file:read:*",
      "resource_type": "file",
      "action": "read",
      "resource_pattern": "*"
    },
    {
      "id": "file:write:*",
      "resource_type": "file",
      "action": "write",
      "resource_pattern": "*"
    }
  ]
}
```

#### 4.1.3 批量检查权限

```
POST /v2/permissions/batch-check
```

请求体：

```json
{
  "requests": [
    {
      "subject": {"type": "role", "id": "pm"},
      "resource": {"type": "tool", "name": "read_file"},
      "action": "execute"
    },
    {
      "subject": {"type": "role", "id": "pm"},
      "resource": {"type": "file", "path": "/workspace/src/main.py"},
      "action": "write"
    }
  ]
}
```

响应：

```json
{
  "results": [
    {
      "allowed": true,
      "decision": "allow"
    },
    {
      "allowed": false,
      "decision": "deny",
      "reason": "role pm does not have write permission"
    }
  ]
}
```

### 4.2 角色管理接口

#### 4.2.1 获取角色列表

```
GET /v2/roles
```

响应：

```json
{
  "roles": [
    {
      "id": "pm",
      "name": "PM",
      "description": "项目管理角色",
      "permission_count": 12,
      "inherits_from": [],
      "priority": 10
    },
    {
      "id": "director",
      "name": "Director",
      "description": "代码执行角色",
      "permission_count": 45,
      "inherits_from": ["pm"],
      "priority": 50
    }
  ]
}
```

#### 4.2.2 获取角色详情

```
GET /v2/roles/{role_id}
```

响应：

```json
{
  "id": "director",
  "name": "Director",
  "description": "代码执行角色",
  "permissions": [
    {
      "id": "file:read:*",
      "resource_type": "file",
      "action": "read",
      "resource_pattern": "*",
      "conditions": []
    },
    {
      "id": "file:write:*",
      "resource_type": "file",
      "action": "write",
      "resource_pattern": "*",
      "conditions": [
        {
          "type": "task_state",
          "operator": "eq",
          "value": "running"
        }
      ]
    }
  ],
  "inherits_from": ["pm"],
  "priority": 50,
  "metadata": {
    "builtin": true,
    "created_at": "2026-01-01T00:00:00Z"
  }
}
```

#### 4.2.3 创建角色

```
POST /v2/roles
```

请求体：

```json
{
  "id": "custom_role",
  "name": "自定义角色",
  "description": "用于特定场景的自定义角色",
  "permissions": ["file:read:*.md", "tool:execute:search"],
  "inherits_from": ["qa"],
  "priority": 30
}
```

响应：

```json
{
  "id": "custom_role",
  "name": "自定义角色",
  "description": "用于特定场景的自定义角色",
  "created_at": "2026-03-04T12:00:00Z"
}
```

#### 4.2.4 更新角色

```
PUT /v2/roles/{role_id}
```

请求体：

```json
{
  "name": "更新后的角色名",
  "description": "更新后的描述",
  "permissions": ["file:read:*", "tool:execute:*"],
  "inherits_from": ["pm", "qa"],
  "priority": 40
}
```

#### 4.2.5 删除角色

```
DELETE /v2/roles/{role_id}
```

响应：

```json
{
  "deleted": true,
  "role_id": "custom_role"
}
```

### 4.3 策略管理接口

#### 4.3.1 获取策略列表

```
GET /v2/policies
```

响应：

```json
{
  "policies": [
    {
      "id": "pm-read-only",
      "name": "PM 只读策略",
      "effect": "allow",
      "subjects": [{"type": "role", "id": "pm"}],
      "resources": [{"type": "file", "pattern": "**/*"}],
      "actions": ["read"],
      "priority": 10,
      "enabled": true
    }
  ]
}
```

#### 4.3.2 创建策略

```
POST /v2/policies
```

请求体：

```json
{
  "id": "restrict-sensitive-files",
  "name": "敏感文件限制",
  "effect": "deny",
  "subjects": [
    {"type": "role", "id": "pm"},
    {"type": "role", "id": "qa"}
  ],
  "resources": [
    {"type": "file", "pattern": "**/*.env"},
    {"type": "file", "pattern": "**/secrets/**"}
  ],
  "actions": ["write", "delete"],
  "conditions": [],
  "priority": 100,
  "enabled": true
}
```

### 4.4 审计日志接口

#### 4.4.1 查询审计日志

```
GET /v2/audit/logs?subject_type=role&subject_id=director&from=2026-03-01T00:00:00Z&to=2026-03-04T23:59:59Z
```

响应：

```json
{
  "logs": [
    {
      "id": "audit-001",
      "timestamp": "2026-03-04T10:30:00Z",
      "subject": {"type": "role", "id": "director"},
      "resource": {"type": "file", "path": "/workspace/src/main.py"},
      "action": "write",
      "decision": "allow",
      "matched_policies": ["director-write-all"],
      "request_id": "req-123"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50
}
```

---

## 五、技术债务与迁移计划

### 5.1 技术债务识别

在现有实现中需要解决以下技术债务问题。RoleToolGateway 与 SecurityService 之间存在功能重叠，部分路径检查逻辑在两处都有实现，需要统一。builtin_profiles.py 中的角色定义采用硬编码方式，不利于动态扩展，需要迁移到策略存储。权限检查的错误处理不够统一，不同组件返回的错误格式各异，需要标准化。

### 5.2 平滑迁移策略

为了确保系统的稳定性，迁移将采用渐进式方式。第一步是并行运行，新实现的 PDP 与现有 RoleToolGateway 同时运行，决策结果进行比对，确保一致性。第二步是灰度切换，先将部分流量切换到新系统，观察无异常后逐步扩大比例。第三步是完全切换，确认新系统稳定后，移除旧代码。

---

## 六、总结

本文档详细阐述了 Polaris 权限策略中心的技术设计方案。核心思路是在现有 RoleToolGateway 的基础上，构建统一的权限决策体系，实现从工具级权限控制向资源级、策略驱动的细粒度权限控制演进。

方案的主要特点包括：采用 RBAC 模型提供清晰的权限管理结构；设计分层 PEP 架构实现多层次的权限实施；支持动态策略满足运行时调整需求；建立完整的审计体系实现权限使用的可追溯性。

通过三阶段的实施路线，可以确保在不影响现有功能的前提下，逐步构建完整的权限控制体系，为 Polaris 的安全运营提供坚实的技术基础。