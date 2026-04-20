from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4


class JobState:
    PENDING = "pending"
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"

    _VALUES = [PENDING, RUNNING, CANCELLING, COMPLETED, FAILED, CANCELLED]

    @classmethod
    def is_valid(cls, value: str) -> bool:
        return value in cls._VALUES


@dataclass
class VerifyJob:
    job_id: str
    state: str = JobState.PENDING
    stage: str = "init"
    progress: float = 0.0
    total_commands: int = 0
    completed_commands: int = 0
    current_command: str = ""
    elapsed_sec: float = 0.0
    eta_sec: float | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    error: str | None = None
    result: dict[str, Any] | None = None
    _events: deque[dict] = field(default_factory=deque, repr=False)
    _event_seq: int = field(default=0, repr=False)
    _start_perf_counter: float | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def to_status(self) -> dict[str, Any]:
        with self._lock:
            if self.state in (JobState.RUNNING, JobState.CANCELLING) and self._start_perf_counter is not None:
                self.elapsed_sec = max(0.0, time.perf_counter() - self._start_perf_counter)
            return {
                "job_id": self.job_id,
                "state": self.state,
                "stage": self.stage,
                "progress": round(self.progress, 2),
                "elapsed_sec": round(self.elapsed_sec, 1),
                "eta_sec": round(self.eta_sec, 1) if self.eta_sec is not None else None,
                "current_command": self.current_command,
                "total_commands": self.total_commands,
                "completed_commands": self.completed_commands,
            }

    def add_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            self._event_seq += 1
            event = {
                "seq": self._event_seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "job_id": self.job_id,
                **payload,
            }
            self._events.append(event)
            return event

    def is_terminal(self) -> bool:
        with self._lock:
            return self.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)

    def add_live_event(self, event_type: str, payload: dict[str, Any]) -> bool:
        with self._lock:
            if self.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False

            live_payload = dict(payload)
            if event_type == "heartbeat":
                live_payload["state"] = self.state
                live_payload.setdefault("stage", self.stage)
                live_payload.setdefault("current_command", self.current_command)
                live_payload.setdefault("completed_commands", self.completed_commands)
                live_payload.setdefault("total_commands", self.total_commands)

            self._event_seq += 1
            event = {
                "seq": self._event_seq,
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": event_type,
                "job_id": self.job_id,
                **live_payload,
            }
            self._events.append(event)
            return True

    def get_events(self, since_seq: int = 0) -> list[dict[str, Any]]:
        with self._lock:
            if since_seq <= 0:
                return list(self._events)
            return [e for e in self._events if e.get("seq", 0) > since_seq]

    def update_progress(self, completed: int, total: int, current_command: str = "") -> None:
        with self._lock:
            self.completed_commands = completed
            self.total_commands = total
            self.current_command = current_command
            if total > 0:
                self.progress = (completed / total) * 100.0
            if completed > 0 and self._start_perf_counter is not None:
                elapsed = time.perf_counter() - self._start_perf_counter
                avg_time = elapsed / completed
                remaining = total - completed
                self.eta_sec = avg_time * remaining
                self.elapsed_sec = elapsed

    def mark_running(self, stage: str = "running") -> None:
        with self._lock:
            self.state = JobState.RUNNING
            self.stage = stage
            if self.start_time is None:
                self.start_time = datetime.now(timezone.utc)
                self._start_perf_counter = time.perf_counter()
                self.elapsed_sec = 0.0

    def try_mark_completed(self, status: str, exit_code: int, result: dict[str, Any] | None = None) -> bool:
        with self._lock:
            if self.state in (JobState.CANCELLING, JobState.CANCELLED):
                return False
            self.state = JobState.COMPLETED
            self.stage = "completed"
            self.end_time = datetime.now(timezone.utc)
            self.result = result
            self.progress = 100.0
            if self.start_time:
                self.elapsed_sec = (self.end_time - self.start_time).total_seconds()
            return True

    def mark_completed(self, status: str, exit_code: int, result: dict[str, Any] | None = None) -> None:
        self.try_mark_completed(status=status, exit_code=exit_code, result=result)

    def mark_failed(self, error: str) -> None:
        with self._lock:
            self.state = JobState.FAILED
            self.stage = "failed"
            self.error = error
            self.end_time = datetime.now(timezone.utc)
            if self.start_time:
                self.elapsed_sec = (self.end_time - self.start_time).total_seconds()

    def mark_cancelled(self) -> None:
        with self._lock:
            self.state = JobState.CANCELLED
            self.stage = "cancelled"
            self.end_time = datetime.now(timezone.utc)
            if self.start_time:
                self.elapsed_sec = (self.end_time - self.start_time).total_seconds()


class JobManager:
    MAX_JOBS = 100
    _instance: JobManager | None = None
    _instance_lock = threading.Lock()
    _initialized: bool
    _jobs: dict[str, VerifyJob]
    _lock: threading.Lock

    @classmethod
    def _initialize_instance(cls, instance: JobManager) -> JobManager:
        instance._jobs = {}
        instance._lock = threading.Lock()
        instance._initialized = True
        return instance

    def __new__(cls) -> JobManager:
        with cls._instance_lock:
            if cls._instance is None:
                instance = super().__new__(cls)
                cls._instance = cls._initialize_instance(instance)
            elif not getattr(cls._instance, "_initialized", False):
                cls._initialize_instance(cls._instance)
            return cls._instance

    def __init__(self) -> None:
        # Singleton initialization is completed in __new__ under class lock.
        return

    def create_job(self, prefix: str = "verify") -> VerifyJob:
        safe_prefix = str(prefix or "verify").strip().lower() or "verify"
        job_id = f"{safe_prefix}_{uuid4().hex[:12]}"
        job = VerifyJob(job_id=job_id)
        with self._lock:
            if len(self._jobs) >= self.MAX_JOBS:
                oldest_key = next(iter(self._jobs))
                del self._jobs[oldest_key]
            self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> VerifyJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_all_jobs(self) -> list[VerifyJob]:
        with self._lock:
            return list(self._jobs.values())

    def cancel_job(self, job_id: str) -> bool:
        job = self.get_job(job_id)
        if job is None:
            return False
        with job._lock:
            if job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED):
                return False
            job.state = JobState.CANCELLING
            return True

    def cleanup_completed(self, max_age_seconds: float = 3600) -> int:
        now = datetime.now(timezone.utc)
        cleaned = 0
        with self._lock:
            to_remove: list[str] = []
            for job_id, job in self._jobs.items():
                if (
                    job.state in (JobState.COMPLETED, JobState.FAILED, JobState.CANCELLED)
                    and job.end_time
                    and (now - job.end_time).total_seconds() > max_age_seconds
                ):
                    to_remove.append(job_id)
            for job_id in to_remove:
                del self._jobs[job_id]
                cleaned += 1
        return cleaned

    def clear_all_jobs(self) -> int:
        """Clear all jobs for test isolation.

        Returns:
            Number of jobs cleared.
        """
        with self._lock:
            count = len(self._jobs)
            self._jobs.clear()
            return count
