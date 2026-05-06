"""Tests for evidence_collector hash behavior."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from polaris.domain.verification.evidence_collector import EvidenceCollector, EvidencePackage


def _expected_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def test_file_change_hashes_use_explicit_utf8_for_non_ascii() -> None:
    before = "旧值\n"
    after = "新值 Δ\n"

    collector = EvidenceCollector(task_id="task-utf8")
    collector.record_file_change(
        path="src/example.py",
        change_type="modified",
        content_before=before,
        content_after=after,
    )

    file_change = collector.get_package().file_changes[0]
    assert file_change.hash_before == _expected_hash(before)
    assert file_change.hash_after == _expected_hash(after)


def test_llm_interaction_hashes_use_explicit_utf8_for_non_ascii() -> None:
    prompt = "请验证证据包"
    output = "通过: café Δ"

    collector = EvidenceCollector(task_id="task-llm")
    collector.record_llm_interaction(role="qa", prompt=prompt, output=output)

    llm_interaction = collector.get_package().llm_interactions[0]
    assert llm_interaction.prompt_hash == _expected_hash(prompt)
    assert llm_interaction.output_hash == _expected_hash(output)


def test_evidence_package_hash_is_stable_for_reordered_json_keys() -> None:
    created_at = datetime(2026, 5, 6, 1, 2, 3, tzinfo=timezone.utc)

    first = EvidencePackage(
        task_id="task-stable",
        created_at=created_at,
        audit_entries=[{"z": "终", "a": "始"}],
        summary="包含非 ASCII 的摘要",
    )
    second = EvidencePackage(
        task_id="task-stable",
        created_at=created_at,
        audit_entries=[{"a": "始", "z": "终"}],
        summary="包含非 ASCII 的摘要",
    )

    assert first.compute_hash() == second.compute_hash()
    assert first.compute_hash() == _package_expected_hash(first.to_dict())


def _package_expected_hash(package: dict[str, Any]) -> str:
    content = json.dumps(package, sort_keys=True, ensure_ascii=False)
    return _expected_hash(content)
