from __future__ import annotations

from enum import Enum, auto
from typing import Protocol


class VerifyStage(Enum):
    INIT = auto()
    LOAD_CACHE = auto()
    SELECT_CMDS = auto()
    RUNNING = auto()
    PARALLEL = auto()
    SEQUENTIAL = auto()
    COMPLETING = auto()
    CLEANUP = auto()


class VerifyProgressCallback(Protocol):
    def on_start(self, job_id: str, total_commands: int) -> None:
        """验证开始"""

    def on_stage_change(self, job_id: str, stage: VerifyStage) -> None:
        """阶段变更"""

    def on_command_start(self, job_id: str, command: str, index: int, total: int) -> None:
        """单个命令开始执行"""

    def on_command_complete(
        self,
        job_id: str,
        command: str,
        exit_code: int,
        duration: float,
        *,
        completed: int | None = None,
        total: int | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None:
        """单个命令完成"""

    def on_progress(self, job_id: str, completed: int, total: int, current_command: str) -> None:
        """进度更新 (每完成一个命令触发)"""

    def on_heartbeat(
        self,
        job_id: str,
        elapsed_sec: float,
        eta_sec: float | None,
        state: str,
        *,
        current_command: str = "",
        command_elapsed_sec: float | None = None,
        command_timeout_sec: float | None = None,
        command_progress_pct: float | None = None,
        stall_detected: bool | None = None,
        stall_elapsed_sec: float | None = None,
    ) -> None:
        """心跳 (每 10 秒触发)"""

    def on_command_output(
        self,
        job_id: str,
        command: str,
        stream: str,
        chunk: str,
        *,
        truncated: bool = False,
    ) -> None:
        """命令输出流事件"""

    def on_cache_hit(self, job_id: str, command: str) -> None:
        """缓存命中"""

    def on_skip(self, job_id: str, command: str, reason: str) -> None:
        """命令跳过"""

    def on_error(self, job_id: str, command: str | None, error: str) -> None:
        """错误发生"""

    def on_complete(self, job_id: str, status: str, exit_code: int) -> None:
        """验证完成"""


class NoOpCallback(VerifyProgressCallback):
    def on_start(self, job_id: str, total_commands: int) -> None:
        pass

    def on_stage_change(self, job_id: str, stage: VerifyStage) -> None:
        pass

    def on_command_start(self, job_id: str, command: str, index: int, total: int) -> None:
        pass

    def on_command_complete(
        self,
        job_id: str,
        command: str,
        exit_code: int,
        duration: float,
        *,
        completed: int | None = None,
        total: int | None = None,
        stdout_tail: str = "",
        stderr_tail: str = "",
    ) -> None:
        pass

    def on_progress(self, job_id: str, completed: int, total: int, current_command: str) -> None:
        pass

    def on_heartbeat(
        self,
        job_id: str,
        elapsed_sec: float,
        eta_sec: float | None,
        state: str,
        *,
        current_command: str = "",
        command_elapsed_sec: float | None = None,
        command_timeout_sec: float | None = None,
        command_progress_pct: float | None = None,
        stall_detected: bool | None = None,
        stall_elapsed_sec: float | None = None,
    ) -> None:
        pass

    def on_command_output(
        self,
        job_id: str,
        command: str,
        stream: str,
        chunk: str,
        *,
        truncated: bool = False,
    ) -> None:
        pass

    def on_cache_hit(self, job_id: str, command: str) -> None:
        pass

    def on_skip(self, job_id: str, command: str, reason: str) -> None:
        pass

    def on_error(self, job_id: str, command: str | None, error: str) -> None:
        pass

    def on_complete(self, job_id: str, status: str, exit_code: int) -> None:
        pass
