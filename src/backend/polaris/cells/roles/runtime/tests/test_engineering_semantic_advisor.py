from __future__ import annotations

import asyncio
import json

from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import (
    RemoteAdvisorConfig,
    RemoteSemanticAdvisor,
    _json_content,
    advisor_configs_from_workspace,
)


def test_json_content_strips_code_fence() -> None:
    assert _json_content('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_summary_evidence_refs_are_whitelisted_deduped_and_capped() -> None:
    class FakeAdvisor(RemoteSemanticAdvisor):
        def _post(self, messages: list[dict[str, str]], schema: dict[str, object] | None = None) -> str:
            return json.dumps(
                {
                    "node_id": "a.py",
                    "snapshot_id": "filecard:a",
                    "summary": "旧文件摘要。",
                    "summary_kind": "model_inference",
                    "coverage": {"observed_line_ranges": [[1, 1]], "summary_complete": False},
                    "evidence_refs": ["filecard:a"] * 10 + ["failure_1"] * 5 + ["invented"],
                    "uncertain": False,
                    "reason": "基于快照。",
                }
            )

    advisor = FakeAdvisor(RemoteAdvisorConfig(base_url="http://localhost", model="fake"))
    result = asyncio.run(
        advisor.summarize_node(
            {
                "node_id": "a.py",
                "snapshot_id": "filecard:a",
                "coverage": {"observed_line_ranges": [[1, 1]]},
                "allowed_evidence_refs": ["failure_1"],
            }
        )
    )
    assert result["evidence_refs"] == ["filecard:a", "failure_1"]


def test_workspace_advisor_config_overrides_environment(tmp_path, monkeypatch) -> None:
    config_dir = tmp_path / ".polaris"
    config_dir.mkdir()
    (config_dir / "config.json").write_text(
        json.dumps({"agents": {"distiller": [{"base_url": "http://ws", "model": "ws-model"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(
        "KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG",
        json.dumps([{"base_url": "http://env", "model": "env-model"}]),
    )
    assert advisor_configs_from_workspace(tmp_path) == [RemoteAdvisorConfig("http://ws", "ws-model")]


def test_advisor_config_falls_back_to_environment_and_ignores_malformed(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(
        "KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG",
        json.dumps([{"base_url": "http://env", "model": "env-model"}]),
    )
    assert advisor_configs_from_workspace(tmp_path) == [RemoteAdvisorConfig("http://env", "env-model")]
    monkeypatch.setenv("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG", "{not json")
    assert advisor_configs_from_workspace(tmp_path) == []
    monkeypatch.delenv("KERNELONE_CONTEXTOS_SEMANTIC_ADVISOR_CONFIG")
    assert advisor_configs_from_workspace(tmp_path) == []


async def test_invalid_reference_type_is_a_rejected_node(monkeypatch) -> None:
    from polaris.cells.roles.runtime.internal.engineering_semantic_advisor_pool import RemoteAdvisorPool

    def malformed(self, messages, schema=None):
        return json.dumps({"evidence_refs": [{}]})

    monkeypatch.setattr(RemoteSemanticAdvisor, "_post", malformed)
    pool = RemoteAdvisorPool([RemoteAdvisorConfig(base_url="http://localhost", model="fixture")])
    rows = await pool.summarize_many(
        [{"node_id": "file:a", "snapshot_id": "filecard:a", "coverage": {"observed_line_ranges": [[1, 1]]}}]
    )
    assert rows[0]["validation_result"] == "REJECTED"
    assert "evidence_refs" in rows[0]["error"]
