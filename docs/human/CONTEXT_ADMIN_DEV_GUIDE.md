# Context Admin 端点开发指南

## 概述

Context admin 端点提供对 `runtime/contexts/` 存储树的监控和管理功能。这些端点默认禁用，需要显式启用。

## 端点列表

| 端点 | 方法 | 功能 | 需要认证 |
|------|------|------|----------|
| `/v2/context/stats` | GET | 轻量级统计信息 | 是 |
| `/v2/context/admin/stats` | GET | 详细统计信息（包含sweep报告） | 是 |
| `/v2/context/admin/sweep` | POST | 强制执行retention sweep | 是 |

## 环境变量配置

### 启用 Admin 端点

设置环境变量 `KERNELONE_CONTEXT_ADMIN_ENABLED=1` 以启用 admin 端点。

支持的值（不区分大小写）：
- `1`
- `true`
- `yes`
- `on`

### 环境变量优先级

1. `KERNELONE_CONTEXT_ADMIN_ENABLED`（直接设置）
2. `POLARIS_CONTEXT_ADMIN_ENABLED`（自动映射到 `KERNELONE_CONTEXT_ADMIN_ENABLED`）

如果两者都设置，`KERNELONE_*` 优先。

## 开发环境配置

### 方法 1：环境变量（推荐）

```bash
# Linux/macOS
export KERNELONE_CONTEXT_ADMIN_ENABLED=1

# Windows PowerShell
$env:KERNELONE_CONTEXT_ADMIN_ENABLED = "1"

# Windows CMD
set KERNELONE_CONTEXT_ADMIN_ENABLED=1
```

### 方法 2：使用 .env 文件

复制 `.env.example` 到 `.env`，确保包含：

```env
KERNELONE_CONTEXT_ADMIN_ENABLED=1
```

### 方法 3：启动脚本自动设置

开发环境的启动脚本已默认启用：

- `infrastructure/scripts/run-web.js` - 设置为 `"1"`
- `infrastructure/scripts/run-dev.js` - 设置为 `"1"`
- `src/electron/main.cjs` - 开发环境默认 `"1"`

## 启动后端服务

```bash
# 基本启动
python -m polaris.delivery.server --host 127.0.0.1 --port 49977

# 带 workspace 参数
python -m polaris.delivery.server --host 127.0.0.1 --port 49977 --workspace /path/to/workspace

# 带调试日志
python -m polaris.delivery.server --host 127.0.0.1 --port 49977 --log-level debug
```

## 验证 Admin 端点

### 检查健康状态

```bash
curl -H "Authorization: Bearer <token>" http://localhost:49977/v2/health
```

响应中的 `context_admin` 字段显示状态：

```json
{
  "context_admin": {
    "enabled": true,
    "endpoints": ["/v2/context/admin/stats", "/v2/context/admin/sweep"],
    "reason": "Context admin surface is enabled",
    "env_var": "KERNELONE_CONTEXT_ADMIN_ENABLED",
    "current_value": "1"
  }
}
```

### 测试 Admin Stats 端点

```bash
curl -H "Authorization: Bearer <token>" http://localhost:49977/v2/context/admin/stats
```

### 测试 Admin Sweep 端点

```bash
curl -X POST -H "Authorization: Bearer <token>" http://localhost:49977/v2/context/admin/sweep
```

## 故障排除

### 端点返回 404

**原因**：Admin 端点未启用

**解决方案**：
1. 检查环境变量：`echo $KERNELONE_CONTEXT_ADMIN_ENABLED`
2. 确保设置为 `1`、`true`、`yes` 或 `on`
3. 重启后端服务

### 环境变量未生效

**原因**：环境变量未正确传递到后端进程

**解决方案**：
1. 在启动命令前设置环境变量
2. 使用 `.env` 文件
3. 检查 `_env_compat.py` 中的映射

## 代码结构

### 关键文件

- `src/backend/polaris/delivery/http/v2/context.py` - Admin 端点实现
- `src/backend/polaris/_env_compat.py` - 环境变量映射
- `src/backend/polaris/delivery/http/routers/system.py` - 健康检查端点
- `src/backend/polaris/delivery/http/schemas/common.py` - 响应模型

### 环境变量处理流程

1. 用户设置 `POLARIS_CONTEXT_ADMIN_ENABLED` 或 `KERNELONE_CONTEXT_ADMIN_ENABLED`
2. `_env_compat.py` 将 `POLARIS_*` 映射到 `KERNELONE_*`
3. `context.py` 中的 `_admin_enabled()` 函数读取环境变量
4. 根据环境变量值决定是否启用 admin 端点

## 安全注意事项

- Admin 端点默认禁用，需要显式启用
- 所有 admin 端点都需要认证
- 生产环境应谨慎启用 admin 端点
- Sweep 操作会删除文件，应谨慎使用

## 相关文档

- [API 文档](../API_V2_QUICK_REFERENCE.md)
- [环境变量迁移指南](../env-migration.md)
- [Context OS 架构](../architecture/context-os.md)