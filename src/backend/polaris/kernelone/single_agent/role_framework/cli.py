"""Role Framework CLI - CLI接口适配器

自动生成角色的命令行接口。
"""

import argparse
import logging
from collections.abc import Callable
from typing import Any

from .base import RoleBase

logger = logging.getLogger(__name__)


class RoleCLI:
    """角色CLI适配器

    为RoleBase子类自动生成CLI接口。

    用法:
        cli = RoleCLI(MyRole)
        cli.run()
    """

    def __init__(
        self,
        role_class: type[RoleBase],
        prog: str | None = None,
        description: str | None = None,
    ) -> None:
        self.role_class = role_class
        self.prog = prog or role_class.__name__.lower()
        self.description = description or f"{role_class.__name__} CLI"
        self._custom_commands: dict[str, dict[str, Any]] = {}

    def add_command(
        self,
        name: str,
        func: Callable[[RoleBase, argparse.Namespace], int],
        help_text: str = "",
        arguments: list[tuple[str, dict[str, Any]]] | None = None,
    ) -> None:
        """添加自定义命令

        Args:
            name: 命令名称
            func: 命令函数，签名应为 func(role: RoleBase, args: argparse.Namespace) -> int
            help_text: 帮助文本
            arguments: 参数列表，每个元素为 (name, kwargs) 元组
        """
        self._custom_commands[name] = {
            "func": func,
            "help": help_text,
            "arguments": arguments or [],
        }

    def _create_parser(self) -> argparse.ArgumentParser:
        """创建参数解析器"""
        parser = argparse.ArgumentParser(
            prog=self.prog,
            description=self.description,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )

        parser.add_argument(
            "--workspace",
            "-w",
            default=".",
            help="Workspace directory (default: current directory)",
        )
        parser.add_argument(
            "--version",
            action="version",
            version="%(prog)s 1.0.0",
        )

        subparsers = parser.add_subparsers(dest="command", help="Commands")

        # init 命令
        init_parser = subparsers.add_parser("init", help="Initialize the role")
        init_parser.add_argument("--name", "-n", help="Project name")
        init_parser.add_argument("--description", "-d", help="Project description")
        init_parser.add_argument("--force", "-f", action="store_true", help="Force reinitialize")

        # status 命令
        subparsers.add_parser("status", help="Show status")

        # health 命令
        subparsers.add_parser("health", help="Show health information")

        # run 命令 (如果角色支持执行)
        run_parser = subparsers.add_parser("run", help="Run the role")
        run_parser.add_argument("--iterations", "-i", type=int, default=1, help="Number of iterations")
        run_parser.add_argument("--timeout", "-t", type=int, help="Timeout in seconds")

        # api-server 命令
        api_parser = subparsers.add_parser("api-server", help="Start API server")
        api_parser.add_argument("--host", "-H", default="127.0.0.1", help="Host to bind")
        api_parser.add_argument("--port", "-p", type=int, default=50000, help="Port to bind")
        api_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")

        # 添加自定义命令
        for name, cmd_info in self._custom_commands.items():
            cmd_parser = subparsers.add_parser(name, help=cmd_info["help"])
            for arg_name, arg_kwargs in cmd_info["arguments"]:
                cmd_parser.add_argument(arg_name, **arg_kwargs)

        return parser

    def _cmd_init(self, role: RoleBase, args: argparse.Namespace) -> int:
        """处理init命令"""
        if role.is_initialized() and not args.force:
            print(f"{role.role_name} already initialized")
            return 0

        result = role.initialize(
            name=args.name or "",
            description=args.description or "",
        )

        if result.get("success"):
            print(f"✓ {role.role_name} initialized successfully")
            print(f"  Workspace: {role.workspace}")
            if args.name:
                print(f"  Name: {args.name}")
        else:
            print(f"✗ Initialization failed: {result.get('message', 'Unknown error')}")
            return 1
        return 0

    def _cmd_status(self, role: RoleBase, args: argparse.Namespace) -> int:
        """处理status命令"""
        if not role.is_initialized():
            print(f"{role.role_name} not initialized")
            print(f"Run: {self.prog} init")
            return 1

        status = role.get_status()

        print("=" * 60)
        print(f"{role.role_name.upper()} Status")
        print("=" * 60)

        for key, value in status.items():
            if isinstance(value, dict):
                print(f"\n{key}:")
                for k, v in value.items():
                    print(f"  {k}: {v}")
            else:
                print(f"{key}: {value}")

        return 0

    def _cmd_health(self, role: RoleBase, args: argparse.Namespace) -> int:
        """处理health命令"""
        if not role.is_initialized():
            print(f"{role.role_name} not initialized")
            return 1

        # 尝试调用health方法，如果不存在则显示基本状态
        health = role.get_health() if hasattr(role, "get_health") else {"state": role.state.name}

        print("=" * 60)
        print(f"{role.role_name.upper()} Health")
        print("=" * 60)

        for key, value in health.items():
            print(f"{key}: {value}")

        return 0

    def _cmd_run(self, role: RoleBase, args: argparse.Namespace) -> int:
        """处理run命令"""
        if not role.is_initialized():
            print(f"{role.role_name} not initialized")
            return 1

        if not hasattr(role, "run"):
            print(f"{role.role_name} does not support run command")
            return 1

        print(f"Running {role.role_name}...")
        print(f"  Iterations: {args.iterations}")
        if args.timeout:
            print(f"  Timeout: {args.timeout}s")
        print()

        try:
            result = role.run(
                iterations=args.iterations,
                timeout=args.timeout,
            )

            print()
            print(f"{'=' * 60}")
            print("Result:")
            for key, value in result.items():
                print(f"  {key}: {value}")

            return 0 if result.get("success") else 1
        except (RuntimeError, ValueError) as e:
            logger.error("Error: %s", e)
            return 1

    def _cmd_api_server(self, role: RoleBase, args: argparse.Namespace) -> int:
        """处理api-server命令"""
        try:
            from .fastapi import RoleFastAPI

            api = RoleFastAPI(
                self.role_class,
                host=args.host,
                port=args.port,
                workspace=str(role.workspace),
            )
            api.run()
            return 0
        except ImportError as e:
            print(f"Failed to start API server: {e}")
            print("Make sure fastapi and uvicorn are installed")
            return 1

    def run(self, argv: list[str] | None = None) -> int:
        """运行CLI

        Args:
            argv: 命令行参数，默认为sys.argv[1:]

        Returns:
            退出码
        """
        parser = self._create_parser()
        args = parser.parse_args(argv)

        if not args.command:
            parser.print_help()
            return 1

        # 创建角色实例
        # 使用类名作为默认 role_name
        default_role_name = self.role_class.__name__.lower()
        role = self.role_class(args.workspace, default_role_name)

        # 路由命令
        handlers = {
            "init": self._cmd_init,
            "status": self._cmd_status,
            "health": self._cmd_health,
            "run": self._cmd_run,
            "api-server": self._cmd_api_server,
        }

        # 检查是否是自定义命令
        if args.command in self._custom_commands:
            return self._custom_commands[args.command]["func"](role, args)

        # 检查是否是标准命令
        handler = handlers.get(args.command)
        if handler:
            return handler(role, args)

        print(f"Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    # 示例用法
    from .base import RoleBase, RoleInfo

    class ExampleRole(RoleBase):
        def get_info(self) -> RoleInfo:
            return RoleInfo(
                name="example",
                version="1.0.0",
                description="Example role",
            )

        def get_status(self) -> dict:
            return {"name": self.role_name, "state": self.state.name}

        def is_initialized(self) -> bool:
            return True

        def initialize(self, **kwargs) -> dict:
            return {"success": True}

    cli = RoleCLI(ExampleRole, prog="example", description="Example role CLI")
    cli.run()
