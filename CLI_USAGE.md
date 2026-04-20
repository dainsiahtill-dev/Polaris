# Polaris CLI 使用指南

## 快速开始

### 方式 1: 直接运行（无需安装）

```bash
# 在项目根目录
python polaris.py --help

# 或使用快捷脚本（Windows）
hp.bat --help

# 或使用快捷脚本（PowerShell）
.\hp.ps1 --help

# 或使用快捷脚本（Linux/macOS）
./hp.sh --help
```

### 方式 2: 安装为命令（推荐）

```bash
# 在项目根目录
pip install -e .

# 安装后可直接使用
polaris --help
# 或简写
hp --help
hpm --help
```

### 方式 3: 开发环境一键初始化（推荐）

```bash
# 在项目根目录（自动安装 Node + Python 依赖）
npm run setup:dev
```

## 可用命令

### 项目初始化与状态

```bash
# 初始化项目
polaris init
polaris init --project-name "My Project" --description "A test project"

# 查看项目状态
polaris status
```

### PM 项目管理

```bash
# PM 状态
polaris pm status

# 文档管理
polaris pm document list
polaris pm document list --type requirements
polaris pm document show docs/product/requirements.md
polaris pm document list --pattern "*.md" --limit 20

# 任务管理
polaris pm task list
polaris pm task list --status pending
polaris pm task history
polaris pm task history --director                    # 查看派发给 Director 的任务
polaris pm task history --director --iteration 5      # 查看第5次迭代的任务

# 需求管理
polaris pm requirement list
polaris pm requirement list --status pending

# 启动 PM API 服务器
polaris pm api-server
polaris pm api-server --port 49980 --host 0.0.0.0
```

### Director 任务执行

```bash
# Director 初始化
polaris director init

# 查看 Director 状态
polaris director status
polaris director health

# 运行 Director 执行任务
polaris director run --iterations 1
polaris director execute --task-path tasks.json --iterations 5 --timeout 300

# 启动 Director API 服务器
polaris director api-server --port 50001
```

### FastAPI 后端

```bash
# 启动后端服务
polaris backend
polaris backend --port 49977
polaris backend --host 0.0.0.0 --port 49977 --reload
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
POLARIS_WORKSPACE=C:\Users\dains\Documents\GitLab\polaris
POLARIS_BACKEND_PORT=49977
POLARIS_PM_API_PORT=49980
POLARIS_PM_PROVIDER=minimax-1771264739
POLARIS_PM_MODEL=MiniMax-M2.5
```

## 使用示例

### 完整工作流程

```bash
# 1. 初始化项目
polaris init --project-name "MyApp"

# 2. 查看状态
polaris status

# 3. 创建文档
echo "# Requirements\n\n- Feature A\n- Feature B" > docs/requirements.md

# 4. 查看文档列表
polaris pm document list

# 5. 启动 PM API 服务器（终端1）
polaris pm api-server --port 49980

# 6. 运行 Director 执行任务（终端2）
polaris director --workspace . --iterations 1

# 7. 查看任务历史
polaris pm task history --director
```

### 启动 FastAPI 后端

```bash
# 基础启动
polaris backend

# 指定端口
polaris backend --port 8080

# 开发模式（热重载）
polaris backend --reload
```

## 故障排除

### ModuleNotFoundError: No module named 'pm'

确保在项目根目录运行命令，或者使用 `hp.bat` / `hp.sh` 脚本。

### 端口被占用

```bash
# 查找占用端口的进程
# Windows
netstat -ano | findstr :49977

# Linux/macOS
lsof -i :49977
```

### 权限问题

```bash
# Windows - 使用 PowerShell 执行策略
Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser

# 然后运行
.\hp.ps1 status
```

## API 端点

启动 `polaris backend` 后，访问：

- `GET http://localhost:49977/docs` - Swagger UI
- `GET http://localhost:49977/redoc` - ReDoc 文档

PM API 端点（启动 `polaris pm api-server` 后）：

- `GET http://localhost:49980/documents` - 列出文档
- `GET http://localhost:49980/tasks` - 列出任务
- `GET http://localhost:49980/tasks/director` - Director 任务历史
