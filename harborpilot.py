#!/usr/bin/env python3
"""Polaris - 统一项目入口

统一CLI入口，支持所有角色和界面：
- PM: 项目管理 (CLI, API Server)
- Director: 任务执行
- Backend: FastAPI 服务
- TUI: 终端界面 (未来)
- Dev: 开发模式

用法:
    python polaris.py <command> [options]
    hp <command> [options]  (如果已安装)

命令:
    pm          PM 项目管理
    director    Director 任务执行
    backend     启动 FastAPI 后端
    dev         开发模式 (前后端)
    init        初始化项目
    status      查看项目状态
"""

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.absolute()
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
FRONTEND_DIR = PROJECT_ROOT / "src" / "frontend"
PM_DIR = BACKEND_DIR / "scripts" / "pm"
DIRECTOR_DIR = BACKEND_DIR / "scripts" / "director"

# 自动设置 PYTHONPATH
def setup_pythonpath():
    """设置 Python 路径，确保所有模块可导入"""
    paths = [
        str(BACKEND_DIR / "scripts"),  # 添加 scripts 目录以支持 pm.xxx 导入
        str(BACKEND_DIR / "scripts" / "pm"),
        str(BACKEND_DIR),
        str(PROJECT_ROOT),
    ]
    for p in paths:
        if p not in sys.path:
            sys.path.insert(0, p)
    # 设置环境变量供子进程使用
    current = os.environ.get("PYTHONPATH", "")
    new_paths = os.pathsep.join(paths)
    os.environ["PYTHONPATH"] = new_paths + (os.pathsep + current if current else "")

setup_pythonpath()


def run_pm_cli(args: List[str]) -> int:
    """运行 PM CLI"""
    from pm.pm_cli import main as pm_main
    sys.argv = ["pm"] + args
    return pm_main()


def run_pm_api_server(args: List[str]) -> int:
    """运行 PM API Server"""
    from pm.api_server import main as api_main
    sys.argv = ["api_server"] + args
    return api_main()


def run_director_cli(args: List[str]) -> int:
    """运行 Director CLI"""
    director_script = DIRECTOR_DIR / "main.py"
    if director_script.exists():
        cmd = [sys.executable, str(director_script)] + args
        return subprocess.run(cmd).returncode
    else:
        print(f"Director 脚本未找到: {director_script}")
        return 1


def run_backend(host: str = "127.0.0.1", port: int = 49977, reload: bool = False) -> int:
    """启动 FastAPI 后端"""
    try:
        import uvicorn
        from app.main import create_app
        from app.state import AppState, Auth

        print(f"启动 Polaris 后端...")
        print(f"  Host: {host}")
        print(f"  Port: {port}")
        print(f"  Reload: {reload}")
        print()

        state = AppState()
        state.settings.workspace = str(PROJECT_ROOT)
        auth = Auth(jwt_secret="dev-secret")
        app = create_app(state, auth, cors_origins=["*"])

        uvicorn.run(app, host=host, port=port, reload=reload)
        return 0
    except ImportError as e:
        print(f"启动失败，缺少依赖: {e}")
        print("请安装依赖: pip install fastapi uvicorn")
        return 1
    except Exception as e:
        print(f"启动失败: {e}")
        return 1


def run_dev_mode() -> int:
    """开发模式：同时启动前后端"""
    print("=== Polaris 开发模式 ===")
    print()

    # 使用 npm run dev 启动全栈
    try:
        cmd = ["npm", "run", "dev"]
        print(f"运行: {' '.join(cmd)}")
        return subprocess.run(cmd, cwd=PROJECT_ROOT).returncode
    except FileNotFoundError:
        print("npm 未找到，请确保 Node.js 已安装")
        return 1


def cmd_pm(args: argparse.Namespace) -> int:
    """PM 命令处理"""
    if args.pm_command == "api-server":
        return run_pm_api_server(args.remainder)
    else:
        return run_pm_cli([args.pm_command] + args.remainder)


def run_director_role_cli(args: List[str]) -> int:
    """运行 Director Role CLI"""
    from director.director_role import main as director_main
    sys.argv = ["director"] + args
    return director_main()


def run_director_role_api(host: str, port: int) -> int:
    """运行 Director Role API Server"""
    try:
        from core.role_framework import RoleFastAPI
        from director.director_role import DirectorRole

        api = RoleFastAPI(
            DirectorRole,
            host=host,
            port=port,
            workspace=str(PROJECT_ROOT),
            title="Director API",
            description="Polaris Director - Task execution API",
        )
        api.run()
        return 0
    except ImportError as e:
        print(f"Failed to start Director API: {e}")
        return 1


def cmd_director(args: argparse.Namespace) -> int:
    """Director 命令处理"""
    # 检查是否是 api-server 子命令
    if args.remainder and args.remainder[0] == "api-server":
        # 解析 api-server 参数
        api_args = argparse.Namespace(
            host="127.0.0.1",
            port=50001,
            reload=False,
        )
        for i, arg in enumerate(args.remainder[1:]):
            if arg == "--host" or arg == "-H":
                api_args.host = args.remainder[i + 2]
            elif arg == "--port" or arg == "-p":
                api_args.port = int(args.remainder[i + 2])
            elif arg == "--reload":
                api_args.reload = True
        return run_director_role_api(api_args.host, api_args.port)
    else:
        return run_director_role_cli(args.remainder)


def cmd_backend(args: argparse.Namespace) -> int:
    """Backend 命令处理"""
    return run_backend(
        host=args.host,
        port=args.port,
        reload=args.reload
    )


def cmd_dev(args: argparse.Namespace) -> int:
    """Dev 命令处理"""
    return run_dev_mode()


def cmd_init(args: argparse.Namespace) -> int:
    """Init 命令处理 - 初始化项目"""
    print("=== 初始化 Polaris 项目 ===")
    print()

    # 初始化 PM
    try:
        from pm.pm_integration import get_pm
        pm = get_pm(str(PROJECT_ROOT))
        if not pm.is_initialized() or args.force:
            result = pm.initialize(
                project_name=args.project_name or "Polaris Project",
                description=args.description or "Project managed by Polaris"
            )
            print(f"✓ PM 系统初始化完成")
            print(f"  项目: {result['project_name']}")
            print(f"  工作区: {result['workspace']}")
        else:
            print("✓ PM 系统已初始化")
    except Exception as e:
        print(f"✗ PM 初始化失败: {e}")
        return 1

    print()
    print("项目初始化完成!")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Status 命令处理 - 查看项目状态"""
    print("=== Polaris 项目状态 ===")
    print()

    try:
        from pm.pm_integration import get_pm
        pm = get_pm(str(PROJECT_ROOT))

        if pm.is_initialized():
            status = pm.get_status()
            print(f"项目: {status['project']}")
            print(f"版本: {status['version']}")
            print()

            task_stats = status['stats']['tasks']
            print("任务统计:")
            print(f"  总数: {task_stats.get('total', 0)}")
            print(f"  已完成: {task_stats.get('completed', 0)}")
            print(f"  进行中: {task_stats.get('in_progress', 0)}")
            print(f"  待处理: {task_stats.get('pending', 0)}")
            print(f"  完成率: {task_stats.get('completion_rate', 0) * 100:.1f}%")
            print()

            health = pm.analyze_project_health()
            print(f"健康状态: {health['overall'].upper()}")
        else:
            print("项目未初始化")
            print("运行: python polaris.py init")
    except Exception as e:
        print(f"获取状态失败: {e}")
        return 1

    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """主入口"""
    parser = argparse.ArgumentParser(
        prog="polaris",
        description="Polaris - 统一项目入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 初始化项目
  python polaris.py init

  # PM 管理
  python polaris.py pm status
  python polaris.py pm document list
  python polaris.py pm task history --director
  python polaris.py pm api-server --port 49980

  # Director 执行
  python polaris.py director --workspace . --iterations 1

  # 启动后端
  python polaris.py backend --port 49977

  # 开发模式
  python polaris.py dev
        """
    )

    parser.add_argument(
        "--version",
        action="version",
        version="%(prog)s 1.0.0"
    )

    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # init 命令
    init_parser = subparsers.add_parser("init", help="初始化项目")
    init_parser.add_argument("--project-name", "-n", default="", help="项目名称")
    init_parser.add_argument("--description", "-d", default="", help="项目描述")
    init_parser.add_argument("--force", "-f", action="store_true", help="强制重新初始化")
    init_parser.set_defaults(func=cmd_init)

    # status 命令
    status_parser = subparsers.add_parser("status", help="查看项目状态")
    status_parser.set_defaults(func=cmd_status)

    # pm 命令
    pm_parser = subparsers.add_parser("pm", help="PM 项目管理")
    pm_parser.add_argument("pm_command", nargs="?", default="status",
                          choices=["init", "status", "health", "document", "task",
                                  "requirement", "api-server"],
                          help="PM 子命令")
    pm_parser.add_argument("remainder", nargs=argparse.REMAINDER,
                          help="传递给 PM CLI 的参数")
    pm_parser.set_defaults(func=cmd_pm)

    # director 命令
    director_parser = subparsers.add_parser("director", help="Director 任务执行")
    director_parser.add_argument("remainder", nargs=argparse.REMAINDER,
                                help="传递给 Director CLI 的参数")
    director_parser.set_defaults(func=cmd_director)

    # backend 命令
    backend_parser = subparsers.add_parser("backend", help="启动 FastAPI 后端")
    backend_parser.add_argument("--host", "-H", default="127.0.0.1", help="绑定地址")
    backend_parser.add_argument("--port", "-p", type=int, default=49977, help="绑定端口")
    backend_parser.add_argument("--reload", action="store_true", help="启用热重载")
    backend_parser.set_defaults(func=cmd_backend)

    # dev 命令
    dev_parser = subparsers.add_parser("dev", help="开发模式 (前后端)")
    dev_parser.set_defaults(func=cmd_dev)

    # tui 命令 (预留)
    tui_parser = subparsers.add_parser("tui", help="启动 TUI 界面 (预留)")
    tui_parser.set_defaults(func=lambda args: print("TUI 界面即将推出"))

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
