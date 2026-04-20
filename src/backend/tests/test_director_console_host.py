from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.roles.runtime.public.contracts import ExecuteRoleSessionCommandV1
from polaris.delivery.cli.director.console_host import (
    DirectorConsoleError,
    DirectorConsoleHost,
    RoleConsoleHostError,
    RoleSessionNotFoundError,
)


@dataclass
class _FakeSession:
    session_id: str
    role: str
    workspace: str | None
    host_kind: str
    session_type: str
    attachment_mode: str
    title: str | None
    context_config: dict[str, Any] = field(default_factory=dict)
    capability_profile: dict[str, Any] = field(default_factory=dict)
    state: str = "active"
    messages: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self, include_messages: bool = False, message_limit: int = 100) -> dict[str, Any]:
        payload = {
            "id": self.session_id,
            "role": self.role,
            "workspace": self.workspace,
            "host_kind": self.host_kind,
            "session_type": self.session_type,
            "attachment_mode": self.attachment_mode,
            "title": self.title,
            "context_config": dict(self.context_config),
            "capability_profile": dict(self.capability_profile),
            "state": self.state,
            "message_count": len(self.messages),
            "updated_at": f"updated-{len(self.messages)}",
        }
        if include_messages:
            payload["messages"] = [dict(item) for item in self.messages[-message_limit:]]
        return payload


class _FakeRoleSessionService:
    def __init__(self) -> None:
        self.sessions: dict[str, _FakeSession] = {}
        self.order: list[str] = []
        self._counter = 0

    def __enter__(self) -> _FakeRoleSessionService:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False

    def create_session(
        self,
        *,
        role: str,
        host_kind: str,
        workspace: str | None,
        session_type: str,
        attachment_mode: str,
        title: str | None,
        context_config: dict[str, Any] | None,
        capability_profile: dict[str, Any] | None,
    ) -> _FakeSession:
        self._counter += 1
        session = _FakeSession(
            session_id=f"session-{self._counter}",
            role=role,
            workspace=workspace,
            host_kind=host_kind,
            session_type=session_type,
            attachment_mode=attachment_mode,
            title=title,
            context_config=dict(context_config or {}),
            capability_profile=dict(capability_profile or {}),
        )
        self.sessions[session.session_id] = session
        self.order.append(session.session_id)
        return session

    def get_session(self, session_id: str) -> _FakeSession | None:
        return self.sessions.get(session_id)

    def get_sessions(
        self,
        *,
        role: str | None = None,
        host_kind: str | None = None,
        workspace: str | None = None,
        session_type: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[_FakeSession]:
        sessions = [self.sessions[token] for token in self.order]
        if role is not None:
            sessions = [item for item in sessions if item.role == role]
        if host_kind is not None:
            sessions = [item for item in sessions if item.host_kind == host_kind]
        if workspace is not None:
            sessions = [item for item in sessions if item.workspace == workspace]
        if session_type is not None:
            sessions = [item for item in sessions if item.session_type == session_type]
        if state is not None:
            sessions = [item for item in sessions if item.state == state]
        return sessions[offset : offset + limit]

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> _FakeSession | None:
        session = self.sessions.get(session_id)
        if session is None:
            return None
        session.messages.append(
            {
                "sequence": len(session.messages),
                "role": role,
                "content": content,
                "thinking": thinking,
                "meta": dict(meta or {}),
            }
        )
        return session


class _DetachedAwareSession:
    def __init__(self, owner: _DetachedAwareRoleSessionService) -> None:
        self._owner = owner

    def to_dict(self, include_messages: bool = False, message_limit: int = 100) -> dict[str, Any]:
        if self._owner.closed:
            raise RuntimeError("detached session payload access")
        payload = {
            "id": "session-detached",
            "role": "director",
            "workspace": "workspace",
            "host_kind": "cli",
            "session_type": "standalone",
            "attachment_mode": "isolated",
            "title": "Detached Session",
            "context_config": {"lane": "director"},
            "capability_profile": {},
            "state": "active",
            "message_count": 2,
            "updated_at": "updated-detached",
        }
        if include_messages:
            payload["messages"] = [
                {"role": "user", "content": "prior user"},
                {"role": "assistant", "content": "prior assistant"},
            ][-message_limit:]
        return payload


class _DetachedAwareRoleSessionService:
    def __init__(self) -> None:
        self.closed = False
        self.session = _DetachedAwareSession(self)

    def __enter__(self) -> _DetachedAwareRoleSessionService:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.closed = True
        return False

    def get_session(self, session_id: str) -> _DetachedAwareSession | None:
        return self.session if session_id == "session-detached" else None

    def get_sessions(
        self,
        *,
        role: str | None = None,
        host_kind: str | None = None,
        workspace: str | None = None,
        session_type: str | None = None,
        state: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[_DetachedAwareSession]:
        del role, host_kind, workspace, session_type, state, offset
        return [self.session][:limit]

    def add_message(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        thinking: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        del role, content, thinking, meta
        return {"id": session_id} if session_id == "session-detached" else None


@dataclass
class _FakeTask:
    task_id: int
    subject: str
    description: str
    metadata: dict[str, Any]
    priority: int | str = 1

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.task_id,
            "subject": self.subject,
            "description": self.description,
            "metadata": dict(self.metadata),
            "priority": self.priority,
            "status": "pending",
        }


class _FakeTaskService:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace
        self.tasks: list[_FakeTask] = []
        self._counter = 0

    def create(
        self,
        *,
        subject: str,
        description: str = "",
        blocked_by: list[int] | None = None,
        priority: int | str = 1,
        owner: str = "",
        assignee: str = "",
        tags: list[str] | None = None,
        estimated_hours: float = 0.0,
        metadata: dict[str, Any] | None = None,
    ) -> _FakeTask:
        del blocked_by, owner, assignee, tags, estimated_hours
        self._counter += 1
        task = _FakeTask(
            task_id=self._counter,
            subject=subject,
            description=description,
            metadata=dict(metadata or {}),
            priority=priority,
        )
        self.tasks.append(task)
        return task

    def list_task_rows(self, *, include_terminal: bool = True) -> list[dict[str, Any]]:
        del include_terminal
        return [task.to_dict() for task in self.tasks]

    def select_next_task(self, *, requested_task_id: Any = None, prefer_resumable: bool = True) -> dict[str, Any] | None:
        del prefer_resumable
        if requested_task_id is not None:
            for task in self.tasks:
                if str(task.task_id) == str(requested_task_id):
                    return task.to_dict()
        return self.tasks[0].to_dict() if self.tasks else None


async def _empty_runtime_stream(_command: Any):
    if False:
        yield {}


class _FakeRoleRuntime:
    def __init__(self, stream_factory=None) -> None:
        self._stream_factory = stream_factory or _empty_runtime_stream
        self.commands: list[Any] = []

    async def stream_chat_turn(self, command: Any):
        self.commands.append(command)
        async for event in self._stream_factory(command):
            yield event


def test_director_console_host_creates_and_reuses_sessions() -> None:
    session_service = _FakeRoleSessionService()
    task_service = _FakeTaskService("workspace")
    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: session_service,
        task_service_factory=lambda workspace: task_service,
        runtime_service_factory=lambda: runtime,
    )

    created = host.ensure_session()
    reused = host.ensure_session(created["id"])
    listed = host.list_sessions()

    assert created["role"] == "director"
    assert created["host_kind"] == "cli"
    assert created["session_type"] == "standalone"
    assert created["attachment_mode"] == "isolated"
    assert reused["id"] == created["id"]
    assert listed[0]["id"] == created["id"]


@pytest.mark.asyncio
async def test_director_console_host_loads_history_and_persists_stream_turn() -> None:
    session_service = _FakeRoleSessionService()
    task_service = _FakeTaskService("workspace")
    runtime = _FakeRoleRuntime()

    async def _fake_runtime_stream(command: Any):
        yield {"type": "content_chunk", "content": "hello "}
        yield {"type": "thinking_chunk", "content": "trace"}
        yield {"type": "complete", "content": "hello world", "thinking": "trace"}
        yield {"type": "done"}

    runtime._stream_factory = _fake_runtime_stream

    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: session_service,
        task_service_factory=lambda workspace: task_service,
        runtime_service_factory=lambda: runtime,
    )

    session = host.create_session(context_config={"lane": "director"})
    session_service.add_message(
        session_id=session["id"],
        role="user",
        content="prior user",
        meta={"source": "seed"},
    )
    session_service.add_message(
        session_id=session["id"],
        role="assistant",
        content="prior assistant",
        thinking="seed-trace",
        meta={"source": "seed"},
    )

    events = [event async for event in host.stream_turn(session["id"], "new request")]
    history = host.load_session_history(session["id"])
    loaded = host.load_session(session["id"])
    command = runtime.commands[-1]

    assert len(runtime.commands) == 1
    assert isinstance(command, ExecuteRoleSessionCommandV1)
    assert command.role == "director"
    assert command.workspace == "workspace"
    assert [item[1] for item in command.history] == ["prior user", "prior assistant"]
    assert events[-1]["type"] == "complete"
    assert events[-1]["data"]["content"] == "hello world"
    assert [item["role"] for item in history] == ["user", "assistant", "user", "assistant"]
    assert loaded["message_count"] == 4
    assert history[-1]["content"] == "hello world"


@pytest.mark.asyncio
async def test_director_console_host_projects_file_diff_for_tool_result(tmp_path: Path) -> None:
    session_service = _FakeRoleSessionService()
    task_service = _FakeTaskService(str(tmp_path))
    target_file = tmp_path / "demo.py"
    target_file.write_text("print('old')\n", encoding="utf-8")
    runtime = _FakeRoleRuntime()

    async def _fake_runtime_stream(_command: Any):
        yield {
            "type": "tool_call",
            "tool": "write_file",
            "args": {"file": "demo.py"},
        }
        target_file.write_text("print('new')\n", encoding="utf-8")
        yield {
            "type": "tool_result",
            "tool": "write_file",
            "result": {"file": "demo.py", "bytes_written": 13, "success": True},
        }
        yield {"type": "complete", "content": "patched", "thinking": ""}
        yield {"type": "done"}

    runtime._stream_factory = _fake_runtime_stream

    host = DirectorConsoleHost(
        str(tmp_path),
        session_service_factory=lambda: session_service,
        task_service_factory=lambda workspace: task_service,
        runtime_service_factory=lambda: runtime,
    )

    session = host.create_session(context_config={"lane": "director"})
    events = [event async for event in host.stream_turn(session["id"], "patch the file")]

    tool_result = next(item for item in events if item["type"] == "tool_result")
    payload = tool_result["data"]

    assert payload["file_path"] == "demo.py"
    assert payload["operation"] == "modify"
    assert "-print('old')" in payload["patch"]
    assert "+print('new')" in payload["patch"]


def test_director_console_host_task_row_apis_use_task_runtime_service() -> None:
    task_service = _FakeTaskService("workspace")
    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: _FakeRoleSessionService(),
        task_service_factory=lambda workspace: task_service,
        runtime_service_factory=lambda: runtime,
    )

    created = host.create_task(subject="wire the CLI", description="build the host service", metadata={"role": "director"})
    listed = host.list_tasks()
    selected = host.select_next_task()

    assert created["subject"] == "wire the CLI"
    assert listed[0]["metadata"]["role"] == "director"
    assert selected is not None
    assert selected["subject"] == "wire the CLI"


@pytest.mark.asyncio
async def test_director_console_host_missing_session_raises() -> None:
    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: _FakeRoleSessionService(),
        task_service_factory=lambda workspace: _FakeTaskService(workspace),
        runtime_service_factory=lambda: runtime,
    )

    with pytest.raises(RoleSessionNotFoundError):
        async for _event in host.stream_turn("missing", "hello"):
            pass


def test_director_console_host_degrades_when_task_runtime_unavailable() -> None:
    def _raise_task_service(workspace: str) -> _FakeTaskService:
        raise RuntimeError(f"task runtime offline for {workspace}")

    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: _FakeRoleSessionService(),
        task_service_factory=_raise_task_service,
        runtime_service_factory=lambda: runtime,
    )

    status = host.get_status()

    assert host.list_tasks() == []
    assert host.select_next_task() is None
    assert status["task_runtime_available"] is False
    assert "task runtime offline" in str(status["task_runtime_error"])
    with pytest.raises((DirectorConsoleError, RoleConsoleHostError)):
        host.create_task(subject="should fail")


def test_director_console_package_exports_canonical_host() -> None:
    from polaris.delivery.cli import director as director_package
    from polaris.delivery.cli.terminal_console import PolarisLazyClaude as _NewPolarisLazyClaude

    assert director_package.DirectorConsoleHost is DirectorConsoleHost
    assert director_package.PolarisLazyClaude is _NewPolarisLazyClaude


def test_director_console_host_constructor_exposes_runtime_service_only() -> None:
    params = inspect.signature(DirectorConsoleHost.__init__).parameters
    assert "runtime_service_factory" in params
    assert "dialogue_streamer" not in params
def test_director_console_host_snapshots_payload_inside_service_context() -> None:
    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: _DetachedAwareRoleSessionService(),
        task_service_factory=lambda workspace: _FakeTaskService(workspace),
        runtime_service_factory=lambda: runtime,
    )

    session = host.load_session("session-detached")
    sessions = host.list_sessions()
    ensured = host.ensure_session("session-detached")

    assert session is not None
    assert session["id"] == "session-detached"
    assert session["messages"][0]["content"] == "prior user"
    assert sessions[0]["id"] == "session-detached"
    assert ensured["id"] == "session-detached"


def test_director_console_host_bootstraps_minimal_runtime_before_task_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from polaris.delivery.cli.director import console_host as console_host_module

    state = {"bootstrapped": False}

    def _bootstrap() -> None:
        state["bootstrapped"] = True

    def _task_service_factory(workspace: str) -> _FakeTaskService:
        assert workspace == "workspace"
        if not state["bootstrapped"]:
            raise RuntimeError("bootstrap was not invoked")
        return _FakeTaskService(workspace)

    monkeypatch.setattr(console_host_module, "_ensure_minimal_runtime_bindings", _bootstrap)

    runtime = _FakeRoleRuntime()
    host = DirectorConsoleHost(
        "workspace",
        session_service_factory=lambda: _FakeRoleSessionService(),
        task_service_factory=_task_service_factory,
        runtime_service_factory=lambda: runtime,
    )

    status = host.get_status()

    assert state["bootstrapped"] is True
    assert status["task_runtime_available"] is True


def test_toad_console_entrypoint_runs_role_console(monkeypatch: pytest.MonkeyPatch) -> None:
    from polaris.delivery.cli.toad import app as toad_app

    captured: dict[str, Any] = {}

    def _fake_run_role_console(
        *,
        workspace: str | Path = ".",
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
        prompt_style: str | None = None,
        omp_config: str | None = None,
        json_render: str | None = None,
    ) -> int:
        captured["workspace"] = Path(workspace).resolve()
        captured["role"] = role
        captured["backend"] = backend
        captured["session_id"] = session_id
        captured["session_title"] = session_title
        captured["prompt_style"] = prompt_style
        captured["omp_config"] = omp_config
        captured["json_render"] = json_render
        return 0

    monkeypatch.setattr(toad_app, "run_role_console", _fake_run_role_console)

    exit_code = toad_app.run_toad(
        workspace="workspace",
        role="director",
        backend="plain",
        session_id="session-1",
        session_title="Director CLI",
        prompt_style="plain",
        json_render="pretty",
    )

    assert exit_code == 0
    assert captured["workspace"] == Path("workspace").resolve()
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"
    assert captured["session_id"] == "session-1"
    assert captured["session_title"] == "Director CLI"
    assert captured["prompt_style"] == "plain"
    assert captured["json_render"] == "pretty"


def test_director_cli_thin_main_routes_console_to_run_director_console(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import sys

    from polaris.delivery.cli import terminal_console
    from polaris.delivery.cli.director import cli_thin

    captured: dict[str, Any] = {}

    def _fake_run_director_console(
        workspace: str,
        *,
        role: str = "director",
        backend: str = "auto",
        session_id: str | None = None,
        session_title: str | None = None,
    ) -> int:
        captured["workspace"] = workspace
        captured["role"] = role
        captured["backend"] = backend
        captured["session_id"] = session_id
        captured["session_title"] = session_title
        return 19

    monkeypatch.setattr(terminal_console, "run_director_console", _fake_run_director_console)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "director-thin",
            "--workspace",
            str(tmp_path),
            "console",
            "--backend",
            "plain",
            "--session-id",
            "session-7",
            "--session-title",
            "Director CLI",
        ],
    )

    exit_code = cli_thin.main()

    assert exit_code == 19
    assert captured["workspace"] == str(tmp_path.resolve())
    assert captured["role"] == "director"
    assert captured["backend"] == "plain"
    assert captured["session_id"] == "session-7"
    assert captured["session_title"] == "Director CLI"


def test_run_director_console_accepts_non_textual_backend_but_runs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from io import StringIO

    from polaris.delivery.cli import terminal_console

    # Feed /exit so the console loop terminates immediately
    monkeypatch.setattr("sys.stdin", StringIO("/exit\n"))

    exit_code = terminal_console.run_role_console(
        workspace=str(tmp_path),
        role="architect",
        backend="plain",
        session_id=None,
        session_title="Fallback",
    )

    assert exit_code == 0
