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
# 仅用于 main 开发实例；bench/临时项目实例不得占用 9420。
cd src/backend && python -m polaris.delivery.server --host 127.0.0.1 --port 9420

# 后端实例运行（main 开发实例；会注册到 Launcher）
cd src/backend
KERNELONE_CONTEXT_ADMIN_ENABLED=1 python -m polaris.delivery.cli.backend serve \
  --workspace /path/to/workspace \
  --runtime-root /path/to/workspace/runtime \
  --port 9420 \
  --token polaris-local-dev \
  --frontend-port 6688 \
  --register-instance \
  --instance-id main \
  --instance-name "Main Polaris Dev" \
  --kind development
# 单人调试后端热重载时才追加 --reload；多 Agent/bench 观测阶段不要默认开启。

# Web 前端单独运行（绑定当前后端实例）
VITE_POLARIS_BACKEND_URL=http://127.0.0.1:9420 \
VITE_POLARIS_BACKEND_TOKEN=polaris-local-dev \
VITE_POLARIS_INSTANCE_ID=main \
VITE_POLARIS_WORKSPACE=/path/to/workspace \
npm run dev:renderer -- --host 127.0.0.1 --port 6688

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


## 内部批次与后端源码编辑互斥

原生 `run_factory_bench.py` 进入真实执行栈前取得源码共享锁并持有到进程退出。多个只读批次可按各自的资格要求并存。`--dry-run`、帮助、已有报告的对比与指纹查询可以在源码编辑期间执行。需要稳定资格证据时，继续保留原运行的启动指纹、独立验收和退出结算。

在仓库根执行以下命令。第一条返回当前可编辑状态、源码根与可定位的运行 PID。第二条把整个前台编辑命令放入独占期。快照查询与编辑之间可能有新批次启动，editor 必须使用第二条取得独占锁。命令行工具、补丁脚本和编辑器都应前台运行到实际写盘完成。

```bash
rtk proxy python src/backend/scripts/factory_bench/source_freeze.py status
rtk proxy python src/backend/scripts/factory_bench/source_freeze.py edit -- rtk proxy python /path/to/edit_script.py
```

两个入口争用时立即返回 75。编辑命令正常运行时保留其退出码。可用 `--backend-root /absolute/checkout/src/backend` 在子命令前指定源码根。工具以真实目录识别别名。普通工程运行、生成项目和已有批次报告均由各自原入口管理。升级前的 native Python 入口及已知 `run_one.sh`、`run_march.sh` 会通过只读进程检查发现。此前已在运行且未持锁的脚本只能提供瞬时证据。

Linux、WSL 与支持目录 `flock` 的 POSIX 系统直接锁定目录 inode，多个工作区和不同 HOME 不会改变锁的身份。前台编辑进程继承锁的描述符，正常退出和崩溃均由内核释放。原生 Windows 使用 checkout 的 `.polaris/internal-bench-source-locks` 下的 SQLite 回滚模式共享读事务及独占写事务，前台命令由持锁父进程等待。Windows 编辑期间强杀持锁父进程时，应先确认编辑子进程已退出。不同操作系统同时挂载同一源码树、网络文件系统及脱离前台的后台编辑无法提供本地锁互斥保证；请使用同一主机内核和本地 checkout。所有编辑器须消费同一入口；直接写盘仍由资格审计的指纹和冻结观察判定。
