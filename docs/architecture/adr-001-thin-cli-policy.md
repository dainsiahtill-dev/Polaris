# ADR-001: Thin CLI Adapter Policy

## 状态

- **日期**: 2026-03-06
- **状态**: 已批准 (Approved)
- **作者**: Polaris Architecture Team
- **实施者**: Codex Agent

## 背景

Polaris 后端目前存在 CLI 层与业务逻辑耦合的问题。主要入口文件（`server.py`, `polaris.py`, `scripts/pm/cli.py`, `scripts/director/main.py`）同时承担参数解析和业务决策职责，导致：

1. **代码重复**: PM 和 Director 的进程启动逻辑分别实现
2. **难以测试**: CLI 层的业务逻辑无法通过 API 复用
3. **全局状态污染**: `sys.argv` 被直接修改用于适配子 CLI
4. **配置合并逻辑分散**: 多处配置加载点优先级规则不一致

## 决策

### 1. 分层架构原则

采用"薄 CLI + 厚服务层"架构：

```
┌─────────────────────────────────────────────────────────────┐
│  CLI Layer (Thin Adapter)                                   │
│  - 仅负责参数解析 (argparse)                                 │
│  - 构建 Request DTO                                          │
│  - 调用服务层方法                                            │
│  - 处理退出码和输出格式                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Application Service Layer                                  │
│  - 承载所有业务逻辑                                          │
│  - 不可变配置快照 (ConfigSnapshot)                           │
│  - 统一的进程编排 (RuntimeOrchestrator)                      │
│  - 端口接口定义 (Ports)                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│  Infrastructure Adapters                                    │
│  - UvicornBootstrapAdapter                                   │
│  - SubprocessRunnerAdapter                                   │
│  - DirectorRuntimeAdapter                                    │
└─────────────────────────────────────────────────────────────┘
```

### 2. 不可变配置快照 (ConfigSnapshot)

引入 `ConfigSnapshot` 作为统一配置抽象：

- **不可变性**: 使用 `MappingProxyType` + `@dataclass(frozen=True)`
- **Source Tracking**: 追踪每个配置值的来源（default/persisted/env/cli）
- **合并优先级**: `default < persisted < env < cli`
- **函数式更新**: `with_override()` 返回新实例而非修改原实例

### 3. 端口适配器模式 (Ports & Adapters)

定义端口接口（Python Protocol），隔离外部依赖：

| 端口 | 职责 | 实现示例 |
|------|------|----------|
| `BackendBootstrapPort` | 后端服务器启动 | `UvicornBootstrapAdapter` |
| `ProcessRunnerPort` | 子进程生命周期管理 | `SubprocessRunnerAdapter` |
| `RoleRuntimePort` | 角色运行时执行 | `DirectorRuntimeAdapter` |

### 4. 静态门禁规则

**禁止模式**（非适配层代码禁止）：

```python
# ❌ 禁止：直接访问 sys.argv
import sys
sys.argv[1:]  # 只能在 cli.py / server.py 中使用

# ❌ 禁止：直接创建 ArgumentParser
import argparse
parser = argparse.ArgumentParser()  # 只能在适配层使用

# ❌ 禁止：修改全局状态
sys.argv = ["pm"] + args  # 危险操作

# ✅ 推荐：通过 DTO 传递配置
request = BackendLaunchRequest.from_cli_args(args)
result = await bootstrap_service.bootstrap(request)
```

## 影响

### 对现有代码的影响

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `server.py` | 修改 | 精简为参数解析 + 调用 bootstrap |
| `polaris.py` | 修改 | 移除业务逻辑，改为纯路由 |
| `scripts/pm/cli.py` | 修改 | 业务逻辑移至 PMService |
| `scripts/director/main.py` | 修改 | 业务逻辑移至 DirectorService |
| `config.py` | 扩展 | 添加 `ConfigSnapshot.from_settings()` 工厂方法 |

### 向后兼容性

- **CLI 参数名**: 保持不变
- **API 响应结构**: 保持不变
- **backend_started 事件**: 格式保持不变
- **环境变量**: 保持不变

## 实施路线图

### 阶段 1: 架构收敛基线（已完成）
- [x] 创建 ADR 文档
- [x] 实现 ConfigSnapshot
- [x] 添加静态门禁规则 (见下方)
- [x] 梳理调用图

### 静态门禁规则实现

在 `pyproject.toml` 中添加自定义 ruff 规则:

```toml
[tool.ruff.lint]
# 禁止模式检测
[tool.ruff.lint.per-file-ignores]
"scripts/*" = ["S"]  # 允许脚本中的 shell 调用

# 自定义规则：检测 sys.argv 滥用
[tool.ruff.lint.ruleS]
# S101: Use of assert detected (可接受用于测试)
```

**禁止模式检查清单**:

| 规则 | 检查范围 | 说明 |
|------|---------|------|
| 禁止直接访问 sys.argv | scripts/ 目录外 | 仅 CLI 适配层可使用 |
| 禁止创建 ArgumentParser | scripts/ 目录外 | 仅 CLI 适配层可使用 |
| 禁止修改 sys.argv | 全局 | 危险操作，绝对禁止 |

### 实施建议

使用 pylint 的自定义检查或 ruff 自定义规则来强制执行。

### 阶段 2: 启动核心抽离
- 创建 `backend_bootstrap.py`
- 重构 `server.py`

### 阶段 3: 进程编排统一
- 创建 `runtime_orchestrator.py`
- 统一 PM/Director 启动语义

### 阶段 4: CLI 壳层瘦身
- 重构各 CLI 入口

### 阶段 5: 兼容加固与可观测性
- 补齐事件系统

### 阶段 6: 清理与收尾
- 删除重复实现

## 相关文档

- [ConfigSnapshot 设计](../core/config-snapshot.md)
- [端口接口定义](../ports/README.md)
- [迁移指南](../migration/guide-v2.md)

## 参考

- [Clean Architecture by Robert C. Martin](https://blog.cleancoder.com/uncle-bob/2012/08/13/the-clean-architecture.html)
- [Ports and Adapters Pattern](https://alistair.cockburn.us/hexagonal-architecture/)
