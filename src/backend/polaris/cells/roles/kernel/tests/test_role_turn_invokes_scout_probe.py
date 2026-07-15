"""End-to-end audit: PM / Chief Engineer / Director really reach the scout cell.

This is the wiring proof the existing scout tests do NOT give:

* ``roles/scout/internal/tests/test_whitelist`` and ``test_role_wiring`` only assert
  ``scout_probe`` sits in the loaded whitelist — static config, no execution.
* ``roles/scout/public/tests/test_service`` only drives ``AgentAccelToolExecutor``
  directly — it never goes through a role's own turn / authorization path.

Neither proves that, on the real role-kernel turn path, (1) the model is *offered*
``scout_probe`` and (2) when the model emits that call, the role's real
authorization + dispatch chain actually reaches the ``roles.scout`` cell and runs
real read-only reconnaissance.

All three are proven here for pm / chief_engineer / director:

1. ``test_role_offers_scout_probe_to_llm`` — the native tool schemas handed to the
   provider as ``tools=`` contain ``scout_probe``.
2. ``test_role_dispatch_reaches_scout_cell`` — the kernel's tool-runtime owner
   authorizes against the real whitelist and runs the scout cell.
3. ``test_full_analyze_turn_invokes_scout_cell`` — a complete ``kernel.run()`` turn
   (model call -> tool dispatch -> tool result -> finalization) genuinely runs the
   scout cell and surfaces a real symbol.

Only the LLM is scripted; the RoleToolGateway, AgentAccelToolExecutor, the
scout_probe handler and the scout cell all run for real. Each role's tool policy is
the real one loaded from the runtime SSOT (``core_roles.yaml``), so gateway
authorization is byte-for-byte the production behaviour.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import polaris.cells.roles.scout.public.service as scout_service
import pytest
from polaris.cells.roles.kernel.internal.kernel import RoleExecutionKernel
from polaris.cells.roles.kernel.internal.kernel.tool_runtime_executor import execute_single_tool
from polaris.cells.roles.kernel.internal.llm_caller.tool_helpers import build_native_tool_schemas
from polaris.cells.roles.profile.public.service import (
    RoleExecutionMode,
    RoleProfileRegistry,
    RoleTurnRequest,
)
from polaris.cells.roles.scout.public.contracts import ScoutProbeTargetV1, ScoutReportV1

# The three roles the audit targets.
ROLES_UNDER_AUDIT: tuple[str, ...] = ("pm", "chief_engineer", "director")

# Runtime SSOT consumed by load_core_roles().
# This file: cells/roles/kernel/tests/ -> parents[2] = cells/roles.
_CORE_ROLES_YAML: Path = Path(__file__).resolve().parents[2] / "profile" / "config" / "roles" / "core_roles.yaml"


@pytest.fixture(scope="module")
def loaded_registry() -> RoleProfileRegistry:
    """A fresh registry populated from the real core_roles.yaml SSOT."""
    assert _CORE_ROLES_YAML.exists(), f"SSOT config missing: {_CORE_ROLES_YAML}"
    reg = RoleProfileRegistry()
    reg.load_from_yaml(_CORE_ROLES_YAML)
    return reg


@pytest.fixture(autouse=True)
def _patch_model_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve any (provider, model) to a tools-capable spec (no llm_config files)."""
    from polaris.kernelone.llm.engine.contracts import ModelSpec
    from polaris.kernelone.llm.engine.model_catalog import ModelCatalog

    fake_spec = ModelSpec(
        provider_id="openai",
        provider_type="openai",
        model="gpt-5",
        max_context_tokens=128000,
        max_output_tokens=4096,
        supports_tools=True,
    )
    monkeypatch.setattr(ModelCatalog, "resolve", lambda self, provider_id, model, **kw: fake_spec)


class _StubRegistry:
    """Minimal profile registry returning a single pre-built profile."""

    def __init__(self, profile: object) -> None:
        self._profile = profile

    def get_profile_or_raise(self, _role: str) -> object:
        return self._profile


async def _noop_call_stream(**_kw: Any) -> Any:
    return
    yield  # pragma: no cover - make this an async generator


async def _noop_call(**_kw: Any) -> Any:
    return SimpleNamespace(content="", tool_calls=[], error=None, metadata={})


def _build_kernel(*, workspace: str, role: str, real_profile: Any, fake_call: Any | None = None) -> RoleExecutionKernel:
    """Build a kernel that runs the REAL tool path for ``role``.

    ``tool_policy`` is the real object loaded from core_roles.yaml, so the
    RoleToolGateway enforces the exact production whitelist + permission flags.
    Only the prompt builder and the LLM caller are stubbed.
    """
    profile = SimpleNamespace(
        role_id=role,
        model="gpt-5",
        provider_id="openai",
        version="1.0.0",
        tool_policy=real_profile.tool_policy,  # real whitelist + permission flags
        context_policy=SimpleNamespace(
            max_context_tokens=100000,
            max_history_turns=20,
            compression_strategy="none",
            include_project_structure=False,
            include_task_history=False,
        ),
    )
    prompt_builder = SimpleNamespace(
        build_fingerprint=lambda _p, _a: SimpleNamespace(full_hash="fp-scout", core_hash="fp-scout"),
        build_system_prompt=lambda _p, _a: "system-prompt",
        build_retry_prompt=lambda _base, _q, _attempt: "system-prompt",
    )
    return RoleExecutionKernel(
        workspace=workspace,
        registry=_StubRegistry(profile),  # type: ignore[arg-type]
        prompt_builder=prompt_builder,
        llm_invoker=SimpleNamespace(
            call=fake_call or _noop_call,
            call_stream=_noop_call_stream,
        ),
    )


def _scout_native_call(query: str, *, call_id: str = "call_scout") -> dict[str, Any]:
    """An OpenAI-shaped native tool call selecting scout_probe."""
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": "scout_probe", "arguments": json.dumps({"query": query})},
    }


def _make_fake_call(query: str) -> Any:
    """LLM script: the first turn emits a scout_probe call; later turns finalize."""
    state = {"n": 0}

    async def _fake_call(**_kwargs: Any) -> Any:
        state["n"] += 1
        if state["n"] == 1:
            return SimpleNamespace(
                content="先用 scout_probe 侦察目标符号的位置。",
                tool_calls=[_scout_native_call(query)],
                tool_call_provider="openai",
                token_estimate=40,
                error=None,
                error_category=None,
                metadata={},
            )
        return SimpleNamespace(
            content="侦察完成，已定位目标实现。",
            tool_calls=[],
            tool_call_provider="auto",
            token_estimate=20,
            error=None,
            error_category=None,
            metadata={},
        )

    return _fake_call


class _ProbeSpy:
    """Wraps a real ScoutProbeService, recording the query and the report."""

    def __init__(self, inner: Any, queries: list[str], reports: list[ScoutReportV1]) -> None:
        self._inner = inner
        self._queries = queries
        self._reports = reports

    async def probe(self, target: ScoutProbeTargetV1) -> ScoutReportV1:
        self._queries.append(target.query)
        report = await self._inner.probe(target)
        self._reports.append(report)
        return report


def _install_scout_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[str], list[str], list[ScoutReportV1]]:
    """Spy on the scout cell's single public chokepoint, delegating to the real factory.

    Returns (workspaces, queries, reports) lists populated as the cell runs.
    """
    real_factory = scout_service.build_default_scout_service
    workspaces: list[str] = []
    queries: list[str] = []
    reports: list[ScoutReportV1] = []

    def _spy_factory(workspace: str) -> Any:
        workspaces.append(workspace)
        return _ProbeSpy(real_factory(workspace), queries, reports)

    monkeypatch.setattr(scout_service, "build_default_scout_service", _spy_factory)
    return workspaces, queries, reports


def _assert_real_scout_report(report: ScoutReportV1, role: str) -> None:
    """The report must be a genuine scout-cell reconnaissance of the temp workspace."""
    assert report.content_hash, f"role {role!r}: scout report missing content_hash"
    assert any(f.path.endswith("pay.py") for f in report.findings), (
        f"role {role!r}: scout cell did not surface pay.py; findings={report.findings!r}"
    )
    assert "tools_used" in report.coverage, f"role {role!r}: scout coverage missing tools_used"


# ── Proof 1: scout_probe is OFFERED to the model (real profile -> native schemas) ──


@pytest.mark.parametrize("role", ROLES_UNDER_AUDIT)
def test_role_offers_scout_probe_to_llm(loaded_registry: RoleProfileRegistry, role: str) -> None:
    """The native tool schema list handed to the provider as ``tools=`` for each
    role contains ``scout_probe`` — i.e. the model is actually told it exists."""
    profile = loaded_registry.get_profile(role)
    assert profile is not None, f"role {role!r} missing from loaded registry"

    schemas = build_native_tool_schemas(profile)
    names = {str((s.get("function") or {}).get("name") or "") for s in schemas if isinstance(s, dict)}
    assert "scout_probe" in names, (
        f"role {role!r}: scout_probe absent from native tool schemas offered to the LLM. Offered tools: {sorted(names)}"
    )


# ── Proof 2: the role's real tool-dispatch method reaches and runs the scout cell ──


@pytest.mark.parametrize("role", ROLES_UNDER_AUDIT)
def test_role_dispatch_reaches_scout_cell(
    loaded_registry: RoleProfileRegistry,
    role: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The TransactionKernel tool-runtime owner authorizes ``scout_probe`` and
    dispatches it to the scout cell, which runs real read tools against a temp
    workspace. Contract-mode independent."""
    real_profile = loaded_registry.get_profile(role)
    assert real_profile is not None, f"role {role!r} missing from loaded registry"

    (tmp_path / "pay.py").write_text("def payment_gateway():\n    return 1\n", encoding="utf-8")
    workspaces, queries, reports = _install_scout_spy(monkeypatch)

    kernel = _build_kernel(workspace=str(tmp_path), role=role, real_profile=real_profile)
    profile = kernel.registry.get_profile_or_raise(role)
    request = RoleTurnRequest(mode=RoleExecutionMode.CHAT, workspace=str(tmp_path), message="audit")

    outcome = asyncio.run(
        execute_single_tool(
            kernel,
            tool_name="scout_probe",
            args={"query": "payment_gateway"},
            context={"profile": profile, "request": request},
        )
    )

    # The gateway authorized + dispatched, and the scout cell genuinely ran.
    assert outcome.get("success") is True, f"role {role!r}: scout dispatch failed: {outcome!r}"
    assert workspaces == [str(tmp_path)], f"role {role!r}: scout cell not invoked with the workspace"
    assert queries == ["payment_gateway"], f"role {role!r}: scout received unexpected queries: {queries!r}"
    _assert_real_scout_report(reports[0], role)


# ── Proof 3: a complete ANALYZE_ONLY turn runs the scout cell end-to-end ──


@pytest.mark.parametrize("role", ROLES_UNDER_AUDIT)
def test_full_analyze_turn_invokes_scout_cell(
    loaded_registry: RoleProfileRegistry,
    role: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A real ``kernel.run()`` turn whose scripted LLM emits a ``scout_probe`` call
    runs the full loop (LLM -> tool dispatch -> tool result -> finalization) and
    genuinely executes the scout cell. The ``[mode:analyze]`` marker pins the turn
    to ANALYZE_ONLY so a read-only reconnaissance batch finalizes cleanly (the
    write-tool delivery contract does not apply to analysis turns)."""
    real_profile = loaded_registry.get_profile(role)
    assert real_profile is not None, f"role {role!r} missing from loaded registry"

    (tmp_path / "pay.py").write_text("def payment_gateway():\n    return 1\n", encoding="utf-8")
    workspaces, queries, reports = _install_scout_spy(monkeypatch)

    kernel = _build_kernel(
        workspace=str(tmp_path),
        role=role,
        real_profile=real_profile,
        fake_call=_make_fake_call("payment_gateway"),
    )
    request = RoleTurnRequest(
        mode=RoleExecutionMode.CHAT,
        workspace=str(tmp_path),
        message="[mode:analyze] 请搜索 payment_gateway 在代码库中的定义位置",
        history=[],
        context_override={},
        metadata={"turn_request_id": f"scout-probe-{role}"},
        validate_output=False,
    )

    result = asyncio.run(kernel.run(role, request))

    # The turn completed cleanly by actually running the scout cell.
    assert result.error is None, f"role {role!r} turn errored: {result.error}"
    assert result.is_complete is True, f"role {role!r} turn did not complete"
    assert workspaces == [str(tmp_path)], f"role {role!r}: scout cell was never invoked in-turn"
    assert queries == ["payment_gateway"], f"role {role!r}: scout received unexpected queries: {queries!r}"

    # The model's scout_probe call was the dispatched tool.
    assert result.tool_calls, f"role {role!r}: no tool calls recorded"
    assert result.tool_calls[0]["tool"] == "scout_probe", (
        f"role {role!r}: dispatched tool was {result.tool_calls[0].get('tool')!r}, not scout_probe"
    )
    assert result.tool_results and result.tool_results[0]["tool"] == "scout_probe"
    _assert_real_scout_report(reports[0], role)
