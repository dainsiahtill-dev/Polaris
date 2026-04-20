# Role Framework - 角色通用接口框架

Role Framework 提供统一的方式为 Polaris 中的角色（PM、Director、QA 等）实现 FastAPI/CLI/TUI 接口，避免重复代码。

## 架构设计

```
┌─────────────────────────────────────────────────────────────┐
│                      Role Framework                          │
├─────────────────────────────────────────────────────────────┤
│  RoleBase (基类)                                             │
│  ├── RoleCLI (CLI适配器)                                     │
│  ├── RoleFastAPI (FastAPI适配器)                             │
│  └── RoleTUI (TUI适配器 - 预留)                              │
└─────────────────────────────────────────────────────────────┘
                           │
       ┌───────────────────┼───────────────────┐
       ▼                   ▼                   ▼
  ┌─────────┐        ┌──────────┐       ┌──────────┐
  │ PMRole  │        │DirectorRole│      │  QARole  │
  └─────────┘        └──────────┘       └──────────┘
```

## 快速开始

### 1. 创建新角色

继承 `RoleBase` 并实现必要方法：

```python
from core.role_framework import RoleBase, RoleCapability, RoleInfo, RoleState

class MyRole(RoleBase):
    def __init__(self, workspace: str):
        super().__init__(workspace, "myrole")

    def get_info(self) -> RoleInfo:
        return RoleInfo(
            name="myrole",
            version="1.0.0",
            description="My custom role",
            capabilities=[
                RoleCapability.STATUS,
                RoleCapability.EXECUTE,
            ],
        )

    def get_status(self) -> dict:
        return {
            "name": self.role_name,
            "state": self.state.name,
            "initialized": self.is_initialized(),
        }

    def is_initialized(self) -> bool:
        # 检查持久化标记或状态
        return self.state == RoleState.READY

    def initialize(self, **kwargs) -> dict:
        self._set_state(RoleState.INITIALIZING)
        # 执行初始化逻辑
        self._set_state(RoleState.READY)
        return {"success": True, "message": "Initialized"}
```

### 2. 添加 CLI 接口

```python
def main():
    from core.role_framework import RoleCLI

    cli = RoleCLI(
        MyRole,
        prog="myrole",
        description="My role CLI",
    )

    # 添加自定义命令
    cli.add_command(
        "custom",
        lambda role, args: handle_custom(role, args),
        help_text="Custom command",
        arguments=[
            ("--arg1", {"help": "Argument 1"}),
        ],
    )

    return cli.run()

if __name__ == "__main__":
    import sys
    sys.exit(main())
```

### 3. 添加 FastAPI 接口

```python
from core.role_framework import RoleFastAPI

api = RoleFastAPI(
    MyRole,
    host="127.0.0.1",
    port=50002,
    title="MyRole API",
    description="API for MyRole",
)
api.run()
```

## 集成到 Polaris

在 `polaris.py` 中添加：

```python
def run_myrole_cli(args: List[str]) -> int:
    from myrole.myrole_role import main as myrole_main
    sys.argv = ["myrole"] + args
    return myrole_main()

def cmd_myrole(args: argparse.Namespace) -> int:
    """MyRole 命令处理"""
    if args.remainder and args.remainder[0] == "api-server":
        # 处理 api-server 子命令
        from core.role_framework import RoleFastAPI
        from myrole.myrole_role import MyRole
        api = RoleFastAPI(MyRole, port=50002)
        api.run()
        return 0
    else:
        return run_myrole_cli(args.remainder)
```

然后在 parser 中添加：

```python
myrole_parser = subparsers.add_parser("myrole", help="MyRole 功能")
myrole_parser.add_argument("remainder", nargs=argparse.REMAINDER,
                          help="传递给 MyRole CLI 的参数")
myrole_parser.set_defaults(func=cmd_myrole)
```

## 现有角色迁移

### PM 迁移示例

PM 使用适配器模式包装现有的 `PM` 类：

```python
class PMRole(RoleBase):
    def __init__(self, workspace: str):
        super().__init__(workspace, "pm")
        self._pm: Optional[PM] = None

    @property
    def pm(self) -> PM:
        """获取内部 PM 实例"""
        if self._pm is None:
            self._pm = get_pm(str(self.workspace))
        return self._pm

    def get_status(self) -> dict:
        # 复用现有 PM 的功能
        return self.pm.get_status()
```

## API 端点

RoleFastAPI 自动生成以下端点：

| 方法 | 路径 | 描述 |
|------|------|------|
| GET | `/` | API 信息 |
| GET | `/status` | 角色状态 |
| POST | `/init` | 初始化角色 |
| GET | `/health` | 健康检查 |
| GET | `/capabilities` | 能力列表 |
| POST | `/run` | 运行角色（如支持） |

## 能力系统

使用 `RoleCapability` 声明角色能力：

```python
RoleInfo(
    name="director",
    capabilities=[
        RoleCapability.STATUS,      # 支持状态查询
        RoleCapability.EXECUTE,     # 支持执行操作
        RoleCapability.QUERY,       # 支持查询操作
    ],
)
```

检查能力：

```python
if role.has_capability(RoleCapability.EXECUTE):
    role.execute()
```

## 状态管理

RoleBase 提供状态管理：

```python
# 设置状态
self._set_state(RoleState.RUNNING)

# 监听状态变化
def on_state_change(new_state):
    print(f"State changed to {new_state}")

role.add_state_listener(on_state_change)
```

## 最佳实践

1. **初始化持久化**: 使用文件标记或数据库存储初始化状态
2. **错误处理**: 在初始化失败时设置 `RoleState.ERROR`
3. **能力声明**: 准确声明角色能力，避免接口不匹配
4. **工作区隔离**: 每个角色实例对应一个工作区目录

## 故障排除

### ImportError: No module named 'role_framework'

确保 PYTHONPATH 包含 `src/backend/core`。

### 状态不持久化

检查 `is_initialized()` 方法是否检查持久化标记文件。

### FastAPI 启动失败

安装依赖：`pip install fastapi uvicorn`
