from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, NoReturn

from polaris.delivery.cli import terminal_console


class _FakeRoleConsoleHost:
    _ALLOWED_ROLES = frozenset({"director", "pm", "architect", "chief_engineer", "qa"})
    instances: list[_FakeRoleConsoleHost] = []
    stream_factory: Any = None

    def __init__(self, workspace: str, *, role: str = "director") -> None:
        self.workspace = workspace
        self.role = role
        self.config = SimpleNamespace(host_kind="cli")
        self.ensure_calls: list[dict[str, Any]] = []
        self.create_calls: list[dict[str, Any]] = []
        self.stream_calls: list[dict[str, Any]] = []
        type(self).instances.append(self)

    def ensure_session(
        self,
        session_id: str | None = None,
        *,
        title: str | None = None,
        context_config: dict[str, Any] | None = None,
        capability_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role = str((context_config or {}).get("role") or self.role)
        resolved = session_id or f"{role}-session-{len(self.ensure_calls) + 1}"
        payload = {
            "session_id": session_id,
            "title": title,
            "context_config": dict(context_config or {}),
            "capability_profile": dict(capability_profile or {}),
            "resolved": resolved,
        }
        self.ensure_calls.append(payload)
        return {
            "id": resolved,
            "context_config": dict(context_config or {}),
            "capability_profile": dict(capability_profile or {}),
        }

    def create_session(
        self,
        *,
        title: str | None = None,
        context_config: dict[str, Any] | None = None,
        capability_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        role = str((context_config or {}).get("role") or self.role)
        resolved = f"{role}-new-{len(self.create_calls) + 1}"
        payload = {
            "title": title,
            "context_config": dict(context_config or {}),
            "capability_profile": dict(capability_profile or {}),
            "resolved": resolved,
        }
        self.create_calls.append(payload)
        return {
            "id": resolved,
            "context_config": dict(context_config or {}),
            "capability_profile": dict(capability_profile or {}),
        }

    async def stream_turn(
        self,
        session_id: str | None,
        message: str,
        *,
        context: dict[str, Any] | None = None,
        role: str | None = None,
        debug: bool = False,
        enable_cognitive: bool | None = None,
    ):
        self.stream_calls.append(
            {
                "session_id": session_id,
                "message": message,
                "context": dict(context or {}),
                "role": role,
                "debug": debug,
                "enable_cognitive": enable_cognitive,
            }
        )
        stream_factory = type(self).stream_factory
        if callable(stream_factory):
            async for event in stream_factory(
                session_id=session_id,
                message=message,
                context=dict(context or {}),
                role=role,
                debug=debug,
                enable_cognitive=enable_cognitive,
            ):
                yield event
            return
        yield {"type": "complete", "data": {"content": "ok"}}


class _FakeTTYStream:
    def __init__(self) -> None:
        self._chunks: list[str] = []

    def isatty(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._chunks.append(str(text))
        return len(str(text))

    def flush(self) -> None:
        return

    def getvalue(self) -> str:
        return "".join(self._chunks)


def test_capability_profile_contains_governance_scope() -> None:
    profile = terminal_console._build_role_capability_profile(role="pm", host_kind="cli")
    assert profile["host_kind"] == "cli"
    assert profile["role"] == "pm"
    assert profile["metadata"]["governance_scope"] == "role:pm"
    assert profile["metadata"]["source"] == "polaris.delivery.cli.terminal_console"


def test_run_role_console_switches_role_with_role_bound_session_and_profile(
    monkeypatch,
) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    _FakeRoleConsoleHost.instances.clear()
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["/role pm", "/session", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    exit_code = terminal_console.run_role_console(
        workspace=".",
        role="director",
        backend="auto",
    )

    assert exit_code == 0
    assert len(_FakeRoleConsoleHost.instances) == 1
    host = _FakeRoleConsoleHost.instances[0]
    assert len(host.create_calls) >= 2

    first = host.create_calls[0]
    assert first["context_config"]["role"] == "director"
    assert first["context_config"]["host_kind"] == "cli"
    assert first["capability_profile"]["role"] == "director"
    assert first["capability_profile"]["metadata"]["governance_scope"] == "role:director"

    second = host.create_calls[1]
    assert second["context_config"]["role"] == "pm"
    assert second["context_config"]["host_kind"] == "cli"
    assert second["capability_profile"]["role"] == "pm"
    assert second["capability_profile"]["metadata"]["governance_scope"] == "role:pm"


def test_run_role_console_uses_explicit_session_id_for_resume(monkeypatch) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    _FakeRoleConsoleHost.instances.clear()
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    exit_code = terminal_console.run_role_console(
        workspace=".",
        role="director",
        session_id="sess-explicit",
    )

    assert exit_code == 0
    assert len(_FakeRoleConsoleHost.instances) == 1
    host = _FakeRoleConsoleHost.instances[0]
    assert len(host.ensure_calls) == 1
    assert host.ensure_calls[0]["session_id"] == "sess-explicit"
    assert host.create_calls == []


def test_json_event_render_modes_roundtrip() -> None:
    packet = terminal_console._json_event_packet("tool_call", {"tool": "read_file"})
    raw = terminal_console._json_event_text(packet, mode="raw")
    pretty = terminal_console._json_event_text(packet, mode="pretty")

    assert json.loads(raw) == {"type": "tool_call", "data": {"tool": "read_file"}}
    assert json.loads(pretty) == {"type": "tool_call", "data": {"tool": "read_file"}}
    assert "\n" not in raw
    assert "\n" in pretty


def test_run_role_console_supports_json_render_switch_command(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    _FakeRoleConsoleHost.instances.clear()
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["/json pretty", "/json", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    exit_code = terminal_console.run_role_console(
        workspace=".",
        role="director",
        json_render="raw",
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "json_render=pretty" in captured.out


def test_run_role_console_omp_prompt_falls_back_to_plain(monkeypatch) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    _FakeRoleConsoleHost.instances.clear()
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    prompts: list[str] = []

    def _fake_input(prompt: str = "") -> str:
        prompts.append(prompt)
        return "/exit"

    def _raise_oserror(*_args, **_kwargs) -> NoReturn:
        raise OSError("oh-my-posh unavailable")

    monkeypatch.setattr("builtins.input", _fake_input)
    monkeypatch.setattr(terminal_console.subprocess, "run", _raise_oserror)

    exit_code = terminal_console.run_role_console(
        workspace=".",
        role="director",
        prompt_style="omp",
    )

    assert exit_code == 0
    assert prompts
    assert prompts[0].endswith("> ")


def test_run_role_console_does_not_print_complete_content_twice(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    async def _stream_factory(**_kwargs):
        yield {"type": "content_chunk", "data": {"content": "summary ready"}}
        yield {"type": "tool_call", "data": {"tool": "read_file", "args": {"path": "README.md"}}}
        yield {
            "type": "tool_result",
            "data": {
                "tool": "read_file",
                "success": True,
                "result": {"path": "README.md"},
            },
        }
        yield {"type": "complete", "data": {"content": "summary ready"}}

    _FakeRoleConsoleHost.instances.clear()
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    try:
        exit_code = terminal_console.run_role_console(
            workspace=".",
            role="director",
            json_render="raw",
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    captured = capsys.readouterr()
    assert exit_code == 0
    assert captured.out.count("summary ready") == 1
    assert "tool_call" in captured.out
    assert "tool_result" in captured.out
    assert "read_file" in captured.out


def test_run_role_console_streams_thinking_blocks(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    async def _stream_factory(**_kwargs):
        yield {"type": "thinking_chunk", "data": {"content": "先分析"}}
        yield {"type": "thinking_chunk", "data": {"content": "目录结构。"}}
        yield {"type": "content_chunk", "data": {"content": "这是最终回答。"}}
        yield {
            "type": "complete",
            "data": {"content": "这是最终回答。", "thinking": "先分析目录结构。"},
        }

    _FakeRoleConsoleHost.instances.clear()
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    try:
        exit_code = terminal_console.run_role_console(
            workspace=".",
            role="director",
            json_render="raw",
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "<thinking>" in captured.out
    assert "</thinking>" in captured.out
    assert "先分析目录结构。" in captured.out
    assert captured.out.count("这是最终回答。") == 1


def test_run_role_console_prints_complete_thinking_fallback(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    async def _stream_factory(**_kwargs):
        yield {
            "type": "complete",
            "data": {"content": "仅最终答案。", "thinking": "先做最终归纳。"},
        }

    _FakeRoleConsoleHost.instances.clear()
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    try:
        exit_code = terminal_console.run_role_console(
            workspace=".",
            role="director",
            json_render="raw",
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "<thinking>" in captured.out
    assert "</thinking>" in captured.out
    assert "先做最终归纳。" in captured.out
    assert captured.out.count("仅最终答案。") == 1


def test_run_role_console_keeps_tool_events_visible_between_thinking_and_answer(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    async def _stream_factory(**_kwargs):
        yield {"type": "thinking_chunk", "data": {"content": "先读取关键文件。"}}
        yield {"type": "tool_call", "data": {"tool": "read_file", "args": {"path": "README.md"}}}
        yield {
            "type": "tool_result",
            "data": {
                "tool": "read_file",
                "success": True,
                "result": {"path": "README.md", "bytes": 128},
            },
        }
        yield {
            "type": "complete",
            "data": {"content": "这是最终回答。", "thinking": "先读取关键文件。"},
        }

    _FakeRoleConsoleHost.instances.clear()
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    try:
        exit_code = terminal_console.run_role_console(
            workspace=".",
            role="director",
            json_render="raw",
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "<thinking>" in captured.out
    assert "</thinking>" in captured.out
    assert "tool_call" in captured.out
    assert "tool_result" in captured.out
    assert "read_file" in captured.out
    assert captured.out.count("这是最终回答。") == 1


def test_run_role_console_renders_debug_events_and_passes_debug_flag(monkeypatch, capsys) -> None:
    import polaris.delivery.cli.director.console_host as console_host_module

    async def _stream_factory(**kwargs):
        assert kwargs["debug"] is True
        yield {
            "type": "debug",
            "data": {
                "category": "llm_request",
                "label": "final",
                "source": "kernelone.llm.stream_executor",
                "tags": {"role": "director", "session_id": "director-new-1"},
                "payload": {
                    "provider_id": "anthropic_compat",
                    "model": "kimi-for-coding",
                    "prompt_input": "最终送给模型的完整内容",
                    "invoke_config": {"temperature": 0.7, "max_tokens": 4000, "stream": True},
                },
            },
        }
        yield {"type": "thinking_chunk", "data": {"content": "先做分析。"}}
        yield {"type": "tool_call", "data": {"tool": "read_file", "args": {"path": "README.md"}}}
        yield {"type": "tool_result", "data": {"tool": "read_file", "success": True}}
        yield {"type": "content_chunk", "data": {"content": "最终答案。"}}
        yield {"type": "complete", "data": {"content": "最终答案。", "thinking": "先做分析。"}}

    _FakeRoleConsoleHost.instances.clear()
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    monkeypatch.setattr(console_host_module, "RoleConsoleHost", _FakeRoleConsoleHost)

    scripted_inputs = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt="": next(scripted_inputs))

    try:
        exit_code = terminal_console.run_role_console(
            workspace=".",
            role="director",
            json_render="pretty",
            debug=True,
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    captured = capsys.readouterr()
    assert exit_code == 0
    host = _FakeRoleConsoleHost.instances[0]
    assert host.stream_calls
    assert host.stream_calls[0]["debug"] is True
    assert "[debug][llm_request][final]" in captured.out
    assert '"prompt_input": "最终送给模型的完整内容"' in captured.out
    assert "<thinking>" in captured.out
    assert "tool_call" in captured.out
    assert "tool_result" in captured.out
    assert captured.out.count("最终答案。") == 1


def test_turn_spinner_writes_and_clears_line() -> None:
    stream = _FakeTTYStream()
    spinner = terminal_console._TurnSpinner(
        enabled=True,
        stream=stream,
        label="LLM request in progress",
        interval_seconds=0.001,
    )

    async def _exercise() -> None:
        spinner.start()
        await asyncio.sleep(0.005)
        await spinner.stop()

    asyncio.run(_exercise())
    output = stream.getvalue()
    assert "LLM request in progress" in output
    assert "\r" in output


def test_prompt_renderer_spinner_label_uses_omp_secondary(monkeypatch) -> None:
    class _Completed:
        def __init__(self, stdout: str) -> None:
            self.returncode = 0
            self.stdout = stdout

    def _fake_run(*args, **kwargs):
        command = list(args[0] if args else kwargs.get("args", []))
        if "secondary" in command:
            return _Completed("OMP-SECONDARY")
        return _Completed("")

    monkeypatch.setattr(terminal_console.subprocess, "run", _fake_run)

    state = terminal_console._ConsoleRenderState(prompt_style="omp")
    renderer = terminal_console._PromptRenderer(state)
    label = renderer.render_spinner_label(
        role="director",
        session_id="sess-1",
        workspace=Path("."),
    )
    assert "OMP-SECONDARY" in label
    assert "LLM request in progress" in label


def test_stream_turn_spinner_ignores_fingerprint_until_visible_event(monkeypatch) -> None:
    stage = {"value": "init"}

    async def _stream_factory(**_kwargs):
        stage["value"] = "fingerprint"
        yield {"type": "fingerprint", "data": {"profile_id": "director.execution"}}
        await asyncio.sleep(0)
        stage["value"] = "content_chunk"
        yield {"type": "content_chunk", "data": {"content": "hello"}}
        stage["value"] = "complete"
        yield {"type": "complete", "data": {"content": "hello"}}

    class _RecorderSpinner:
        def __init__(self) -> None:
            self.stops: list[str] = []
            self._stopped = False

        def start(self) -> None:
            return

        async def stop(self) -> None:
            if self._stopped:
                return
            self._stopped = True
            self.stops.append(stage["value"])

    spinner = _RecorderSpinner()
    monkeypatch.setattr(terminal_console, "_create_turn_spinner", lambda **_kwargs: spinner)

    host = _FakeRoleConsoleHost(".")
    _FakeRoleConsoleHost.stream_factory = _stream_factory
    try:
        asyncio.run(
            terminal_console._stream_turn(
                host,  # type: ignore[arg-type]
                role="director",
                session_id="sess-1",
                message="hi",
                json_render="raw",
                debug=False,
                spinner_label="LLM request in progress",
            )
        )
    finally:
        _FakeRoleConsoleHost.stream_factory = None

    assert spinner.stops == ["content_chunk"]
