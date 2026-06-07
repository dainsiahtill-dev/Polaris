# Polaris CLI 使用指南

## 快速开始

### 安装控制台脚本（推荐）

```bash
# 在项目根目录
pip install -e .

# 安装后可用三个控制台脚本（见 pyproject.toml [project.scripts]）：
#   polaris  = polaris.delivery.server:main   （后端启动器）
#   pm       = polaris.delivery.cli.pm.cli:main
#   director = polaris.delivery.cli.director.cli_thin:main
polaris --help
pm --help
director --help
```

### 开发环境一键初始化

```bash
# 在项目根目录（自动安装 Node + Python 依赖）
npm run setup:dev
```

## 可用命令

> 说明：`polaris` 控制台脚本是单一后端启动器，仅接受 `--host/--port/--workspace` 等标志，
> **没有 `init/status/pm/director/backend` 等子命令**。PM 与 Director 是各自独立的控制台脚本 `pm` / `director`。

### 启动后端（polaris）

```bash
# 默认启动
polaris

# 指定端口 / 主机 / 工作区
polaris --port 49977
polaris --host 0.0.0.0 --port 49977
polaris --workspace /path/to/proj

# 等价地，也可直接运行入口脚本
python src/backend/server.py --host 127.0.0.1 --port 49977
```

### PM 项目管理（pm）

```bash
# 运行 PM 编排（可选择联动 Director）
pm --workspace . --iterations 1
pm --workspace . --run-director --director-iterations 1
```

### Director 任务执行（director）

```bash
# 运行 Director 执行任务
director --workspace . --iterations 1
```

### 开发模式

```bash
# 首次执行会自动做 predev 依赖自检
npm run dev
```

## 环境变量

复制 `.env.example` 为 `.env` 并修改：

```bash
cp .env.example .env
```

常用环境变量：

```env
KERNELONE_WORKSPACE=/path/to/polaris
KERNELONE_BACKEND_PORT=49977
KERNELONE_PM_PROVIDER=minimax-1771264739
KERNELONE_PM_MODEL=MiniMax-M2.5
```

## 使用示例

### 完整工作流程

```bash
# 1. 启动后端（终端1）
polaris --port 49977

# 2. 运行 PM 编排（终端2）
pm --workspace . --iterations 1

# 3. 运行 Director 执行任务
director --workspace . --iterations 1
```

## 故障排除

### ModuleNotFoundError: No module named 'polaris'

确保已执行 `pip install -e .`，使控制台脚本与 `polaris` 包正确安装到当前环境。

### 端口被占用

```bash
# 查找占用端口的进程
# Windows
netstat -ano | findstr :49977

# Linux/macOS
lsof -i :49977
```

## API 端点

启动 `polaris` 后端后，访问：

- `GET http://localhost:49977/docs` - Swagger UI
- `GET http://localhost:49977/redoc` - ReDoc 文档
