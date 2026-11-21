from __future__ import annotations

import asyncio
import json

from polaris.cells.roles.runtime.internal.engineering_semantic_advice import (
    CandidateRefs,
    ValidationResult,
    build_advice_envelope,
)
from polaris.cells.roles.runtime.internal.engineering_semantic_advisor import (
    RemoteAdvisorConfig,
    RemoteSemanticAdvisor,
    _json_content,
)


def test_json_content_strips_code_fence() -> None:
    assert _json_content('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_remote_advice_validates_real_contract_shape() -> None:
    class FakeAdvisor(RemoteSemanticAdvisor):
        def _post(self, messages: list[dict[str, str]], schema: dict[str, object] | None = None) -> str:
            return json.dumps(
                {
                    "assessments": [
                        {
                            "node_id": "file_util_hpp",
                            "relation": "FAILURE_CANDIDATE",
                            "suggested_view": "SLICE",
                            "keep_slice_ids": ["slice_display_width"],
                            "evidence_refs": ["failure_unicode_alignment"],
                            "uncertain": True,
                            "reason": "失败涉及显示宽度。",
                        }
                    ],
                    "need_evidence": [],
                }
            )

    advisor = FakeAdvisor(RemoteAdvisorConfig(base_url="http://localhost", model="fake"))
    result = asyncio.run(
        advisor.advise(
            {},
            CandidateRefs(
                node_ids={"file_util_hpp"},
                slice_ids={"slice_display_width"},
                evidence_refs={"failure_unicode_alignment"},
            ),
        )
    )
    assert result["assessments"][0]["suggested_view"] == "SLICE"


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


def test_advice_envelope_binds_fingerprint_and_provenance() -> None:
    envelope = build_advice_envelope(
        advice_job_id="job-1",
        input_package={"goal": "fix"},
        source_snapshot_ids=["snapshot_util_v17"],
        related_requirement_versions=["req_3"],
        related_failure_versions=["failure_9"],
        model_id="local-model",
        model_revision="unknown",
        prompt_version="semantic-advice-v1",
        created_at=1.0,
        finished_at=2.0,
        validation_result=ValidationResult.VALID,
    )
    assert envelope["schema_version"] == "semantic-advice.v1"
    assert envelope["validation_result"] == "VALID"
    assert len(envelope["source_input_fingerprint"]) == 64
