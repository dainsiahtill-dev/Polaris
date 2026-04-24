"""PM CLI - 管理命令行接口

提供PM系统的管理命令。
"""

from __future__ import annotations

import argparse
import os
import sys

from polaris.cells.delivery.cli.public.service import register_pm_management_handlers
from polaris.delivery.cli.pm.pm_integration import get_pm
from polaris.delivery.cli.pm.requirements_tracker import (
    RequirementPriority,
    RequirementStatus,
    RequirementType,
)
from polaris.delivery.cli.pm.task_orchestrator import AssigneeType, Task, TaskPriority, TaskStatus


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize PM system."""
    pm = get_pm(args.workspace)

    if pm.is_initialized() and not args.force:
        print(f"PM already initialized at: {args.workspace}")
        print("Use --force to reinitialize.")
        return 1

    result = pm.initialize(
        project_name=args.project_name,
        description=args.description,
    )

    print("PM initialized successfully!")
    print(f"  Workspace: {result['workspace']}")
    print(f"  Project: {result['project_name']}")
    print(f"  Version: {result['pm_version']}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """Show PM status."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print(f"PM not initialized at: {args.workspace}")
        print("Run: python -m pm.pm_cli init --workspace <path>")
        return 1

    status = pm.get_status()

    print("=" * 60)
    print("PM 状态报告")
    print("=" * 60)
    print(f"项目: {status['project']}")
    print(f"版本: {status['version']}")
    print()
    print("任务统计:")
    task_stats = status["stats"]["tasks"]
    print(f"  总数: {task_stats.get('total', 0)}")
    print(f"  已完成: {task_stats.get('completed', 0)}")
    print(f"  进行中: {task_stats.get('in_progress', 0)}")
    print(f"  待处理: {task_stats.get('pending', 0)}")
    print(f"  完成率: {task_stats.get('completion_rate', 0) * 100:.1f}%")
    print()
    print("需求覆盖:")
    req_stats = status["stats"]["requirements"]
    print(f"  总需求: {req_stats.get('total', 0)}")
    print(f"  已实现: {req_stats.get('implemented', 0)}")
    print(f"  已验证: {req_stats.get('verified', 0)}")
    print(f"  覆盖率: {req_stats.get('coverage', 0):.1f}%")

    return 0


def cmd_requirement_add(args: argparse.Namespace) -> int:
    """Add a requirement."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    priority = RequirementPriority(args.priority)
    req_type = RequirementType(args.type)

    req = pm.requirements.register_requirement(
        title=args.title,
        description=args.description,
        source=args.source or "manual",
        source_section=args.section or "",
        priority=priority,
        req_type=req_type,
        tags=args.tags.split(",") if args.tags else [],
    )

    print(f"需求已注册: {req.id}")
    print(f"  标题: {req.title}")
    print(f"  优先级: {req.priority.value}")
    print(f"  状态: {req.status.value}")
    return 0


def cmd_requirement_list(args: argparse.Namespace) -> int:
    """List requirements."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    status = RequirementStatus(args.status) if args.status else None
    requirements = pm.requirements.list_requirements(status=status)

    print("=" * 80)
    print(f"需求列表 (共 {len(requirements)} 个)")
    print("=" * 80)
    print(f"{'ID':<12} {'状态':<12} {'优先级':<8} {'标题'}")
    print("-" * 80)

    for req in requirements:
        print(f"{req.id:<12} {req.status.value:<12} {req.priority.value:<8} {req.title}")

    return 0


def cmd_requirement_status(args: argparse.Namespace) -> int:
    """Update requirement status."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    new_status = RequirementStatus(args.status)
    req = pm.requirements.update_status(
        args.req_id,
        new_status,
        reason=args.reason,
    )

    if req:
        print(f"需求 {req.id} 状态已更新为: {req.status.value}")
        return 0
    else:
        print(f"需求未找到: {args.req_id}")
        return 1


def cmd_task_add(args: argparse.Namespace) -> int:
    """Add a task."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    priority = TaskPriority(args.priority)

    task = pm.tasks.register_task(
        title=args.title,
        description=args.description,
        priority=priority,
        requirements=args.requirements.split(",") if args.requirements else [],
        dependencies=args.dependencies.split(",") if args.dependencies else [],
        estimated_effort=args.effort,
    )

    print(f"任务已注册: {task.id}")
    print(f"  标题: {task.title}")
    print(f"  优先级: {task.priority.value}")
    print(f"  状态: {task.status.value}")
    return 0


def cmd_task_list(args: argparse.Namespace) -> int:
    """List tasks."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    status = TaskStatus(args.status) if args.status else None
    tasks: list[Task] = (
        pm.tasks.get_tasks_by_status(status)
        if status
        else [t for tid in pm.tasks._load_registry().get("tasks", {}) if (t := pm.tasks.get_task(tid)) is not None]
    )

    print("=" * 100)
    print(f"任务列表 (共 {len(tasks)} 个)")
    print("=" * 100)
    print(f"{'ID':<12} {'状态':<12} {'优先级':<8} {'执行者':<15} {'标题'}")
    print("-" * 100)

    for task in tasks:
        if task:
            assignee = task.assignee or "-"
            print(f"{task.id:<12} {task.status.value:<12} {task.priority.value:<8} {assignee:<15} {task.title[:40]}")

    return 0


def cmd_task_assign(args: argparse.Namespace) -> int:
    """Assign task to executor."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    executor_type = AssigneeType(args.executor_type)

    task = pm.tasks.assign_task(
        args.task_id,
        args.executor,
        executor_type,
        notes=args.notes,
    )

    if task:
        assignee_type_str = task.assignee_type.value if task.assignee_type else "unknown"
        print(f"任务 {task.id} 已分配给: {task.assignee} ({assignee_type_str})")
        return 0
    else:
        print(f"任务分配失败: {args.task_id}")
        return 1


def cmd_task_complete(args: argparse.Namespace) -> int:
    """Mark task as completed."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    from polaris.delivery.cli.pm.task_orchestrator import TaskVerification

    verification = TaskVerification(
        method=args.method,
        evidence=args.evidence,
        verified_by=args.executor,
    )

    task = pm.tasks.complete_task(
        args.task_id,
        args.executor,
        verification,
        result_summary=args.summary,
    )

    if task:
        print(f"任务 {task.id} 已完成")
        return 0
    else:
        print(f"任务完成标记失败: {args.task_id}")
        return 1


def cmd_health(args: argparse.Namespace) -> int:
    """Show project health."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    health = pm.analyze_project_health()

    print("=" * 60)
    print("项目健康度报告")
    print("=" * 60)
    print(f"整体状态: {health['overall'].upper()}")
    print()
    print("组件状态:")
    for component, status in health["components"].items():
        icon = "✓" if status == "healthy" else "⚠" if status == "at_risk" else "✗"
        print(f"  {icon} {component}: {status}")
    print()
    print("关键指标:")
    for metric, value in health["metrics"].items():
        print(f"  {metric}: {value * 100:.1f}%")
    print()
    if health["recommendations"]:
        print("建议:")
        for rec in health["recommendations"]:
            print(f"  - {rec}")

    return 0


def cmd_report(args: argparse.Namespace) -> int:
    """Generate comprehensive report."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    report_path = pm.generate_comprehensive_report(args.output)
    print(f"报告已生成: {report_path}")
    return 0


def cmd_coverage(args: argparse.Namespace) -> int:
    """Show requirements coverage."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    coverage = pm.requirements.get_coverage_report()

    print("=" * 60)
    print("需求覆盖率报告")
    print("=" * 60)
    print(f"总需求: {coverage['total']}")
    print(f"已实现: {coverage['implemented']}")
    print(f"已验证: {coverage['verified']}")
    print(f"覆盖率: {coverage['coverage']:.1f}%")
    print()
    print("按状态分布:")
    for status, count in coverage.get("by_status", {}).items():
        print(f"  {status}: {count}")
    print()
    print("按优先级分布:")
    for priority, count in coverage.get("by_priority", {}).items():
        print(f"  {priority}: {count}")

    return 0


def cmd_api_server(args: argparse.Namespace) -> int:
    """Start PM API server."""
    import subprocess
    import sys

    # Build command for api_server.py
    cmd = [
        sys.executable,
        "-m",
        "pm.api_server",
        "--workspace",
        args.workspace,
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]

    if args.reload:
        cmd.append("--reload")

    print("启动PM API服务器...")
    print(f"  工作区: {args.workspace}")
    print(f"  地址: http://{args.host}:{args.port}")
    print()

    try:
        server_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        # Wait briefly for startup failure (e.g. port already in use, import error)
        try:
            stdout, stderr = server_proc.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            # Server started successfully and is running in background
            print("\nPM API 服务器已在后台启动")
            return 0
        # Server exited immediately — startup failed
        print(f"PM API 服务器启动失败 (exit {server_proc.returncode})")
        if stdout:
            print(stdout.decode("utf-8", errors="replace"))
        if stderr:
            print(stderr.decode("utf-8", errors="replace"))
        return 1
    except KeyboardInterrupt:
        print("\n服务器已停止")
        return 0


def cmd_document_list(args: argparse.Namespace) -> int:
    """List documents."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    result = pm.list_documents(
        doc_type=args.type,
        pattern=args.pattern,
        limit=args.limit,
        offset=args.offset,
    )

    documents = result.get("documents", [])
    pagination = result.get("pagination", {})

    print("=" * 80)
    print(f"文档列表 (共 {pagination.get('total', 0)} 个)")
    print("=" * 80)
    print(f"{'路径':<50} {'版本':<8} {'修改时间'}")
    print("-" * 80)

    for doc in documents:
        path = doc.get("path", "")
        version = doc.get("current_version", "")
        modified = doc.get("last_modified", "")[:19]  # Truncate to seconds
        # Truncate path if too long
        display_path = path if len(path) <= 48 else "..." + path[-45:]
        print(f"{display_path:<50} {version:<8} {modified}")

    if pagination.get("has_more"):
        print(f"\n(还有更多结果, 使用 --offset {pagination.get('offset', 0) + pagination.get('limit', 100)} 查看)")

    return 0


def cmd_document_show(args: argparse.Namespace) -> int:
    """Show document content."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    doc_path = args.path if os.path.isabs(args.path) else os.path.join(args.workspace, args.path)

    doc_info = pm.get_document(doc_path)
    if doc_info is None:
        print(f"文档未找到: {args.path}")
        return 1

    content = pm.get_document_content(doc_path, args.version)

    print("=" * 80)
    print(f"文档: {args.path}")
    print("=" * 80)
    print(f"当前版本: {doc_info.get('current_version', 'N/A')}")
    print(f"版本数量: {len(doc_info.get('versions', []))}")

    analysis = doc_info.get("analysis")
    if analysis:
        print(f"需求数量: {len(analysis.get('requirements', []))}")
        print(f"接口数量: {len(analysis.get('interfaces', []))}")
    print("-" * 80)

    if content:
        print(content)
    else:
        print("(无内容)")

    return 0


def cmd_task_history(args: argparse.Namespace) -> int:
    """Show task history (especially Director tasks)."""
    pm = get_pm(args.workspace)

    if not pm.is_initialized():
        print("PM not initialized!")
        return 1

    if args.director:
        result = pm.get_director_task_history(
            iteration=args.iteration,
            limit=args.limit,
            offset=args.offset,
        )
        title = "Director任务历史"
    else:
        result = pm.get_task_history(
            assignee=args.assignee,
            status=args.status,
            limit=args.limit,
            offset=args.offset,
        )
        title = "任务历史"

    tasks = result.get("tasks", [])
    pagination = result.get("pagination", {})

    print("=" * 100)
    print(f"{title} (共 {pagination.get('total', 0)} 个)")
    print("=" * 100)
    print(f"{'ID':<20} {'状态':<12} {'优先级':<8} {'分配人':<15} {'标题'}")
    print("-" * 100)

    for task in tasks:
        task_id = task.get("id", "")[:18]
        status = task.get("status", "")
        priority = task.get("priority", "")
        assignee = task.get("assignee", "") or "N/A"
        assignee = assignee[:13]
        title = task.get("title", "")
        title = title[:45] if len(title) <= 45 else title[:42] + "..."
        print(f"{task_id:<20} {status:<12} {priority:<8} {assignee:<15} {title}")

    if pagination.get("has_more"):
        print(f"\n(还有更多结果, 使用 --offset {pagination.get('offset', 0) + pagination.get('limit', 100)} 查看)")

    return 0


def main(argv: list[str] | None = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        prog="pm",
        description="PM - 项目管理系统CLI",
    )
    parser.add_argument(
        "--workspace",
        default=".",
        help="Workspace path (default: current directory)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Commands")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize PM")
    init_parser.add_argument("--project-name", default="", help="Project name")
    init_parser.add_argument("--description", default="", help="Project description")
    init_parser.add_argument("--force", action="store_true", help="Force reinitialize")
    init_parser.set_defaults(func=cmd_init)

    # status
    status_parser = subparsers.add_parser("status", help="Show status")
    status_parser.set_defaults(func=cmd_status)

    # health
    health_parser = subparsers.add_parser("health", help="Show project health")
    health_parser.set_defaults(func=cmd_health)

    # report
    report_parser = subparsers.add_parser("report", help="Generate report")
    report_parser.add_argument("--output", "-o", help="Output directory")
    report_parser.set_defaults(func=cmd_report)

    # coverage
    coverage_parser = subparsers.add_parser("coverage", help="Show requirements coverage")
    coverage_parser.set_defaults(func=cmd_coverage)

    # requirement commands
    req_parser = subparsers.add_parser("requirement", aliases=["req"], help="Requirement management")
    req_subparsers = req_parser.add_subparsers(dest="req_command")

    req_add = req_subparsers.add_parser("add", help="Add requirement")
    req_add.add_argument("title", help="Requirement title")
    req_add.add_argument("--description", "-d", default="", help="Description")
    req_add.add_argument("--source", "-s", help="Source document")
    req_add.add_argument("--section", help="Source section")
    req_add.add_argument("--priority", "-p", default="medium", choices=["critical", "high", "medium", "low"])
    req_add.add_argument(
        "--type",
        "-t",
        default="functional",
        choices=["functional", "non_functional", "technical", "business", "interface"],
    )
    req_add.add_argument("--tags", help="Comma-separated tags")
    req_add.set_defaults(func=cmd_requirement_add)

    req_list = req_subparsers.add_parser("list", help="List requirements")
    req_list.add_argument("--status", choices=[s.value for s in RequirementStatus])
    req_list.set_defaults(func=cmd_requirement_list)

    req_status = req_subparsers.add_parser("status", help="Update requirement status")
    req_status.add_argument("req_id", help="Requirement ID")
    req_status.add_argument("status", choices=[s.value for s in RequirementStatus])
    req_status.add_argument("--reason", "-r", default="", help="Reason for change")
    req_status.set_defaults(func=cmd_requirement_status)

    # task commands
    task_parser = subparsers.add_parser("task", aliases=["t"], help="Task management")
    task_subparsers = task_parser.add_subparsers(dest="task_command")

    task_add = task_subparsers.add_parser("add", help="Add task")
    task_add.add_argument("title", help="Task title")
    task_add.add_argument("--description", "-d", default="", help="Description")
    task_add.add_argument("--priority", "-p", default="medium", choices=["critical", "high", "medium", "low"])
    task_add.add_argument("--requirements", "-r", help="Comma-separated requirement IDs")
    task_add.add_argument("--dependencies", help="Comma-separated task IDs")
    task_add.add_argument("--effort", type=int, default=0, help="Estimated effort (minutes)")
    task_add.set_defaults(func=cmd_task_add)

    task_list = task_subparsers.add_parser("list", help="List tasks")
    task_list.add_argument("--status", choices=[s.value for s in TaskStatus])
    task_list.set_defaults(func=cmd_task_list)

    task_assign = task_subparsers.add_parser("assign", help="Assign task")
    task_assign.add_argument("task_id", help="Task ID")
    task_assign.add_argument("executor", help="Executor ID")
    task_assign.add_argument("--type", "-t", default="Director", choices=["ChiefEngineer", "Director", "PM"])
    task_assign.add_argument("--notes", "-n", default="", help="Assignment notes")
    task_assign.set_defaults(func=cmd_task_assign)

    task_complete = task_subparsers.add_parser("complete", help="Complete task")
    task_complete.add_argument("task_id", help="Task ID")
    task_complete.add_argument("executor", help="Executor ID")
    task_complete.add_argument(
        "--method", "-m", default="manual_review", choices=["test_passed", "manual_review", "auto_check", "code_review"]
    )
    task_complete.add_argument("--evidence", "-e", required=True, help="Verification evidence")
    task_complete.add_argument("--summary", "-s", default="", help="Result summary")
    task_complete.set_defaults(func=cmd_task_complete)

    # task history
    task_history = task_subparsers.add_parser("history", help="Show task history")
    task_history.add_argument("--director", "-d", action="store_true", help="Show only Director tasks")
    task_history.add_argument("--iteration", "-i", type=int, help="Filter by PM iteration")
    task_history.add_argument("--assignee", "-a", help="Filter by assignee")
    task_history.add_argument("--status", "-s", choices=[s.value for s in TaskStatus])
    task_history.add_argument("--limit", "-l", type=int, default=50)
    task_history.add_argument("--offset", "-o", type=int, default=0)
    task_history.set_defaults(func=cmd_task_history)

    # document commands
    doc_parser = subparsers.add_parser("document", aliases=["doc", "docs"], help="Document management")
    doc_subparsers = doc_parser.add_subparsers(dest="doc_command")

    doc_list = doc_subparsers.add_parser("list", help="List documents")
    doc_list.add_argument("--type", "-t", help="Filter by type")
    doc_list.add_argument("--pattern", "-p", help="Glob pattern")
    doc_list.add_argument("--limit", "-l", type=int, default=100)
    doc_list.add_argument("--offset", "-o", type=int, default=0)
    doc_list.set_defaults(func=cmd_document_list)

    doc_show = doc_subparsers.add_parser("show", help="Show document content")
    doc_show.add_argument("path", help="Document path")
    doc_show.add_argument("--version", "-v", help="Specific version")
    doc_show.set_defaults(func=cmd_document_show)

    # api-server command
    api_parser = subparsers.add_parser("api-server", help="Start PM API server")
    api_parser.add_argument("--host", "-H", default="127.0.0.1", help="Host to bind")
    api_parser.add_argument("--port", "-p", type=int, default=49980, help="Port to bind")
    api_parser.add_argument("--reload", action="store_true", help="Enable auto-reload")
    api_parser.set_defaults(func=cmd_api_server)

    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 1

    return args.func(args)


# ── Register PM management handlers (after all cmd_* are defined) ─────────────────
register_pm_management_handlers(sys.modules[__name__])


if __name__ == "__main__":
    sys.exit(main())
