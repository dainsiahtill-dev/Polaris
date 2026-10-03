"""CE residual patch through real Factory, Runtime, Kernel and sealed Provider.

The only external dependency double is a private loopback HTTP endpoint. All
workspace/config/runtime effects belong to pytest's tmp_path. No opaque Factory
grant, Runtime result, Kernel or provider implementation is substituted.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import socket
import threading
from collections.abc import Iterator
from copy import deepcopy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from polaris.cells.chief_engineer.blueprint.public import VerificationCommandAuthorityV1
from polaris.cells.events.fact_stream.public import (
    BootstrapFactStreamWorkspaceCommandV1,
    bootstrap_fact_stream_workspace,
    fact_stream_bootstrap_streams,
)
from polaris.cells.factory.pipeline.public.service import FactoryConfig, FactoryRunService
from polaris.cells.roles.kernel.public.provider_attempt_lifecycle_replay import (
    QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
    QueryFactoryProviderAttemptLifecycleReplayV1,
    query_factory_provider_attempt_lifecycle_replay,
)


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


class _NativeEndpoint:
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
                self.base["item"] = {"draft_note": "This is not a declared portfolio member."}
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


@pytest.fixture
def isolated_native_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[_NativeEndpoint]:
    """Isolate roots and refuse every socket outside the private HTTP endpoint."""
    endpoint = _NativeEndpoint(str(request.param))

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
    thread.start()
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
    try:
        yield endpoint
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("isolated_native_endpoint", ["accept", "foreign_owner", "forged_hash"], indirect=True)
def test_real_factory_runtime_adjudicates_residual_patch_without_replaying_pm(
    tmp_path: Path, isolated_native_endpoint: _NativeEndpoint
) -> None:
    """Catch partial-protocol rejection, forged CAS acceptance and semantic bypass."""

    async def run() -> None:
        catalog = tmp_path / ".polaris" / "catalog_contract.json"
        catalog.parent.mkdir(parents=True, exist_ok=True)
        catalog.write_text(json.dumps({"project_kind": "library"}), encoding="utf-8")
        bootstrap_fact_stream_workspace(
            BootstrapFactStreamWorkspaceCommandV1(
                workspace=str(tmp_path),
                maintenance_reason="ce-schema-real-runtime-test",
                streams=fact_stream_bootstrap_streams(),
            )
        )
        service = FactoryRunService(tmp_path)
        factory_run = await service.create_run(
            FactoryConfig(
                name="Cancellation library",
                description="Python cancellation library",
                stages=["pm_planning", "chief_engineer_review"],
            )
        )
        await service.start_run(factory_run.id)
        context = {
            "directive": "Build a Python cancellation plan library with behavior tests and README. This is a library, not a CLI or application.",
            "timeout": 1200,
        }
        pm_result = await service.execute_stage(factory_run.id, "pm_planning", context)
        assert pm_result.status == "success", pm_result.output
        runtime_root = tmp_path / ".polaris" / "runtime"
        pm_path = runtime_root / "tasks" / "plan.json"
        pm_tasks = json.loads(pm_path.read_text(encoding="utf-8"))["tasks"]
        assert {task["id"] for task in pm_tasks} == {"TASK-1", "TASK-2"}
        assert {path for task in pm_tasks for path in task["target_files"]} == {
            "src/cancel.py",
            "requirements.txt",
            "tests/test_cancel.py",
            "README.md",
        }
        pm_hash = hashlib.sha256(pm_path.read_bytes()).hexdigest()
        pm_posts = len(isolated_native_endpoint.requests)
        ce_result = await service.execute_stage(factory_run.id, "chief_engineer_review", context)
        ce_output = str(ce_result.output or "")
        expected_status = "success" if isolated_native_endpoint.outcome == "accept" else "failed"
        assert ce_result.status == expected_status, ce_output
        assert hashlib.sha256(pm_path.read_bytes()).hexdigest() == pm_hash
        events = await service.get_run_events(factory_run.id)
        assert (
            sum(event.get("type") == "stage_started" and event.get("stage") == "pm_planning" for event in events) == 1
        )
        assert (
            sum(
                event.get("type") == "stage_started" and event.get("stage") == "chief_engineer_review"
                for event in events
            )
            == 1
        )
        assert not isolated_native_endpoint.errors
        patch_request = isolated_native_endpoint.requests[pm_posts + 1]
        patch_parameters = patch_request["tools"][0]["function"]["parameters"]
        assert patch_parameters["required"] == ["base_candidate_hash", "edits"]
        assert patch_parameters["properties"]["edits"]["prefixItems"][0]["properties"]["path"]["const"] == [
            "construction_plan",
            "task_plans",
        ]
        assert patch_parameters["properties"]["edits"]["prefixItems"][1]["properties"]["operation"]["const"] == "remove"
        served_patch = isolated_native_endpoint.outputs[pm_posts + 1]
        assert set(served_patch) == {"base_candidate_hash", "edits"}
        assert "construction_plan" not in served_patch
        # The source and result are bound to different native artifacts: the
        # request contains a patch schema, the retained full candidate does not.
        review = json.loads(
            (runtime_root / "state" / "blueprints" / f"{factory_run.id}.review.json").read_text(encoding="utf-8")
        )
        if isolated_native_endpoint.outcome == "accept":
            assert isolated_native_endpoint.ce_calls == 2
            signal = next(
                item for item in review["signals"] if item["code"] == "chief_engineer.structured_candidate_persisted"
            )
            candidates = list(
                (runtime_root / "state" / "blueprints" / "semantic-repair").rglob(f"{signal['candidate_hash']}.json")
            )
            assert len(candidates) == 1
            candidate_record = json.loads(candidates[0].read_text(encoding="utf-8"))
            candidate = candidate_record["candidate"]
            assert (
                candidate["project_completion_contract"]
                == isolated_native_endpoint._ce_payload()["project_completion_contract"]
            )
            assert "base_candidate_hash" not in candidate and "edits" not in candidate
            assert "item" not in candidate
            assert candidate["construction_plan"]["task_plans"]["TASK-1"]["behavior_invariant_refs"] == [
                "cancel-stable"
            ]
            assert review["generated_blueprints"] == 2
        else:
            assert review["generated_blueprints"] == 0
            if isolated_native_endpoint.outcome == "forged_hash":
                assert not any(
                    item["code"] == "chief_engineer.structured_candidate_persisted" for item in review["signals"]
                )
            else:
                # Persisting a schema-valid *untrusted* candidate is deliberate;
                # it must never approve an unknown PM owner or yield a handoff.
                assert "invalid_project_completion_contract" in ce_output
                assert "TASK-FOREIGN" in ce_output or any(
                    "owner" in str(item.get("detail", "")).lower() for item in review["signals"]
                )
            assert 2 <= isolated_native_endpoint.ce_calls <= 3
        lifecycle = query_factory_provider_attempt_lifecycle_replay(
            QueryFactoryProviderAttemptLifecycleReplayV1(
                schema_version=QUERY_FACTORY_PROVIDER_ATTEMPT_LIFECYCLE_REPLAY_SCHEMA,
                workspace=str(tmp_path),
                factory_run_id=factory_run.id,
            )
        )
        ce_starts = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "start"]
        ce_terminals = [fact for fact in lifecycle.facts if fact.role == "chief_engineer" and fact.phase == "terminal"]
        assert len(ce_starts) == len(ce_terminals) == isolated_native_endpoint.ce_calls
        assert {fact.provider_request_id for fact in ce_starts} == {fact.provider_request_id for fact in ce_terminals}
        patch_start = ce_starts[1]
        snapshot_ref = patch_start.context_snapshot_ref
        snapshot = json.loads((runtime_root / "contexts" / snapshot_ref[:2] / snapshot_ref).read_text(encoding="utf-8"))
        assert snapshot["provider_request_id"] == patch_start.provider_request_id
        wire_tools = snapshot["durable_view"]["physical_wire"]["body"]["tools"]
        assert wire_tools[0]["function"]["parameters"] == patch_parameters
        if isolated_native_endpoint.outcome == "accept":
            llm_events = [
                json.loads(line)
                for line in (runtime_root / "events" / "chief_engineer.llm.events.jsonl")
                .read_text(encoding="utf-8")
                .splitlines()
                if line.strip()
            ]
            patch_end = next(
                event["data"]
                for event in llm_events
                if event["data"]["event_type"] == "llm_call_end"
                and event["data"]["task_id"].endswith("SCHEMA-REPAIR")
                and event["data"]["metadata"].get("native_tool_call_envelopes")
            )
            argument_audit = patch_end["metadata"]["native_tool_call_envelopes"][0]["metadata"][
                "provider_argument_audit"
            ]
            served_argument_hash = hashlib.sha256(
                json.dumps(served_patch, ensure_ascii=False).encode("utf-8")
            ).hexdigest()
            assert argument_audit["raw_arguments_sha256"] == served_argument_hash
            assert argument_audit["raw_arguments_length"] == len(json.dumps(served_patch, ensure_ascii=False))
            assert served_argument_hash != signal["candidate_hash"]
        drain = service._physical_attempt_coordinator(factory_run.id).drain_snapshot()
        assert drain.settled and not drain.blocking_reservation_ids
        lease = json.loads((runtime_root / "factory" / ".workspace_run_lease.json").read_text(encoding="utf-8"))
        assert lease["stage_execution_claim"] is None
        assert lease["lifecycle_operation_claim"] is None
        assert all(
            not (tmp_path / target).exists()
            for target in ("src/cancel.py", "tests/test_cancel.py", "requirements.txt", "README.md")
        )

    asyncio.run(run())
