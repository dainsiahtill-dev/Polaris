from __future__ import annotations

import asyncio
import json
from pathlib import Path

from polaris.cells.roles.runtime.internal.engineering_epoch_kimi import KimiEpochPool, kimi_epoch_pool_from_environment


def test_devourer_role_binding_drives_kimi_pool(tmp_path: Path, monkeypatch) -> None:
    config = tmp_path / "llm.json"
    config.write_text(
        json.dumps(
            {
                "providers": {"kimi": {"base_url": "https://kimi.example", "api_key": "secret"}},
                "roles": {"devourer": {"provider_id": "kimi", "model": "kimi-for-coding-highspeed"}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("KERNELONE_LLM_CONFIG", str(config))
    pool = kimi_epoch_pool_from_environment()
    assert pool is not None and pool.model == "kimi-for-coding-highspeed"
    assert pool.base_url == "https://kimi.example"


def test_kimi_epoch_pool_validates_summary() -> None:
    pool = KimiEpochPool(base_url="https://kimi.example", api_key="secret", model="kimi-for-coding-highspeed")
    pool._post = lambda _request: json.dumps(
        {
            "node_id": "epoch-0001-0010",
            "snapshot_id": "epoch-0001-0010",
            "summary": "完成基础模块。",
            "summary_kind": "model_inference",
            "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
            "evidence_refs": [],
            "uncertain": False,
            "reason": "基于关闭 epoch。",
        }
    )
    result = asyncio.run(
        pool.summarize_many(
            [
                {
                    "node_id": "epoch-0001-0010",
                    "snapshot_id": "epoch-0001-0010",
                    "coverage": {"observed_line_ranges": [[1, 1]]},
                    "allowed_evidence_refs": [],
                }
            ]
        )
    )
    assert result[0]["validation_result"] == "VALID"
