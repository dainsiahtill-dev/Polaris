from __future__ import annotations

import asyncio

from polaris.bootstrap.config import Settings
from polaris.cells.llm.evaluation.public.service import EvaluationRunner
from polaris.kernelone.llm.engine import EvaluationRequest


async def _suite_capture(
    provider_cfg: dict[str, object],
    model: str,
    bucket: dict[str, object],
) -> dict[str, object]:
    bucket["provider_cfg"] = dict(provider_cfg)
    bucket["model"] = model
    return {"ok": True}


def test_runner_loads_provider_cfg_when_context_missing_type(tmp_path, monkeypatch):
    settings = Settings(workspace=str(tmp_path), ramdisk_root="")
    runner = EvaluationRunner(workspace=str(tmp_path), settings=settings)
    captured: dict[str, object] = {}

    async def fake_connectivity(provider_cfg: dict[str, object], model: str):
        return await _suite_capture(provider_cfg, model, captured)

    monkeypatch.setattr(
        runner,
        "_load_provider_cfg",
        lambda provider_id: (
            {
                "type": "anthropic_compat",
                "base_url": "https://kimi.moonshot.cn",
                "timeout": 30,
            }
            if provider_id == "anthropic_compat"
            else {}
        ),
    )
    monkeypatch.setitem(runner.SUITE_RUNNERS, "connectivity", fake_connectivity)

    request = EvaluationRequest(
        provider_id="anthropic_compat",
        model="kimi-for-coding",
        role="connectivity",
        suites=["connectivity"],
        context={"provider_cfg": {}},
        options={"update_index": False},
    )

    result = asyncio.run(runner.run(request))

    assert result.summary.get("ready") is True
    assert captured["model"] == "kimi-for-coding"
    assert captured["provider_cfg"] == {
        "type": "anthropic_compat",
        "base_url": "https://kimi.moonshot.cn",
        "timeout": 30,
    }


def test_runner_merges_loaded_provider_cfg_with_context_overrides(tmp_path, monkeypatch):
    settings = Settings(workspace=str(tmp_path), ramdisk_root="")
    runner = EvaluationRunner(workspace=str(tmp_path), settings=settings)
    captured: dict[str, object] = {}

    async def fake_connectivity(provider_cfg: dict[str, object], model: str):
        return await _suite_capture(provider_cfg, model, captured)

    monkeypatch.setattr(
        runner,
        "_load_provider_cfg",
        lambda provider_id: (
            {
                "type": "openai_compat",
                "base_url": "https://from-config.example",
                "timeout": 30,
            }
            if provider_id == "openai_compat"
            else {}
        ),
    )
    monkeypatch.setitem(runner.SUITE_RUNNERS, "connectivity", fake_connectivity)

    request = EvaluationRequest(
        provider_id="openai_compat",
        model="gpt-4.1-mini",
        role="connectivity",
        suites=["connectivity"],
        context={
            "provider_cfg": {
                "base_url": "https://override.example",
                "timeout": 10,
            },
            "api_key": "from_payload",
        },
        options={"update_index": False},
    )

    result = asyncio.run(runner.run(request))

    assert result.summary.get("ready") is True
    assert captured["provider_cfg"] == {
        "type": "openai_compat",
        "base_url": "https://override.example",
        "timeout": 10,
        "api_key": "from_payload",
    }


def test_runner_keeps_direct_provider_cfg_without_loading(tmp_path, monkeypatch):
    settings = Settings(workspace=str(tmp_path), ramdisk_root="")
    runner = EvaluationRunner(workspace=str(tmp_path), settings=settings)
    captured: dict[str, object] = {}

    async def fake_connectivity(provider_cfg: dict[str, object], model: str):
        return await _suite_capture(provider_cfg, model, captured)

    def _unexpected_load(_provider_id: str):
        raise AssertionError("should not load provider config when direct type is provided")

    monkeypatch.setattr(runner, "_load_provider_cfg", _unexpected_load)
    monkeypatch.setitem(runner.SUITE_RUNNERS, "connectivity", fake_connectivity)

    request = EvaluationRequest(
        provider_id="direct_openai_compat",
        model="gpt-4.1",
        role="connectivity",
        suites=["connectivity"],
        context={
            "provider_cfg": {
                "type": "openai_compat",
                "base_url": "https://direct.example",
                "api_path": "/v1/chat/completions",
            }
        },
        options={"update_index": False},
    )

    result = asyncio.run(runner.run(request))

    assert result.summary.get("ready") is True
    assert captured["provider_cfg"] == {
        "type": "openai_compat",
        "base_url": "https://direct.example",
        "api_path": "/v1/chat/completions",
    }
