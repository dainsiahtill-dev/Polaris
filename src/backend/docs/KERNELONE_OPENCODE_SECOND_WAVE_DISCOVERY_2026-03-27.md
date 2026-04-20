# OpenCode 机制深度探索报告（第二轮）

**报告日期**: 2026-03-27
**探索范围**: OpenCode 第二轮机制分析
**与 Polaris 集成潜力评估**

---

## 摘要

经过对 OpenCode 代码库第二轮深度分析，发现以下高价值机制可引入到 Polaris：

| 优先级 | 机制 | 价值定位 | 引入难度 |
|--------|------|----------|----------|
| **P0** | Event Sourcing (SyncEvent) | 状态持久化与溯源 | 高 |
| **P0** | Instance-scoped State | 工作区隔离状态管理 | 中 |
| **P1** | Agent Permission Ruleset | 细粒度权限控制 | 中 |
| **P1** | Output Truncation | LLM 输出安全边界 | 低 |
| **P2** | Workspace Adaptor | 多后端存储抽象 | 中 |
| **P2** | File-based Skill | 可扩展 Agent 能力 | 低 |
| **P2** | Template with Hints | 模板参数自动提取 | 低 |

---

## 1. Event Sourcing (SyncEvent)

### 1.1 OpenCode 实现

```typescript
// packages/opencode/src/sync/index.ts
export namespace SyncEvent {
  // 定义事件溯源
  export const define = <Type, Agg, Schema>(input: {
    type: Type,
    version: number,
    aggregate: Agg,
    schema: Schema
  }) => {
    registry.set(versionedType(type, version), def)
    return def
  }

  // 立即事务处理
  export const run = (def, data) => {
    Database.transaction((tx) => {
      const seq = getNextSequence(tx, agg)
      const event = { id, seq, aggregateID: agg, data }
      projector(tx, event.data)  // 投影器更新物化视图
      saveEvent(tx, event)       // 持久化事件
      Bus.publish(def.type, data) // 发布到 Bus
    }, { behavior: "immediate" })
  }
}
```

### 1.2 核心设计模式

1. **Versioned Events**: 事件带版本号，支持 schema 演进
2. **Projector Pattern**: 事件驱动物化视图更新
3. **Immediate Transaction**: 立即隔离级别确保一致性
4. **Bus Bridge**: 事件溯源与 PubSub 桥接

### 1.3 Polaris 引入方案

**目标位置**: `polaris/kernelone/events/sourcing/`

```python
# event_sourcing.py
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Generic, TypeVar
import uuid

T = TypeVar("T")

@dataclass
class Event(Generic[T]):
    """带版本的事件定义"""
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    version: int = 1
    aggregate_id: str | None = None
    timestamp: datetime = field(default_factory=datetime.utcnow)
    data: T | None = None

class EventSourcedAggregate:
    """事件溯源聚合根基类"""
    _events: list[Event] = []
    _version: int = 0

    def _record(self, event: Event) -> None:
        self._events.append(event)
        self._version += 1
        event.version = self._version

    def apply(self, event: Event) -> None:
        """子类实现事件应用逻辑"""
        raise NotImplementedError

    @classmethod
    def from_events(cls, events: list[Event]) -> "EventSourcedAggregate":
        """从事件历史重建聚合"""
        aggregate = cls.__new__(cls)
        aggregate._events = []
        aggregate._version = 0
        for event in events:
            aggregate.apply(event)
        return aggregate

class Projector:
    """物化视图投影器"""
    def project(self, event: Event) -> None:
        raise NotImplementedError

class EventStore:
    """事件存储"""
    async def save(self, aggregate_id: str, events: list[Event]) -> None:
        raise NotImplementedError

    async def load(self, aggregate_id: str) -> list[Event]:
        raise NotImplementedError
```

**应用场景**:
- Session 状态变更历史
- Director 执行轨迹
- Context 变更记录

---

## 2. Instance-scoped State

### 2.1 OpenCode 实现

```typescript
// packages/opencode/src/effect/instance-state.ts
export namespace InstanceState {
  // Scoped Cache: 按实例目录隔离状态
  export const make = <A>(init: (ctx: InstanceContext) => Effect.Effect<A>) => {
    const cache = yield* ScopedCache.make<string, A>({
      capacity: Number.POSITIVE_INFINITY,
      lookup: () => init(Instance.current),
    })
    // 实例销毁时自动失效
    const off = registerDisposer((directory) =>
      Effect.runPromise(ScopedCache.invalidate(cache, directory))
    )
    return { cache }
  }
}
```

### 2.2 核心设计模式

1. **Directory-scoped**: 状态按工作区目录隔离
2. **Auto Cleanup**: 实例销毁时自动清理
3. **Lazy Init**: 按需初始化

### 2.3 Polaris 引入方案

**目标位置**: `polaris/kernelone/effect/instance_state.py`

```python
# instance_state.py
from __future__ import annotations
import asyncio
from contextvars import ContextVar
from typing import Any, Callable, TypeVar
from weakref import WeakValueDictionary

T = TypeVar("T")

# 当前实例上下文
_current_instance: ContextVar[str | None] = ContextVar("current_instance", default=None)

class InstanceScopedCache:
    """按实例隔离的缓存

    每个工作区目录有独立的缓存实例，目录销毁时自动清理。
    """
    _caches: WeakValueDictionary[str, dict[str, Any]] = WeakValueDictionary()

    @classmethod
    def get_cache(cls, instance_id: str) -> dict[str, Any]:
        """获取或创建实例缓存"""
        if instance_id not in cls._caches:
            cls._caches[instance_id] = {}
        return cls._caches[instance_id]

    @classmethod
    def get_or_init(
        cls,
        instance_id: str,
        key: str,
        factory: Callable[[], T],
    ) -> T:
        """获取或初始化缓存值"""
        cache = cls.get_cache(instance_id)
        if key not in cache:
            cache[key] = factory()
        return cache[key]

    @classmethod
    def invalidate(cls, instance_id: str, key: str | None = None) -> None:
        """清除缓存"""
        if instance_id in cls._caches:
            if key is None:
                cls._caches[instance_id].clear()
            elif key in cls._caches[instance_id]:
                del cls._caches[instance_id][key]

    @classmethod
    def register_cleanup(
        cls,
        instance_id: str,
        cleanup_fn: Callable[[], None],
    ) -> None:
        """注册实例销毁时的清理回调"""
        cache = cls.get_cache(instance_id)
        if "_cleanup_fns" not in cache:
            cache["_cleanup_fns"] = []
        cache["_cleanup_fns"].append(cleanup_fn)


class InstanceState:
    """实例作用域状态管理器

    Usage:
        async with InstanceState.for_workspace("/path/to/workspace"):
            # 当前实例的缓存
            cache = InstanceState.get_cache()
            cache["my_data"] = some_data
    """
    _state: dict[str, Any] = {}

    @classmethod
    def for_workspace(cls, workspace: str) -> "InstanceState":
        """为指定工作区创建实例状态"""
        instance = cls()
        instance._workspace = workspace
        instance._state = InstanceScopedCache.get_cache(workspace)
        return instance

    @classmethod
    def get_cache(cls) -> dict[str, Any]:
        """获取当前实例的缓存"""
        instance_id = _current_instance.get()
        if instance_id is None:
            return {}
        return InstanceScopedCache.get_cache(instance_id)

    def __enter__(self) -> "InstanceState":
        token = _current_instance.set(self._workspace)
        self._token = token
        return self

    def __exit__(self, *args: Any) -> None:
        if hasattr(self, "_token"):
            _current_instance.reset(self._token)

        # 执行清理回调
        cleanup_fns = self._state.get("_cleanup_fns", [])
        for fn in cleanup_fns:
            try:
                fn()
            except Exception:
                pass
```

**应用场景**:
- 工具执行上下文缓存
- LLM Provider 实例缓存
- 工作区特定的配置

---

## 3. Agent Permission Ruleset

### 3.1 OpenCode 实现

```typescript
// packages/opencode/src/agent/agent.ts
export namespace Agent {
  export const Info = z.object({
    name: z.string(),
    description: z.string().optional(),
    mode: z.enum(["subagent", "primary", "all"]),
    native: z.boolean().optional(),
    permission: Permission.Ruleset,  // 权限规则集
    model: z.object({ modelID, providerID }).optional(),
    prompt: z.string().optional(),
    steps: z.number().int().positive().optional(),
  })

  // Permission Ruleset
  export const Ruleset = z.object({
    version: z.number(),
    rules: z.array(Permission.Rule),
    inherits: z.array(z.string()).optional(),  // 继承其他 ruleset
  })
}

// packages/opencode/src/permission/index.ts
export namespace Permission {
  export const Rule = z.object({
    permission: z.string(),   // "edit", "bash", "read"
    pattern: z.string(),     // "*.py", "src/**"
    action: z.enum(["allow", "deny", "ask"])
  })
}
```

### 3.2 核心设计模式

1. **Rule-based Permission**: 规则按文件和操作模式匹配
2. **Inheritance**: 权限规则可继承组合
3. **Pattern Matching**: Glob 模式匹配

### 3.3 Polaris 引入方案

**目标位置**: `polaris/kernelone/agent/permission.py`

```python
# permission.py
from __future__ import annotations
import fnmatch
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

class PermissionAction(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"  # 运行时询问用户

class PermissionType(str, Enum):
    READ = "read"
    WRITE = "write"
    EDIT = "edit"
    BASH = "bash"
    EXECUTE = "execute"
    NETWORK = "network"

@dataclass
class PermissionRule:
    """单个权限规则"""
    permission: PermissionType
    pattern: str  # Glob 模式，如 "*.py", "src/**"
    action: PermissionAction
    description: str | None = None

@dataclass
class PermissionRuleset:
    """权限规则集

    支持规则继承和合并。
    """
    name: str
    version: int = 1
    rules: list[PermissionRule] = field(default_factory=list)
    inherits: list[str] = field(default_factory=list)  # 继承的 ruleset 名称
    _parent_rulesets: dict[str, "PermissionRuleset"] = field(default_factory=dict)

    def add_rule(
        self,
        permission: PermissionType,
        pattern: str,
        action: PermissionAction,
        description: str | None = None,
    ) -> "PermissionRuleset":
        """链式添加规则"""
        self.rules.append(PermissionRule(
            permission=permission,
            pattern=pattern,
            action=action,
            description=description,
        ))
        return self

    def check(self, permission: PermissionType, path: "Path | str") -> PermissionAction:
        """检查权限"""
        path_str = str(path)

        # 收集所有规则（包含继承的）
        all_rules = list(self.rules)
        for parent_name in self.inherits:
            if parent_name in self._parent_rulesets:
                all_rules.extend(self._parent_rulesets[parent_name].rules)

        # 按优先级匹配：deny > allow > ask
        result = PermissionAction.ASK
        for rule in all_rules:
            if rule.permission == permission and fnmatch.fnmatch(path_str, rule.pattern):
                if rule.action == PermissionAction.DENY:
                    return PermissionAction.DENY
                elif rule.action == PermissionAction.ALLOW:
                    result = PermissionAction.ALLOW

        return result

    def register_parent(self, ruleset: "PermissionRuleset") -> None:
        """注册父规则集（用于继承）"""
        self._parent_rulesets[ruleset.name] = ruleset

    @classmethod
    def merge(cls, *rulesets: "PermissionRuleset") -> "PermissionRuleset":
        """合并多个规则集"""
        merged = cls(name="merged")
        for rs in rulesets:
            merged.rules.extend(rs.rules)
        return merged


# 预定义规则集
SAFE_RULESET = PermissionRuleset(name="safe").add_rule(
    PermissionType.READ, "**", PermissionAction.ALLOW
).add_rule(
    PermissionType.BASH, "*.sh", PermissionAction.ASK
)

TRUSTED_RULESET = PermissionRuleset(name="trusted").add_rule(
    PermissionType.READ, "**", PermissionAction.ALLOW
).add_rule(
    PermissionType.WRITE, "**", PermissionAction.ALLOW
).add_rule(
    PermissionType.BASH, "**", PermissionAction.ASK
)
```

**应用场景**:
- Agent 执行权限控制
- 文件访问白名单
- 危险操作二次确认

---

## 4. Output Truncation

### 4.1 OpenCode 实现

```typescript
// packages/opencode/src/tool/tool.ts
export function define(id, init) {
  return {
    init: async (initCtx) => {
      const toolInfo = await init(initCtx)
      toolInfo.execute = async (args, ctx) => {
        toolInfo.parameters.parse(args)  // 校验
        const result = await execute(args, ctx)
        // 输出截断
        const truncated = await Truncate.output(result.output)
        return {
          ...result,
          output: truncated.content,
          metadata: {
            ...result.metadata,
            truncated: truncated.truncated
          }
        }
      }
      return toolInfo
    }
  }
}
```

### 4.2 Polaris 引入方案

**目标位置**: `polaris/kernelone/llm/truncation.py`

```python
# truncation.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

# 默认截断阈值
DEFAULT_MAX_TOKENS = 16000
DEFAULT_MAX_CHARS = 50000

@dataclass
class TruncationResult:
    """截断结果"""
    content: str
    truncated: bool
    original_size: int
    truncated_size: int
    reason: str | None = None

class OutputTruncator:
    """LLM 输出截断器

    防止工具输出过大导致上下文溢出。
    支持多种截断策略。
    """
    def __init__(
        self,
        max_tokens: int = DEFAULT_MAX_TOKENS,
        max_chars: int = DEFAULT_MAX_CHARS,
        strategy: str = "head_tail",  # head_tail, head, tail
    ):
        self.max_tokens = max_tokens
        self.max_chars = max_chars
        self.strategy = strategy

    def truncate(self, content: str) -> TruncationResult:
        """截断内容"""
        original_size = len(content)

        # 先检查字符数
        if original_size <= self.max_chars:
            return TruncationResult(
                content=content,
                truncated=False,
                original_size=original_size,
                truncated_size=original_size,
            )

        # 应用截断策略
        if self.strategy == "head":
            truncated = self._truncate_head(content)
        elif self.strategy == "tail":
            truncated = self._truncate_tail(content)
        elif self.strategy == "head_tail":
            truncated = self._truncate_head_tail(content)
        else:
            truncated = content[:self.max_chars]

        return TruncationResult(
            content=truncated,
            truncated=True,
            original_size=original_size,
            truncated_size=len(truncated),
            reason=f"Exceeded max_chars={self.max_chars}",
        )

    def _truncate_head(self, content: str) -> str:
        """保留头部"""
        return content[:self.max_chars]

    def _truncate_tail(self, content: str) -> str:
        """保留尾部"""
        return content[-self.max_chars:]

    def _truncate_head_tail(self, content: str) -> str:
        """保留头部和尾部，中间截断"""
        if len(content) <= self.max_chars:
            return content

        # 头部:尾部 = 3:2
        head_size = int(self.max_chars * 0.6)
        tail_size = self.max_chars - head_size

        head = content[:head_size]
        tail = content[-tail_size:]

        separator = f"\n\n[... {len(content) - self.max_chars} characters truncated ...]\n\n"
        return head + separator + tail

    def truncate_dict(self, data: dict[str, Any]) -> dict[str, Any]:
        """截断字典中的字符串值"""
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.truncate(value).content
            elif isinstance(value, dict):
                result[key] = self.truncate_dict(value)
            elif isinstance(value, list):
                result[key] = [self.truncate(v).content if isinstance(v, str) else v for v in value]
            else:
                result[key] = value
        return result


# 全局截断器实例
_default_truncator: OutputTruncator | None = None

def get_default_truncator() -> OutputTruncator:
    global _default_truncator
    if _default_truncator is None:
        _default_truncator = OutputTruncator()
    return _default_truncator

def truncate_output(content: str) -> TruncationResult:
    """快捷函数"""
    return get_default_truncator().truncate(content)
```

**应用场景**:
- 工具执行结果截断
- LLM 流式输出缓冲
- 文件读取结果限制

---

## 5. Workspace Adaptor

### 5.1 OpenCode 实现

```typescript
// packages/opencode/src/control-plane/workspace.ts
export namespace Workspace {
  export const create = fn(CreateInput, async (input) => {
    const adaptor = await getAdaptor(input.type)  // "worktree", "remote", etc.
    const config = await adaptor.configure({ ...input, id, name: null, directory: null })
    await adaptor.create(config)
  })

  // SSE 实时同步
  async function workspaceEventLoop(space: Info, stop: AbortSignal) {
    while (!stop.aborted) {
      const res = await adaptor.fetch(space, "/event", { method: "GET", signal: stop })
      await parseSSE(res.body, stop, (event) =>
        GlobalBus.emit("event", { payload: event })
      )
    }
  }
}
```

### 5.2 Polaris 引入方案

**目标位置**: `polaris/kernelone/storage/adaptor.py`

```python
# adaptor.py
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from typing import Any, AsyncIterator

class WorkspaceType(str, Enum):
    LOCAL = "local"
    REMOTE = "remote"
    VIRTUAL = "virtual"
    COMPOSITE = "composite"

@dataclass
class WorkspaceConfig:
    """工作区配置"""
    id: str
    name: str
    workspace_type: WorkspaceType
    root: str
    metadata: dict[str, Any]

@dataclass
class WorkspaceEvent:
    """工作区事件"""
    event_type: str
    path: str | None
    data: Any

class WorkspaceAdaptor(ABC):
    """工作区适配器基类

    支持多种后端存储（本地、远程、虚拟等）。
    """
    def __init__(self, config: WorkspaceConfig):
        self.config = config

    @abstractmethod
    async def read(self, path: str) -> bytes:
        """读取文件"""
        raise NotImplementedError

    @abstractmethod
    async def write(self, path: str, content: bytes) -> None:
        """写入文件"""
        raise NotImplementedError

    @abstractmethod
    async def list(self, pattern: str = "**/*") -> list[str]:
        """列出文件"""
        raise NotImplementedError

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """检查文件存在"""
        raise NotImplementedError

    @abstractmethod
    async def watch(self) -> AsyncIterator[WorkspaceEvent]:
        """监视文件变化"""
        raise NotImplementedError


class LocalWorkspaceAdaptor(WorkspaceAdaptor):
    """本地文件系统适配器"""

    def __init__(self, config: WorkspaceConfig):
        super().__init__(config)
        self._root = Path(config.root)

    async def read(self, path: str) -> bytes:
        full_path = self._root / path
        return await async_read_file(full_path)

    async def write(self, path: str, content: bytes) -> None:
        full_path = self._root / path
        await async_write_file(full_path, content)

    async def list(self, pattern: str = "**/*") -> list[str]:
        return [str(p.relative_to(self._root)) for p in self._root.glob(pattern)]

    async def exists(self, path: str) -> bool:
        return (self._root / path).exists()


class RemoteWorkspaceAdaptor(WorkspaceAdaptor):
    """远程工作区适配器（通过 API）"""

    async def read(self, path: str) -> bytes:
        response = await self._fetch("GET", f"/files/{path}")
        return response.content

    async def write(self, path: str, content: bytes) -> None:
        await self._fetch("PUT", f"/files/{path}", body=content)

    async def list(self, pattern: str = "**/*") -> list[str]:
        response = await self._fetch("GET", f"/files?pattern={pattern}")
        return response.json()

    async def exists(self, path: str) -> bool:
        response = await self._fetch("HEAD", f"/files/{path}")
        return response.status_code == 200

    async def watch(self) -> AsyncIterator[WorkspaceEvent]:
        async for event in self._sse_watch("/events"):
            yield WorkspaceEvent(**event)


class WorkspaceFactory:
    """工作区工厂

    根据类型创建对应的适配器。
    """
    _adaptors: dict[WorkspaceType, type[WorkspaceAdaptor]] = {
        WorkspaceType.LOCAL: LocalWorkspaceAdaptor,
        WorkspaceType.REMOTE: RemoteWorkspaceAdaptor,
    }

    @classmethod
    def register(cls, workspace_type: WorkspaceType, adaptor_cls: type[WorkspaceAdaptor]) -> None:
        cls._adaptors[workspace_type] = adaptor_cls

    @classmethod
    def create(cls, config: WorkspaceConfig) -> WorkspaceAdaptor:
        adaptor_cls = cls._adaptors.get(config.workspace_type)
        if adaptor_cls is None:
            raise ValueError(f"Unknown workspace type: {config.workspace_type}")
        return adaptor_cls(config)
```

**应用场景**:
- 多后端存储支持
- 远程开发环境
- 虚拟工作区

---

## 6. File-based Skill

### 6.1 OpenCode 实现

```typescript
// packages/opencode/src/skill/index.ts
export namespace Skill {
  export const Info = z.object({
    name: z.string(),
    description: z.string(),
    location: z.string(),
    content: z.string(),  // SKILL.md 正文
  })

  // 多来源扫描
  async function loadSkills(state: State, discovery, directory, worktree) {
    // 1. 全局目录 (~/.claude, ~/.agents)
    // 2. 项目目录向上查找
    // 3. OpenCode 内置目录
    // 4. 用户配置路径
    // 5. URL 远程拉取
    await scan(state, root, EXTERNAL_SKILL_PATTERN)
  }
}
```

### 6.2 Polaris 引入方案

**目标位置**: `polaris/kernelone/prompts/skill.py`

```python
# skill.py
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
import re
from typing import Any

# SKILL.md 模式
SKILL_PATTERN = "SKILL.md"
SKILL_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)

@dataclass
class Skill:
    """Skill 定义"""
    name: str
    description: str
    location: str  # 文件路径
    content: str  # 正文内容
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: Path) -> "Skill":
        """从文件加载 Skill"""
        content = path.read_text(encoding="utf-8")

        # 解析 frontmatter
        metadata = {}
        match = SKILL_FRONT_MATTER.match(content)
        if match:
            metadata = cls._parse_frontmatter(match.group(1))
            content = content[match.end():]

        # 提取 name（从文件名或 frontmatter）
        name = metadata.get("name", path.parent.name)

        return cls(
            name=name,
            description=metadata.get("description", ""),
            location=str(path),
            content=content.strip(),
            metadata=metadata,
        )

    @staticmethod
    def _parse_frontmatter(text: str) -> dict[str, Any]:
        """解析 YAML frontmatter"""
        # 简化实现，实际可使用 yaml 库
        result = {}
        for line in text.split("\n"):
            if ":" in line:
                key, value = line.split(":", 1)
                result[key.strip()] = value.strip().strip('"').strip("'")
        return result

    def to_prompt(self) -> str:
        """转换为提示词"""
        return f"# {self.name}\n\n{self.description}\n\n---\n\n{self.content}"


class SkillRegistry:
    """Skill 注册表

    支持多来源发现和加载。
    """
    def __init__(self):
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> None:
        """注册 Skill"""
        self._skills[skill.name] = skill

    def get(self, name: str) -> Skill | None:
        """获取 Skill"""
        return self._skills.get(name)

    def list_all(self) -> list[Skill]:
        """列出所有 Skill"""
        return list(self._skills.values())

    async def discover_from_directory(self, root: Path) -> list[Skill]:
        """从目录发现 Skill"""
        skills = []
        for path in root.rglob(SKILL_PATTERN):
            try:
                skill = Skill.from_file(path)
                self.register(skill)
                skills.append(skill)
            except Exception:
                continue
        return skills

    async def discover_from_paths(
        self,
        paths: list[Path],
        include_builtins: bool = True,
    ) -> None:
        """从多个路径发现 Skill

        路径来源：
        1. 全局目录 (~/.polaris/skills)
        2. 项目目录 (向上查找 SKILL.md)
        3. 用户配置路径
        4. 内置路径
        """
        for path in paths:
            if path.is_dir():
                await self.discover_from_directory(path)
            elif path.is_file():
                skill = Skill.from_file(path)
                self.register(skill)

        # 内置 Skill
        if include_builtins:
            await self._load_builtins()


# 全局注册表
_default_registry: SkillRegistry | None = None

def get_skill_registry() -> SkillRegistry:
    global _default_registry
    if _default_registry is None:
        _default_registry = SkillRegistry()
    return _default_registry
```

**SKILL.md 示例**:
```markdown
---
name: code_review
description: Perform comprehensive code review
version: 1.0
author: polaris
---

# Code Review Skill

You are an expert code reviewer. Analyze the code for:
- Security vulnerabilities
- Performance issues
- Code style consistency
- Best practices

## Review Process

1. Read the target files
2. Identify potential issues
3. Provide actionable recommendations
```

**应用场景**:
- 扩展 Agent 能力
- 领域特定提示词模板
- 可插拔的专家知识

---

## 7. 集成路线图

### Phase 1: 快速落地 (1-2 周)

| 机制 | 优先级 | 工作量 | 风险 |
|------|--------|--------|------|
| Output Truncation | P1 | 1-2 days | 低 |
| File-based Skill | P2 | 2-3 days | 低 |

### Phase 2: 核心增强 (2-4 周)

| 机制 | 优先级 | 工作量 | 风险 |
|------|--------|--------|------|
| Agent Permission | P1 | 1-2 weeks | 中 |
| Instance-scoped State | P0 | 1-2 weeks | 中 |

### Phase 3: 架构演进 (4-8 周)

| 机制 | 优先级 | 工作量 | 风险 |
|------|--------|--------|------|
| Event Sourcing | P0 | 2-3 weeks | 高 |
| Workspace Adaptor | P2 | 2-3 weeks | 中 |

---

## 附录: 参考文件

| 模块 | OpenCode 路径 | Polaris 目标 |
|------|--------------|-----------------|
| Event Sourcing | `src/sync/index.ts` | `polaris/kernelone/events/sourcing/` |
| Instance State | `src/effect/instance-state.ts` | `polaris/kernelone/effect/instance_state.py` |
| Permission | `src/permission/index.ts` | `polaris/kernelone/agent/permission.py` |
| Truncation | `src/tool/tool.ts` | `polaris/kernelone/llm/truncation.py` |
| Workspace | `src/control-plane/workspace.ts` | `polaris/kernelone/storage/adaptor.py` |
| Skill | `src/skill/index.ts` | `polaris/kernelone/prompts/skill.py` |

---

**下一步行动**: 选择 Phase 1 中的机制开始实现，建议从 Output Truncation 开始，因为：
1. 工作量小，见效快
2. 直接解决实际问题（LLM 输出过大）
3. 可独立测试和验证
