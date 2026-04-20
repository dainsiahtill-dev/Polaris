"""
Director Interface - 抽象导演层

为 PM 提供统一的 Director 调用接口，
支持多种 Director 实现：Script Director、No Director、其他实现。

设计原则：
1. PM 只依赖接口，不依赖具体实现
2. 通过配置切换 Director 实现
3. 统一的输入输出契约
"""

import os
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from polaris.kernelone.runtime.shared_types import normalize_path_list, timeout_seconds_or_none
from polaris.kernelone.storage import resolve_runtime_path


@dataclass
class DirectorTask:
    """Director 任务定义"""

    task_id: str
    goal: str
    target_files: list[str]
    acceptance_criteria: list[str]
    constraints: list[str]
    context: dict[str, Any]
    scope_paths: list[str] | None = None
    scope_mode: str = "module"

    def __post_init__(self) -> None:
        self.target_files = normalize_path_list(self.target_files or [])
        self.scope_paths = normalize_path_list(self.scope_paths or [])
        if not isinstance(self.acceptance_criteria, list):
            self.acceptance_criteria = []
        if not isinstance(self.constraints, list):
            self.constraints = []


@dataclass
class DirectorResult:
    """Director 执行结果"""

    success: bool
    task_id: str
    changed_files: list[str]
    patches: list[dict[str, Any]]
    error: str | None = None
    metadata: dict[str, Any] = None

    def __post_init__(self) -> None:
        self.changed_files = normalize_path_list(self.changed_files or [])
        if self.metadata is None:
            self.metadata = {}


class DirectorInterface(ABC):
    """
    Director 抽象接口

    所有 Director 实现必须遵循此接口，
    包括：Script Director、No Director、Mock Director 等。
    """

    def __init__(self, workspace: Path, config: dict | None = None):
        self.workspace = Path(workspace)
        self.config = config or {}

    @abstractmethod
    def execute(self, task: DirectorTask) -> DirectorResult:
        """
        执行单个任务

        Args:
            task: 任务定义

        Returns:
            DirectorResult: 执行结果
        """
        pass

    @abstractmethod
    def is_available(self) -> bool:
        """检查 Director 是否可用"""
        pass

    @abstractmethod
    def get_info(self) -> dict[str, str]:
        """获取 Director 信息"""
        pass


class ScriptDirectorAdapter(DirectorInterface):
    """
    Script Director Adapter (DEPRECATED)

    Wraps the original loop-director.py subprocess calls
    to conform to DirectorInterface.

    .. deprecated::
        This adapter is deprecated. Use the unified Director runtime instead.
    """

    def __init__(self, workspace: Path, config: dict | None = None):
        warnings.warn(
            "ScriptDirectorAdapter is deprecated. Use the unified Director runtime instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        cfg = config or {}
        super().__init__(workspace, cfg)
        self.director_script = cfg.get("script", "src/backend/scripts/loop-director.py")
        timeout_raw = cfg.get("timeout", 3600)
        # Preserve explicit disable semantics from caller (`None` / <=0).
        if "timeout" in cfg:
            self.timeout = timeout_seconds_or_none(timeout_raw, default=0)
        else:
            self.timeout = timeout_seconds_or_none(timeout_raw, default=3600)
        self.pm_task_path = str(cfg.get("pm_task_path") or "").strip()
        self.director_result_path = str(cfg.get("director_result_path") or "").strip()
        self.director_log_path = str(cfg.get("director_log_path") or "").strip()
        self.prompt_profile = str(cfg.get("prompt_profile") or "").strip()
        self.planner_response_path = str(cfg.get("planner_response_path") or "").strip()
        self.ollama_response_path = str(cfg.get("ollama_response_path") or "").strip()
        self.qa_response_path = str(cfg.get("qa_response_path") or "").strip()
        self.reviewer_response_path = str(cfg.get("reviewer_response_path") or "").strip()
        self._project_root = cfg.get("project_root", self._find_project_root())

    def _resolve_task_timeout(self) -> int:
        """Resolve loop-director per-task timeout and keep margin from process timeout."""
        raw_task_timeout = self.config.get("task_timeout")
        if raw_task_timeout is not None:
            task_timeout = timeout_seconds_or_none(raw_task_timeout, default=0)
            if task_timeout is not None:
                return min(max(int(task_timeout), 30), 1800)

        if self.timeout is not None:
            # Leave a safety margin so loop-director can write result before process kill.
            return min(max(int(self.timeout) - 30, 30), 1800)

        env_raw = os.environ.get("POLARIS_DIRECTOR_TASK_TIMEOUT", "600")
        env_timeout = timeout_seconds_or_none(env_raw, default=600)
        if env_timeout is None:
            return 600
        return min(max(int(env_timeout), 30), 1800)

    def _find_project_root(self) -> Path:
        """Find project root by looking for src/backend/scripts/loop-director.py."""
        # Start from workspace and go up
        current = self.workspace
        for _ in range(5):  # Check up to 5 levels up
            if (current / "src" / "backend" / "scripts" / "loop-director.py").exists():
                return current
            parent = current.parent
            if parent == current:
                break
            current = parent
        return self.workspace

    def is_available(self) -> bool:
        """Check if director script exists."""
        script_path = self._project_root / self.director_script
        return script_path.exists()

    def execute(self, task: DirectorTask) -> DirectorResult:
        """Execute director script via subprocess."""
        import json
        import subprocess
        import sys

        # Find director script
        script_path = self._project_root / self.director_script
        if not script_path.exists():
            return DirectorResult(
                success=False,
                task_id=task.task_id,
                changed_files=[],
                patches=[],
                error=f"Director script not found: {self.director_script}",
            )

        # Build command line
        cmd = [
            sys.executable,
            str(script_path),
            "--iterations",
            "1",
            "--workspace",
            str(self.workspace),
            "--no-rollback-on-fail",
            "--timeout",
            str(self._resolve_task_timeout()),
        ]

        # Reuse engine-generated PM task contract when available.
        pm_task_file = Path(self.pm_task_path) if self.pm_task_path else None
        if not (pm_task_file and pm_task_file.is_file()):
            pm_task_file = Path(
                resolve_runtime_path(
                    str(self.workspace),
                    f"runtime/contracts/pm_tasks.{task.task_id}.contract.json",
                )
            )
            pm_task_file.parent.mkdir(parents=True, exist_ok=True)
            scope_paths = (
                task.scope_paths
                if isinstance(task.scope_paths, list)
                else (task.context.get("task", {}).get("scope_paths", []) if isinstance(task.context, dict) else [])
            )
            if not isinstance(scope_paths, list):
                scope_paths = []
            scope_mode = str(task.scope_mode or "").strip() or "module"
            pm_payload = {
                "schema_version": 1,
                "pm_iteration": task.context.get("iteration") if isinstance(task.context, dict) else None,
                "tasks": [
                    {
                        "id": task.task_id,
                        "title": task.goal,
                        "goal": task.goal,
                        "target_files": normalize_path_list(task.target_files),
                        "scope_paths": normalize_path_list(scope_paths),
                        "scope_mode": scope_mode,
                        "acceptance_criteria": task.acceptance_criteria,
                        "constraints": task.constraints,
                    }
                ],
            }
            pm_task_file.write_text(json.dumps(pm_payload, ensure_ascii=False), encoding="utf-8")
        cmd.extend(["--pm-task-path", str(pm_task_file)])

        # Execute Director
        result_file = (
            Path(self.director_result_path)
            if self.director_result_path
            else Path(
                resolve_runtime_path(
                    str(self.workspace),
                    "runtime/results/director.result.json",
                )
            )
        )
        result_file.parent.mkdir(parents=True, exist_ok=True)
        cmd.extend(["--director-result-path", str(result_file)])
        if self.director_log_path:
            cmd.extend(["--log-path", self.director_log_path])
        if self.prompt_profile:
            cmd.extend(["--prompt-profile", self.prompt_profile])
        if self.planner_response_path:
            cmd.extend(["--planner-response-path", self.planner_response_path])
        if self.ollama_response_path:
            cmd.extend(["--ollama-response-path", self.ollama_response_path])
        if self.qa_response_path:
            cmd.extend(["--qa-response-path", self.qa_response_path])
        if self.reviewer_response_path:
            cmd.extend(["--reviewer-response-path", self.reviewer_response_path])

        try:
            process = subprocess.Popen(
                cmd,
                cwd=self.workspace,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            stdout, stderr = process.communicate(timeout=self.timeout)
            return_code = int(process.returncode or 0)

            # 读取结果
            if result_file.exists():
                result_data = json.loads(result_file.read_text(encoding="utf-8"))
                status = str(result_data.get("status") or "").strip().lower()
                acceptance = result_data.get("acceptance")
                explicit_success = result_data.get("success")
                if isinstance(explicit_success, bool):
                    success = explicit_success
                else:
                    success = bool(acceptance is True or status == "success")
                error_text = (
                    str(result_data.get("error") or "").strip()
                    or str(result_data.get("reason") or "").strip()
                    or str(result_data.get("error_code") or "").strip()
                )
                if not success and not error_text and return_code != 0:
                    error_text = f"Director exited with code {return_code}"
                return DirectorResult(
                    success=success,
                    task_id=task.task_id,
                    changed_files=normalize_path_list(result_data.get("changed_files", [])),
                    patches=result_data.get("patches", []),
                    error=error_text or None,
                    metadata={
                        "return_code": return_code,
                        "status": status,
                        "stderr": stderr.decode(errors="replace") if stderr else None,
                        "stdout": stdout.decode(errors="replace") if stdout else None,
                    },
                )
            else:
                stderr_text = stderr.decode(errors="replace") if stderr else ""
                message = (
                    f"Director exited with code {return_code} and did not produce result file"
                    if return_code != 0
                    else "Director did not produce result file"
                )
                if stderr_text.strip():
                    message = f"{message}: {stderr_text.strip()}"
                return DirectorResult(
                    success=False,
                    task_id=task.task_id,
                    changed_files=[],
                    patches=[],
                    error=message,
                    metadata={"return_code": return_code},
                )

        except subprocess.TimeoutExpired:
            process.kill()
            timeout_hint = f"{self.timeout}s" if self.timeout is not None else "disabled"
            return DirectorResult(
                success=False,
                task_id=task.task_id,
                changed_files=[],
                patches=[],
                error=f"Director timeout after {timeout_hint}",
            )
        except Exception as e:
            return DirectorResult(
                success=False,
                task_id=task.task_id,
                changed_files=[],
                patches=[],
                error=str(e),
            )

    def get_info(self) -> dict[str, str]:
        return {
            "type": "script",
            "name": "Director Script (loop-director.py)",
            "script": self.director_script,
        }


class NoDirectorAdapter(DirectorInterface):
    """
    No Director Adapter - 无导演模式

    PM独立运行时使用，不调用任何Director。
    用于任务分解、规划等纯PM功能。
    """

    def __init__(self, workspace: Path, config: dict | None = None):
        super().__init__(workspace, config)
        self.config = config or {}

    def is_available(self) -> bool:
        """No Director总是可用"""
        return True

    def execute(self, task: DirectorTask) -> DirectorResult:
        """
        不执行任何操作，直接返回成功

        PM独立运行时，任务由PM自己分解规划，
        不需要Director执行代码生成。
        """
        from datetime import datetime

        return DirectorResult(
            success=True,
            task_id=task.task_id,
            changed_files=[],
            patches=[],
            error=None,
            metadata={
                "note": "PM running in standalone mode - no Director executed",
                "timestamp": datetime.now().isoformat(),
                "mode": "no_director",
            },
        )

    def get_info(self) -> dict[str, str]:
        return {
            "type": "none",
            "name": "No Director (Standalone PM)",
            "description": "PM runs independently without Director",
        }


class DirectorFactory:
    """
    Director 工厂

    根据配置创建对应的 Director 实例。
    """

    _registry: dict[str, type] = {
        "script": ScriptDirectorAdapter,
        "none": NoDirectorAdapter,
    }

    @classmethod
    def create(
        cls,
        director_type: str,
        workspace: Path,
        config: dict | None = None,
    ) -> DirectorInterface:
        """
        创建 Director 实例

        Args:
            director_type: Director 类型 ("script", "none", ...)
            workspace: 工作空间路径
            config: 配置参数

        Returns:
            DirectorInterface 实例
        """
        if director_type not in cls._registry:
            raise ValueError(f"Unknown director type: {director_type}. Available: {list(cls._registry.keys())}")

        director_class = cls._registry[director_type]
        return director_class(workspace, config)

    @classmethod
    def register(cls, name: str, director_class: type):
        """注册新的 Director 类型"""
        if not issubclass(director_class, DirectorInterface):
            raise ValueError("Director class must inherit from DirectorInterface")
        cls._registry[name] = director_class

    @classmethod
    def list_available(cls) -> list[str]:
        """列出可用的 Director 类型"""
        return list(cls._registry.keys())


def create_director(
    workspace: str,
    director_type: str | None = None,
    config: dict | None = None,
) -> DirectorInterface:
    """
    创建 Director 的便捷函数

    根据环境变量或配置自动选择合适的 Director。

    Usage:
        # 自动检测
        director = create_director("/path/to/workspace")

        # 明确指定
        director = create_director("/path/to/workspace", "script")

        # 带配置
        director = create_director(
            "/path/to/workspace",
            "script",
            {"timeout": 1200}
        )
    """
    workspace_path = Path(workspace)

    # 如果没有指定类型，从环境变量或自动检测
    if director_type is None:
        director_type = os.getenv("POLARIS_DIRECTOR_TYPE", "auto")

    if director_type == "auto":
        # 优先使用 script，如果不可用则使用 NoDirector
        if ScriptDirectorAdapter(workspace_path, config).is_available():
            director_type = "script"
        else:
            director_type = "none"

    return DirectorFactory.create(director_type, workspace_path, config)


# Public exports
__all__ = [
    "DirectorFactory",
    "DirectorInterface",
    "DirectorResult",
    "DirectorTask",
    "NoDirectorAdapter",
    "ScriptDirectorAdapter",
    "create_director",
]
