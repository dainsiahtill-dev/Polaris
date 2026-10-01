"""Regression: physical provider PASS must reach the same workspace's socket."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from polaris.cells.runtime.projection.public.service import resolve_workspace_runtime_context
from polaris.delivery.http.routers import tests as tests_router
from polaris.delivery.http.routers.llm_models import LlmTestPayload
from polaris.delivery.ws.endpoints.protocol_utils import build_v2_subscription_subjects


@pytest.mark.asyncio
async def test_test_events_reach_only_bound_workspace_consumer(tmp_path, monkeypatch):
    """A global/synthetic publisher or missing channel mapping loses terminal PASS."""
    workspace = tmp_path / "alpha"
    other_workspace = tmp_path / "beta"
    workspace.mkdir()
    other_workspace.mkdir()
    context = resolve_workspace_runtime_context(configured_workspace=str(workspace), default_workspace=str(workspace))
    other = resolve_workspace_runtime_context(
        configured_workspace=str(other_workspace), default_workspace=str(other_workspace)
    )
    published: list[tuple[str, dict[str, Any]]] = []

    async def provider_result(**kwargs):
        assert kwargs["workspace"] == str(workspace)
        return {
            "test_run_id": "report-1",
            "target": {},
            "suites": {"connectivity": {"ok": True}},
            "final": {"ready": True, "grade": "PASS"},
        }

    async def network_sink(*, subject: str, payload: dict[str, Any]) -> bool:
        published.append((subject, payload))
        return True

    monkeypatch.setattr(tests_router, "run_llm_tests", provider_result)
    monkeypatch.setattr(tests_router, "publish_to_jetstream", network_sink)
    await tests_router._run_llm_test_jetstream(
        settings=SimpleNamespace(workspace=str(workspace)),
        workspace=str(workspace),
        payload=LlmTestPayload(role="connectivity", provider_id="provider-1", model="model-1"),
        test_context=SimpleNamespace(
            role="connectivity",
            effective_provider_id="provider-1",
            model="model-1",
            suites=["connectivity"],
            use_direct_config=False,
            provider_cfg=None,
        ),
        run_id="test-1",
    )
    expected = f"hp.runtime.{context.workspace_key}.llm.test.test-1"
    assert build_v2_subscription_subjects(context.workspace_key, ["llm-test:test-1"]) == [expected]
    assert [event["payload"]["type"] for _, event in published] == [
        "start",
        "suite_start",
        "suite_result",
        "suite_complete",
        "complete",
    ]
    assert all(subject == expected for subject, _ in published)
    assert all(event["workspace_key"] == context.workspace_key for _, event in published)
    assert all(event["meta"]["workspace"] == str(workspace) for _, event in published)
    assert published[-1][1]["payload"]["data"]["final"]["ready"] is True
    assert expected not in build_v2_subscription_subjects(other.workspace_key, ["llm-test:test-1"])


@pytest.mark.parametrize("run_id", ["test-1", "test.v2", "x" * 96])
def test_connection_test_channel_maps_valid_run_ids(run_id):
    assert build_v2_subscription_subjects("alpha", [f"llm-test:{run_id}"]) == [f"hp.runtime.alpha.llm.test.{run_id}"]


@pytest.mark.parametrize("run_id", ["", "*", ">", "test.*", "test.>", "a..b", "x" * 97])
def test_connection_test_channel_rejects_wildcard_or_malformed_run_ids(run_id):
    assert build_v2_subscription_subjects("alpha", [f"llm-test:{run_id}"]) == []
