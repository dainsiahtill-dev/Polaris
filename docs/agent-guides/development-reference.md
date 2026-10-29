# Polaris 开发与验证命令

本文从仓库根 `CLAUDE.md` 原样迁出，保留完整约束。适用任务须在修改或派工前读取；与更细粒度架构规范冲突时遵循原有优先级。

## 3) 常用命令
```bash
# 全栈开发（Electron + Backend + Frontend）
npm run dev

# 前端 / Electron 单独运行
npm run dev:renderer
npm run dev:electron

# 后端单独运行（旧根 src/backend/server.py shim 已退役；唯一服务入口为 polaris.delivery.server）
# 仅用于 main 开发实例；bench/临时项目实例不得占用 49977。
cd src/backend && python -m polaris.delivery.server --host 127.0.0.1 --port 49977

# 后端实例运行（main 开发实例；会注册到 Launcher）
cd src/backend
KERNELONE_CONTEXT_ADMIN_ENABLED=1 python -m polaris.delivery.cli.backend serve \
  --workspace /path/to/workspace \
  --runtime-root /path/to/workspace/runtime \
  --port 49977 \
  --token polaris-local-dev \
  --frontend-port 5173 \
  --register-instance \
  --instance-id main \
  --instance-name "Main Polaris Dev" \
  --kind development
# 单人调试后端热重载时才追加 --reload；多 Agent/bench 观测阶段不要默认开启。

# Web 前端单独运行（绑定当前后端实例）
VITE_POLARIS_BACKEND_URL=http://127.0.0.1:49977 \
VITE_POLARIS_BACKEND_TOKEN=polaris-local-dev \
VITE_POLARIS_INSTANCE_ID=main \
VITE_POLARIS_WORKSPACE=/path/to/workspace \
npm run dev:renderer -- --host 127.0.0.1 --port 5173

# PM CLI (项目管理) - 控制台脚本 pm = polaris.delivery.cli.pm.cli:main
pm --workspace <repo> --run-director --director-iterations 1

# Director CLI (推荐) - 控制台脚本 director = polaris.delivery.cli.director.cli_thin:main
director --workspace <repo> --iterations 1

# Architect CLI (架构设计 - 交互式)
python -m polaris.cells.architect.design.internal.architect_cli --mode interactive --workspace <repo>

# Chief Engineer CLI (技术分析 - 交互式)
python -m polaris.cells.chief_engineer.blueprint.internal.chief_engineer_cli --mode interactive --workspace <repo>

# 统一角色对话 API (所有 5 个角色)
# POST /v2/role/{pm|architect|chief_engineer|director|qa}/chat

# V2 API 端点
# PM: /v2/pm/*
# Director: /v2/director/*
# Role Chat: /v2/role/{role}/chat
```

## 4) 验证命令（按改动面最小执行）
```bash
# 前端改动
npm run typecheck
npm run lint
npm run test

# Electron E2E (Playwright；testDir=src/backend/polaris/tests/electron)
npm run test:e2e
# 专项 e2e 入口另有 test:e2e:smoke / :runtime / :settings / :acceptance / :real-flow /
# :dual-full-chain / :production-stability / :connectivity / :panel / :task / :semantic

# Python/后端改动
pytest
pytest src/backend/polaris/tests

# 工厂冒烟（可选）
python scripts/run_factory_e2e_smoke.py --workspace .
```

