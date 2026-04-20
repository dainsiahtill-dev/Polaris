from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKEND_ROOT = ROOT / "src" / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from polaris.cells.llm.control_plane.internal.tui_llm_client import LLMMessage, TUILLMClient  # noqa: E402
from polaris.cells.orchestration.workflow_runtime.internal.models import TaskContract  # noqa: E402
from polaris.cells.orchestration.workflow_runtime.internal.ports import RoleOrchestrationAdapter  # noqa: E402
from polaris.cells.orchestration.workflow_runtime.internal.runtime_contracts import (  # noqa: E402
    OrchestrationMode,
    OrchestrationRunRequest,
    RoleEntrySpec,
)
from polaris.cells.orchestration.workflow_runtime.internal.unified_orchestration_service import (  # noqa: E402
    UnifiedOrchestrationService,
    get_orchestration_service,
    reset_orchestration_service,
)
from polaris.kernelone.llm.toolkit.contracts import (  # noqa: E402
    AIResponse,
    ServiceLocator,
    StreamChunk,
    StreamEventType,
    Usage,
)
from polaris.kernelone.workflow.contracts import RetryPolicy, TaskSpec  # noqa: E402


class _FakeProvider:
    def __init__(self) -> None:
        self.generate_requests = []
        self.stream_requests = []

    async def generate(self, request):
        self.generate_requests.append(request)
        return AIResponse.success(
            output="service-locator-output",
            model="fake-model",
            provider_id="fake-provider",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            metadata={},
        )

    async def generate_stream(self, request):
        self.stream_requests.append(request)
        yield StreamChunk(content="chunk-a", event_type=StreamEventType.CHUNK)
        yield StreamChunk(content="reasoning", event_type=StreamEventType.REASONING_CHUNK)
        yield StreamChunk(content="chunk-b", event_type=StreamEventType.CHUNK)
        yield StreamChunk(content="", event_type=StreamEventType.COMPLETE, is_final=True)


class _CaptureRoleAdapter(RoleOrchestrationAdapter):
    def __init__(self, role_id: str) -> None:
        self._role_id = role_id
        self.calls: list[dict[str, object]] = []

    @property
    def role_id(self) -> str:
        return self._role_id

    async def execute(
        self,
        task_id: str,
        input_data: dict[str, object],
        context: dict[str, object],
    ) -> dict[str, object]:
        self.calls.append(
            {
                "task_id": task_id,
                "input_data": dict(input_data),
                "context": dict(context),
            }
        )
        return {"success": True}

    def get_capabilities(self) -> list[str]:
        return ["capture"]


def test_core_runtime_files_do_not_import_app_directly():
    targets = [
        BACKEND_ROOT / "polaris" / "cells" / "orchestration" / "workflow_runtime" / "internal" / "unified_orchestration_service.py",
        BACKEND_ROOT / "polaris" / "bootstrap" / "backend_bootstrap.py",
        BACKEND_ROOT / "polaris" / "cells" / "llm" / "control_plane" / "internal" / "tui_llm_client.py",
    ]

    for path in targets:
        assert path.is_file(), f"missing runtime module: {path}"
        content = path.read_text(encoding="utf-8")
        assert "from app." not in content, f"{path} should not import app.* directly"
        assert "import app." not in content, f"{path} should not import app.* directly"


@pytest.mark.asyncio
async def test_app_role_adapters_register_factory_for_core_singleton():
    reset_orchestration_service()
    from polaris.cells.roles.adapters.public.service import create_role_adapter

    service = await get_orchestration_service()
    assert service._role_adapter_factory is create_role_adapter


@pytest.mark.asyncio
async def test_tui_llm_client_uses_service_locator(monkeypatch):
    provider = _FakeProvider()
    monkeypatch.setattr(ServiceLocator, "_provider", provider, raising=False)

    client = TUILLMClient(role="director", workspace=".")
    seen_tokens: list[str] = []
    full = await client.chat_stream(
        [LLMMessage(role="user", content="hello")],
        on_token=seen_tokens.append,
    )

    assert full == "chunk-achunk-b"
    assert seen_tokens == ["chunk-a", "chunk-b"]
    assert len(provider.stream_requests) == 1


def test_tui_llm_client_is_configured_from_global_role_binding(monkeypatch):
    monkeypatch.setattr(ServiceLocator, "_provider", None, raising=False)

    from polaris.kernelone.llm import runtime_config as llm_runtime_config

    monkeypatch.setattr(
        llm_runtime_config,
        "get_role_model",
        lambda role: ("provider-bound", "model-bound") if role == "director" else ("", ""),
    )

    client = TUILLMClient(role="director", workspace=".")
    assert client.is_configured() is True


def test_task_contract_requires_canonical_id():
    contract = TaskContract.from_mapping({"task_id": "LEGACY-1", "title": "legacy"})
    assert contract.task_id == ""

    canonical = TaskContract.from_mapping({"id": "TASK-1", "title": "canonical"})
    assert canonical.task_id == "TASK-1"


def test_retry_policy_requires_seconds_suffix():
    policy = RetryPolicy.from_mapping({"initial_interval": 9, "max_interval": 10})
    assert policy.initial_interval_seconds == 0.2
    assert policy.max_interval_seconds == 5.0

    canonical = RetryPolicy.from_mapping(
        {"initial_interval_seconds": 3, "max_interval_seconds": 7}
    )
    assert canonical.initial_interval_seconds == 3.0
    assert canonical.max_interval_seconds == 7.0


def test_task_spec_requires_canonical_id():
    spec = TaskSpec.from_mapping(
        {"task_id": "LEGACY-1", "type": "noop"},
        default_timeout_seconds=30.0,
        default_retry_policy=RetryPolicy(),
    )
    assert spec.task_id == ""

    canonical = TaskSpec.from_mapping(
        {"id": "TASK-1", "type": "noop"},
        default_timeout_seconds=30.0,
        default_retry_policy=RetryPolicy(),
    )
    assert canonical.task_id == "TASK-1"


def test_unified_orchestration_service_ignores_legacy_timeout_metadata(tmp_path):
    service = UnifiedOrchestrationService()
    request = OrchestrationRunRequest(
        run_id="run-1",
        workspace=tmp_path,
        mode=OrchestrationMode.WORKFLOW,
        role_entries=[RoleEntrySpec(role_id="pm", input="demo")],
        metadata={"timeout": 12, "global_timeout": 34},
    )

    canonical = service._canonicalize_workflow_request(request)

    assert canonical.pipeline_spec is not None
    assert canonical.pipeline_spec.tasks[0].timeout_seconds == 3600
    assert canonical.pipeline_spec.global_timeout_seconds == 7200


@pytest.mark.asyncio
async def test_unified_orchestration_service_passes_role_entry_metadata_to_adapter(
    tmp_path: Path,
) -> None:
    adapter = _CaptureRoleAdapter("director")
    service = UnifiedOrchestrationService(role_adapters=[adapter])
    request = OrchestrationRunRequest(
        run_id="run-meta-1",
        workspace=tmp_path,
        mode=OrchestrationMode.WORKFLOW,
        role_entries=[
            RoleEntrySpec(
                role_id="director",
                input="执行受控后端任务",
                scope_paths=[str(tmp_path)],
                metadata={
                    "execution_backend": "projection_generate",
                    "projection": {
                        "scenario_id": "scenario_alpha",
                        "project_slug": "projection_lab",
                    },
                },
            )
        ],
    )

    await service.submit_run(request)
    task = service._active_runs.get("run-meta-1")  # noqa: SLF001
    assert task is not None
    await asyncio.wait_for(task, timeout=5)

    assert adapter.calls
    input_data = adapter.calls[0]["input_data"]
    assert isinstance(input_data, dict)
    metadata = input_data.get("metadata")
    assert isinstance(metadata, dict)
    assert metadata["execution_backend"] == "projection_generate"
    assert metadata["projection"]["scenario_id"] == "scenario_alpha"
