"""Shared synthetic native HTTP provider endpoint for Factory/Role runtime tests.

This module is the single owner of the loopback-only native provider endpoint
used by the CE native runtime tests: the real local HTTP transport, the
network-deny isolation, the process-local provider configuration and the server
teardown. Only the remote model answers are synthetic; Factory, Runtime,
Kernel, provider attempt and receipt paths stay real.

Consumers wire the resource through :func:`native_endpoint_context`, an explicit
resource context manager. pytest decorators stay in test modules for test
assembly only; a decorated fixture is never called as a plain function.
"""

from __future__ import annotations

import hashlib
import json
import socket
import threading
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, TypeVar

import pytest
from polaris.cells.chief_engineer.blueprint.public import VerificationCommandAuthorityV1

NativeEndpointT = TypeVar("NativeEndpointT", bound="NativeEndpoint")


def _pm_payload() -> dict[str, Any]:
    """Hand-written two-task library contract, not a mocked PM stage result."""
    return {
        "tasks": [
            {
                "id": "TASK-1",
                "title": "Implement cancellation plan library",
                "goal": "Implement a deterministic cancellation plan with normal and invalid input behavior.",
                "description": "Create a Python library API build_cancellation_plan and retain cancellation invariants.",
                "scope": "Python cancellation library and dependency manifest",
                "scope_paths": ["src/cancel.py", "requirements.txt"],
                "target_files": ["src/cancel.py", "requirements.txt"],
                "steps": ["Declare dependency manifest", "Implement deterministic cancellation plan API"],
                "acceptance": ["Python compilation passes", "Library exports build_cancellation_plan"],
                "verification_commands": [
                    {
                        "modality": "environment_prep",
                        "argv": ["python", "-m", "pip", "install", "-r", "requirements.txt"],
                        "cwd": ".",
                    },
                    {"modality": "build", "argv": ["python", "-m", "compileall", "-q", "src"], "cwd": "."},
                ],
                "phase": "implementation",
                "depends_on": [],
                "assigned_to": "Director",
            },
            {
                "id": "TASK-2",
                "title": "Verify cancellation plan library",
                "goal": "Test normal, boundary and invalid cancellation behavior and document library usage.",
                "description": "Cover the library API with behavior assertions and describe installation and tests.",
                "scope": "Cancellation tests and README",
                "scope_paths": ["tests/test_cancel.py", "README.md"],
                "target_files": ["tests/test_cancel.py", "README.md"],
                "steps": ["Test cancellation plan outcomes", "Document installation and test command"],
                "acceptance": ["Unittest executes real behavior assertions", "README documents the Python library API"],
                "verification_commands": [
                    {"modality": "test", "argv": ["python", "-m", "unittest", "discover", "-s", "tests"], "cwd": "."}
                ],
                "phase": "verification",
                "depends_on": ["TASK-1"],
                "assigned_to": "Director",
            },
        ]
    }


class NativeEndpoint:
    """Real local HTTP transport supplying deliberately bounded native answers."""

    def __init__(self, outcome: str) -> None:
        self.outcome = outcome
        self.requests: list[dict[str, Any]] = []
        self.outputs: list[dict[str, Any]] = []
        self.errors: list[str] = []
        self.ce_calls = 0
        self.base: dict[str, Any] | None = None

    @staticmethod
    def _ce_payload() -> dict[str, Any]:
        source = "artifact-source"
        test = "artifact-test"
        build = VerificationCommandAuthorityV1(
            task_id="TASK-1", modality="build", argv=("python", "-m", "compileall", "-q", "src"), cwd="."
        )
        tests = VerificationCommandAuthorityV1(
            task_id="TASK-2", modality="test", argv=("python", "-m", "unittest", "discover", "-s", "tests"), cwd="."
        )
        environment = VerificationCommandAuthorityV1(
            task_id="TASK-1",
            modality="environment_prep",
            argv=("python", "-m", "pip", "install", "-r", "requirements.txt"),
            cwd=".",
        )
        return {
            "construction_plan": {
                "task_plans": {task: {"behavior_invariant_refs": ["cancel-stable"]} for task in ("TASK-1", "TASK-2")},
                "project_interface_contract": {
                    "provider_declarations": [
                        {
                            "path": "src/cancel.py",
                            "name": "build_cancellation_plan",
                            "symbol_kind": "function",
                            "signature": "build_cancellation_plan() -> dict[str, object]",
                            "semantic_role": "build cancellation behavior",
                        }
                    ],
                    "consumer_declarations": [],
                },
                "shared_behavior_contract": {
                    "invariants": [
                        {
                            "invariant_id": "cancel-stable",
                            "statement": "Identical valid cancellation inputs yield identical plans; invalid inputs raise ValueError.",
                            "owner_task_id": "TASK-1",
                            "consumer_task_ids": ["TASK-2"],
                            "covered_obligation_ids": [source, test],
                            "verification_examples": [
                                {
                                    "given": "a valid cancellation request",
                                    "when": "build_cancellation_plan is called twice",
                                    "then": "both plans contain identical cancellation outcomes",
                                }
                            ],
                        }
                    ]
                },
            },
            "project_completion_contract": {
                "obligations": {
                    "artifacts": [
                        {
                            "obligation_id": source,
                            "path": "src/cancel.py",
                            "semantic_role": "source",
                            "applicability": "required",
                            "owner_task_id": "TASK-1",
                        },
                        {
                            "obligation_id": "artifact-manifest",
                            "path": "requirements.txt",
                            "semantic_role": "manifest",
                            "applicability": "required",
                            "owner_task_id": "TASK-1",
                        },
                        {
                            "obligation_id": test,
                            "path": "tests/test_cancel.py",
                            "semantic_role": "test",
                            "applicability": "required",
                            "owner_task_id": "TASK-2",
                        },
                        {
                            "obligation_id": "artifact-docs",
                            "path": "README.md",
                            "semantic_role": "docs",
                            "applicability": "required",
                            "owner_task_id": "TASK-2",
                        },
                    ],
                    "entrypoints": [
                        {
                            "obligation_id": "library-na",
                            "kind": "library",
                            "applicability": "not_applicable",
                            "owner_task_id": None,
                            "source_path": None,
                            "runtime_path": None,
                            "command": None,
                        }
                    ],
                    "verification": [
                        {
                            "obligation_id": "verify-build",
                            "modality": "build",
                            "command_authority_hash": build.authority_hash,
                            "applicability": "required",
                            "owner_task_id": "TASK-1",
                            "covers_obligation_ids": [source],
                        },
                        {
                            "obligation_id": "verify-test",
                            "modality": "test",
                            "command_authority_hash": tests.authority_hash,
                            "applicability": "required",
                            "owner_task_id": "TASK-2",
                            "covers_obligation_ids": [source, test],
                        },
                        {
                            "obligation_id": "verify-environment",
                            "modality": "environment_prep",
                            "command_authority_hash": environment.authority_hash,
                            "applicability": "required",
                            "owner_task_id": "TASK-1",
                            "covers_obligation_ids": ["artifact-manifest"],
                        },
                    ],
                }
            },
            "risk_flags": [],
        }

    def response(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(deepcopy(request))
        messages = request.get("messages", [])
        role_text = "\n".join(str(message.get("content") or "") for message in messages)
        tools = request.get("tools", [])
        function: dict[str, Any] | None = None
        if "polaris.role_identity.v1:pm" in role_text:
            # PM consumes a visible task-contract JSON object. Optional read
            # tools are not a result channel for that contract.
            payload = _pm_payload()
        else:
            function = next(
                tool["function"]
                for tool in tools
                if tool.get("function", {}).get("name") == "submit_structured_role_output"
            )
            self.ce_calls += 1
            if self.ce_calls == 1:
                self.base = self._ce_payload()
                if self.outcome == "foreign_owner":
                    self.base["project_completion_contract"]["obligations"]["artifacts"][0]["owner_task_id"] = (
                        "TASK-FOREIGN"
                    )
                self.base["construction_plan"].pop("task_plans")
                self.base["item"] = "This is not a declared portfolio member."
                payload = deepcopy(self.base)
            elif self.ce_calls == 2:
                assert self.base is not None
                base_hash = hashlib.sha256(
                    json.dumps(self.base, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest()
                payload = {
                    "base_candidate_hash": base_hash,
                    "edits": [
                        {
                            "operation": "add",
                            "path": ["construction_plan", "task_plans"],
                            "value": self._ce_payload()["construction_plan"]["task_plans"],
                        },
                        {"operation": "remove", "path": ["item"]},
                    ],
                }
                if self.outcome == "forged_hash":
                    payload["base_candidate_hash"] = "0" * 64
            elif self.ce_calls == 3 and self.outcome != "accept":
                assert self.base is not None
                # An attempted full reconstruction stays explicitly invalid;
                # it must not conceal rejection of the forged partial patch.
                payload = deepcopy(self.base)
            else:
                raise AssertionError("Unexpected extra CE physical request")
        self.outputs.append(deepcopy(payload))
        message: dict[str, Any] = {"role": "assistant", "content": None}
        if function is None:
            message["content"] = json.dumps(payload, ensure_ascii=False)
            finish_reason = "stop"
        else:
            message["tool_calls"] = [
                {
                    "id": f"fixture-call-{len(self.requests)}",
                    "type": "function",
                    "function": {"name": function["name"], "arguments": json.dumps(payload, ensure_ascii=False)},
                }
            ]
            finish_reason = "tool_calls"
        return {
            "id": f"fixture-completion-{len(self.requests)}",
            "object": "chat.completion",
            "created": 0,
            "model": request.get("model", "gpt-4.1"),
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 128, "completion_tokens": 128, "total_tokens": 256},
        }


class _LoopbackServerOwner:
    """Own one loopback HTTP server plus its serving thread.

    The owner tracks whether ``serve_forever`` was actually started, so a
    failure in ``thread.start()`` never calls ``shutdown()`` on a server whose
    serve loop never ran (that would block forever). Release is idempotent and
    every step is attempted even when an earlier step raises, so one failing
    step cannot silently skip the remaining releases.
    """

    def __init__(self, server: ThreadingHTTPServer, thread: threading.Thread) -> None:
        self._server = server
        self._thread = thread
        self._serving = False
        self._released = False

    def mark_serving(self) -> None:
        """Record that the serve loop is running and can be shut down."""
        self._serving = True

    def release(self) -> None:
        """Stop the serve loop, close the listener and join the thread."""
        if self._released:
            return
        self._released = True
        errors: list[Exception] = []
        if self._serving:
            try:
                self._server.shutdown()
            except (OSError, RuntimeError) as exc:
                errors.append(exc)
        try:
            self._server.server_close()
        except (OSError, RuntimeError) as exc:
            errors.append(exc)
        if self._thread.is_alive():
            self._thread.join(timeout=5)
            if self._thread.is_alive():
                errors.append(RuntimeError("native endpoint serving thread did not stop"))
        if errors:
            raise RuntimeError("native endpoint resource release failed") from errors[0]


@contextmanager
def native_endpoint_context(
    *,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    outcome: str,
    endpoint_class: type[NativeEndpointT],
) -> Iterator[NativeEndpointT]:
    """Own the loopback endpoint, network deny, provider config and teardown.

    The endpoint serves deliberately bounded native answers over a real local
    HTTP transport. Every socket outside that private endpoint is refused, the
    provider configuration is process-local and the server is always torn down.

    ``endpoint_class`` is an explicit dependency: consumers that need a
    different response behaviour pass their own subclass instead of rewriting a
    module global behind this resource.
    """
    endpoint = endpoint_class(outcome)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            try:
                assert self.path == "/v1/chat/completions"
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])).decode("utf-8"))
                response = endpoint.response(request)
                if request.get("stream"):
                    choice = response["choices"][0]
                    message = choice["message"]
                    delta: dict[str, Any] = {"role": "assistant"}
                    if message.get("tool_calls"):
                        delta["tool_calls"] = [{"index": 0, **message["tool_calls"][0]}]
                    else:
                        delta["content"] = message["content"]
                    first = {
                        "id": response["id"],
                        "model": response["model"],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
                    }
                    last = {
                        "id": response["id"],
                        "model": response["model"],
                        "choices": [{"index": 0, "delta": {}, "finish_reason": choice["finish_reason"]}],
                        "usage": response["usage"],
                    }
                    body = (
                        "data: "
                        + json.dumps(first, ensure_ascii=False)
                        + "\n\ndata: "
                        + json.dumps(last, ensure_ascii=False)
                        + "\n\ndata: [DONE]\n\n"
                    ).encode("utf-8")
                    content_type = "text/event-stream"
                else:
                    body = json.dumps(response, ensure_ascii=False).encode("utf-8")
                    content_type = "application/json"
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except (AssertionError, KeyError, TypeError, ValueError) as exc:
                endpoint.errors.append(f"{type(exc).__name__}:{exc}")
                self.send_error(500, "Fixture request rejected")

        def log_message(self, format: str, *args: object) -> None:
            del format, args

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    owner = _LoopbackServerOwner(server, thread)
    with ExitStack() as resources:
        # The listener socket already exists at this point, so the release
        # callback is registered before anything else can fail. A failure in
        # thread.start() therefore still closes the socket, and the owner knows
        # the serve loop never ran so it never calls the blocking shutdown().
        resources.callback(owner.release)
        thread.start()
        owner.mark_serving()
        port = int(server.server_address[1])
        original_connect = socket.socket.connect
        original_getaddrinfo = socket.getaddrinfo

        def restricted_connect(sock: socket.socket, address: Any) -> Any:
            if not isinstance(address, tuple) or address[:2] != ("127.0.0.1", port):
                raise OSError("Test denies non-fixture network connection")
            return original_connect(sock, address)

        def restricted_getaddrinfo(host: Any, requested_port: Any, *args: Any, **kwargs: Any) -> Any:
            if host not in {"127.0.0.1", b"127.0.0.1"} or int(requested_port) != port:
                raise OSError("Test denies non-fixture DNS or network lookup")
            return original_getaddrinfo(host, requested_port, *args, **kwargs)

        monkeypatch.setattr(socket.socket, "connect", restricted_connect)
        monkeypatch.setattr(socket.socket, "connect_ex", restricted_connect)
        monkeypatch.setattr(socket, "getaddrinfo", restricted_getaddrinfo)
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("KERNELONE_HOME", str(tmp_path / "home"))
        monkeypatch.setenv("KERNELONE_WORKSPACE", str(tmp_path))
        monkeypatch.setenv("KERNELONE_RUNTIME_ROOT", str(tmp_path / ".polaris" / "runtime"))
        monkeypatch.delenv("KERNELONE_RUNTIME_CACHE_ROOT", raising=False)
        monkeypatch.setenv("NO_PROXY", "*")
        monkeypatch.setenv("no_proxy", "*")
        monkeypatch.setenv("KERNELONE_LIGHTWEIGHT_STREAM_SESSIONS", "0")
        # A preceding unit test may have cached the lightweight fake aiohttp
        # module. Reset only these test-owned import caches so this fixture always
        # uses the installed real transport, with all sockets still restricted.
        from polaris.infrastructure.llm.providers.provider_helpers import _http_pooling

        monkeypatch.setattr(_http_pooling, "_aiohttp_module", None)
        monkeypatch.setattr(_http_pooling, "_REAL_CLIENT_SESSION_TYPE", None)
        # Delivery push is outside this test: canonical Factory/FactStream/attempt
        # ledgers remain real. Do not contact the developer's shared NATS process.
        monkeypatch.setenv("KERNELONE_NATS_ENABLED", "0")
        config_path = tmp_path / "llm-config.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "providers": {
                        "runtime-fixture": {
                            "type": "openai_compat",
                            "name": "Private local native fixture",
                            "base_url": f"http://127.0.0.1:{port}/v1",
                            "api_key": "non-secret-fixture-value",
                            "default_model": "gpt-4.1",
                            "max_tokens": 128000,
                            "context_window": 1000000,
                            "retries": 0,
                        }
                    },
                    "roles": {
                        role: {"provider_id": "runtime-fixture", "model": "gpt-4.1"}
                        for role in ("pm", "architect", "chief_engineer", "director", "qa")
                    },
                    "policies": {},
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(config_path))
        # The ExitStack owns the single release path: leaving the with block
        # closes the server and joins the thread exactly once.
        yield endpoint
